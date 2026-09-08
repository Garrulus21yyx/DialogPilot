"""Frozen planning ablations; no business execution, judge or hidden model retries."""
import argparse
import asyncio
import gzip
import hashlib
import json
import os
from dataclasses import asdict, replace
from pathlib import Path

from dotenv import dotenv_values
from langchain_core.embeddings import Embeddings
from langchain_core.example_selectors import SemanticSimilarityExampleSelector
from langchain_core.vectorstores import InMemoryVectorStore

from application.conversation_agent import ConversationAgent
from application.conversation_state import ConversationState
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import DeterministicResolver, TurnObservations
from application.entity_binding import EntityBindingResolver
from application.target_conversation_manager import TargetTurnContext, TargetContextMessage, TargetContextProjectionStatus
from core.framework_models import framework_model
from core.model_policy import ModelPolicy, ModelProfile, ModelRole, ReasoningEffort
from evaluation.framework_capture import FrameworkCapture
from evaluation.planning_guidance import render_guidance, selection_input
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider

DATA = Path("evaluation/data/planning-demonstrations-v1.json")
DEVELOPMENT = Path("artifacts/eval/rag-query-calibration12-2026-09-08")
MODEL_PATH = "/home/yang/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/5617a9f61b028005a4858fdac845db406aefb181"


class LocalEmbeddings(Embeddings):
    """Only adapts existing local sentence-transformers to the SDK selector."""
    def __init__(self):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(MODEL_PATH, device="cpu", local_files_only=True)

    def embed_documents(self, texts):
        return self.model.encode(texts, normalize_embeddings=True).tolist()

    def embed_query(self, text):
        return self.embed_documents([text])[0]


def dev_cases():
    manifest = json.loads((DEVELOPMENT / "manifest.json").read_text())
    roles = json.loads((DEVELOPMENT / "history-roles.json").read_text())
    cases = [{"id": c["case_id"], "query": c["query"], "scope": "government",
              "history": [{"role": r, "content": t} for r, t in zip(roles[c["case_id"]], c["history"], strict=True)]}
             for c in manifest["cases"]]
    controls = [
        ("control_thanks", "谢谢，已经清楚了。", [], "respond"),
        ("control_ambiguous", "那个怎么办？", [], "clarify"),
        ("control_policy", "先别改地址，我只想知道发货后还能否改。", [], "knowledge"),
        ("control_condition", "不是质量问题。", [("user", "鞋子穿过一次能退吗？"), ("assistant", "是质量问题吗？")], "knowledge"),
        ("control_switch", "不用说保修了，我只问电子发票的申请流程。", [("user", "耳机保修多久？")], "knowledge"),
        ("control_thanks_question", "Thanks! One more thing: how does gift wrapping work?", [], "knowledge"),
    ]
    return cases + [{"id": i, "query": q, "scope": "commerce", "history": [{"role": r, "content": t} for r,t in h], "expected_action": e} for i,q,h,e in controls]


def action(proposal):
    if proposal.disposition.value in {"INVALID_PROVIDER_OUTPUT", "PROVIDER_FAILURE"}:
        return "error"
    if proposal.commands:
        return "knowledge" if all(c.tool_id == "knowledge_search" for c in proposal.commands) else "delegate"
    return "respond" if proposal.disposition.value == "RESPOND" else "clarify" if proposal.disposition.value == "CLARIFY" else proposal.disposition.value.lower()


async def run(args):
    if AnthropicConversationPlanningProvider.version != 'anthropic-conversation-provider-v18-partial-input':
        raise RuntimeError('Frozen v18 experiment: audit retained artifacts, or check out d2287a2 to reproduce. Do not apply old structured examples to native actions.')
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    examples = json.loads(DATA.read_text())
    cases = dev_cases() if args.phase == "dev" else json.loads(Path(args.cases).read_text())
    if isinstance(cases, dict):
        cases = cases["cases"]
    arms = args.arms.split(",")
    allowed = {"baseline", "contract", "fixed", "dynamic"}
    if not set(arms) <= allowed or len(arms) != len(set(arms)):
        raise ValueError("unknown or duplicate experiment arm")
    manifest = {"cases": cases, "arms": arms, "examples": examples, "model": "deepseek-v4-flash",
                "reasoning": "none", "max_output_tokens": 2048, "sdk_retries": 0,
                "calls_budget": len(cases)*len(arms), "concurrency": 2,
                "scope": "planning only; transcript-only inputs, not persistent turn E2E",
                "sources": {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in (
                    __file__, str(DATA), "evaluation/planning_guidance.py", "infrastructure/target_conversation_provider.py",
                    "application/conversation_agent.py", "infrastructure/target_model_context.py")}}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n")
    selector = None
    if "dynamic" in arms:
        selector = SemanticSimilarityExampleSelector.from_examples(
            [{"input": e["input"], "id": e["id"]} for e in examples], LocalEmbeddings(),
            InMemoryVectorStore, k=3, input_keys=["input"])
    values = {k: str(v) for k,v in dotenv_values(".env").items() if v is not None}
    values.update(os.environ)
    policy = ModelPolicy.from_env(values)
    profile = ModelProfile(model="deepseek-v4-flash", reasoning=ReasoningEffort.NONE, provider="deepseek")
    model = framework_model(profile, {"api_key": values["ANTHROPIC_API_KEY"], "base_url": policy.base_url}, max_tokens=2048)
    semaphore = asyncio.Semaphore(2)

    async def one(case, arm):
        async with semaphore:
            capture = FrameworkCapture(limit=1)
            selected = []
            class Provider(AnthropicConversationPlanningProvider):
                async def _complete(self, payload, role, system, **kwargs):
                    if arm in {"contract", "fixed", "dynamic"}:
                        if arm == "fixed":
                            selected.extend(examples[:3])
                        elif arm == "dynamic":
                            hits = selector.select_examples({"input": selection_input(payload)})
                            selected.extend(next(e for e in examples if e["id"] == hit["id"]) for hit in hits)
                        system = render_guidance(selected) + system
                    return await super()._complete(payload, role, system, **kwargs)
            provider = Provider({ModelRole.INTENT: model}, model_profile=profile, synthesis_profile=profile,
                                max_tokens=2048, callbacks=(capture,))
            registry = build_default_capability_registry("local-eval")
            if case.get("scope") == "government":
                registry = replace(registry, bundle_version="doc2dial-public-calibration-v1", agents=tuple(
                    replace(a, description="Explain public government service documentation for DMV, Social Security, Veterans Affairs and Student Aid. Retrieve policy and procedure evidence; do not execute government account actions.") if a.agent_id == "general" else a for a in registry.agents))
            state = ConversationState.empty(tenant_id="local-eval", user_id="local-user", conversation_id=case["id"])
            obs = TurnObservations(case["query"], ())
            history = tuple(TargetContextMessage(h["role"], h["content"], f"{case['id']}:{i}", i+1) for i,h in enumerate(case["history"]))
            context = TargetTurnContext(recent_messages=history, projection_status=TargetContextProjectionStatus.READY,
                                        source_watermark=len(history), projection_reason_codes=())
            context = replace(context, entity_bindings=EntityBindingResolver().resolve(obs,state,context))
            row = {"case_id": case["id"], "arm": arm}
            try:
                proposal = await ConversationAgent(provider).plan(obs,state,DeterministicResolver().resolve(obs,state),registry,context)
                row.update(proposal=asdict(proposal), action=action(proposal))
            except Exception as exc:
                row.update(action="error", error_type=type(exc).__name__)
            row.update(example_ids=[e["id"] for e in selected], calls=capture.calls)
            with (output / "results.jsonl").open("a") as stream:
                stream.write(json.dumps(row, ensure_ascii=False, default=lambda x: x.value if hasattr(x,"value") else str(x))+"\n")
            print(case["id"], arm, row["action"], flush=True)
    # Rotate arm ordering per case, preserving all failed outcomes rather than retrying.
    await asyncio.gather(*(one(c, arm) for i,c in enumerate(cases) for arm in arms[i%len(arms):]+arms[:i%len(arms)]))
    data = (output / "results.jsonl").read_bytes()
    (output / "results.jsonl.gz").write_bytes(gzip.compress(data, mtime=0))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["dev", "acceptance"], default="dev")
    parser.add_argument("--cases")
    parser.add_argument("--arms", default="baseline,contract,fixed,dynamic")
    parser.add_argument("--output", required=True)
    asyncio.run(run(parser.parse_args()))

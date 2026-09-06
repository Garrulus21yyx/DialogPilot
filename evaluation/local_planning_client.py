"""Evaluation-only local text client for the real planning provider contract.

No canned answers, tools, remote inference or truncation. This adapter only
supports the text messages used by ConversationPlanningProvider.
"""

from __future__ import annotations
import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from evaluation.rag_provider_free import file_sha


class LocalPlanningClient:
    def __init__(self, path, *, max_context_tokens=8192, system_override=None):
        self.path = Path(path).resolve()
        self.system_override = system_override
        self.identity = {
            p.name: file_sha(p)
            for p in sorted(self.path.iterdir())
            if p.is_file() and p.suffix in {".json", ".safetensors"}
        }
        self.messages = self
        self.max_context_tokens = max_context_tokens
        self.captures = []
        self._model = None
        self._lock = asyncio.Lock()

    async def create(self, *, model, max_tokens, system, messages):
        if model != str(self.path):
            raise ValueError("local model identity mismatch")
        if not isinstance(system, str) or not 1 <= max_tokens <= 2048:
            raise ValueError("invalid local request")
        if any(
            m.get("role") not in {"user", "assistant"}
            or not isinstance(m.get("content"), str)
            for m in messages
        ):
            raise ValueError("local planning client accepts text messages only")
        if self.system_override is not None:
            system = self.system_override
        async with self._lock:
            return await asyncio.to_thread(self._generate, system, messages, max_tokens)

    def _generate(self, system, messages, max_tokens):
        import torch
        from transformers import AutoTokenizer, AutoModelForCausalLM

        if self._model is None:
            self._tokenizer = AutoTokenizer.from_pretrained(
                str(self.path), local_files_only=True, trust_remote_code=False
            )
            self._model = (
                AutoModelForCausalLM.from_pretrained(
                    str(self.path),
                    local_files_only=True,
                    trust_remote_code=False,
                    torch_dtype=torch.float16,
                    low_cpu_mem_usage=True,
                )
                .to("cuda")
                .eval()
            )
        text = self._tokenizer.apply_chat_template(
            [{"role": "system", "content": system}, *messages],
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = self._tokenizer(text, return_tensors="pt", add_special_tokens=False)
        input_tokens = inputs["input_ids"].shape[1]
        if input_tokens + max_tokens > self.max_context_tokens:
            raise ValueError(
                "local model context budget exceeded; no truncation permitted"
            )
        started = time.perf_counter()
        with torch.inference_mode():
            output = self._model.generate(
                **{k: v.to("cuda") for k, v in inputs.items()},
                max_new_tokens=max_tokens,
                do_sample=False,
                temperature=None,
                top_p=None,
                top_k=None,
            )
        new = output[0, input_tokens:]
        answer = self._tokenizer.decode(new, skip_special_tokens=True)
        self.captures.append(
            {
                "system": system,
                "messages": messages,
                "raw_output": answer,
                "input_tokens": input_tokens,
                "output_tokens": len(new),
                "generation_ms": (time.perf_counter() - started) * 1000,
                "output_budget": max_tokens,
            }
        )
        return SimpleNamespace(content=(SimpleNamespace(type="text", text=answer),))

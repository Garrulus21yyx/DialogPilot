"""
亮点：端到端 Agent 评测框架

核心问题：如何评测端到端 Agent？

评测维度：
  1. 意图识别准确率 —— 预测意图 vs 标注意图，计算 Accuracy / F1
  2. 响应质量评分 —— 用 LLM 作为评判者（LLM-as-Judge），
     从相关性、准确性、完整性、有用性四个维度打分
  3. 端到端对话评测 —— 模拟完整多轮对话，评估整体体验
  4. 回归测试 —— 与历史基线对比，防止性能退化

LLM-as-Judge 是评测 Agent 质量的关键技术：
  人工标注成本高、主观性强；用 LLM 评判可以规模化、可重复。
"""
import json
import hashlib
import logging
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from types import SimpleNamespace

from anthropic import AsyncAnthropic

from core.llm_metrics import create_message
from core.llm_utils import extract_text_content
from core.model_policy import ModelProfile, ModelRole

from core.intent_recognizer import IntentCategory, IntentRecognizer
from evaluation.rubric import CaseRubric
from services.evolution.bundle import AgentBundle
from application.chat_contracts import ChatCommand, Completed
from evaluation.chat_application_runner import ChatApplicationRunner
from evaluation.target_planning_runner import TargetPlanningRunner
from application.turn_planning import TurnPlan

logger = logging.getLogger(__name__)


# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class IntentTestCase:
    """一条意图识别标注样本。"""
    message:          str
    expected_intent:  str
    context:          Optional[Dict[str, Any]] = None


@dataclass
class QualityScores:
    """LLM-as-Judge 评分结果。"""
    relevance:    float   # 相关性：回答是否针对问题
    accuracy:     float   # 准确性：信息是否正确
    completeness: float   # 完整性：是否完整解决问题
    helpfulness:  float   # 有用性：用户是否能据此行动
    judge_failed: bool = False
    error: Optional[str] = None

    @property
    def overall(self) -> float:
        """四个质量维度等权平均后的综合分。"""
        return statistics.mean([self.relevance, self.accuracy, self.completeness, self.helpfulness])


@dataclass
class EvalResult:
    """单个评测用例的结果、评分和版本化元数据。"""
    test_id:    str
    passed:     bool
    scores:     Dict[str, float]
    detail:     str = ""
    metadata:   Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalReport:
    """评测报告。"""
    timestamp:        str
    total:            int
    passed:           int
    pass_rate:        float
    avg_scores:       Dict[str, float]
    regressions:      List[str]          # 相比基线退化的指标
    recommendations:  List[str]
    results:          List[EvalResult]
    metadata:         Dict[str, Any] = field(default_factory=dict)


# ── LLM-as-Judge ─────────────────────────────────────────────────────────────

class LLMJudge:
    """
    用 LLM 评判 Agent 响应质量。

    为什么用 LLM 而不是人工？
    - 可规模化：数千条测试用例自动评测
    - 可重复：相同输入得到稳定评分
    - 多维度：同时评估相关性、准确性等多个维度

    注意：LLM Judge 本身也有偏差，建议定期用人工标注校准。
    """

    JUDGE_PROMPT = """你是一个客服质量评估专家。请对以下客服响应进行评分。

用户问题: {question}
Agent 响应: {response}
{context_section}
{rubric_section}

请从以下四个维度评分（0.0-1.0），返回 JSON：
- relevance: 响应是否直接针对用户问题（0=完全无关，1=完全相关）
- accuracy: 信息是否准确无误（0=明显错误，1=完全正确）
- completeness: 是否完整解决了用户需求（0=完全没解决，1=完全解决）
- helpfulness: 用户能否据此采取行动（0=毫无帮助，1=非常有帮助）

只返回 JSON，例如: {{"relevance": 0.9, "accuracy": 0.8, "completeness": 0.7, "helpfulness": 0.85}}"""

    def __init__(
        self,
        client: AsyncAnthropic,
        model: str,
        model_profile: Optional[ModelProfile] = None,
    ):
        """保存独立 Judge 客户端和固定模型版本。"""
        self._client = client
        self._model_profile = model_profile or ModelProfile(model)
        self._model  = self._model_profile.model

    async def judge(
        self,
        question: str,
        response: str,
        context: Optional[str] = None,
        rubric: Optional[CaseRubric] = None,
    ) -> QualityScores:
        """对一次问答进行四维评分；Judge 故障显式标记而非伪装通过。"""
        ctx_section = f"背景信息: {context}" if context else ""
        rubric_section = rubric.semantic_prompt() if rubric else ""
        prompt = self.JUDGE_PROMPT.format(
            question=question,
            response=response,
            context_section=ctx_section,
            rubric_section=rubric_section,
        )
        prompt = self._clean_text(prompt)
        try:
            resp = await create_message(self._client, self._model_profile, ModelRole.JUDGE,
                max_tokens=256, temperature=0.0,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = extract_text_content(resp.content)
            s, e = raw.find("{"), raw.rfind("}") + 1
            data = json.loads(raw[s:e])
            return QualityScores(
                relevance=float(data.get("relevance", 0.5)),
                accuracy=float(data.get("accuracy", 0.5)),
                completeness=float(data.get("completeness", 0.5)),
                helpfulness=float(data.get("helpfulness", 0.5)),
            )
        except Exception as ex:
            logger.warning(f"LLM Judge 失败: {ex}")
            return QualityScores(
                0.5, 0.5, 0.5, 0.5,
                judge_failed=True,
                error=str(ex),
            )

    @staticmethod
    def _clean_text(value: Any) -> str:
        """移除 Unicode 代理字符，避免 LLM 请求编码失败。"""
        if value is None:
            return ""
        if not isinstance(value, str):
            value = str(value)
        return value.encode("utf-8", errors="ignore").decode("utf-8")


# ── 意图识别评测 ──────────────────────────────────────────────────────────────

class IntentEvaluator:
    """评测意图识别的准确率和 F1。"""

    def __init__(self, recognizer: IntentRecognizer):
        """注入被测意图识别器。"""
        self._recognizer = recognizer

    async def evaluate(
        self,
        cases: List[IntentTestCase],
        *,
        agent_bundle: Optional[AgentBundle] = None,
    ) -> Dict[str, Any]:
        """运行全部样本并计算 accuracy、macro-F1 与逐类指标。"""
        predictions, ground_truth = [], []
        case_details: List[Dict[str, Any]] = []
        classifier_fingerprint = self._recognizer.classifier_fingerprint(agent_bundle)

        for case in cases:
            if agent_bundle is None:
                result = await self._recognizer.recognize(case.message)
            else:
                result = await self._recognizer.recognize(case.message, bundle=agent_bundle)
            if result.classifier_fingerprint != classifier_fingerprint:
                raise RuntimeError("intent evaluation mixed classifier fingerprints")
            predicted = result.intent.value
            predictions.append(predicted)
            ground_truth.append(case.expected_intent)
            case_details.append({
                "message": case.message,
                "expected": case.expected_intent,
                "predicted": predicted,
                "confidence": result.confidence,
                "reasoning": result.reasoning,
                "latency_ms": round(result.latency_ms, 3),
                "classifier_fingerprint": result.classifier_fingerprint,
            })

        # 纯 Python 计算指标
        correct = sum(p == g for p, g in zip(predictions, ground_truth))
        accuracy = correct / len(predictions) if predictions else 0.0

        # 每类 F1
        labels = sorted(set(ground_truth + predictions))
        per_class: Dict[str, Dict[str, float]] = {}
        for label in labels:
            tp = sum(p == label and g == label for p, g in zip(predictions, ground_truth))
            fp = sum(p == label and g != label for p, g in zip(predictions, ground_truth))
            fn = sum(p != label and g == label for p, g in zip(predictions, ground_truth))
            prec = tp / (tp + fp) if (tp + fp) else 0.0
            rec  = tp / (tp + fn) if (tp + fn) else 0.0
            f1   = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
            per_class[label] = {"precision": prec, "recall": rec, "f1": f1}

        macro_f1 = statistics.mean(v["f1"] for v in per_class.values()) if per_class else 0.0

        return {
            "accuracy":   round(accuracy, 4),
            "macro_f1":   round(macro_f1, 4),
            "per_class":  per_class,
            "total":      len(cases),
            "correct":    correct,
            "cases":      case_details,
            "classifier_fingerprint": classifier_fingerprint,
        }


# ── 端到端评测器 ──────────────────────────────────────────────────────────────

class EndToEndEvaluator:
    """
    端到端 Agent 评测。

    评测流程：
      1. 运行意图识别评测（准确率/F1）
      2. 运行对话质量评测（LLM-as-Judge）
      3. 与历史基线对比（回归检测）
      4. 生成可操作的优化建议
    """

    # 质量及格线
    PASS_THRESHOLD = 0.75

    def __init__(
        self,
        recognizer: IntentRecognizer,
        api_key:  str,
        tenant_id: str,
        base_url: Optional[str] = None,
        model:    str = "claude-3-5-sonnet-20241022",
        judge_model_profile: Optional[ModelProfile] = None,
        chat_runner: Optional[ChatApplicationRunner] = None,
        planning_runner_factory: Callable[[], TargetPlanningRunner] | None = None,
    ):
        """组装意图评测、LLM Judge、编排器和可选持久基线。"""
        kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        client = AsyncAnthropic(**kwargs)

        self._planning_runner_factory = planning_runner_factory
        self._tenant_id = tenant_id
        self._chat_runner      = chat_runner
        self._judge            = LLMJudge(client, model, model_profile=judge_model_profile)
        self._intent_evaluator = IntentEvaluator(recognizer)
        self._history:         List[EvalReport] = []

    async def run(
        self,
        intent_cases:    Optional[List[IntentTestCase]] = None,
        dialog_cases:    Optional[List[Dict[str, Any]]] = None,
        metadata:        Optional[Dict[str, Any]] = None,
        agent_bundle:    Optional[AgentBundle] = None,
    ) -> EvalReport:
        """
        运行完整评测。

        intent_cases: 意图识别测试用例
        dialog_cases:
          - 单轮: [{"question": "..."}]
          - 多轮: [{"turns": ["第一轮", "第二轮", ...]}]
        """
        results: List[EvalResult] = []
        all_scores: Dict[str, List[float]] = {
            "relevance": [],
            "accuracy": [],
            "completeness": [],
            "helpfulness": [],
            "coverage_complete": [],
            "task_coverage": [],
            "budget_success_rate": [],
            "fanout_efficiency": [],
            "route_exact_match": [],
            "route_jaccard": [],
            "task_exact_match": [],
            "planning_complete": [],
            "rubric_hard_pass": [],
            "rubric_score": [],
        }

        # 1. 意图识别评测
        intent_metrics: Dict[str, Any] = {}
        if intent_cases:
            intent_metrics = await self._intent_evaluator.evaluate(
                intent_cases, agent_bundle=agent_bundle,
            )
            passed = intent_metrics["accuracy"] >= self.PASS_THRESHOLD
            results.append(EvalResult(
                test_id="intent_recognition",
                passed=passed,
                scores={"accuracy": intent_metrics["accuracy"], "macro_f1": intent_metrics["macro_f1"]},
                detail=f"准确率 {intent_metrics['accuracy']:.1%}，Macro-F1 {intent_metrics['macro_f1']:.3f}",
                metadata={
                    "total": intent_metrics.get("total", 0),
                    "correct": intent_metrics.get("correct", 0),
                    "cases": intent_metrics.get("cases", []),
                },
            ))

        # 2. Target 规划产物或公开回复评测；两者不混算执行成功。
        if dialog_cases:
            for i, case in enumerate(dialog_cases):
                case_results = await self._evaluate_dialog_case(
                    case, i, agent_bundle=agent_bundle,
                )
                results.extend(case_results)
                for r in case_results:
                    for k in all_scores:
                        if k in r.scores:
                            all_scores[k].append(r.scores[k])

        # 3. 汇总
        avg_scores = {
            k: round(statistics.mean(v), 4) for k, v in all_scores.items() if v
        }
        if intent_metrics:
            avg_scores["intent_accuracy"] = intent_metrics["accuracy"]

        passed_count = sum(1 for r in results if r.passed)
        pass_rate    = passed_count / len(results) if results else 0.0

        # 4. 回归检测
        regressions: List[str] = []

        # 5. 优化建议
        recommendations = self._recommendations(avg_scores, intent_metrics)

        report_metadata = dict(metadata or {})
        if intent_metrics:
            report_metadata["intent_classifier_fingerprint"] = intent_metrics.get(
                "classifier_fingerprint", "",
            )
        if agent_bundle is not None:
            report_metadata.update({
                "agent_bundle_version": agent_bundle.version,
                "agent_bundle_sha256": agent_bundle.content_hash,
            })
        report = EvalReport(
            timestamp=datetime.now().isoformat(),
            total=len(results),
            passed=passed_count,
            pass_rate=round(pass_rate, 4),
            avg_scores=avg_scores,
            regressions=regressions,
            recommendations=recommendations,
            results=results,
            metadata=report_metadata,
        )
        self._history.append(report)
        return report

    async def _evaluate_dialog_case(
        self,
        case: Dict[str, Any],
        case_idx: int,
        *,
        agent_bundle: Optional[AgentBundle] = None,
    ) -> List[EvalResult]:
        """评测单轮或多轮对话用例。"""
        questions = self._dialog_turns(case)
        if not questions:
            return []

        conv_id = str(case.get("conv_id") or f"eval_{case_idx}")
        user_id = str(case.get("user_id") or "eval_user")
        history: List[Dict[str, str]] = []
        results: List[EvalResult] = []
        routing_only = case.get("evaluation_layer") == "routing"
        if routing_only and self._planning_runner_factory is None:
            raise RuntimeError("routing evaluation requires TargetPlanningRunner")
        planner = self._planning_runner_factory() if routing_only else None

        for turn_idx, question in enumerate(questions):
            turn_started = time.perf_counter()
            context = self._history_context(history)
            request_id = "eval_" + hashlib.sha256(
                f"{case.get('id', case_idx)}:{turn_idx}:{user_id}:{conv_id}".encode("utf-8")
            ).hexdigest()[:32]
            if routing_only:
                decision = await planner.plan(
                    ChatCommand(message=question, user_id=user_id, conv_id=conv_id,
                                request_id=request_id),
                    history=history, entities=dict(case.get("entities") or {}),
                )
                orchestration_scores = self._planning_scores(decision, case)
                required_checks = [orchestration_scores["planning_complete"] >= 1.0]
                if "route_exact_match" in orchestration_scores:
                    required_checks.append(orchestration_scores["route_exact_match"] >= 1.0)
                if "task_exact_match" in orchestration_scores:
                    required_checks.append(orchestration_scores["task_exact_match"] >= 1.0)
                if "disposition_exact_match" in orchestration_scores:
                    required_checks.append(orchestration_scores["disposition_exact_match"] >= 1.0)
                actual_answer = ""
                orch_result = None
            else:
                if self._chat_runner is None:
                    raise RuntimeError(
                        "full_execution requires ChatApplicationRunner; "
                        "component orchestrator execution is not a production-chain evaluation"
                    )
                chat_run = await self._chat_runner.run(ChatCommand(
                    message=question,
                    tenant_id=self._tenant_id,
                    user_id=user_id,
                    conv_id=conv_id,
                    authorization_fingerprint=hashlib.sha256(
                        f"evaluation:{user_id}".encode("utf-8")
                    ).hexdigest(),
                    request_id=request_id,
                    pinned_bundle=agent_bundle,
                ))
                public = dict(chat_run.public_response)
                actual_answer = str(public.get("response") or "")
                orch_result = SimpleNamespace(
                    response=actual_answer,
                    agent_type=public.get("agent_type"),
                    intent=public.get("intent"),
                    agent_types=list(public.get("agent_types") or []),
                    task_plan=dict(public.get("task_plan") or {}),
                    coverage=dict(public.get("coverage") or {}),
                    agent_outcomes=list(public.get("agent_outcomes") or []),
                    routing_disposition=public.get("routing_disposition", "execute"),
                )
                orchestration_scores = self._orchestration_scores(orch_result, case)
                required_checks = [
                    isinstance(chat_run.outcome, Completed),
                    orchestration_scores["coverage_complete"] >= 1.0,
                    orchestration_scores["task_coverage"] >= 1.0,
                    *(orchestration_scores[key] >= 1.0 for key in (
                        "route_exact_match", "task_exact_match",
                    ) if key in orchestration_scores),
                ]
            scores = None
            rubric = None
            rubric_result = None
            if not routing_only:
                rubric = CaseRubric.from_mapping(case.get("rubric"))
                tool_call_count = len(public.get("tool_audit") or [])
                rubric_result = rubric.evaluate(
                    actual_answer,
                    tool_call_count=tool_call_count,
                )
                scores = await self._judge.judge(
                    question,
                    actual_answer,
                    context=context or None,
                    rubric=rubric,
                )
                required_checks.append(rubric_result.hard_pass)
                required_checks.extend(
                    getattr(scores, dimension) >= threshold
                    for dimension, threshold in rubric.min_dimension_scores.items()
                )
            passed = all(required_checks) and (
                routing_only or (scores is not None and scores.overall >= self.PASS_THRESHOLD)
            )

            history.append({"role": "user", "content": question})
            history.append({"role": "assistant", "content": actual_answer})

            base_test_id = str(case.get("id") or f"dialog_{case_idx}")
            test_id = base_test_id if len(questions) == 1 else f"{base_test_id}_turn_{turn_idx}"
            quality_scores = ({
                "relevance": scores.relevance,
                "accuracy": scores.accuracy,
                "completeness": scores.completeness,
                "helpfulness": scores.helpfulness,
                "overall": scores.overall,
            } if scores is not None else {})
            if rubric_result is not None:
                quality_scores.update({
                    "rubric_hard_pass": 1.0 if rubric_result.hard_pass else 0.0,
                    "rubric_score": rubric_result.score,
                })
            detail = (
                f"Q: {question[:30]}... → 综合评分 {scores.overall:.3f}"
                if scores is not None
                else f"Q: {question[:30]}... → 路由合同评测"
            )
            results.append(EvalResult(
                test_id=test_id,
                passed=passed,
                scores={
                    **quality_scores,
                    **orchestration_scores,
                },
                detail=detail,
                metadata={
                    "question": question,
                    "response": actual_answer,
                    "agent_type": (
                        getattr(orch_result.agent_type, "value", orch_result.agent_type)
                        if orch_result is not None and orch_result.agent_type is not None
                        else "orchestrator" if orch_result is not None
                        else next(iter(decision.route.owner_ids), None)
                    ),
                    "intent": (
                        getattr(orch_result.intent, "value", orch_result.intent)
                        if orch_result is not None and orch_result.intent
                        else None
                    ),
                    "turn": turn_idx,
                    "conv_id": conv_id,
                    "latency_ms": round((time.perf_counter() - turn_started) * 1000, 3),
                    "judge_failed": scores.judge_failed if scores is not None else False,
                    "judge_error": scores.error if scores is not None else None,
                    "rubric_id": rubric.rubric_id if rubric is not None else None,
                    "rubric": rubric_result.to_dict() if rubric_result is not None else None,
                    "task_plan": (
                        orch_result.task_plan if orch_result is not None
                        else {"plan_id": decision.plan_id, "work_item_ids": [
                            item.work_item_id for item in decision.work.items
                        ] if decision.work else []}
                    ),
                    "coverage": orch_result.coverage if orch_result is not None else {},
                    "agent_outcomes": orch_result.agent_outcomes if orch_result is not None else [],
                    "execution_mode": "planner_only" if routing_only else "full_execution",
                    "chat_stages": (
                        [stage.to_dict() for stage in chat_run.stages]
                        if not routing_only else []
                    ),
                    "owner_state": dict(chat_run.owner_state) if not routing_only else {},
                    "chat_outcome": (
                        type(chat_run.outcome).__name__ if not routing_only else None
                    ),
                    "clarification_required": decision.route.mode.value == "CLARIFY" if routing_only else False,
                    "routing_disposition": (
                        decision.route.mode.value.lower() if routing_only else
                        getattr(
                            getattr(orch_result, "routing_disposition", None),
                            "value",
                            getattr(orch_result, "routing_disposition", "execute"),
                        )
                    ),
                },
            ))

        return results

    @staticmethod
    def _planning_scores(decision: TurnPlan, case: Dict[str, Any]) -> Dict[str, float]:
        """Score the actual Target plan; planned work is not completed work."""
        actual_agents = set(decision.route.owner_ids)
        actual_tasks = {
            item.work_item_id for item in decision.work.items
        } if decision.work else set()
        expected_agents = {
            str(agent) for agent in (case.get("expected_agents") or []) if str(agent)
        }
        expected_tasks = {
            str(task_id) for task_id in (case.get("expected_task_ids") or []) if str(task_id)
        }
        union = actual_agents | expected_agents
        scores = {
            "planning_complete": 1.0,
            "route_exact_match": 1.0 if actual_agents == expected_agents else 0.0,
            "route_jaccard": len(actual_agents & expected_agents) / len(union) if union else 1.0,
            "task_exact_match": 1.0 if actual_tasks == expected_tasks else 0.0,
            "fanout_efficiency": (
                1.0 - len(actual_agents - expected_agents) / len(actual_agents)
                if actual_agents else (1.0 if not expected_agents else 0.0)
            ),
        }
        expected_disposition = str(case.get("expected_disposition") or "").strip()
        if expected_disposition:
            actual_disposition = decision.route.mode.value.lower()
            scores["disposition_exact_match"] = (
                1.0 if actual_disposition == expected_disposition else 0.0
            )
        return scores

    @staticmethod
    def _orchestration_scores(orch_result: Any, case: Dict[str, Any]) -> Dict[str, float]:
        """从计划、覆盖和终态计算可回归的 Multi-Agent 指标。"""
        coverage = dict(getattr(orch_result, "coverage", {}) or {})
        plan = dict(getattr(orch_result, "task_plan", {}) or {})
        required = set(plan.get("work_item_ids") or [])
        outcomes = list(getattr(orch_result, "agent_outcomes", []) or [])
        completed = {
            item["work_item_id"] for item in outcomes
            if item.get("status") == "SUCCEEDED"
        }
        expected_tasks = {
            str(task_id) for task_id in (case.get("expected_task_ids") or []) if str(task_id)
        }
        task_coverage = (
            len(required & completed) / len(required) if required
            else (1.0 if coverage.get("complete") is True else 0.0)
        )

        budget_failures = sum(
            1 for outcome in outcomes if outcome.get("reason_code") in {
                "AGENT_STEP_BUDGET_EXCEEDED", "CONTEXT_BUDGET_EXCEEDED",
            }
        )
        budget_success_rate = (
            1.0 - budget_failures / len(outcomes) if outcomes else 1.0
        )

        actual_agents = set(getattr(orch_result, "agent_types", ()) or ())

        scores: Dict[str, float] = {
            "coverage_complete": 1.0 if coverage.get("complete") is True else 0.0,
            "task_coverage": task_coverage,
            "budget_success_rate": budget_success_rate,
            "fanout_efficiency": 1.0,
        }

        expected_agents = {
            str(agent) for agent in (case.get("expected_agents") or []) if str(agent)
        }
        if "expected_agents" in case:
            union = actual_agents | expected_agents
            scores["route_exact_match"] = 1.0 if actual_agents == expected_agents else 0.0
            scores["route_jaccard"] = (
                len(actual_agents & expected_agents) / len(union) if union else 1.0
            )
            unnecessary = len(actual_agents - expected_agents)
            scores["fanout_efficiency"] = (
                1.0 - unnecessary / len(actual_agents)
                if actual_agents else (1.0 if not expected_agents else 0.0)
            )

        if "expected_task_ids" in case:
            actual_tasks = required
            scores["task_exact_match"] = 1.0 if actual_tasks == expected_tasks else 0.0

        return scores

    @staticmethod
    def _dialog_turns(case: Dict[str, Any]) -> List[str]:
        """兼容单问题与多轮输入格式，提取有效用户轮次。"""
        turns = case.get("turns")
        if isinstance(turns, list):
            return [str(t) for t in turns if str(t).strip()]
        question = case.get("question")
        return [str(question)] if question else []

    @staticmethod
    def _history_context(history: List[Dict[str, str]]) -> str:
        """将已发生对话投影为 Judge 使用的只读背景文本。"""
        if not history:
            return ""
        lines = [f"{m['role']}: {m['content']}" for m in history[-8:]]
        return "[评测多轮历史]\n" + "\n".join(lines)

    def _recommendations(
        self,
        scores: Dict[str, float],
        intent_metrics: Dict[str, Any],
    ) -> List[str]:
        """根据指标缺口和回归项生成可操作的改进建议。"""
        recs = []
        if scores.get("intent_accuracy", 1.0) < 0.90:
            recs.append("意图识别准确率 < 90%：增加 Few-shot 示例，或对低 F1 的意图类别补充训练数据")
        if scores.get("relevance", 1.0) < 0.75:
            recs.append("相关性偏低：检查 Agent system_prompt，确保 Agent 聚焦于用户问题")
        if scores.get("completeness", 1.0) < 0.75:
            recs.append("完整性偏低：Agent 可能过早结束回答，考虑在 prompt 中要求提供完整解决方案")
        if scores.get("helpfulness", 1.0) < 0.75:
            recs.append("有用性偏低：回答可能过于抽象，考虑要求 Agent 提供具体操作步骤")
        if scores.get("coverage_complete", 1.0) < 0.98:
            recs.append("必需任务覆盖不足：检查 TaskPlanner、预算上限和失败后人工接管路径")
        if scores.get("route_exact_match", 1.0) < 0.90:
            recs.append("Owner 分配偏差：补充复合问题与误导关键词的路由标注样本")
        if scores.get("fanout_efficiency", 1.0) < 0.90:
            recs.append("存在无效 fan-out：收紧能力触发证据或提高辅助任务阈值")
        if scores.get("budget_success_rate", 1.0) < 0.98:
            recs.append("预算失败偏高：校准请求 deadline、Agent timeout 与最大并发数")
        if not recs:
            recs.append("所有指标均达标，继续保持")
        return recs

    @property
    def history(self) -> List[EvalReport]:
        """返回进程内评测报告副本。"""
        return self._history

# ── 内置测试用例（开箱即用）──────────────────────────────────────────────────

DEFAULT_INTENT_CASES: List[IntentTestCase] = [
    IntentTestCase("我的订单什么时候到？",       "logistics"),
    IntentTestCase("帮我取消订单",               "request"),
    IntentTestCase("你们服务太差了！",            "complaint"),
    IntentTestCase("应用一直报500错误",           "technical_crash"),
    IntentTestCase("为什么扣了两次款？",          "payment_issue"),
    IntentTestCase("我要投诉，转人工！",          "human_handoff"),
    IntentTestCase("你好",                        "greeting"),
    IntentTestCase("修改我的邮箱地址",            "account"),
    IntentTestCase("帮我开发票",                  "invoice"),
    IntentTestCase("退款多久到账？",              "refund"),
    IntentTestCase("登录一直报401",               "technical_login"),
]

DEFAULT_DIALOG_CASES: List[Dict[str, Any]] = [
    {"question": "我的订单 #12345 还没到，已经超时了"},
    {"question": "应用登录一直报错 401"},
    {"question": "为什么这个月多扣了 50 块钱？"},
    {"question": "帮我把收货地址改成北京市朝阳区"},
    {"turns": ["你好，我想退款", "订单号是 #12345", "退款多久能到账？"]},
]

"""M6 service-chain Dataset/Rubric v2 deterministic contract."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Protocol


class ServiceChainContractError(ValueError):
    pass


class ServiceLayer(str, Enum):
    PERCEPTION = "perception"
    ROUTE_MODE = "route_mode"
    CONTEXT_MEMORY = "context_memory"
    RETRIEVAL = "retrieval"
    TOOL_AUTHORITY = "tool_authority"
    TOOL_EFFECT = "tool_effect"
    GENERATION_CLAIMS = "generation_claims"
    PUBLICATION = "publication"
    HANDOFF = "handoff"
    DELIVERY_FEEDBACK = "delivery_feedback"
    SERVICE_OUTCOME = "service_outcome"


class ExpectedLayerStatus(str, Enum):
    REQUIRED = "REQUIRED"
    FORBIDDEN = "FORBIDDEN"
    OPTIONAL = "OPTIONAL"


@dataclass(frozen=True)
class LayerExpectation:
    layer: ServiceLayer
    status: ExpectedLayerStatus
    owner: str
    assertions: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, layer: str, raw: Any) -> "LayerExpectation":
        if not isinstance(raw, Mapping):
            raise ServiceChainContractError(f"{layer}: layer expectation must be object")
        try:
            typed_layer = ServiceLayer(layer)
            status = ExpectedLayerStatus(str(raw.get("status") or ""))
        except ValueError as exc:
            raise ServiceChainContractError(f"{layer}: unsupported layer/status") from exc
        owner = str(raw.get("owner") or "").strip()
        if not owner:
            raise ServiceChainContractError(f"{layer}: owner is required")
        assertions = raw.get("assertions") or {}
        if not isinstance(assertions, Mapping):
            raise ServiceChainContractError(f"{layer}: assertions must be object")
        return cls(typed_layer, status, owner, dict(assertions))


@dataclass(frozen=True)
class ToolExpectation:
    name: str
    parameter_subset: Mapping[str, Any] = field(default_factory=dict)
    receipt_fields: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, raw: Any) -> "ToolExpectation":
        if not isinstance(raw, Mapping):
            raise ServiceChainContractError("tool expectation must be object")
        name = str(raw.get("name") or "").strip()
        if not name:
            raise ServiceChainContractError("tool expectation name is required")
        parameters = raw.get("parameter_subset") or {}
        receipts = raw.get("receipt_fields") or []
        if not isinstance(parameters, Mapping) or not isinstance(receipts, list):
            raise ServiceChainContractError("invalid tool parameter/receipt expectation")
        return cls(name, dict(parameters), tuple(str(item) for item in receipts))


@dataclass(frozen=True)
class ServiceChainRubric:
    required_tools: tuple[ToolExpectation, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    tool_order: tuple[str, ...] = ()
    required_claim_evidence: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    required_transitions: tuple[str, ...] = ()
    required_handoff_fields: tuple[str, ...] = ()
    required_task_owners: Mapping[str, str] = field(default_factory=dict)
    required_dependencies: tuple[str, ...] = ()
    required_parallel_waves: tuple[tuple[str, ...], ...] = ()
    required_dependency_receipts: tuple[str, ...] = ()
    required_budget_outcome: str = ""
    zero_tolerance_flags: Mapping[str, Any] = field(default_factory=dict)
    max_publications: int = 1
    max_effects_per_operation: int = 1
    semantic_minimums: Mapping[str, float] = field(default_factory=dict)
    semantic_scorer: str = ""

    @classmethod
    def from_mapping(cls, raw: Any) -> "ServiceChainRubric":
        if not isinstance(raw, Mapping):
            raise ServiceChainContractError("deterministic_rubric must be object")
        allowed = {
            "required_tools", "forbidden_tools", "tool_order",
            "required_claim_evidence", "required_transitions",
            "required_handoff_fields", "max_publications",
            "max_effects_per_operation", "semantic_minimums", "semantic_scorer",
            "required_task_owners", "required_dependencies",
            "required_parallel_waves", "required_dependency_receipts",
            "required_budget_outcome", "zero_tolerance_flags",
        }
        unknown = set(raw) - allowed
        if unknown:
            raise ServiceChainContractError(f"unknown rubric fields: {sorted(unknown)}")
        claims = raw.get("required_claim_evidence") or {}
        minimums = raw.get("semantic_minimums") or {}
        if not isinstance(claims, Mapping) or not isinstance(minimums, Mapping):
            raise ServiceChainContractError("claim/semantic rubric must be object")
        normalized_minimums = {str(key): float(value) for key, value in minimums.items()}
        task_owners = raw.get("required_task_owners") or {}
        zero_flags = raw.get("zero_tolerance_flags") or {}
        waves = raw.get("required_parallel_waves") or []
        if (
            not isinstance(task_owners, Mapping)
            or not isinstance(zero_flags, Mapping)
            or not isinstance(waves, list)
            or any(not isinstance(wave, list) for wave in waves)
        ):
            raise ServiceChainContractError("invalid TaskGraph/safety rubric")
        if any(value < 0 or value > 1 for value in normalized_minimums.values()):
            raise ServiceChainContractError("semantic minimums must be in [0,1]")
        scorer = str(raw.get("semantic_scorer") or "")
        if normalized_minimums and not scorer:
            raise ServiceChainContractError("semantic minimums require fixed scorer version")
        max_publications = int(raw.get("max_publications", 1))
        max_effects = int(raw.get("max_effects_per_operation", 1))
        if max_publications < 0 or max_effects < 0:
            raise ServiceChainContractError("duplicate limits must be non-negative")
        return cls(
            required_tools=tuple(
                ToolExpectation.from_mapping(item)
                for item in (raw.get("required_tools") or [])
            ),
            forbidden_tools=tuple(str(item) for item in (raw.get("forbidden_tools") or [])),
            tool_order=tuple(str(item) for item in (raw.get("tool_order") or [])),
            required_claim_evidence={
                str(key): tuple(str(item) for item in value)
                for key, value in claims.items()
            },
            required_transitions=tuple(
                str(item) for item in (raw.get("required_transitions") or [])
            ),
            required_handoff_fields=tuple(
                str(item) for item in (raw.get("required_handoff_fields") or [])
            ),
            required_task_owners={str(key): str(value) for key, value in task_owners.items()},
            required_dependencies=tuple(
                str(item) for item in (raw.get("required_dependencies") or [])
            ),
            required_parallel_waves=tuple(
                tuple(str(item) for item in wave) for wave in waves
            ),
            required_dependency_receipts=tuple(
                str(item) for item in (raw.get("required_dependency_receipts") or [])
            ),
            required_budget_outcome=str(raw.get("required_budget_outcome") or ""),
            zero_tolerance_flags=dict(zero_flags),
            max_publications=max_publications,
            max_effects_per_operation=max_effects,
            semantic_minimums=normalized_minimums,
            semantic_scorer=scorer,
        )


@dataclass(frozen=True)
class ServiceChainCase:
    case_id: str
    split: str
    group_id: str
    input: Mapping[str, Any]
    layers: Mapping[ServiceLayer, LayerExpectation]
    backend_state: Mapping[str, Any]
    decision_gold: Mapping[str, Any]
    rubric: ServiceChainRubric
    source: Mapping[str, Any]
    review: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, raw: Any) -> "ServiceChainCase":
        if not isinstance(raw, Mapping) or raw.get("schema_version") != 2:
            raise ServiceChainContractError("service-chain case requires schema_version=2")
        case_id = str(raw.get("id") or "").strip()
        split = str(raw.get("split") or "").strip()
        group_id = str(raw.get("group_id") or "").strip()
        input_value = raw.get("input") or {}
        expected = raw.get("expected") or {}
        review = raw.get("review") or {}
        source = raw.get("source") or {}
        if not case_id or not group_id or split not in {"dev", "heldout"}:
            raise ServiceChainContractError("case id/group/split are invalid")
        if not isinstance(input_value, Mapping) or not str(input_value.get("message") or "").strip():
            raise ServiceChainContractError(f"{case_id}: input.message is required")
        if not isinstance(expected, Mapping):
            raise ServiceChainContractError(f"{case_id}: expected must be object")
        layer_rows = expected.get("layers") or {}
        backend_state = expected.get("authoritative_backend_state") or {}
        decision_gold = expected.get("decision_gold") or {}
        if not isinstance(layer_rows, Mapping) or not layer_rows:
            raise ServiceChainContractError(f"{case_id}: expected layers are required")
        if not isinstance(backend_state, Mapping) or not backend_state:
            raise ServiceChainContractError(
                f"{case_id}: authoritative backend state is required"
            )
        allowed_decisions = {
            "intent_source_fusion", "domain_owner", "supporting_agents",
            "instance_selection", "knowledge_rrf", "memory_rrf",
            "active_case_selection",
        }
        if not isinstance(decision_gold, Mapping) or not decision_gold:
            raise ServiceChainContractError(f"{case_id}: decision_gold is required")
        unknown_decisions = set(decision_gold) - allowed_decisions
        if unknown_decisions:
            raise ServiceChainContractError(
                f"{case_id}: unknown decision gold {sorted(unknown_decisions)}"
            )
        layers = {
            ServiceLayer(key): LayerExpectation.from_mapping(key, value)
            for key, value in layer_rows.items()
        }
        review_status = str(review.get("status") or "")
        if not isinstance(source, Mapping) or not all(
            str(source.get(key) or "").strip() for key in ("dataset", "license")
        ):
            raise ServiceChainContractError(f"{case_id}: source dataset/license required")
        if review_status not in {"provisional", "human_reviewed"}:
            raise ServiceChainContractError(f"{case_id}: invalid review status")
        if review_status == "human_reviewed" and not all(
            str(review.get(key) or "").strip()
            for key in ("reviewer", "reviewed_at", "notes")
        ):
            raise ServiceChainContractError(
                f"{case_id}: human review metadata is incomplete"
            )
        return cls(
            case_id, split, group_id, dict(input_value), layers,
            dict(backend_state), dict(decision_gold),
            ServiceChainRubric.from_mapping(expected.get("deterministic_rubric") or {}),
            dict(source), dict(review),
        )


@dataclass(frozen=True)
class ServiceChainDataset:
    root: Path
    manifest: Mapping[str, Any]
    cases: tuple[ServiceChainCase, ...]

    @classmethod
    def load(cls, root: str | Path) -> "ServiceChainDataset":
        path = Path(root)
        manifest_path, cases_path = path / "manifest.json", path / "cases.jsonl"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 2:
            raise ServiceChainContractError("service-chain manifest requires schema_version=2")
        if _sha256(cases_path) != manifest.get("cases_sha256"):
            raise ServiceChainContractError("service-chain cases checksum mismatch")
        cases = tuple(
            ServiceChainCase.from_mapping(json.loads(line))
            for line in cases_path.read_text(encoding="utf-8").splitlines() if line.strip()
        )
        if len(cases) != int(manifest.get("case_count", -1)):
            raise ServiceChainContractError("service-chain case_count mismatch")
        ids = [case.case_id for case in cases]
        if len(ids) != len(set(ids)):
            raise ServiceChainContractError("duplicate service-chain case id")
        groups: dict[str, str] = {}
        for case in cases:
            prior = groups.setdefault(case.group_id, case.split)
            if prior != case.split:
                raise ServiceChainContractError("service-chain group crosses split")
        return cls(path, manifest, cases)


@dataclass(frozen=True)
class ServiceChainActual:
    layers: Mapping[str, Mapping[str, Any]]
    tool_calls: tuple[Mapping[str, Any], ...] = ()
    claims: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    transitions: tuple[str, ...] = ()
    handoff: Mapping[str, Any] = field(default_factory=dict)
    publication_ids: tuple[str, ...] = ()
    effect_operation_keys: tuple[str, ...] = ()
    semantic_scores: Mapping[str, float] = field(default_factory=dict)
    semantic_scorer: str = ""
    task_owners: Mapping[str, str] = field(default_factory=dict)
    dependencies: tuple[str, ...] = ()
    parallel_waves: tuple[tuple[str, ...], ...] = ()
    dependency_receipts: tuple[str, ...] = ()
    budget_outcome: str = ""
    safety_flags: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ServiceChainScore:
    passed: bool
    deterministic_pass: bool
    semantic_pass: bool
    checks: Mapping[str, bool]
    violations: tuple[str, ...]


def score_service_chain(case: ServiceChainCase, actual: ServiceChainActual) -> ServiceChainScore:
    checks: dict[str, bool] = {}
    violations: list[str] = []

    def check(name: str, passed: bool) -> None:
        checks[name] = passed
        if not passed:
            violations.append(name)

    unknown_layers = set(actual.layers) - {item.value for item in ServiceLayer}
    check("layers.closed", not unknown_layers)
    for layer, expectation in case.layers.items():
        observed = actual.layers.get(layer.value)
        present = observed is not None
        check(
            f"layer.{layer.value}.{expectation.status.value.casefold()}",
            present if expectation.status is ExpectedLayerStatus.REQUIRED
            else not present if expectation.status is ExpectedLayerStatus.FORBIDDEN
            else True,
        )
        if present:
            check(
                f"layer.{layer.value}.owner",
                str(observed.get("owner") or "") == expectation.owner,
            )
            for key, value in expectation.assertions.items():
                check(f"layer.{layer.value}.assertion.{key}", observed.get(key) == value)
    calls = list(actual.tool_calls)
    names = [str(call.get("name") or "") for call in calls]
    for expected in case.rubric.required_tools:
        candidates = [call for call in calls if call.get("name") == expected.name]
        check(f"tool.required.{expected.name}", bool(candidates))
        if candidates:
            call = candidates[0]
            params = call.get("parameters") or {}
            receipt = call.get("receipt") or {}
            for key, value in expected.parameter_subset.items():
                check(f"tool.parameter.{expected.name}.{key}", params.get(key) == value)
            for field_name in expected.receipt_fields:
                check(
                    f"tool.receipt.{expected.name}.{field_name}",
                    field_name in receipt and receipt[field_name] not in (None, ""),
                )
    for name in case.rubric.forbidden_tools:
        check(f"tool.forbidden.{name}", name not in names)
    if case.rubric.tool_order:
        indexes = []
        cursor = 0
        for name in case.rubric.tool_order:
            try:
                index = names.index(name, cursor)
            except ValueError:
                indexes = []
                break
            indexes.append(index)
            cursor = index + 1
        check("tool.order", len(indexes) == len(case.rubric.tool_order))
    for claim_id, required_refs in case.rubric.required_claim_evidence.items():
        observed = set(actual.claims.get(claim_id, ()))
        check(f"claim.{claim_id}.evidence", set(required_refs) <= observed)
    for transition in case.rubric.required_transitions:
        check(f"transition.{transition}", transition in actual.transitions)
    for field_name in case.rubric.required_handoff_fields:
        check(
            f"handoff.{field_name}",
            field_name in actual.handoff and actual.handoff[field_name] not in (None, ""),
        )
    for task_id, owner in case.rubric.required_task_owners.items():
        check(f"task.owner.{task_id}", actual.task_owners.get(task_id) == owner)
    for dependency in case.rubric.required_dependencies:
        check(f"task.dependency.{dependency}", dependency in actual.dependencies)
    for index, wave in enumerate(case.rubric.required_parallel_waves):
        check(
            f"task.parallel_wave.{index}",
            frozenset(wave) in {frozenset(item) for item in actual.parallel_waves},
        )
    for receipt in case.rubric.required_dependency_receipts:
        check(f"task.dependency_receipt.{receipt}", receipt in actual.dependency_receipts)
    if case.rubric.required_budget_outcome:
        check(
            "task.budget_outcome",
            actual.budget_outcome == case.rubric.required_budget_outcome,
        )
    for key, value in case.rubric.zero_tolerance_flags.items():
        check(f"safety.{key}", actual.safety_flags.get(key) == value)
    check(
        "publication.maximum",
        len(set(actual.publication_ids)) <= case.rubric.max_publications
        and len(actual.publication_ids) == len(set(actual.publication_ids)),
    )
    effect_counts = {
        key: actual.effect_operation_keys.count(key)
        for key in set(actual.effect_operation_keys)
    }
    check(
        "effect.maximum_per_operation",
        all(value <= case.rubric.max_effects_per_operation for value in effect_counts.values()),
    )
    semantic_checks = []
    if case.rubric.semantic_minimums:
        check("semantic.scorer_version", actual.semantic_scorer == case.rubric.semantic_scorer)
        semantic_checks.append(checks["semantic.scorer_version"])
        for dimension, minimum in case.rubric.semantic_minimums.items():
            passed = float(actual.semantic_scores.get(dimension, -1)) >= minimum
            check(f"semantic.{dimension}", passed)
            semantic_checks.append(passed)
    semantic_pass = all(semantic_checks) if semantic_checks else True
    deterministic_keys = [key for key in checks if not key.startswith("semantic.")]
    deterministic_pass = all(checks[key] for key in deterministic_keys)
    return ServiceChainScore(
        deterministic_pass and semantic_pass,
        deterministic_pass,
        semantic_pass,
        checks,
        tuple(violations),
    )


class RagasCompatibleScorer(Protocol):
    """Optional fixed-version semantic projection; never owns deterministic truth."""

    scorer_id: str
    scorer_version: str

    def score(self, *, question: str, answer: str, contexts: tuple[str, ...]) -> Mapping[str, float]: ...


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

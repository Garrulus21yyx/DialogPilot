"""Deterministic local Agent producer for demand-driven media processing."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from application.media_requirement import (
    MediaNecessity,
    MediaRequirementBinding,
    MediaRequirementDecision,
    MediaRequirementMode,
    MediaRequirementPolicy,
    MediaStage,
)


class UnsupportedMediaTask(ValueError):
    code = "UNSUPPORTED_MEDIA_TASK"


@dataclass(frozen=True)
class AgentMediaPlan:
    """Agent interpretation consumed by both its decision and policy boundary."""

    stage: MediaStage | None
    reason_code: str
    requirement_id: str | None = None
    region_key: str | None = None


class LocalMediaRequirementAgent:
    """Bounded local policy: L0 by default, OCR before targeted visual reasoning."""

    policy_version = "media-requirement-policy-v1"
    _l1_terms = (
        "错误码", "报错信息", "订单号", "快递单", "运单号", "发票",
        "截图文字", "图片文字", "读取截图", "识别文字", "ocr",
        "error code", "tracking number", "invoice number", "read the text",
    )
    _l2_terms = (
        "红框", "圈出", "破损", "损坏", "少件", "错发", "按钮位置",
        "哪个按钮", "外观", "布局", "位置关系", "接线", "孔歪", "装反",
        "red box", "damaged", "missing part", "which button", "visual layout",
    )

    def analyze(self, task: Mapping[str, Any]) -> AgentMediaPlan:
        explicit = str(task.get("media_need") or "").strip().upper()
        region = str(task.get("region_key") or "").strip() or None
        message = str(task.get("message") or "").casefold()
        if region is None:
            if "红框" in message or "red box" in message:
                region = "mentioned:red-box"
            elif "圈出" in message:
                region = "mentioned:marked-region"
        if explicit:
            aliases = {
                "L0": None,
                "L0_TEXT_ONLY": None,
                "NO_MEDIA_REQUIRED": None,
                "L1": MediaStage.L1_TEXT_EXTRACTION,
                "L1_TEXT_EXTRACTION": MediaStage.L1_TEXT_EXTRACTION,
                "L2": MediaStage.L2_VISUAL_REASONING,
                "L2_VISUAL_REASONING": MediaStage.L2_VISUAL_REASONING,
            }
            if explicit not in aliases:
                raise UnsupportedMediaTask(f"unsupported media_need: {explicit}")
            stage = aliases[explicit]
        else:
            if any(term in message for term in self._l1_terms):
                stage = MediaStage.L1_TEXT_EXTRACTION
            elif any(term in message for term in self._l2_terms):
                stage = MediaStage.L2_VISUAL_REASONING
            else:
                stage = None
        if stage is None:
            return AgentMediaPlan(None, "MEDIA_IRRELEVANT")
        if stage is MediaStage.L1_TEXT_EXTRACTION:
            return AgentMediaPlan(
                stage, "TEXT_EXTRACTION_REQUIRED",
                str(task.get("requirement_id") or "current-turn-media-text"), region,
            )
        return AgentMediaPlan(
            stage, "VISUAL_EVIDENCE_REQUIRED",
            str(task.get("requirement_id") or "current-turn-visual-evidence"), region,
        )

    async def decide_media_requirement(
        self, *, task: Mapping[str, Any], asset_ids: tuple[str, ...],
    ) -> MediaRequirementDecision:
        task_hash = _task_schema_hash(task)
        assets = tuple(dict.fromkeys(str(item).strip() for item in asset_ids))
        if any(not item for item in assets):
            raise UnsupportedMediaTask("asset IDs must be non-empty")
        if not assets:
            return MediaRequirementDecision.create(
                mode=MediaRequirementMode.NO_MEDIA_REQUIRED, bindings=(),
                decision_reason_codes=("NO_RELEVANT_ASSET",),
                task_schema_hash=task_hash,
            )
        plan = self.analyze(task)
        if plan.stage is None:
            return MediaRequirementDecision.create(
                mode=MediaRequirementMode.NO_MEDIA_REQUIRED, bindings=(),
                decision_reason_codes=(plan.reason_code,),
                task_schema_hash=task_hash,
            )
        bindings = tuple(
            MediaRequirementBinding.create(
                requirement_id=plan.requirement_id or "",
                asset_id=asset_id,
                necessity=MediaNecessity.REQUIRED,
                required_stage=plan.stage,
                reason_code=plan.reason_code,
                region_key=plan.region_key,
            )
            for asset_id in assets
        )
        return MediaRequirementDecision.create(
            mode=MediaRequirementMode.MEDIA_TARGETS, bindings=bindings,
            decision_reason_codes=(plan.reason_code,), task_schema_hash=task_hash,
        )

    def policies_for_task(
        self, task: Mapping[str, Any], *, has_assets: bool,
    ) -> tuple[MediaRequirementPolicy, ...]:
        plan = self.analyze(task)
        if not has_assets or plan.stage is None:
            return ()
        return (MediaRequirementPolicy(
            requirement_id=plan.requirement_id or "",
            media_required=True,
            required=True,
            minimum_stage=plan.stage,
        ),)


def _task_schema_hash(task: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(
            task, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise UnsupportedMediaTask("task must be canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()

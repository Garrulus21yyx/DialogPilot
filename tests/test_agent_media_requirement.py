"""The local Agent is the only producer of the canonical media decision."""
import asyncio

import pytest

from agents.media_requirement import (
    LocalMediaRequirementAgent,
    UnsupportedMediaTask,
)
from application.media_requirement import (
    MediaRequirementMode,
    MediaRequirementValidator,
    MediaStage,
)


def _decide(agent, *, task, asset_ids):
    return asyncio.run(agent.decide_media_requirement(
        task=task, asset_ids=asset_ids,
    ))


def test_no_asset_is_l0_even_when_message_mentions_an_error_code():
    agent = LocalMediaRequirementAgent()
    task = {"message": "请读取截图中的错误码"}

    decision = _decide(agent, task=task, asset_ids=())

    assert decision.mode is MediaRequirementMode.NO_MEDIA_REQUIRED
    assert decision.bindings == ()
    assert decision.decision_reason_codes == ("NO_RELEVANT_ASSET",)
    assert agent.policies_for_task(task, has_assets=False) == ()


def test_irrelevant_attachment_stays_l0_without_ocr_or_vlm_binding():
    agent = LocalMediaRequirementAgent()
    decision = _decide(
        agent,
        task={"message": "我的退款什么时候到账"},
        asset_ids=("asset-a",),
    )

    assert decision.mode is MediaRequirementMode.NO_MEDIA_REQUIRED
    assert decision.decision_reason_codes == ("MEDIA_IRRELEVANT",)


def test_error_code_prefers_l1_ocr_and_validates_against_task_policy():
    agent = LocalMediaRequirementAgent()
    task = {"message": "请读取截图中的错误码并帮我排查"}
    decision = _decide(
        agent,
        task=task, asset_ids=("asset-a", "asset-a"),
    )

    assert decision.mode is MediaRequirementMode.MEDIA_TARGETS
    assert len(decision.bindings) == 1
    assert decision.bindings[0].required_stage is MediaStage.L1_TEXT_EXTRACTION
    assert decision.bindings[0].reason_code == "TEXT_EXTRACTION_REQUIRED"
    assert MediaRequirementValidator().validate(
        decision,
        policies=agent.policies_for_task(task, has_assets=True),
        allowed_asset_ids=("asset-a",),
    ) is decision


def test_visual_relation_requests_target_l2_and_keep_region():
    agent = LocalMediaRequirementAgent()
    task = {"message": "红框里的按钮位置对吗", "region_key": "red-box"}
    decision = _decide(
        agent,
        task=task, asset_ids=("asset-a",),
    )

    binding = decision.bindings[0]
    assert binding.required_stage is MediaStage.L2_VISUAL_REASONING
    assert binding.region_key == "red-box"
    assert binding.reason_code == "VISUAL_EVIDENCE_REQUIRED"


def test_explicit_media_need_is_closed_and_unknown_values_fail_typed():
    agent = LocalMediaRequirementAgent()
    decision = _decide(
        agent,
        task={"message": "看一下", "media_need": "L1"},
        asset_ids=("asset-a",),
    )
    assert decision.bindings[0].required_stage is MediaStage.L1_TEXT_EXTRACTION

    with pytest.raises(UnsupportedMediaTask) as error:
        _decide(
            agent,
            task={"message": "看一下", "media_need": "L9"},
            asset_ids=("asset-a",),
        )
    assert error.value.code == "UNSUPPORTED_MEDIA_TASK"


def test_non_json_task_fails_before_creating_an_unstable_identity():
    with pytest.raises(UnsupportedMediaTask, match="canonical JSON"):
        _decide(
            LocalMediaRequirementAgent(),
            task={"message": object()}, asset_ids=("asset-a",),
        )

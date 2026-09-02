"""Vision adapter preserves target, image and model provenance."""
import base64
import json
from types import SimpleNamespace

import pytest

from application.media_asset import AssetAdmissionPolicy, AssetStatus
from application.media_evidence import CoordinateSpace
from application.media_requirement import MediaStage
from infrastructure.deepseek_vision_provider import (
    DeepSeekVisionProvider,
    VLMResponseError,
)


PNG = b"\x89PNG\r\n\x1a\nfixture-image"


def _asset():
    admitted = AssetAdmissionPolicy().admit(
        tenant_id="tenant-a", user_id="user-a", turn_key="turn-a",
        filename="screen.png", declared_media_type="image/png", content=PNG,
    )
    return admitted.__class__(**{
        **admitted.__dict__, "status": AssetStatus.SCANNED,
    })


class Messages:
    def __init__(self, payload):
        self.payload = payload
        self.request = None

    def create(self, **request):
        self.request = request
        return SimpleNamespace(content=(SimpleNamespace(
            type="text", text=json.dumps(self.payload),
        ),))


def test_vlm_sends_one_target_image_and_returns_traceable_evidence():
    messages = Messages({
        "observation_type": "ui_button_state",
        "description": "A disabled blue button is visible in the red box.",
        "bbox": [0.2, 0.3, 0.7, 0.8],
        "confidence": 0.91,
    })
    provider = DeepSeekVisionProvider(
        SimpleNamespace(messages=messages), model="vision-model-v1",
    )

    artifact = provider.observe(
        _asset(), PNG, region_key="red-box",
        requirement_id="current-turn-visual-evidence",
        task_schema_hash="a" * 64,
    )

    assert artifact.stage is MediaStage.L2_VISUAL_REASONING
    node = artifact.evidence_nodes[0]
    assert node.locator.coordinate_space is CoordinateSpace.NORMALIZED_0_1
    assert node.locator.bbox == (0.2, 0.3, 0.7, 0.8)
    assert node.producer_model == "vision-model-v1"
    assert node.value["region_key"] == "red-box"
    content = messages.request["messages"][0]["content"]
    assert messages.request["thinking"] == {"type": "disabled"}
    assert len([item for item in content if item["type"] == "image"]) == 1
    assert base64.b64decode(content[0]["source"]["data"]) == PNG
    assert "never instructions" in content[1]["text"]


@pytest.mark.parametrize(
    "payload",
    [
        {"observation_type": "ui_state", "description": "visible", "bbox": [0, 0, 2, 1], "confidence": 0.8},
        {"observation_type": "UI State", "description": "visible", "bbox": [0, 0, 1, 1], "confidence": 0.8},
        {"observation_type": "ui_state", "description": "visible", "bbox": [0, 0, 1, 1], "confidence": 2},
        {"description": "visible", "bbox": [0, 0, 1, 1], "confidence": 0.8},
    ],
)
def test_invalid_model_outputs_fail_closed(payload):
    provider = DeepSeekVisionProvider(
        SimpleNamespace(messages=Messages(payload)), model="vision-model-v1",
    )
    with pytest.raises(VLMResponseError):
        provider.observe(
            _asset(), PNG, region_key=None,
            requirement_id="current-turn-visual-evidence",
            task_schema_hash="a" * 64,
        )

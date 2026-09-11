import pytest

from scripts.build_chinese_intent_training_data import (
    extract_json_object,
    normalize_text,
    validate_batch,
)


def test_extract_json_object_ignores_markdown_wrapper():
    assert extract_json_object('```json\n{"items": []}\n```') == {"items": []}


def test_validate_batch_requires_exact_ids_and_chinese():
    payload = {"items": [{"id": "a", "text": "还是没有变化"}]}
    assert validate_batch(payload, {"a"})[0]["text"] == "还是没有变化"
    with pytest.raises(ValueError, match="id mismatch"):
        validate_batch(payload, {"a", "b"})
    with pytest.raises(ValueError, match="Chinese"):
        validate_batch({"items": [{"id": "a", "text": "still broken"}]}, {"a"})


def test_normalize_text_detects_spacing_and_punctuation_duplicates():
    assert normalize_text("退 款还没到！") == normalize_text("退款还没到")

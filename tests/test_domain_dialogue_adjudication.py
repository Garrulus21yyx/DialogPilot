import hashlib
import json

import pytest

from evaluation.domain_dialogue_adjudication import adjudicate, build


def fixture_data(tmp_path):
    source = tmp_path / "source"
    (source / "en").mkdir(parents=True)
    rows = [{"case_id": f"domain-v3:en:0:0:{i}", "group_id": "domain-v3:en:0:0",
             "language": "en", "label": "general", "text": f"turn {i}",
             "messages": [{"role": "user", "content": "history"}]} for i in range(3)]
    path = source / "en/train.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    ledger = {"reviewer": "test", "scope": "fixture", "codes": {
        "G": "general", "D": "__DEFER__", "X": None},
        "source_sha256": {"en/train.jsonl": hashlib.sha256(path.read_bytes()).hexdigest()},
        "families": {"en:0": ["GDX|0普通回复；1明确撤回；2语义不明"]}}
    review = tmp_path / "review.json"
    review.write_text(json.dumps(ledger))
    return source, review, rows, ledger


def test_complete_review_preserves_input_and_isolates_only_explicit_rejections(tmp_path):
    source, review, rows, _ = fixture_data(tmp_path)
    output = tmp_path / "reviewed"
    before = (source / "en/train.jsonl").read_bytes()
    build(source, review, output)
    accepted = [json.loads(x) for x in (output / "en/train.jsonl").read_text().splitlines()]
    audit = [json.loads(x) for x in (output / "audit.jsonl").read_text().splitlines()]
    assert len(accepted) == 2 and len(audit) == 3
    assert [r["status"] for r in audit] == ["retained", "corrected", "quarantined"]
    for original, result in zip(rows, accepted):
        assert result["text"] == original["text"] and result["messages"] == original["messages"]
        assert result["group_id"] == original["group_id"]
    assert (source / "en/train.jsonl").read_bytes() == before


def test_changed_source_cannot_reuse_old_review(tmp_path):
    source, review, _, _ = fixture_data(tmp_path)
    path = source / "en/train.jsonl"
    path.write_text(path.read_text() + "\n")
    output = tmp_path / "reviewed"
    with pytest.raises(ValueError, match="snapshot"):
        build(source, review, output)
    assert not output.exists()


@pytest.mark.parametrize("decision", ["GDX|0only", "GD|0a；1b；2c", "GDX|0a；1b"])
def test_incomplete_family_review_is_not_implicitly_accepted(tmp_path, decision):
    _, _, rows, ledger = fixture_data(tmp_path)
    ledger["families"]["en:0"][0] = decision
    with pytest.raises(ValueError, match="cover"):
        adjudicate(rows[0], ledger)


def test_no_review_means_no_generated_approval(tmp_path):
    _, _, rows, ledger = fixture_data(tmp_path)
    ledger["families"] = {}
    with pytest.raises(KeyError):
        adjudicate(rows[0], ledger)

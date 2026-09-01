import math

import pytest

from infrastructure.reconciliation import (
    ReconciliationStatus,
    reconcile,
    snapshot_records,
)


def _snapshot(name, rows):
    return snapshot_records(
        snapshot_id=name, object_name="tickets", high_watermark="42", records=rows,
    )


def test_canonical_snapshot_and_reconciliation_distinguish_count_and_content():
    source = _snapshot("source", [{"id": "1", "value": "a"}])
    same = _snapshot("target", [{"value": "a", "id": "1"}])
    changed = _snapshot("changed", [{"id": "1", "value": "b"}])
    extra = _snapshot("extra", [
        {"id": "1", "value": "a"}, {"id": "2", "value": "b"},
    ])

    assert reconcile(source, same).status is ReconciliationStatus.MATCHED
    assert reconcile(source, changed).status is ReconciliationStatus.CONTENT_MISMATCH
    assert reconcile(source, extra).status is ReconciliationStatus.COUNT_MISMATCH


def test_snapshot_order_is_explicit_and_non_finite_values_fail_closed():
    first = _snapshot("first", [{"id": "1"}, {"id": "2"}])
    reversed_rows = _snapshot("second", [{"id": "2"}, {"id": "1"}])
    assert first.sha256 != reversed_rows.sha256
    with pytest.raises(ValueError):
        _snapshot("invalid", [{"value": math.nan}])

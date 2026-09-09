"""The tau3 runner exports checkpoint lineage without checkpoint payloads."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

from evaluation.tau3_checkpoint_projection import project_session_checkpoints


@dataclass
class Work:
    work_item_id: str
    control_id: str
    revision: int
    raw_text: str


class Saver:
    def alist(self, config, *, filter, limit):
        assert config is None
        assert filter == {"langfuse_session_id": "session-20"}
        assert limit is None

        async def items():
            yield SimpleNamespace(
                config={"configurable": {
                    "thread_id": "turn:invocation-4", "checkpoint_id": "cp-2",
                }},
                checkpoint={
                    "ts": "2026-09-09T10:00:02Z",
                    "channel_values": {"prepared": Work("work-1", "control-1", 2, "secret")},
                },
                metadata={"step": 2, "source": "loop"},
                parent_config={"configurable": {"checkpoint_id": "cp-1"}},
            )

        return items()


class DeltaSaver:
    def alist(self, config, *, filter, limit):
        async def items():
            yield SimpleNamespace(
                config={"configurable": {"thread_id": "thread", "checkpoint_id": "cp-1"}},
                checkpoint={"ts": "1", "channel_values": {
                    "work_item_id": "work-1", "status": "WAITING_APPROVAL",
                }},
                metadata={"step": 1, "source": "loop"}, parent_config=None,
            )
            yield SimpleNamespace(
                config={"configurable": {"thread_id": "thread", "checkpoint_id": "cp-2"}},
                checkpoint={"ts": "2", "channel_values": {"work_item_id": "work-1"}},
                metadata={"step": 2, "source": "loop"},
                parent_config={"configurable": {"checkpoint_id": "cp-1"}},
            )
        return items()


def test_projection_retains_lineage_and_excludes_unapproved_payloads():
    result = asyncio.run(project_session_checkpoints(
        Saver(), session_id="session-20", task_id="20",
    ))

    assert result["status"] == "AVAILABLE"
    assert result["snapshot_count"] == 1
    snapshot = result["snapshots"][0]
    assert snapshot["parent_checkpoint_id"] == "cp-1"
    projected = {(item["field"], item["value"]) for item in snapshot["lineage"]}
    assert ("work_item_id", "work-1") in projected
    assert ("control_id", "control-1") in projected
    assert ("revision", 2) in projected
    assert "secret" not in str(result)


def test_projection_failure_is_typed_and_does_not_hide_task_result():
    class BrokenSaver:
        def alist(self, *args, **kwargs):
            raise RuntimeError("database unavailable")

    result = asyncio.run(project_session_checkpoints(
        BrokenSaver(), session_id="session-20", task_id="20",
    ))

    assert result == {
        "schema_version": "tau3-checkpoint-projection-v1",
        "status": "ERROR",
        "task_id": "20",
        "session_id": "session-20",
        "snapshot_count": 0,
        "error_type": "RuntimeError",
    }


def test_projection_stores_parent_delta_and_removed_lineage_paths():
    result = asyncio.run(project_session_checkpoints(
        DeltaSaver(), session_id="session-20", task_id="20",
    ))

    child = result["snapshots"][1]
    assert child["lineage"] == []
    assert child["removed_lineage_paths"] == ["$.channel_values.status"]

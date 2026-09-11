"""Deterministic causal probes over checkpoint-derived tau3 lineage."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


REQUIRED_EVENT_FIELDS = (
    "event_id", "sequence", "event_type", "task_id", "turn_id", "owner", "evidence_origin",
)
LINEAGE_FIELDS = (
    "control_id", "control_revision", "work_item_id", "proposal_id",
    "approval_id", "tool_call_id", "receipt_id", "action_name", "requirement_id",
    "observation_read_call_id", "emitted_business_call_id", "source_business_call_ids",
    "reuse_decision",
)
_EVIDENCE_ORIGINS = {"CHECKPOINT_PROJECTION", "OWNER_EVENT"}


def probe_report(report: Mapping[str, Any], run_dir: Path) -> Mapping[str, Any]:
    """Run causal probes and promote only owner-bound, linked evidence."""
    enriched = deepcopy(report)
    for task in enriched.get("tasks", []):
        task.setdefault(
            "quality_root_cause_status",
            "OPEN" if any(item.get("layer") == "quality" for item in task.get("findings", ()))
            else "NOT_APPLICABLE",
        )
        result = _load_result(run_dir, task["result_file"])
        events = (
            task.get("checkpoint_events") or result.get("checkpoint_events")
            or task.get("causal_events") or result.get("causal_events") or []
        )
        probes = _validate_events(events, task["task_id"])
        if probes["contract_status"] == "VALID":
            probes["results"] = [
                _goal_revision_probe(events, task),
                _approval_execution_probe(events, task),
                _read_replay_after_compaction_probe(events, task),
                _cross_continuation_read_replay_probe(events, task),
                _read_replay_scope_probe(events, task, result),
            ]
            for probe in probes["results"]:
                finding = probe.get("verified_root_cause")
                if finding:
                    duplicate = any(
                        item.get("code") == finding["code"]
                        and (item.get("causal_chain") or {}).get("first_bad_event_id")
                        == (finding.get("causal_chain") or {}).get("first_bad_event_id")
                        for item in task["findings"]
                    )
                    if not duplicate:
                        task["findings"].append(finding)
                    task["root_cause_status"] = "VERIFIED"
                quality_finding = probe.get("verified_quality_root_cause")
                if quality_finding:
                    duplicate = any(
                        item.get("code") == quality_finding["code"]
                        and (item.get("causal_chain") or {}).get("first_bad_event_id")
                        == (quality_finding.get("causal_chain") or {}).get("first_bad_event_id")
                        for item in task["findings"]
                    )
                    if not duplicate:
                        task["findings"].append(quality_finding)
                    task["quality_root_cause_status"] = "VERIFIED"
        else:
            probes["results"] = [{
                "probe": "causal_lineage",
                "status": "INCONCLUSIVE",
                "reason": "The artifact does not contain normalized checkpoint-transition lineage.",
                "missing_evidence": probes["errors"],
            }]
        task["causal_probes"] = probes
    enriched["schema_version"] = "dialogpilot-tau3-rca-v2"
    from evaluation.tau3_rca import failure_clusters, quality_root_clusters
    enriched["failure_clusters"] = failure_clusters(enriched.get("tasks", ()))
    enriched["quality_root_clusters"] = quality_root_clusters(enriched.get("tasks", ()))
    enriched["read_replay_attribution_clusters"] = _cross_task_read_replay_clusters(
        enriched.get("tasks", ())
    )
    return enriched


def _read_replay_after_compaction_probe(
    events: Sequence[Mapping[str, Any]], task: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Find owner transitions that allowed known reads inside one work item."""
    observed: dict[tuple[str, str], Mapping[str, Any]] = {}
    compactions: list[Mapping[str, Any]] = []
    replay_chains = []
    for event in events:
        event_type = event["event_type"]
        work_item = str(event.get("work_item_id") or "")
        identity = str(event.get("read_identity") or "")
        if event_type == "READ_OBSERVED" and work_item and identity:
            observed[(work_item, identity)] = event
        elif event_type == "CONTEXT_COMPACTED" and work_item and event.get("summary_applied") is True:
            compactions.append(event)
        elif event_type == "READ_REPLAYED" and work_item and identity:
            first = observed.get((work_item, identity))
            compact = next((item for item in reversed(compactions)
                            if item.get("work_item_id") == work_item
                            and first is not None
                            and first["sequence"] < item["sequence"] < event["sequence"]), None)
            if first is None or compact is None:
                continue
            if event.get("guard_decision") not in {"ALLOW_FIRST_REPLAY", "WARN"}:
                continue
            replay_chains.append({"first": first, "compact": compact, "replay": event})
    if not replay_chains:
        return {"probe": "read_reuse_after_compaction", "status": "NOT_APPLICABLE"}

    blocking = _impact_evidence(task, {
        "EPISODE_STEP_BUDGET_EXHAUSTED",
        "REDUNDANT_READ_REPLAY_EXHAUSTED_STEP_BUDGET",
    }, "TASK_BLOCKING")
    root_impact = "TASK_BLOCKING"
    if not blocking:
        blocking = _impact_evidence(task, {
            "EPISODE_STEP_BUDGET_EXHAUSTED",
            "REDUNDANT_READ_REPLAY_EXHAUSTED_TERMINATION_BUDGET",
        }, "EVALUATION_BLOCKING")
        root_impact = "EVALUATION_BLOCKING"
    blocking = [item for item in blocking if item != "unchanged-read-replays"]
    first_chain = replay_chains[0]
    if not blocking:
        return {
            "probe": "read_reuse_after_compaction", "status": "INCONCLUSIVE",
            "event_id": first_chain["replay"].get("event_id"),
            "reason": "Replays followed compaction, but no blocking impact is linked.",
        }
    root = _root_finding(
        "READ_REUSE_CONTRACT_NOT_ENFORCED_AFTER_COMPACTION", first_chain["replay"],
        "Working-context compaction was followed by same-work-item unchanged reads because the progress owner allowed the replays.",
        blocking, impact=root_impact,
    )
    root["evidence_ids"] = list(dict.fromkeys([
        *root["evidence_ids"],
        *(event["event_id"] for chain in replay_chains
          for event in (chain["first"], chain["compact"], chain["replay"])),
    ]))
    root["causal_signature"] = {
        "owner": "agent_progress",
        "violated_invariant": "VALID_COMPLETED_READ_REUSED_UNLESS_REFRESH_AUTHORIZED",
        "trigger": "WORKING_CONTEXT_SUMMARIZED",
        "mechanism": "UNCHANGED_READ_EXECUTED_AFTER_COMPACTION",
        "guard_failure": "ALLOW_FIRST_REPLAY_OR_WARN",
    }
    root["causal_chain"] = {
        "first_observation_event_id": first_chain["first"]["event_id"],
        "trigger_event_id": first_chain["compact"]["event_id"],
        "first_bad_event_id": first_chain["replay"]["event_id"],
        "attributed_replay_event_count": len(replay_chains),
        "replay_event_ids": [chain["replay"]["event_id"] for chain in replay_chains],
        "impact_evidence_ids": blocking,
        "scope": "SAME_WORK_ITEM_AFTER_COMPACTION",
    }
    return {
        "probe": "read_reuse_after_compaction", "status": "FAIL",
        "event_id": first_chain["replay"].get("event_id"), "verified_root_cause": root,
    }


def _cross_continuation_read_replay_probe(
    events: Sequence[Mapping[str, Any]], task: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Prove that a known read became novel when progress state changed scope."""
    quality_evidence = list(dict.fromkeys(
        str(evidence_id)
        for finding in task.get("findings", ())
        if finding.get("code") == "EXACT_READ_REPLAY"
        for evidence_id in finding.get("evidence_ids", ())
    ))
    if not quality_evidence:
        return {"probe": "cross_continuation_read_reuse", "status": "NOT_APPLICABLE"}

    first_by_identity: dict[str, Mapping[str, Any]] = {}
    compactions: list[Mapping[str, Any]] = []
    replay_chains = []
    for event in events:
        event_type = event["event_type"]
        identity = str(event.get("read_identity") or "")
        if event_type == "CONTEXT_COMPACTED" and event.get("summary_applied") is True:
            compactions.append(event)
            continue
        if event_type != "READ_OBSERVED" or not identity:
            continue
        first = first_by_identity.setdefault(identity, event)
        if first is event or first.get("work_item_id") == event.get("work_item_id"):
            continue
        compact = next((
            item for item in reversed(compactions)
            if first["sequence"] < item["sequence"] < event["sequence"]
            and item.get("work_item_id") == event.get("work_item_id")
        ), None)
        if compact is None or event.get("guard_decision") != "ALLOW_NEW_EVIDENCE":
            continue
        replay_chains.append({"first": first, "compact": compact, "replay": event})

    if not replay_chains:
        return {"probe": "cross_continuation_read_reuse", "status": "NOT_APPLICABLE"}

    first_chain = replay_chains[0]
    first = first_chain["first"]
    compact = first_chain["compact"]
    replay = first_chain["replay"]
    root = {
        "code": "READ_NOVELTY_AUTHORITY_SCOPED_TOO_NARROWLY",
        "layer": "quality_root_cause",
        "level": "VERIFIED",
        "summary": (
            "The progress owner scoped known read identities to one worker graph, so a "
            "compacted continuation classified previously completed reads as new evidence."
        ),
        "owner_candidate": "agent_progress",
        "evidence_ids": list(dict.fromkeys([
            *(
                event["event_id"]
                for chain in replay_chains
                for event in (chain["first"], chain["compact"], chain["replay"])
            ),
        ])),
        "missing_evidence": [],
        "next_probe": None,
        "impact": "EFFICIENCY_DEGRADED",
        "causal_signature": {
            "owner": "agent_progress",
            "violated_invariant": "COMPLETED_READ_IDENTITY_REMAINS_KNOWN_ACROSS_CONTINUATION",
            "trigger": "COMPACTED_CONTINUATION_WORK_ITEM_STARTED",
            "mechanism": "KNOWN_READ_CLASSIFIED_AS_NEW_IN_FRESH_GRAPH_STATE",
            "guard_failure": "ALLOW_NEW_EVIDENCE",
        },
        "causal_chain": {
            "first_observation_event_id": first["event_id"],
            "trigger_event_id": compact["event_id"],
            "first_bad_event_id": replay["event_id"],
            "replayed_read_count": len(replay_chains),
            "replay_event_ids": [chain["replay"]["event_id"] for chain in replay_chains],
            "impact_evidence_ids": [chain["replay"]["event_id"] for chain in replay_chains],
            "scope": "CROSS_CONTINUATION",
        },
    }
    return {
        "probe": "cross_continuation_read_reuse",
        "status": "FAIL",
        "event_id": replay.get("event_id"),
        "verified_quality_root_cause": root,
    }


def _read_replay_scope_probe(
    events: Sequence[Mapping[str, Any]], task: Mapping[str, Any], result: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Join every exact replay to the consumed source that informed its emission."""
    exact_evidence = next((
        evidence.get("data") or {}
        for evidence in task.get("evidence", ())
        if evidence.get("evidence_id") == "exact-read-replays"
    ), {})
    exact_count = int(exact_evidence.get("redundant_call_count") or 0)
    first_by_identity: dict[str, Mapping[str, Any]] = {}
    first_by_work_identity: dict[tuple[str, str], Mapping[str, Any]] = {}
    cross_events = []
    same_work_events = []
    blocked_events = []
    for event in events:
        event_type = event.get("event_type")
        identity = str(event.get("read_identity") or "")
        work_item = str(event.get("work_item_id") or "")
        if event_type == "READ_OBSERVED" and identity and work_item:
            first = first_by_identity.setdefault(identity, event)
            first_by_work_identity.setdefault((work_item, identity), event)
            if first is not event and first.get("work_item_id") != work_item:
                cross_events.append(event)
        elif event_type == "READ_REPLAYED" and identity and work_item:
            if event.get("guard_decision") == "BLOCK":
                blocked_events.append(event)
            elif (work_item, identity) in first_by_work_identity:
                same_work_events.append(event)
    joined_calls = _joined_repeated_calls(events, exact_evidence, result)
    attributed_count = sum(item["status"] == "JOINED" for item in joined_calls)
    if not exact_count:
        join_status = "NOT_APPLICABLE"
    elif not joined_calls or attributed_count == 0:
        join_status = "UNJOINED"
    elif attributed_count == exact_count:
        join_status = "COMPLETE"
    else:
        join_status = "PARTIAL"
    missing = [] if join_status == "COMPLETE" else [
        field for field, present in (
            ("source_business_call_id", any(
                _source_call_ids(event) for event in events
            )),
            ("observation_read_call_id", any(
                event.get("observation_read_call_id") for event in events
            )),
            ("emitted_business_call_id", any(
                event.get("emitted_business_call_id") for event in events
            )),
            ("reuse_decision", any(
                event.get("reuse_decision") for event in events
            )),
            ("business_call_binding", bool(_business_call_bindings(result))),
        ) if not present
    ]
    return {
        "probe": "read_replay_scope",
        "status": "PASS" if join_status == "COMPLETE" else "PARTIAL" if exact_count else "NOT_APPLICABLE",
        "exact_redundant_tool_call_count": exact_count,
        "attributed_redundant_tool_call_count": attributed_count,
        "event_scopes": {
            "cross_continuation_observed_count": len(cross_events),
            "same_work_item_allowed_replay_count": len(same_work_events),
            "blocked_replay_attempt_count": len(blocked_events),
        },
        "join_status": join_status,
        "missing_join_keys": missing,
        "per_call_attribution": joined_calls,
        "attribution_clusters": _call_attribution_clusters(joined_calls),
        "reason": (
            "Only repeated calls with a source-read-emission chain, both internal-to-benchmark "
            "bindings, and a verified pre-execution reuse decision are attributed; other calls remain open."
            if exact_count and join_status != "COMPLETE" else None
        ),
    }


def _business_call_bindings(result: Mapping[str, Any]) -> dict[str, Mapping[str, str]]:
    bindings = {}
    for record in result.get("target_trace") or ():
        link = record.get("causal_link") if isinstance(record, Mapping) else None
        if not isinstance(link, Mapping) or link.get("schema_version") != "tau3-business-call-binding-v1":
            continue
        internal_id = str(link.get("internal_tool_call_id") or "")
        business_id = str(link.get("business_call_id") or "")
        if internal_id and business_id:
            bindings[internal_id] = {
                "business_call_id": business_id,
                "tool_name": str(link.get("tool_name") or ""),
            }
    return bindings


def _joined_repeated_calls(
    events: Sequence[Mapping[str, Any]], exact_evidence: Mapping[str, Any], result: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    bindings = _business_call_bindings(result)
    internal_by_business = {
        binding["business_call_id"]: internal_id for internal_id, binding in bindings.items()
    }
    groups_by_call = {}
    repeated_ids = []
    for group in exact_evidence.get("groups") or ():
        call_ids = [str(call_id) for call_id in group.get("call_ids", ()) if call_id]
        for index, call_id in enumerate(call_ids[1:], start=1):
            groups_by_call[call_id] = (group, frozenset(call_ids[:index]))
            repeated_ids.append(call_id)

    read_events = [event for event in events
                   if event.get("event_type") in {"READ_OBSERVED", "READ_REPLAYED"}
                   and event.get("owner") == "agent_progress"]
    emissions = [event for event in events
                 if event.get("event_type") == "READ_INFORMED_TOOL_EMISSION"
                 and event.get("owner") == "agent_progress"]
    decisions = {
        str(event.get("emitted_business_call_id") or event.get("tool_call_id") or ""): event
        for event in events if event.get("event_type") == "READ_REUSE_DECIDED"
        and event.get("owner") == "tool_read_reuse"
    }
    attributed = []
    for business_call_id in repeated_ids:
        group, preceding_ids = groups_by_call[business_call_id]
        emitted_internal_id = internal_by_business.get(business_call_id)
        links = [event for event in emissions
                 if str(event.get("emitted_business_call_id") or "") == str(emitted_internal_id or "")]
        chains = []
        for link in links:
            source_internal_ids = _source_call_ids(link)
            source_business_ids = [
                bindings[source_id]["business_call_id"] for source_id in source_internal_ids
                if source_id in bindings and bindings[source_id]["business_call_id"] in preceding_ids
            ]
            if not source_business_ids:
                continue
            read_event = next((event for event in reversed(read_events)
                if event.get("sequence", -1) < link.get("sequence", -1)
                and event.get("observation_read_call_id") == link.get("observation_read_call_id")
                and event.get("read_identity") == link.get("read_identity")), None)
            observation_id = str(link.get("observation_read_call_id") or "")
            historical = any(source_id != observation_id for source_id in source_internal_ids)
            decision = decisions.get(str(emitted_internal_id or ""))
            reuse_decision = str((decision or {}).get("reuse_decision") or "")
            cause_code = {
                "POLICY_DISABLED": "READ_REUSE_POLICY_DISABLED",
                "NO_PRIOR_RESULT": "REUSABLE_READ_STATE_MISSING",
                "PRIOR_RESULT_INVALIDATED": "READ_REUSE_INVALIDATED",
                "REFRESH_REQUESTED": "REFRESH_REQUEST_BYPASSED_REUSE",
            }.get(reuse_decision)
            chains.append({
                "source_business_call_ids": source_business_ids,
                "observation_read_call_id": observation_id,
                "emitted_business_call_id": str(emitted_internal_id),
                "lineage_event_id": link.get("event_id"),
                "read_event_id": read_event.get("event_id") if read_event else None,
                "source_path": "HISTORICAL_OBSERVATION" if historical else "DIRECT_TOOL_RESULT",
                "reuse_decision_event_id": decision.get("event_id") if decision else None,
                "reuse_decision": reuse_decision or None,
                "cause_code": cause_code or "PRE_EXECUTION_REUSE_DECISION_MISSING",
                "owner": "tool_read_reuse",
                "root_cause_status": "VERIFIED" if cause_code in {
                    "READ_REUSE_POLICY_DISABLED", "REUSABLE_READ_STATE_MISSING",
                } else "CONDITIONAL" if cause_code else "OPEN",
            })
        verified = [chain for chain in chains if chain["root_cause_status"] == "VERIFIED"]
        attributed.append({
            "business_call_id": business_call_id,
            "tool_name": str(group.get("tool") or ""),
            "status": "JOINED" if verified else "CONDITIONAL" if chains else "OPEN",
            "causal_chains": chains,
        })
    return attributed


def _source_call_ids(event: Mapping[str, Any]) -> list[str]:
    values = event.get("source_business_call_ids") or ()
    if isinstance(values, str):
        try:
            values = json.loads(values)
        except (TypeError, ValueError):
            return []
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    return [str(value) for value in values if str(value)]


def _call_attribution_clusters(calls: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    clusters: dict[tuple[str, str, str], set[str]] = {}
    for call in calls:
        for chain in call.get("causal_chains", ()):
            if chain.get("root_cause_status") != "VERIFIED":
                continue
            key = (str(chain["cause_code"]), str(chain["owner"]), str(chain["source_path"]))
            clusters.setdefault(key, set()).add(str(call["business_call_id"]))
    return [{
        "cause_code": code, "owner": owner, "source_path": source_path,
        "call_count": len(call_ids), "business_call_ids": sorted(call_ids),
    } for (code, owner, source_path), call_ids in sorted(
        clusters.items(), key=lambda item: (-len(item[1]), item[0])
    )]


def _cross_task_read_replay_clusters(tasks: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    clusters: dict[tuple[str, str, str], dict[str, set[str]]] = {}
    for task in tasks:
        scope = next((probe for probe in (task.get("causal_probes") or {}).get("results", ())
                      if probe.get("probe") == "read_replay_scope"), None)
        for cluster in (scope or {}).get("attribution_clusters", ()):
            key = (cluster["cause_code"], cluster["owner"], cluster["source_path"])
            target = clusters.setdefault(key, {"task_ids": set(), "business_call_ids": set()})
            target["task_ids"].add(str(task["task_id"]))
            target["business_call_ids"].update(cluster["business_call_ids"])
    return [{
        "cluster_kind": "VERIFIED_READ_REPLAY_ROOT_CAUSE",
        "cause_code": code, "owner": owner, "source_path": source_path,
        "case_count": len(values["task_ids"]),
        "call_count": len(values["business_call_ids"]),
        "task_ids": sorted(values["task_ids"]),
        "business_call_ids": sorted(values["business_call_ids"]),
    } for (code, owner, source_path), values in sorted(
        clusters.items(), key=lambda item: (-len(item[1]["task_ids"]), item[0])
    )]


def _impact_evidence(
    task: Mapping[str, Any], codes: set[str], impact: str,
) -> list[str]:
    return list(dict.fromkeys(
        str(evidence_id)
        for finding in task.get("findings", ())
        if finding.get("code") in codes and finding.get("impact") == impact
        for evidence_id in finding.get("evidence_ids", ())
    ))


def _validate_events(events: Any, task_id: str) -> Mapping[str, Any]:
    errors = []
    if not isinstance(events, Sequence) or isinstance(events, (str, bytes)) or not events:
        errors.append("normalized checkpoint transition events are absent or empty")
        return {"contract_status": "MISSING", "event_count": 0, "errors": errors}
    previous = -1
    seen_event_ids = set()
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            errors.append(f"event[{index}] is not an object")
            continue
        missing = [field for field in REQUIRED_EVENT_FIELDS if event.get(field) in (None, "")]
        if missing:
            errors.append(f"event[{index}] missing {','.join(missing)}")
        if str(event.get("task_id")) != str(task_id):
            errors.append(f"event[{index}] task_id does not match result")
        if event.get("evidence_origin") not in _EVIDENCE_ORIGINS:
            errors.append(f"event[{index}] evidence_origin is not a checkpoint-backed projection")
        sequence = event.get("sequence")
        if not isinstance(sequence, int) or sequence <= previous:
            errors.append(f"event[{index}] sequence is not strictly increasing")
        elif isinstance(sequence, int):
            previous = sequence
        event_id = event.get("event_id")
        if event_id:
            if event_id in seen_event_ids:
                errors.append(f"event[{index}] duplicates event_id {event_id}")
            seen_event_ids.add(event_id)
    return {
        "contract_status": "INVALID" if errors else "VALID",
        "event_count": len(events),
        "lineage_coverage": {
            field: sum(1 for event in events if isinstance(event, Mapping) and event.get(field))
            for field in LINEAGE_FIELDS
        },
        "errors": errors,
    }


def _goal_revision_probe(
    events: Sequence[Mapping[str, Any]], task: Mapping[str, Any],
) -> Mapping[str, Any]:
    active: tuple[str, int] | None = None
    for event in events:
        if event["event_type"] == "GOAL_REVISED":
            control_id = event.get("control_id")
            revision = event.get("control_revision")
            if not control_id or not isinstance(revision, int):
                return {
                    "probe": "goal_revision_propagation", "status": "INCONCLUSIVE",
                    "event_id": event.get("event_id"),
                    "reason": "GOAL_REVISED omitted the authoritative control binding.",
                }
            active = (control_id, revision)
            continue
        if active and event["event_type"] in {
            "WORK_ITEM_RESUMED", "ACTOR_DECISION", "OUTCOME_REVIEWED", "PROPOSAL_CREATED",
        }:
            if event.get("control_id") != active[0]:
                continue
            consumed = event.get("control_revision")
            if isinstance(consumed, int) and consumed != active[1]:
                blocking = _linked_blocking_evidence(task, event)
                if not blocking:
                    return {
                        "probe": "goal_revision_propagation",
                        "status": "INCONCLUSIVE",
                        "expected_revision": active[1],
                        "consumed_revision": consumed,
                        "event_id": event.get("event_id"),
                        "reason": "A stale revision was consumed, but no blocking outcome evidence is linked to it.",
                    }
                return {
                    "probe": "goal_revision_propagation",
                    "status": "FAIL",
                    "expected_revision": active[1],
                    "consumed_revision": consumed,
                    "event_id": event.get("event_id"),
                    "verified_root_cause": _root_finding(
                        "STALE_GOAL_REVISION_CONSUMED", event,
                        "A downstream owner consumed an older goal revision after GOAL_REVISED.",
                        blocking,
                    ),
                }
            if not isinstance(consumed, int):
                return {
                    "probe": "goal_revision_propagation", "status": "INCONCLUSIVE",
                    "reason": "A downstream goal consumer omitted control_revision.",
                    "event_id": event.get("event_id"),
                }
    return {
        "probe": "goal_revision_propagation",
        "status": "PASS" if active else "NOT_APPLICABLE",
    }


def _approval_execution_probe(
    events: Sequence[Mapping[str, Any]], task: Mapping[str, Any],
) -> Mapping[str, Any]:
    approvals = [event for event in events if event["event_type"] == "ACTION_APPROVED"]
    for approval in approvals:
        proposal_id = approval.get("proposal_id")
        if not proposal_id:
            return {
                "probe": "approval_to_execution", "status": "INCONCLUSIVE",
                "reason": "ACTION_APPROVED omitted proposal_id.",
                "event_id": approval.get("event_id"),
            }
        downstream = [
            event for event in events
            if event["sequence"] > approval["sequence"] and event.get("proposal_id") == proposal_id
        ]
        if any(event["event_type"] == "TOOL_COMMITTED" for event in downstream):
            continue
        failure = next((event for event in downstream if event["event_type"] == "EXECUTION_FAILED"), None)
        blocking = _linked_blocking_evidence(task, failure) if failure else []
        if failure and failure.get("reason_code") and blocking:
            return {
                "probe": "approval_to_execution", "status": "FAIL",
                "proposal_id": proposal_id,
                "event_id": failure.get("event_id"),
                "verified_root_cause": _root_finding(
                    "APPROVED_ACTION_EXECUTION_FAILED", failure,
                    f"The approved proposal failed before commit with {failure['reason_code']}.",
                    blocking,
                ),
            }
        if failure and failure.get("reason_code"):
            return {
                "probe": "approval_to_execution", "status": "INCONCLUSIVE",
                "proposal_id": proposal_id,
                "event_id": failure.get("event_id"),
                "reason": "A linked execution failure exists, but terminal outcome impact is not linked.",
            }
        return {
            "probe": "approval_to_execution", "status": "INCONCLUSIVE",
            "proposal_id": proposal_id,
            "reason": "No linked TOOL_COMMITTED or typed EXECUTION_FAILED event follows approval.",
        }
    return {"probe": "approval_to_execution", "status": "NOT_APPLICABLE"}


def _linked_blocking_evidence(
    task: Mapping[str, Any], event: Mapping[str, Any],
) -> list[str]:
    blocking_ids = set()
    for finding in task.get("findings", []):
        if finding.get("impact") == "TASK_BLOCKING":
            blocking_ids.update(str(item) for item in finding.get("evidence_ids", []))
    explicit = next((
        link for link in task.get("causal_impact_links", [])
        if link.get("event_id") == event.get("event_id")
    ), {})
    linked = [
        str(item) for item in explicit.get("blocking_evidence_ids", [])
        if str(item) in blocking_ids
    ]
    event_action = event.get("action_name")
    event_requirement = event.get("requirement_id")
    for evidence in task.get("evidence", []):
        evidence_id = str(evidence.get("evidence_id"))
        if evidence_id not in blocking_ids:
            continue
        data = evidence.get("data") or {}
        expected = data.get("expected") or {}
        if (
            event_action and expected.get("name") == event_action
            or event_requirement and data.get("requirement_id") == event_requirement
        ):
            linked.append(evidence_id)
    return list(dict.fromkeys(linked))


def _root_finding(
    code: str,
    event: Mapping[str, Any],
    summary: str,
    blocking_evidence_ids: Sequence[str],
    *,
    impact: str = "TASK_BLOCKING",
) -> Mapping[str, Any]:
    event_id = event.get("event_id") or f"causal-event-{event['sequence']}"
    return {
        "code": code,
        "layer": "root_cause",
        "level": "VERIFIED",
        "summary": summary,
        "owner_candidate": event.get("owner"),
        "evidence_ids": [event_id, *blocking_evidence_ids],
        "missing_evidence": [],
        "next_probe": None,
        "impact": impact,
    }


def _load_result(run_dir: Path, filename: str) -> Mapping[str, Any]:
    return json.loads((run_dir / filename).read_text())

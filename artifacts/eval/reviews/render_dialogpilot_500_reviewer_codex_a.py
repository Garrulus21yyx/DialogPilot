#!/usr/bin/env python3
"""Render the completed Codex Reviewer A judgments for dialogpilot-500-v1."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
DATASET_ROOT = ROOT / "data" / "eval" / "dialogpilot-500-v1"
OUTPUT_ROOT = Path(__file__).resolve().parent
REVIEW_PATH = OUTPUT_ROOT / "dialogpilot-500-v1-reviewer-codex-a.jsonl"
SUMMARY_PATH = OUTPUT_ROOT / "dialogpilot-500-v1-reviewer-codex-a.summary.json"
HUMAN_QUEUE_PATH = OUTPUT_ROOT / "dialogpilot-500-v1-reviewer-codex-a.human-queue.jsonl"
REVIEWER_ID = "codex-a-2026-08-30"


INTENT_AMBIGUOUS = {
    "intent-account-013": "The standalone phrase 'change my address' can mean account-profile or delivery-address work.",
    "intent-account-014": "The standalone change-of-address request does not identify account profile versus an order delivery address.",
    "intent-account-019": "A postal change-of-address form does not uniquely establish the project's account-profile intent.",
    "intent-account-020": "The message omits whether the changed address belongs to the account profile or an order.",
    "intent-account_security-001": "The ungrammatical phrase can mean an unrecognized withdrawal or cash not received from an authorized withdrawal.",
    "intent-account_security-006": "The wording can describe unauthorized cash activity or a cash-dispense/payment dispute; the security reading is not unique.",
    "intent-account_security-015": "'I can get my identification verified' states success; it is likely a missing-negation source typo and does not express a clear problem.",
    "intent-logistics-004": "'How long will it take to get to me?' has no referent, so delivery is only hidden source context.",
    "intent-payment_issue-014": "'Weird charges' can be a fee/duplication issue or unauthorized activity owned by account security.",
    "intent-refund-005": "Canceling a purchase can be a generic order action or a refund request; the message does not state which contract applies.",
    "intent-refund-020": "A purchase-cancellation request does not uniquely imply the refund intent without an explicit money-return or refund condition.",
    "intent-technical-017": "The pronouns in 'uninstall the app before I try it again' omit the failed feature and make the technical subdomain unrecoverable.",
    "intent-technical_login-009": "'Unblock my card' does not expose the source label's PIN context and can be security, payment, or authentication work.",
    "intent-technical_login-019": "The message says the PIN is already unlocked and supplies no remaining failure or requested operation.",
}


INTENT_REVISIONS: dict[str, tuple[dict[str, Any], str]] = {
    "intent-other-018": (
        {"intent": "logistics"},
        "DHL delivery coverage for an address is a direct logistics question under the supported taxonomy, not OOS.",
    ),
    "intent-payment_issue-008": (
        {"intent": "account_security"},
        "The user explicitly does not recognize the charge; unauthorized activity is owned by account_security rather than an explained fee/payment failure.",
    ),
    "intent-technical-002": (
        {"intent": "payment_issue"},
        "A merchant rejecting a virtual-card payment is observably a declined payment; no separate software malfunction is stated.",
    ),
    "intent-technical-004": (
        {"intent": "query"},
        "The message asks where contactless is supported and reports no malfunction, so it is an information query.",
    ),
    "intent-technical-010": (
        {"intent": "payment_issue"},
        "Merchant rejection describes a declined payment, not a demonstrated technical feature failure.",
    ),
    "intent-technical-011": (
        {"intent": "payment_issue"},
        "The only observed event is a rejected attempt to pay, which matches payment_issue.",
    ),
    "intent-technical-012": (
        {"intent": "payment_issue"},
        "A virtual-card payment declined during billing setup is a payment authorization failure.",
    ),
    "intent-technical-015": (
        {"intent": "payment_issue"},
        "The card is rejected at use time, which is the project's declined-payment boundary.",
    ),
    "intent-technical-019": (
        {"intent": "payment_issue"},
        "A merchant rejecting a subscription payment is a payment issue; the message gives no independent app/card-feature error.",
    ),
}


ROUTING_REVISIONS: dict[str, dict[str, Any]] = {
    "routing-general_bill-3": {
        "corrected_input": {"intent": "logistics"},
        "reason": "The Owner set is complete, but the primary supplied intent says order_status while the request explicitly asks about logistics.",
    },
    "routing-general_bill-4": {
        "corrected_input": {"intent": "logistics"},
        "reason": "The Owner set is complete, but a delivery anomaly is logistics rather than order_status input truth.",
    },
    "routing-general_tech-3": {
        "corrected_input": {"intent": "technical_crash"},
        "corrected_expected": {"owners": ["technical"], "task_ids": ["technical_task"]},
        "reason": "The user reports that the delivery-query page errors but does not ask for an order or delivery fact; one technical task is the minimum complete plan.",
    },
    "routing-general_tech-4": {
        "corrected_input": {"intent": "order_status"},
        "reason": "The two-Owner expected plan is correct, but 'check the order' is order_status rather than logistics input truth.",
    },
    "routing-negated_tech-2": {
        "corrected_input": {"intent": "refund"},
        "reason": "The billing Owner is correct, but the visible request says refund and explicitly negates a system error; payment_issue is the wrong supplied intent.",
    },
}


STATEFUL_EXPECTED_REVISIONS: dict[str, dict[str, bool]] = {
    "memory-short-close": {
        "episodic_archived": True,
        "working_state_cleared_after_durable_archive": True,
    },
    "memory-remote-failure": {
        "storage_mode_explicit": True,
        "startup_failed_typed": True,
        "no_embedded_fallback_write": True,
    },
    "memory-mode-recovery": {
        "remote_backend_reconnected_explicitly": True,
        "embedded_data_not_auto_merged": True,
        "reconciliation_requires_operator_action": True,
    },
    "react-security-valid-approval": {
        "call_executed_once": True,
        "approval_subject_matched": True,
        "approval_tool_and_action_matched": True,
        "approval_unexpired": True,
        "approval_consumed_once": True,
    },
    "react-security-cancelled-request": {
        "cancellation_requested": True,
        "outcome_closed_typed": True,
        "side_effect_known_or_manual_reconcile_required": True,
    },
}


STATEFUL_SPECIAL_REASONS = {
    "memory-short-close": "The conceptual archive-before-clear invariant is correct, but 'redis_expired_safe' is not an observable postcondition; replace it with ordered durable-archive/working-state assertions and define the fixture.",
    "memory-remote-failure": "The proposed health-degraded outcome conflicts with the repository's authoritative remote-mode contract, which fails startup and forbids embedded fallback writes.",
    "memory-mode-recovery": "The scenario assumes local degraded data even though remote mode is fail-closed; any legacy embedded-data reconciliation must be an explicit operator workflow, not an automatic mode switch.",
    "react-security-valid-approval": "Subject matching and exactly-once execution are necessary but incomplete: a valid approval must also be bound to tool/action, be unexpired, and be single-use.",
    "react-security-tool-timeout": "A timeout oracle is incomplete until the fixture declares the tool read-only/idempotent or records an UNKNOWN side-effect state; 'outcome_closed' alone can hide an uncertain write.",
    "react-security-cancelled-request": "Cancellation propagation does not establish whether an in-flight write happened; the oracle must close the side-effect state or require manual reconciliation when unknown.",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def intent_review(case: dict[str, Any]) -> dict[str, Any]:
    case_id = case["id"]
    message = case["input"]["message"]
    expected = case["expected"]
    evidence = [
        f"input: {message}",
        f"intent contract: core/intent_recognizer.py -> IntentCategory/{expected['intent']}",
        f"source provenance: {case['source'].get('dataset')}:{case['source'].get('original_label', '')}",
    ]
    if case_id in INTENT_AMBIGUOUS:
        return {
            "decision": "ambiguous",
            "corrected_expected": {},
            "confidence": 0.97,
            "reason": INTENT_AMBIGUOUS[case_id],
            "evidence": evidence,
        }
    if case_id in INTENT_REVISIONS:
        corrected, reason = INTENT_REVISIONS[case_id]
        return {
            "decision": "revise",
            "corrected_expected": corrected,
            "confidence": 0.96,
            "reason": reason,
            "evidence": evidence,
        }
    return {
        "decision": "accept",
        "corrected_expected": {},
        "confidence": 0.96,
        "reason": f"The visible message has one supported {expected['intent']} reading under the bounded intent contract; no competing supported label is evident.",
        "evidence": evidence,
    }


def routing_review(case: dict[str, Any]) -> dict[str, Any]:
    case_id = case["id"]
    expected = case["expected"]
    evidence = [
        f"input: {case['input']['message']}",
        f"supplied intent: {case['input']['intent']}",
        "Owner contract: agents/orchestration_contracts.py and documented responsibilities in agents/agent_orchestrator.py",
        "independence: current Planner was not executed for this review",
    ]
    if case_id in ROUTING_REVISIONS:
        change = ROUTING_REVISIONS[case_id]
        result = {
            "decision": "revise",
            "corrected_expected": change.get("corrected_expected", {}),
            "confidence": 0.97,
            "reason": change["reason"],
            "evidence": evidence,
        }
        if "corrected_input" in change:
            result["corrected_input"] = change["corrected_input"]
        return result
    return {
        "decision": "accept",
        "corrected_expected": {},
        "confidence": 0.97,
        "reason": f"The minimum complete Owner set is {expected['owners']}; each selected Owner has exactly its authoritative <owner>_task and negated domains add no task.",
        "evidence": evidence,
    }


def retrieval_review(case: dict[str, Any], corpus: dict[str, dict[str, Any]]) -> dict[str, Any]:
    relevant_ids = case["expected"]["relevant_ids"]
    docs = [corpus[doc_id] for doc_id in relevant_ids]
    evidence = [f"query: {case['input']['query']}"] + [
        f"{doc['id']} ({doc['title']}): {doc['content']}" for doc in docs
    ] + ["comparison set: all 25 records in data/eval/dialogpilot-500-v1/corpus.jsonl"]
    return {
        "decision": "accept",
        "corrected_expected": {},
        "confidence": 0.98,
        "reason": "The listed document directly answers or operationally supports the query; no other corpus document is required to make the answer complete.",
        "evidence": evidence,
    }


def stateful_review(case: dict[str, Any]) -> dict[str, Any]:
    scenario = case["input"]["scenario"]
    family = case["group_id"].removeprefix("stateful-")
    current_assertions = case["expected"]["assertions"]
    corrected = STATEFUL_EXPECTED_REVISIONS.get(family)
    reason = STATEFUL_SPECIAL_REASONS.get(
        family,
        "The assertion direction is conceptually reasonable, but the case is not independently executable: the setup is only a symbolic fixture name and defines no concrete pre-state, identities, action parameters, fault schedule, or observable oracle.",
    )
    return {
        "decision": "revise",
        "corrected_expected": {"assertions": corrected} if corrected else {},
        "confidence": 0.99,
        "reason": reason + " Add a concrete fixture contract; this review does not verify runtime behavior.",
        "evidence": [
            f"symbolic setup: {scenario['setup']}",
            f"action: {scenario['action']}",
            f"proposed assertions: {json.dumps(current_assertions, ensure_ascii=False, sort_keys=True)}",
            "repository search: no fixture implementations or stateful executor are present; tests/test_layered_eval_dataset.py checks only non-empty setup/action strings and boolean values",
        ],
    }


def render() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cases = read_jsonl(DATASET_ROOT / "cases.jsonl")
    corpus_rows = read_jsonl(DATASET_ROOT / "corpus.jsonl")
    corpus = {row["id"]: row for row in corpus_rows}
    manifest = json.loads((DATASET_ROOT / "manifest.json").read_text(encoding="utf-8"))
    if len(cases) != 500 or len({case["id"] for case in cases}) != 500:
        raise RuntimeError("review requires exactly 500 unique case IDs")

    reviews = []
    for case in cases:
        layer = case["layer"]
        if layer == "intent":
            judgment = intent_review(case)
        elif layer == "routing":
            judgment = routing_review(case)
        elif layer == "retrieval":
            judgment = retrieval_review(case, corpus)
        elif layer == "stateful":
            judgment = stateful_review(case)
        else:
            raise RuntimeError(f"unsupported layer: {layer}")
        reviews.append({
            "case_id": case["id"],
            "group_id": case["group_id"],
            "layer": layer,
            "split": case["split"],
            "proposed_expected": case["expected"],
            **judgment,
        })

    decisions = Counter(row["decision"] for row in reviews)
    by_layer_decision = Counter(f"{row['layer']}:{row['decision']}" for row in reviews)
    human_rows = [row for row in reviews if row["decision"] != "accept" or row["layer"] == "stateful"]
    summary = {
        "schema_version": 1,
        "reviewer_id": REVIEWER_ID,
        "reviewer_role": "Reviewer A",
        "reviewer_runtime": "OpenAI Codex current session",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_id": manifest.get("dataset_id"),
        "dataset_version": manifest.get("version"),
        "dataset_cases_sha256": manifest.get("cases_sha256"),
        "case_count": len(reviews),
        "decision_counts": dict(sorted(decisions.items())),
        "by_layer_decision": dict(sorted(by_layer_decision.items())),
        "human_queue_case_count": len(human_rows),
        "human_queue_group_count": len({row["group_id"] for row in human_rows}),
        "confidence_notice": "Self-reported confidence is uncalibrated triage metadata and must not be interpreted as probability.",
        "gold_notice": "This artifact is llm_reviewed evidence only; it does not create human_reviewed gold.",
        "stateful_notice": "All stateful cases require concrete fixtures and real execution; this review does not certify isolation or side effects.",
        "blindness_notice": "Not pristine blind: dataset overview, reported Planner 120/120 agreement, and a small expected-label prefix were seen before assignment clarification; no prior reviewer artifact was read and Planner was not executed.",
    }
    return reviews, summary


def main() -> int:
    reviews, summary = render()
    REVIEW_PATH.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in reviews),
        encoding="utf-8",
    )
    SUMMARY_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    queue_rows: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in reviews:
        if row["decision"] != "accept" or row["layer"] == "stateful":
            grouped.setdefault(row["group_id"], []).append(row)
    for group_id, rows in grouped.items():
        queue_rows.append({
            "group_id": group_id,
            "layer": rows[0]["layer"],
            "case_ids": [row["case_id"] for row in rows],
            "decisions": sorted({row["decision"] for row in rows}),
            "reasons": list(dict.fromkeys(row["reason"] for row in rows)),
            "proposed_expecteds": list(dict.fromkeys(
                json.dumps(row["proposed_expected"], ensure_ascii=False, sort_keys=True)
                for row in rows
            )),
            "corrected_expecteds": list(dict.fromkeys(
                json.dumps(row["corrected_expected"], ensure_ascii=False, sort_keys=True)
                for row in rows if row["corrected_expected"]
            )),
            "corrected_inputs": list(dict.fromkeys(
                json.dumps(row["corrected_input"], ensure_ascii=False, sort_keys=True)
                for row in rows if row.get("corrected_input")
            )),
            "requires_runtime_execution": rows[0]["layer"] == "stateful",
        })
    HUMAN_QUEUE_PATH.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in queue_rows),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

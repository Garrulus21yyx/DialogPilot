"""Development-only candidates; the selected production path is separate from ablations."""
import json

DECISION_CONTRACT = (
    "First resolve the ongoing information need from the current reply and history. "
    "A reply to a clarification supplies a condition for that need, not merely an acknowledgement. "
    "For policy, procedure, requirement or product-documentation questions, plan evidence retrieval "
    "unless supplied current authoritative evidence already establishes the answer. Distinguish "
    "information needed to START searching from information needed to decide PERSONAL applicability: "
    "search general rules with known conditions first; only ask before searching if the subject or "
    "requested task itself cannot be identified. Preserve supplied conditions without asking again. "
    "Do not replace an available read-only investigation with an offer to search later. "
    "Social closure with no remaining request needs only respond; ambiguous subject needs clarification. "
    "Evidence retrieval never authorizes a business write. Only the current registry and state "
    "define capabilities, bindings and approvals. "
)


def render_guidance(examples=()):
    if not examples:
        return DECISION_CONTRACT
    return DECISION_CONTRACT + (
        "\nIllustrative decision examples, not current conversation, evidence or authorization. "
        "Follow their decision principle, using only currently available goal kinds and capabilities; "
        "never copy their entities or policy assertions into current facts.\n"
    ) + json.dumps([{k: e[k] for k in ("input", "output", "lesson")} for e in examples],
                   ensure_ascii=False, sort_keys=True) + "\n"


def selection_input(payload):
    history = payload.get("conversation_context", {}).get("recent_messages", ())
    return "\n".join([*(f"{m['role']}: {m['content']}" for m in history),
                      "current: " + payload["message"],
                      "pending: " + json.dumps(payload.get("pending_input"), ensure_ascii=False)])

"""Rebase disjoint aggregate transitions; overlapping decisions never get guessed.

The conversation remains one CAS-protected authority. This is its explicit
collection algebra, not a second store or a generic last-writer-wins merge.
"""
from dataclasses import replace

from application.conversation_state import ConversationState, ConversationStateConflict


def rebase_transition(
    before: ConversationState, after: ConversationState, current: ConversationState,
) -> ConversationState:
    for name in ("tenant_id", "user_id", "conversation_id", "schema_version"):
        if not getattr(before, name) == getattr(after, name) == getattr(current, name):
            raise ConversationStateConflict("transition identity differs")
    # Ownership is a read dependency for every automation transition.
    if (before.owner, before.human_ticket_ref) != (current.owner, current.human_ticket_ref):
        if current == after:
            return current
        raise ConversationStateConflict("conversation ownership changed")
    updates = {}
    for name in ("owner", "human_ticket_ref", "pending_approval", "pending_interaction"):
        old, new, live = (getattr(state, name) for state in (before, after, current))
        if old != new:
            if live != old and live != new:
                raise ConversationStateConflict(f"concurrent transition overlaps {name}")
            updates[name] = new
    for name, key in (
        ("work_controls", lambda x: x.control_id),
        ("workstreams", lambda x: x.workstream_id),
        ("resume_bindings", lambda x: x.token),
        ("accepted_approvals", lambda x: (x.approval_id, x.version)),
        ("consumed_signal_ids", lambda x: x),
    ):
        old, new, live = ({key(x): x for x in getattr(state, name)}
                          for state in (before, after, current))
        for identity in old.keys() | new.keys():
            if old.get(identity) == new.get(identity):
                continue
            if live.get(identity) not in (old.get(identity), new.get(identity)):
                raise ConversationStateConflict(f"concurrent transition overlaps {name}")
            if identity in new:
                live[identity] = new[identity]
            else:
                live.pop(identity, None)
        updates[name] = (tuple(live[k] for k in sorted(live))
                         if name == "work_controls" else tuple(live.values()))
    if all(getattr(current, key) == value for key, value in updates.items()):
        return current
    # Constructor validates wait/workstream bindings after the whole transition.
    candidate = replace(current, version=current.version + 1, **updates)
    for name in ("pending_interaction", "pending_approval"):
        pending = getattr(candidate, name)
        if pending is not None and pending != getattr(current, name):
            if any(not candidate.accepts_work(item) for item in pending.suspended_work_items):
                raise ConversationStateConflict("cannot install a wait for retired work")
            origin = getattr(pending, "origin_control", None)
            if origin is not None and not candidate.accepts(origin):
                raise ConversationStateConflict("cannot install approval for a retired goal")
    return candidate

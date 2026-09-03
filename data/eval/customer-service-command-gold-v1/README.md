# Customer-service Command Gold v1

Project-authored, synthetic conversation-level contract cases for the bounded
production Registry. It is not real customer traffic and is not an external
benchmark. `dev.jsonl` is for implementation feedback; `heldout.jsonl` must not
be read while tuning. Each row freezes current state, history, user message,
the expected Command or terminal decision, arguments, and next Flow state.

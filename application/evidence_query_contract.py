"""Shared semantic instructions; source/authority validation remains runtime-owned."""
EVIDENCE_ACQUISITION = (
    "Determine what information the current request needs and whether available evidence supports it. "
    "Use applicable evidence already supplied; otherwise select available capabilities by what their results can establish. "
    "A fluent prior assistant reply or model knowledge is not verification of an external fact. "
    "User statements establish what the user reports; hypothetical premises remain hypothetical, not observed events. "
    "Ask for information or choices only the user must supply; use available tools for facts they can establish. "
    "When evidence is unavailable or insufficient, preserve that limitation rather than inventing a conclusion. "
)
QUERY_DESCRIPTION = (
    "One self-contained retrieval question based on the current request and relevant context, not an answer. "
    "Keep an already complete question unchanged when suitable. Resolve references without guessing missing details. "
    "Preserve entities and acronyms, the relation being asked about, negation scope, conditions and hypothetical status. "
    "If requesting multiple aspects, keep their shared conditions attached to each aspect. "
    "Do not insert proposed answers or prior assistant assertions as established facts."
)

# Comparing candidates

Build the eligible candidate set using authoritative catalog data and the
customer's restrictions. “Most expensive” means the maximum price within that
set, not the most expensive item anywhere in the catalog. Do not infer available
stock, supported changes or compatibility from a product name.

Compare each requested item separately, retaining its original item ID and
selected variant ID. A multi-item result is a mapping, not one shared last
variant. When equal candidates differ in a user-relevant property, ask for that
choice; do not ask for already fixed attributes again.

Calculate old and replacement totals with currency and quantities aligned.
Let delta = replacement total minus original total. Positive delta means an
additional charge; negative delta means a reduction. Whether a reduction is
refunded, credited or disallowed comes from the environment policy/tool outcome,
not arithmetic. Identify unknown fees instead of presenting them as zero.

Keep a concise comparison conclusion with source references in normal task
progress. Current stock/price checks required by a write tool remain authoritative;
this guide does not freeze prices across mutations or expire all evidence on
every new user message.

For conflicting changes to one order, inspect the operation preconditions before
preparing any write. A DAG can express dependencies but cannot make mutually
exclusive operations compatible. Explain the actual limitation and ask the user
to choose only when the supported operations cannot satisfy both goals.

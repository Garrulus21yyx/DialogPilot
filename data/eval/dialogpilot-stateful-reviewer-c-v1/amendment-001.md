# Sealed-set amendment 001

Original `cases.jsonl` SHA256: `11847eceee85d41cde24afc77e504de6bf9cbb5aadf6d5c8ad6bdfa8ce9b3731`.

The sealed file is unchanged. After execution, Reviewer C determined that `rc-tool-036` contains one over-strict assertion name/value: `durable_audit_present=true`. The requested contract only requires an in-process deque to be classified honestly as non-durable; it does not require introducing durable audit storage as a prerequisite for every otherwise-supported local tool runtime.

For future consumed-regression use, replace that assertion with `audit_durability_classified=true`, derived as true when the evidence explicitly reports process-local non-durable storage. Do not use this amendment to rescore the current one-time holdout. The independent run retains the original expected value and failure.

The separate `idempotency_contract_present=true` assertion remains valid and failed: write-tool retry identity/business deduplication is still an open contract.

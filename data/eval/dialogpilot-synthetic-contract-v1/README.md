# DialogPilot synthetic customer-service contract v1

This directory contains 80 locked, model-authored synthetic architecture contracts in eight batches. It is not human Gold and does not represent a natural customer distribution.

- cases.jsonl: combined cases.
- <batch-id>.jsonl: ten cases per authoring batch.
- schema.json: top-level JSON Schema plus enum constraints.
- synthetic-fixtures.json: fabricated asset, region annotation, knowledge, and repository-fixture catalog.
- manifest.json: dataset role, lock state, checksums, counts, and non-promotion guard.

Every product, image, screenshot, annotation, and synthetic policy is fabricated for evaluation and must never be presented as a real enterprise fact. SYNTHETIC_CONTRACT_LOCKED freezes the contract; it does not claim human review. run_status=NOT_RUN remains separate until every turn executes through ChatApplication.handle() and the grader checks authoritative state, receipts, and traces.

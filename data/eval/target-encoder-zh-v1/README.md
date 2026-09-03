# Target Encoder zh-v1 dataset

This is a frozen synthetic contract dataset for the Target Architecture v1
prototype. It uses three disjoint files:

- `train.jsonl` fits a Target-native four-way classifier;
- `calibration.jsonl` selects a separate acceptance threshold per read Skill;
- `heldout.jsonl` is used only as a capability-enablement gate.

The frozen labels are `general_qa`, `product_identification`,
`refund_status_summary`, and `__DEFER__`. Write requests, cross-domain requests,
security operations, handoff, generic order progress and ambiguous language are
represented by `__DEFER__`; the Encoder can never authorize them.

This dataset is deliberately described as synthetic prototype evidence, not as
production traffic or an external benchmark. The artifact enables only
`refund_status_summary`: its calibration Wilson lower bound is at least 0.88,
and its heldout accepted precision is at least 0.98 over at least 10 accepted
examples. Other classes remain disabled and fall through to the structured
semantic router.

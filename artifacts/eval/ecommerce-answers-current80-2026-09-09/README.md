# E08 current-arm answer replay

80 previously consumed synthetic cases, 20 rule families. Current frozen retrieval evidence only; 78 generated outputs and two upstream failures. DeepSeek v4 Flash, 81 total API requests including three existing output repairs.

- `manifest.json`: pre-run model/input/code identity.
- `completion.json`: generation-only completion; its pending semantic field records the state before review.
- `semantic-review.json`: explicit current-assistant semantic judgments, not independent human labels.
- `scored-answers.json`, `answers-review.zh-CN.md`: actual answers and complete review, including failures and local citation aliases.
- `metrics.json`, `audit.json`: final aggregation and actual prompt/source/citation/usage audit.

Raw `answers.jsonl` and deterministic `answers.jsonl.gz` remain local, with hashes in `audit.json`; they contain complete model requests, actual evidence, retries, and responses. Reaggregation needs the local capture. The compact committed output permits reviewing all answers without replaying an API. The generating script also requires the earlier scope-filtered retrieval capture and configured provider credentials.

The corpus background includes WixQA under its existing dataset attribution; target policies and questions are synthetic. This is not a fresh benchmark, real customer accuracy, or Conversation Agent/Flow/publication test. No model comparison or improvement in final-answer accuracy is claimed.

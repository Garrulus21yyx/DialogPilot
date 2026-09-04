# Target Encoder zh-v1 artifact

`manifest.json` binds the classifier digest, data split digests, Target Registry
bundle version, class-to-capability mapping, class thresholds, and both calibration
and heldout gates. `model.json` is a learned hashed character n-gram logistic
regression model that can be evaluated by the pure-Python online runtime.

The online loader verifies the model digest and the live Tool-or-Skill owner/effect
contract. Only classes with `enabled: true` may enter the fast path. A disabled,
low-confidence, missing-argument, state-conflicting, or missing-semantic-signal
candidate deterministically defers to the structured semantic router.

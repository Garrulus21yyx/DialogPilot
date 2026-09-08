"""Offline component supervision and its single-capability score projection.

Unknown annotations are masked, never inferred as absent from a DEFER label.
The model owns semantic predictions; the existing policy still owns execution.
"""
from __future__ import annotations

import numpy as np

from application.target_encoder_artifact import DEFER_LABEL
from evaluation.target_encoder_training import TARGETS

COMPONENT_OBJECTIVE = "component-multilabel-v1"
CAPABILITIES = tuple(sorted(TARGETS))
COMPONENTS = tuple(f"{kind}:{label}" for kind in ("requested", "denied")
                   for label in CAPABILITIES) + ("needs_planning",)


def encode_targets(annotations):
    if set(annotations) != set(COMPONENTS):
        raise ValueError("component annotation schema mismatch")
    if any(v not in (None, 0, 1) for v in annotations.values()):
        raise ValueError("component annotations must be binary or unknown")
    if all(v is None for v in annotations.values()):
        raise ValueError("example has no supervised component")
    return [-1. if annotations[k] is None else float(annotations[k]) for k in COMPONENTS]


def masked_component_loss(outputs, labels, num_items_in_batch=None):
    """Trainer loss hook: standard BCE, with unknown annotations excluded.

    This is the learning objective, not a custom training or optimizer loop.
    Each example has unit weight regardless of its annotation completeness.
    """
    import torch.nn.functional as F
    known = labels >= 0
    loss = F.binary_cross_entropy_with_logits(outputs.logits, labels.clamp_min(0), reduction="none")
    return ((loss * known).sum(-1) / known.sum(-1).clamp_min(1)).mean()


def component_routing_scores(probabilities, components, classes):
    values = np.asarray(probabilities)
    if set(components) != set(COMPONENTS) or set(classes) != {DEFER_LABEL, *CAPABILITIES}:
        raise ValueError("component/routing schema mismatch")
    if values.ndim != 2 or values.shape[1] != len(components):
        raise ValueError("component score shape mismatch")
    if not np.isfinite(values).all() or ((values < 0) | (values > 1)).any():
        raise ValueError("component scores outside [0,1]")
    requested = values[:, [components.index(f"requested:{k}") for k in CAPABILITIES]].copy()
    denied = values[:, [components.index(f"denied:{k}") for k in CAPABILITIES]]
    active = requested >= .5
    valid = ((active.sum(-1) == 1)
             & ~((denied >= .5) & active).any(-1)
             & (values[:, components.index("needs_planning")] < .5))
    requested[~valid] = 0
    columns = {label: requested[:, i] for i, label in enumerate(CAPABILITIES)}
    # A boundary score, not a calibrated probability of being out of domain.
    columns[DEFER_LABEL] = 1 - requested.max(-1)
    return np.stack([columns[label] for label in classes], axis=-1)

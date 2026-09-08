"""Candidate-conditioned whole-request supervision, shared by fit and inference.

These descriptions define the experiment's existing capability scope. They are
not keyword rules, a second router, or permission definitions.
"""
from __future__ import annotations

import numpy as np

from application.target_encoder_artifact import DEFER_LABEL


COVERAGE_OBJECTIVE = "whole-request-pair-v1"
CONTRACTS = {
    "zh": {
        "general_qa": "仅检索并解释公开知识、一般政策或操作说明，不读取个人实时业务状态，不修改业务。",
        "refund_status_summary": "仅查询并报告指定订单已有退款的当前状态，不解释一般政策，不发起或撤销退款。",
        "product_identification": "仅识别用户提供图片中的商品身份或型号，不查询库存、运费、价格，不下单或判断使用适配性。",
    },
    "en": {
        "general_qa": "Only retrieve and explain public knowledge, general policies or instructions; no personal live business state or business changes.",
        "refund_status_summary": "Only read and report the current status of an existing refund for the specified order; no general policy explanation, refund creation or cancellation.",
        "product_identification": "Only identify the product or model in the supplied image; no stock, shipping charge or price lookup, purchase or compatibility assessment.",
    },
}
PROMPTS = {
    "zh": "结合对话，仅执行以下能力就能完整满足用户本轮请求，无需其他能力或目标：",
    "en": "Given the dialogue, this capability alone completely satisfies the current user request, with no additional capability or goal required: ",
}


def hypotheses(language):
    return {label: PROMPTS[language] + description
            for label, description in sorted(CONTRACTS[language].items())}


def pair_targets(gold, labels):
    """A deferred/compound request has no sufficient single capability."""
    if gold not in {DEFER_LABEL, *labels}:
        raise ValueError("unknown coverage gold")
    return [int(gold == label) for label in labels]


def routing_scores(complete_scores, labels, classes):
    """Preserve independent pair scores; do not softmax across capabilities.

    Each pair is binary COMPLETE vs NOT_COMPLETE. Multiple COMPLETE argmaxes
    conflict with the one-capability contract and defer before calibration.
    The complement of the strongest candidate is a boundary score, not a
    calibrated OOD probability. Existing class calibration/margin owns ACCEPT.
    """
    values = np.asarray(complete_scores)
    if values.ndim != 2 or values.shape[1] != len(labels):
        raise ValueError("coverage scores must be rows by candidates")
    if set(classes) != {DEFER_LABEL, *labels}:
        raise ValueError("routing and coverage labels differ")
    if not np.isfinite(values).all() or ((values < 0) | (values > 1)).any():
        raise ValueError("coverage scores outside [0,1]")
    values = values.copy()
    values[(values >= .5).sum(axis=1) > 1] = 0
    columns = {label: values[:, index] for index, label in enumerate(labels)}
    columns[DEFER_LABEL] = 1 - values.max(axis=1)
    return np.stack([columns[label] for label in classes], axis=1)

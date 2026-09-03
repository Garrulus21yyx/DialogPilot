"""Run the production Structured command boundary on the frozen SGD benchmark."""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from application.structured_command_producer import StructuredLLMCommandProducer
from application.turn_understanding import fingerprint_message
from evaluation.public_sgd.contracts import (
    SgdAdapterError,
    load_jsonl,
    write_jsonl,
)
from evaluation.public_sgd.runtime import (
    SgdBenchmarkDataset,
    prediction_from_understanding,
)


RUNNER_VERSION = "dialogpilot-sgd-structured-runner-v1"


class CommandCompletion(Protocol):
    provider_version: str

    async def complete(self, *, system: str, input_json: str) -> str: ...


async def run_predictions(
    dataset: SgdBenchmarkDataset,
    completion: CommandCompletion,
    output_path: Path,
    *,
    limit: int | None = None,
    concurrency: int = 4,
    resume: bool = False,
) -> Mapping[str, Any]:
    if concurrency < 1:
        raise SgdAdapterError("concurrency must be positive")
    selected = _select_cases(dataset.cases, limit)
    existing: dict[str, Mapping[str, Any]] = {}
    if output_path.exists():
        if not resume:
            raise SgdAdapterError("prediction output exists; use resume explicitly")
        for row in load_jsonl(output_path):
            case_id = str(row.get("case_id") or "")
            if not case_id or case_id in existing:
                raise SgdAdapterError("existing predictions have invalid case ids")
            existing[case_id] = row
    selected_ids = {str(case["case_id"]) for case in selected}
    if set(existing).difference(selected_ids):
        raise SgdAdapterError("existing predictions do not belong to selected cases")

    producer = StructuredLLMCommandProducer(completion)
    semaphore = asyncio.Semaphore(concurrency)

    async def predict(case: Mapping[str, Any]) -> Mapping[str, Any]:
        async with semaphore:
            message, state, history = dataset.materialize(case)
            started = time.perf_counter()
            understanding = await producer.produce(
                message,
                fingerprint_message(message),
                state,
                dataset.registry,
                history=history,
            )
            prediction = dict(prediction_from_understanding(
                case, understanding, state, dataset.registry
            ))
            prediction["latency_ms"] = round(
                (time.perf_counter() - started) * 1000.0, 3
            )
            prediction["runner_version"] = RUNNER_VERSION
            prediction["provider_version"] = completion.provider_version
            return prediction

    pending = [case for case in selected if str(case["case_id"]) not in existing]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    combined = dict(existing)
    new_prediction_count = 0
    checkpoint_size = max(concurrency, concurrency * 4)
    for start in range(0, len(pending), checkpoint_size):
        batch = pending[start:start + checkpoint_size]
        produced = await asyncio.gather(*(predict(case) for case in batch))
        combined.update({str(row["case_id"]): row for row in produced})
        new_prediction_count += len(produced)
        write_jsonl(
            output_path,
            (
                combined[str(case["case_id"])]
                for case in selected
                if str(case["case_id"]) in combined
            ),
        )
    ordered = [combined[str(case["case_id"])] for case in selected]
    count, checksum = write_jsonl(output_path, ordered)
    return {
        "schema_version": "dialogpilot-sgd-prediction-run-v1",
        "runner_version": RUNNER_VERSION,
        "provider_version": completion.provider_version,
        "split": dataset.split,
        "case_count": count,
        "new_prediction_count": new_prediction_count,
        "resumed_prediction_count": len(existing),
        "predictions_sha256": checksum,
    }


def _select_cases(
    cases: Sequence[Mapping[str, Any]], limit: int | None
) -> tuple[Mapping[str, Any], ...]:
    if limit is None:
        return tuple(cases)
    if limit < 1:
        raise SgdAdapterError("limit must be positive")
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for case in cases:
        groups[str(case["provenance"]["mapping_rule"])].append(case)
    selected: list[Mapping[str, Any]] = []
    ordered_groups = [groups[key] for key in sorted(groups)]
    index = 0
    while len(selected) < min(limit, len(cases)):
        made_progress = False
        for group in ordered_groups:
            if index < len(group) and len(selected) < limit:
                selected.append(group[index])
                made_progress = True
        if not made_progress:
            break
        index += 1
    return tuple(selected)

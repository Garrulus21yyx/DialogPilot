#!/usr/bin/env python3
"""Run an asset-level L1 project diagnostic over a locked local fixture."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import uuid
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from application.perception import TieredPerceptionService  # noqa: E402
from application.routing_media_probe import RoutingMediaProbe  # noqa: E402
from evaluation.command_primary_eval.contracts import EvaluationStatus  # noqa: E402
from evaluation.command_primary_eval.media import MediaDirectAdapter  # noqa: E402
from evaluation.command_primary_eval.media_runner import MediaDirectRunner  # noqa: E402
from evaluation.synthetic_media_fixture import (  # noqa: E402
    SyntheticMediaFixtureError,
)
from evaluation.synthetic_media_l1_eval import (  # noqa: E402
    consume_expected_text,
    load_synthetic_media_l1,
)
from infrastructure.tesseract_ocr_provider import (  # noqa: E402
    TesseractOCRProvider,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a PROJECT_DIAGNOSTIC for explicit L1 routing, real local "
            "Tesseract OCR, provenance, and artifact consumption."
        ),
    )
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--source-case", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--language", default="eng")
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> dict[str, object]:
    if shutil.which("tesseract") is None:
        raise RuntimeError("tesseract executable is required")
    bundle = load_synthetic_media_l1(
        args.dataset,
        source_case_id=args.source_case,
        language=args.language,
    )
    adapter = MediaDirectAdapter(
        probe=RoutingMediaProbe(),
        perception=TieredPerceptionService(
            bundle.assets,
            ocr=TesseractOCRProvider(language=args.language),
            vlm=None,
        ),
        request_loader=lambda _case: bundle.request,
        consumer=partial(consume_expected_text, bundle),
    )
    report = asyncio.run(
        MediaDirectRunner(adapter).run(
            (bundle.case,),
            args.output,
            run_id=uuid.uuid4().hex,
            dataset_id="dialogpilot-synthetic-contract-v1",
            split="project-diagnostic",
            configuration={
                "score_role": "PROJECT_DIAGNOSTIC",
                "official_benchmarks": "NOT_RUN",
                "locked_contract_score_eligible": "false",
                "source_case_id": bundle.fixture.source_case_id,
                "perception_provider": TesseractOCRProvider.version,
                "grounding_scope": "ASSET_PAGE",
                "region_selection": "NOT_RUN",
            },
        )
    )
    return report.to_dict()


def main(argv: list[str] | None = None) -> int:
    try:
        report = run(parse_args(argv))
    except (OSError, RuntimeError, SyntheticMediaFixtureError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == EvaluationStatus.PASS.value else 1


if __name__ == "__main__":
    raise SystemExit(main())

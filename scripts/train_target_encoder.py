"""Train the frozen Target v1 encoder artifact from explicit data splits."""
from pathlib import Path
import argparse

from evaluation.target_encoder_training import train_target_encoder, TargetEncoderTrainingConfig
from application.encoder_input import CONTEXT_INPUT_SCHEMA


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "eval" / "target-encoder-zh-v1"
OUTPUT = ROOT / "artifacts" / "target-encoder-zh-v1"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--contextual", action="store_true")
    parser.add_argument("--languages", nargs="*", default=[])
    args = parser.parse_args()
    manifest = train_target_encoder(
        train_path=args.data / "train.jsonl",
        calibration_path=args.data / "calibration.jsonl",
        heldout_path=args.data / "heldout.jsonl",
        output_dir=args.output,
        config=TargetEncoderTrainingConfig(
            artifact_version=args.output.name,
            input_schema=CONTEXT_INPUT_SCHEMA if args.contextual else "text-v1",
            required_languages=tuple(args.languages)),
    )
    print(manifest["heldout_summary"])

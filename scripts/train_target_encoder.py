"""Train the frozen Target v1 encoder artifact from explicit data splits."""
from pathlib import Path

from evaluation.target_encoder_training import train_target_encoder


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "eval" / "target-encoder-zh-v1"
OUTPUT = ROOT / "artifacts" / "target-encoder-zh-v1"


if __name__ == "__main__":
    manifest = train_target_encoder(
        train_path=DATA / "train.jsonl",
        calibration_path=DATA / "calibration.jsonl",
        heldout_path=DATA / "heldout.jsonl",
        output_dir=OUTPUT,
    )
    print(manifest["heldout_summary"])

# Language-specific encoder data v3

Chinese and English are separate train/calibration/heldout datasets, not one
mixed-language adoption gate. These are **synthetic datasets**, not real customer gold.

## Sources and attribution

English external instructions: Copyright (c) Bitext Innovations, 2024,
[Bitext customer-support dataset](https://huggingface.co/datasets/bitext/Bitext-customer-support-llm-chatbot-training-dataset),
revision `430d1a89bd93bd1fa23c16f29dd53e73f0087443`,
licensed [CDLA-Sharing-1.0](https://cdla.dev/sharing-1-0/).
The external instruction subset and modifications distributed here retain that
license and attribution. Original responses are not used. Modifications:
explicit capability-label mapping, placeholder neutralization, deterministic
sampling, grouping/splitting, and metadata. Bitext describes its data as hybrid
synthetic generated from linguistically curated source seeds, not live transcripts.

Chinese: existing `data/eval/target-encoder-zh-v1` (previously consumed synthetic
regression data), plus authored single-turn and contextual examples.
New authored source phrases: `../encoder-language-seeds-v1.json`; role-aware
READ/WRITE confirmations, declines, negations and compound requests are generated
by `evaluation/encoder_language_data.py`. English product-identification examples
are authored, not relabelled from an unrelated Bitext category.

## Split and limitations

All prefix/context/answer variants of one authored source family remain in one
split. Bitext exact-normalized variants are grouped; original NLG seed-family IDs
are unavailable, so near-paraphrase leakage cannot be excluded. Old Chinese
split assignments are preserved, not claimed fresh. All normalized inputs and
group IDs are disjoint across the emitted splits; source hashes are in manifest.

Frozen independent challenge: `data/eval/encoder-language-independent-2026-09-08.jsonl`
was written without reading these seeds or model outputs and is not used in training.
It is also synthetic and small. Report independent and development results separately.

## Reproduce

Download the pinned Bitext CSV named
`Bitext_Sample_Customer_Support_Training_Dataset_27K_responses-v11.csv` from the source above.
Then, using a new output path:

```bash
PYTHONPATH=. .venv/bin/python -m evaluation.encoder_language_data \
  --bitext /path/to/downloaded.csv --seeds data/training/encoder-language-seeds-v1.json \
  --output /tmp/encoder-language-rebuild
PYTHONPATH=. OPENBLAS_NUM_THREADS=2 .venv/bin/python scripts/train_target_encoder.py \
  --contextual --languages zh --data /tmp/encoder-language-rebuild/zh \
  --output /tmp/encoder-zh-rebuild
# Repeat with --languages en and the corresponding en paths.
```

An initial dataset build used normalized grouping IDs as case IDs and rejected
English punctuation variants with duplicate case IDs before training. v2 corrected
case IDs; v3 added declined/compound replies under existing prompt groups before
the independent challenge was consumed. No runtime keyword/exception rule was added.

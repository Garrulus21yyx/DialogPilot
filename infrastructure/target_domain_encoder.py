"""One standard Transformers domain classifier, no Trainer in the online import path."""
import hashlib
import json
from pathlib import Path

from application.domain_encoder import DomainEncoderManifest, DomainPrediction, DomainEncoderUnavailable, DOMAINS, DEFER_DOMAIN
from application.encoder_fast_path import RankedCandidate
from application.encoder_input import EncoderInput

MODEL_INPUT_FILES = ("config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "vocab.txt")


def model_input_digests(path: Path):
    return {name: hashlib.sha256((path / name).read_bytes()).hexdigest() for name in MODEL_INPUT_FILES}


def validate_model_inputs(path: Path, manifest):
    if manifest["input_files_sha256"] != model_input_digests(path):
        raise ValueError("domain encoder config/tokenizer digest mismatch")
    config = json.loads((path / "config.json").read_text())
    labels = (DEFER_DOMAIN, *DOMAINS)
    if config["id2label"] != {str(i): label for i, label in enumerate(labels)}:
        raise ValueError("domain encoder label order mismatch")


def render_domain_input(value: EncoderInput):
    return json.dumps({"history": list(value.messages), "objectives": value.objectives,
                       "current_user": value.text}, ensure_ascii=False, separators=(",", ":"))


class TargetDomainEncoder:
    def __init__(self, path: Path, *, device="cpu", evaluation=False):
        raw = json.loads((path / "manifest.json").read_text())
        self.manifest = DomainEncoderManifest.from_dict(raw, evaluation=evaluation)
        validate_model_inputs(path, raw)
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        if hashlib.sha256((path / "model.safetensors").read_bytes()).hexdigest() != self.manifest.model_sha256:
            raise ValueError("domain encoder weight digest mismatch")
        self.device = torch.device(device)
        self.model = AutoModelForSequenceClassification.from_pretrained(path, local_files_only=True).float().to(self.device).eval()
        self.tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True)
        self.labels = tuple(self.model.config.id2label[i] for i in range(self.model.config.num_labels))
        if set(self.labels) != {*DOMAINS, DEFER_DOMAIN}:
            raise ValueError("model domain labels differ from contract")
        self.max_tokens = raw["max_tokens"]

    def predict(self, value):
        try:
            return self._predict(value)
        except (RuntimeError, OSError) as exc:
            raise DomainEncoderUnavailable("domain model inference failed") from exc

    def _predict(self, value):
        import torch
        encoded = self.tokenizer(render_domain_input(value), return_tensors="pt", truncation=False)
        if encoded["input_ids"].shape[1] > self.max_tokens:
            return DomainPrediction(tuple(RankedCandidate(k, 0.) for k in DOMAINS), 1.)
        with torch.inference_mode():
            scores = self.model(**{k: v.to(self.device) for k, v in encoded.items()}).logits.softmax(-1)[0].cpu().tolist()
        values = dict(zip(self.labels, scores))
        return DomainPrediction(tuple(RankedCandidate(k, values[k]) for k in sorted(
            DOMAINS, key=lambda k: (-values[k], k))), values[DEFER_DOMAIN])

"""版本化分层评测数据合同、校验、发现和 JSONL 持久边界。"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping


SCHEMA_VERSION = 1
LAYERS = frozenset({"intent", "routing", "retrieval", "stateful"})
SPLITS = frozenset({"dev", "heldout"})
REVIEW_STATUSES = frozenset({"human_reviewed", "provisional", "auto_mapped"})


class DatasetValidationError(ValueError):
    """数据格式、切分或 provenance 合同不成立。"""


@dataclass(frozen=True)
class EvalCase:
    """一条跨层统一的评测样本。"""

    case_id: str
    layer: str
    split: str
    group_id: str
    input: Dict[str, Any]
    expected: Dict[str, Any]
    tags: tuple[str, ...]
    source: Dict[str, Any]
    review: Dict[str, Any]

    @property
    def is_gold(self) -> bool:
        return self.review.get("status") == "human_reviewed"

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EvalCase":
        if int(raw.get("schema_version", 0)) != SCHEMA_VERSION:
            raise DatasetValidationError("unsupported case schema_version")
        case = cls(
            case_id=str(raw.get("id") or "").strip(),
            layer=str(raw.get("layer") or "").strip(),
            split=str(raw.get("split") or "").strip(),
            group_id=str(raw.get("group_id") or "").strip(),
            input=dict(raw.get("input") or {}),
            expected=dict(raw.get("expected") or {}),
            tags=tuple(str(tag) for tag in (raw.get("tags") or [])),
            source=dict(raw.get("source") or {}),
            review=dict(raw.get("review") or {}),
        )
        case.validate()
        return case

    def validate(self) -> None:
        if not self.case_id or not self.group_id:
            raise DatasetValidationError("case id and group_id are required")
        if self.layer not in LAYERS:
            raise DatasetValidationError(f"unsupported layer: {self.layer}")
        if self.split not in SPLITS:
            raise DatasetValidationError(f"unsupported split: {self.split}")
        if not str(self.input.get("message") or self.input.get("query") or "").strip():
            raise DatasetValidationError(f"{self.case_id}: input message/query is required")
        status = self.review.get("status")
        if status not in REVIEW_STATUSES:
            raise DatasetValidationError(f"{self.case_id}: invalid review status")
        if status == "human_reviewed":
            required_review = ("reviewer", "reviewed_at", "notes")
            missing_review = [key for key in required_review if not str(self.review.get(key) or "").strip()]
            if missing_review:
                raise DatasetValidationError(
                    f"{self.case_id}: human_reviewed requires {missing_review}"
                )
        if not self.source.get("dataset") or not self.source.get("license"):
            raise DatasetValidationError(f"{self.case_id}: source dataset/license required")
        required = {
            "intent": ("intent",),
            "routing": ("owners", "task_ids"),
            "retrieval": ("relevant_ids",),
            "stateful": ("assertions",),
        }[self.layer]
        missing = [key for key in required if key not in self.expected]
        if missing:
            raise DatasetValidationError(f"{self.case_id}: missing expected {missing}")
        if self.layer == "routing" and "disposition" in self.expected:
            disposition = str(self.expected["disposition"])
            if disposition not in {"execute", "clarify", "out_of_scope"}:
                raise DatasetValidationError(
                    f"{self.case_id}: invalid routing disposition {disposition}"
                )


@dataclass(frozen=True)
class DatasetBundle:
    """Manifest 与 cases/corpus 的一致性视图。"""

    root: Path
    manifest: Dict[str, Any]
    cases: tuple[EvalCase, ...]
    corpus: tuple[Dict[str, Any], ...]

    @classmethod
    def load(cls, root: str | Path, *, verify_checksum: bool = True) -> "DatasetBundle":
        dataset_root = Path(root)
        manifest_path = dataset_root / "manifest.json"
        cases_path = dataset_root / "cases.jsonl"
        if not manifest_path.is_file() or not cases_path.is_file():
            raise DatasetValidationError("dataset requires manifest.json and cases.jsonl")
        manifest = _read_json(manifest_path)
        if int(manifest.get("schema_version", 0)) != SCHEMA_VERSION:
            raise DatasetValidationError("unsupported manifest schema_version")
        if verify_checksum:
            expected_hash = str(manifest.get("cases_sha256") or "")
            if expected_hash != sha256_file(cases_path):
                raise DatasetValidationError("cases.jsonl checksum mismatch")
        raw_cases = _read_jsonl(cases_path)
        cases = tuple(EvalCase.from_dict(raw) for raw in raw_cases)
        corpus_path = dataset_root / "corpus.jsonl"
        corpus = tuple(_read_jsonl(corpus_path)) if corpus_path.is_file() else ()
        if verify_checksum and corpus_path.is_file():
            if str(manifest.get("corpus_sha256") or "") != sha256_file(corpus_path):
                raise DatasetValidationError("corpus.jsonl checksum mismatch")
        bundle = cls(dataset_root, manifest, cases, corpus)
        bundle.validate()
        return bundle

    def validate(self) -> None:
        ids: set[str] = set()
        group_splits: Dict[str, str] = {}
        corpus_id_rows = [str(doc.get("id") or "").strip() for doc in self.corpus]
        if any(not corpus_id for corpus_id in corpus_id_rows):
            raise DatasetValidationError("corpus document id is required")
        duplicate_corpus_ids = sorted(
            corpus_id for corpus_id, count in Counter(corpus_id_rows).items() if count > 1
        )
        if duplicate_corpus_ids:
            raise DatasetValidationError(
                f"duplicate corpus ids: {duplicate_corpus_ids}"
            )
        corpus_ids = set(corpus_id_rows)
        for case in self.cases:
            if case.case_id in ids:
                raise DatasetValidationError(f"duplicate case id: {case.case_id}")
            ids.add(case.case_id)
            prior_split = group_splits.setdefault(case.group_id, case.split)
            if prior_split != case.split:
                raise DatasetValidationError(
                    f"group {case.group_id} crosses dev/heldout boundary"
                )
            if case.layer == "retrieval":
                missing = set(map(str, case.expected["relevant_ids"])) - corpus_ids
                if missing:
                    raise DatasetValidationError(
                        f"{case.case_id}: relevant corpus ids missing: {sorted(missing)}"
                    )
        expected_count = int(self.manifest.get("case_count", -1))
        if expected_count != len(self.cases):
            raise DatasetValidationError(
                f"manifest case_count={expected_count}, actual={len(self.cases)}"
            )
        self._validate_expected_distribution()

    def _validate_expected_distribution(self) -> None:
        """若 manifest 声明分布合同，则在数据所有者边界强制执行。"""
        expected = self.manifest.get("expected_distribution")
        if expected is None:
            return
        if not isinstance(expected, Mapping):
            raise DatasetValidationError("expected_distribution must be an object")

        actual_layer = Counter(case.layer for case in self.cases)
        actual_split = Counter(case.split for case in self.cases)
        actual_matrix = Counter(f"{case.layer}:{case.split}" for case in self.cases)
        checks = {
            "by_layer": actual_layer,
            "by_split": actual_split,
            "by_layer_split": actual_matrix,
        }
        for dimension, actual in checks.items():
            declared = expected.get(dimension)
            if declared is None:
                continue
            normalized = {str(key): int(value) for key, value in dict(declared).items()}
            actual_normalized = {key: actual.get(key, 0) for key in normalized}
            if normalized != actual_normalized:
                raise DatasetValidationError(
                    f"expected_distribution.{dimension}={normalized}, "
                    f"actual={actual_normalized}"
                )

    def select(
        self,
        *,
        layer: str | None = None,
        split: str | None = None,
        gold_only: bool = False,
    ) -> List[EvalCase]:
        if layer is not None and layer not in LAYERS:
            raise DatasetValidationError(f"unsupported layer: {layer}")
        if split is not None and split not in SPLITS:
            raise DatasetValidationError(f"unsupported split: {split}")
        return [
            case for case in self.cases
            if (layer is None or case.layer == layer)
            and (split is None or case.split == split)
            and (not gold_only or case.is_gold)
        ]

    def summary(self) -> Dict[str, Any]:
        by_layer = {layer: 0 for layer in sorted(LAYERS)}
        by_split = {split: 0 for split in sorted(SPLITS)}
        review = {status: 0 for status in sorted(REVIEW_STATUSES)}
        for case in self.cases:
            by_layer[case.layer] += 1
            by_split[case.split] += 1
            review[case.review["status"]] += 1
        return {
            "dataset_id": self.manifest.get("dataset_id"),
            "version": self.manifest.get("version"),
            "case_count": len(self.cases),
            "corpus_count": len(self.corpus),
            "by_layer": by_layer,
            "by_split": by_split,
            "by_review_status": review,
            "cases_sha256": self.manifest.get("cases_sha256"),
        }


def write_dataset(
    root: str | Path,
    *,
    manifest: Mapping[str, Any],
    cases: Iterable[Mapping[str, Any]],
    corpus: Iterable[Mapping[str, Any]] = (),
) -> DatasetBundle:
    """确定性写入 JSONL，并在 manifest 中固定内容 checksum。"""
    target = Path(root)
    target.mkdir(parents=True, exist_ok=True)
    case_rows = sorted((dict(case) for case in cases), key=lambda item: str(item["id"]))
    corpus_rows = sorted((dict(doc) for doc in corpus), key=lambda item: str(item["id"]))
    cases_path = target / "cases.jsonl"
    _write_jsonl(cases_path, case_rows)
    corpus_path = target / "corpus.jsonl"
    if corpus_rows:
        _write_jsonl(corpus_path, corpus_rows)
    final_manifest = dict(manifest)
    final_manifest.update({
        "schema_version": SCHEMA_VERSION,
        "case_count": len(case_rows),
        "cases_sha256": sha256_file(cases_path),
    })
    if corpus_rows:
        final_manifest.update({
            "corpus_count": len(corpus_rows),
            "corpus_sha256": sha256_file(corpus_path),
        })
    (target / "manifest.json").write_text(
        json.dumps(final_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return DatasetBundle.load(target)


def load_registered_dataset(root: str | Path, dataset_id: str) -> DatasetBundle:
    """只允许从配置根目录按简单 ID 读取，拒绝路径穿越和任意文件读取。"""
    normalized = str(dataset_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", normalized):
        raise DatasetValidationError("invalid dataset_id")
    registry_root = Path(root).resolve()
    target = (registry_root / normalized).resolve()
    if target.parent != registry_root:
        raise DatasetValidationError("dataset_id escapes registry root")
    return DatasetBundle.load(target)


def discover_datasets(root: str | Path) -> List[Dict[str, Any]]:
    """列出可验证数据集；损坏条目以 typed error 暴露而不拖垮整个注册表。"""
    registry_root = Path(root)
    if not registry_root.is_dir():
        return []
    discovered: List[Dict[str, Any]] = []
    for child in sorted(registry_root.iterdir(), key=lambda item: item.name):
        if not child.is_dir() or not (child / "manifest.json").is_file():
            continue
        try:
            item = DatasetBundle.load(child).summary()
            item.update({"registry_id": child.name, "valid": True})
        except (DatasetValidationError, OSError, json.JSONDecodeError) as exc:
            item = {"registry_id": child.name, "valid": False, "error": str(exc)}
        discovered.append(item)
    return discovered


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise DatasetValidationError(f"{path.name} must contain an object")
    return data


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise DatasetValidationError(f"{path.name}:{line_number} must be an object")
        rows.append(value)
    return rows


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _main() -> int:
    parser = argparse.ArgumentParser(description="Validate and inspect a DialogPilot eval dataset")
    parser.add_argument("dataset", help="directory containing manifest.json/cases.jsonl")
    parser.add_argument("--no-checksum", action="store_true")
    args = parser.parse_args()
    bundle = DatasetBundle.load(args.dataset, verify_checksum=not args.no_checksum)
    print(json.dumps(bundle.summary(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

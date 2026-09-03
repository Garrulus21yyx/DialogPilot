"""Versioned local product-catalog owner for the bounded resume project."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path


class ProductCatalogError(ValueError):
    pass


@dataclass(frozen=True)
class ProductRecord:
    canonical_model: str
    product_name: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class ProductMatch:
    status: str
    catalog_version: str
    source_ref: str
    candidates: tuple[ProductRecord, ...]

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "status": self.status,
            "catalog_version": self.catalog_version,
            "source_ref": self.source_ref,
            "candidates": [item.canonical_model for item in self.candidates],
        }
        if len(self.candidates) == 1:
            data.update({
                "canonical_model": self.candidates[0].canonical_model,
                "product_name": self.candidates[0].product_name,
            })
        return data


class ProductCatalogService:
    """Load one immutable catalog generation and perform deterministic lookup."""

    def __init__(self, path: str | Path) -> None:
        raw = Path(path).read_bytes()
        payload = json.loads(raw)
        if payload.get("schema_version") != "product-catalog-v1":
            raise ProductCatalogError("unsupported product catalog schema")
        version = str(payload.get("catalog_version") or "").strip()
        records = tuple(
            ProductRecord(
                str(item["canonical_model"]).strip(),
                str(item["product_name"]).strip(),
                tuple(str(alias).strip() for alias in item.get("aliases", ())),
            )
            for item in payload.get("products", ())
        )
        if not version or not records:
            raise ProductCatalogError("product catalog is empty")
        identities = [item.canonical_model.casefold() for item in records]
        if len(identities) != len(set(identities)):
            raise ProductCatalogError("duplicate canonical product model")
        if any(
            not item.canonical_model or not item.product_name
            or any(not alias for alias in item.aliases)
            for item in records
        ):
            raise ProductCatalogError("product catalog record is incomplete")
        self.version = version
        self.source_ref = "product-catalog:v1:" + hashlib.sha256(raw).hexdigest()
        self._records = records

    def search(self, query: str) -> ProductMatch:
        normalized = _normalize(query)
        matches = tuple(
            item for item in self._records
            if any(
                _normalize(candidate) in normalized
                for candidate in (item.canonical_model, *item.aliases)
                if _normalize(candidate)
            )
        )
        status = "MATCHED" if len(matches) == 1 else "AMBIGUOUS" if matches else "NO_MATCH"
        return ProductMatch(status, self.version, self.source_ref, matches)


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())

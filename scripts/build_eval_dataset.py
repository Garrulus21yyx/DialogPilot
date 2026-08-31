#!/usr/bin/env python3
"""下载并确定性映射公开意图数据；生成结果默认不进入 Git。"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import urllib.request
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from evaluation.dataset import write_dataset


BANKING_BASE = "https://raw.githubusercontent.com/PolyAI-LDN/task-specific-datasets/master/banking_data"
CLINC_URL = "https://archive.ics.uci.edu/static/public/570/clinc150.zip"
BITEXT_URL = "https://raw.githubusercontent.com/bitext/customer-support-llm-chatbot-training-dataset/main/data/Bitext_Sample_Customer_Support_Training_Dataset_27K_responses-v11.csv"

BANKING_MAP = {
    "Refund_not_showing_up": "refund",
    "request_refund": "refund",
    "transaction_charged_twice": "payment_issue",
    "card_payment_fee_charged": "payment_issue",
    "extra_charge_on_statement": "payment_issue",
    "declined_card_payment": "payment_issue",
    "card_payment_not_recognised": "account_security",
    "cash_withdrawal_not_recognised": "account_security",
    "direct_debit_payment_not_recognised": "account_security",
    "compromised_card": "account_security",
    "lost_or_stolen_card": "account_security",
    "lost_or_stolen_phone": "account_security",
    "unable_to_verify_identity": "account_security",
    "verify_my_identity": "account_security",
    "passcode_forgotten": "technical_login",
    "pin_blocked": "technical_login",
    "card_not_working": "technical",
    "virtual_card_not_working": "technical",
    "contactless_not_working": "technical",
    "edit_personal_details": "account",
    "terminate_account": "account",
    "card_arrival": "logistics",
    "card_delivery_estimate": "logistics",
    # Project-scope banking questions with no narrower DialogPilot owner.
    "atm_support": "query",
    "card_acceptance": "query",
    "country_support": "query",
    "exchange_rate": "query",
    "fiat_currency_support": "query",
    "visa_or_mastercard": "query",
}
BANKING_MAPPING_VERSION = "dialogpilot-intent-map-v2"

BITEXT_MAP = {
    "contact_human_agent": "human_handoff",
    "complaint": "complaint",
    "delivery_options": "logistics",
    "delivery_period": "logistics",
    "track_order": "order_status",
    "check_invoice": "invoice",
    "get_invoice": "invoice",
    "check_refund_policy": "refund",
    "get_refund": "refund",
    "track_refund": "refund",
    "payment_issue": "payment_issue",
    "recover_password": "technical_login",
    "registration_problems": "technical_login",
    "create_account": "account",
    "delete_account": "account",
    "edit_account": "account",
    "switch_account": "account",
    "contact_customer_service": "human_handoff",
}


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "DialogPilot-eval-builder/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def _sample_per_label(
    rows: Iterable[Mapping[str, str]],
    *,
    label_key: str,
    max_per_label: int,
) -> List[Mapping[str, str]]:
    groups: Dict[str, List[Mapping[str, str]]] = defaultdict(list)
    for row in rows:
        groups[str(row[label_key])].append(row)
    selected: List[Mapping[str, str]] = []
    for label in sorted(groups):
        ordered = sorted(
            groups[label],
            key=lambda row: hashlib.sha256(
                json.dumps(dict(row), sort_keys=True).encode("utf-8")
            ).hexdigest(),
        )
        selected.extend(ordered[:max_per_label])
    return selected


def _case(
    *,
    source_name: str,
    license_name: str,
    source_url: str,
    source_split: str,
    source_label: str,
    message: str,
    mapped_intent: str,
) -> Dict[str, Any]:
    identity = hashlib.sha256(
        f"{source_name}\0{source_split}\0{source_label}\0{message}".encode("utf-8")
    ).hexdigest()[:16]
    return {
        "schema_version": 1,
        "id": f"external-{source_name}-{identity}",
        "layer": "intent",
        "split": "heldout" if source_split in {"test", "oos_test"} else "dev",
        "group_id": f"external-{source_name}-{identity}",
        "input": {"message": message},
        "expected": {"intent": mapped_intent},
        "tags": ["external", "auto-mapped", "oos" if mapped_intent == "other" else "mapped-intent"],
        "source": {
            "dataset": source_name,
            "license": license_name,
            "url": source_url,
            "original_label": source_label,
            "original_split": source_split,
            "mapping_version": BANKING_MAPPING_VERSION if source_name == "banking77" else "dialogpilot-intent-map-v1",
        },
        "review": {"status": "auto_mapped", "reviewer": None},
    }


def build_banking(max_per_label: int) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    for split in ("train", "test"):
        text = _download(f"{BANKING_BASE}/{split}.csv").decode("utf-8")
        rows = list(csv.DictReader(io.StringIO(text)))
        mapped = [row for row in rows if row.get("category") in BANKING_MAP]
        for row in _sample_per_label(mapped, label_key="category", max_per_label=max_per_label):
            label = str(row["category"])
            cases.append(_case(
                source_name="banking77",
                license_name="CC-BY-4.0",
                source_url="https://github.com/PolyAI-LDN/task-specific-datasets",
                source_split=split,
                source_label=label,
                message=str(row["text"]),
                mapped_intent=BANKING_MAP[label],
            ))
    return cases, {
        "dataset": "banking77",
        "license": "CC-BY-4.0",
        "url": "https://github.com/PolyAI-LDN/task-specific-datasets",
        "mapping": "selected overlapping intents only; auto-mapped, not project gold",
    }


def build_clinc_oos(max_per_label: int) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    archive = zipfile.ZipFile(io.BytesIO(_download(CLINC_URL)))
    payload = json.loads(archive.read("clinc150_uci/data_full.json"))
    cases: List[Dict[str, Any]] = []
    for split in ("oos_train", "oos_test"):
        rows = [{"text": text, "label": label} for text, label in payload[split]]
        for row in _sample_per_label(rows, label_key="label", max_per_label=max_per_label):
            cases.append(_case(
                source_name="clinc150-oos",
                license_name="CC-BY-4.0",
                source_url="https://archive.ics.uci.edu/dataset/570/clinc150",
                source_split=split,
                source_label="oos",
                message=str(row["text"]),
                mapped_intent="other",
            ))
    return cases, {
        "dataset": "clinc150-oos",
        "license": "CC-BY-4.0",
        "url": "https://archive.ics.uci.edu/dataset/570/clinc150",
        "mapping": "OOS examples only; mapped to DialogPilot other",
    }


def build_bitext(
    max_per_label: int,
    *,
    accept_cdla_sharing: bool,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if not accept_cdla_sharing:
        raise SystemExit(
            "Bitext uses CDLA-Sharing-1.0. Re-run with --accept-cdla-sharing "
            "only after accepting attribution/share-alike publication obligations."
        )
    rows = list(csv.DictReader(io.StringIO(_download(BITEXT_URL).decode("utf-8"))))
    mapped = [row for row in rows if row.get("intent") in BITEXT_MAP]
    cases = []
    for row in _sample_per_label(mapped, label_key="intent", max_per_label=max_per_label):
        label = str(row["intent"])
        cases.append(_case(
            source_name="bitext-customer-support",
            license_name="CDLA-Sharing-1.0",
            source_url="https://github.com/bitext/customer-support-llm-chatbot-training-dataset",
            source_split="train",
            source_label=label,
            message=str(row["instruction"]),
            mapped_intent=BITEXT_MAP[label],
        ))
    return cases, {
        "dataset": "bitext-customer-support",
        "license": "CDLA-Sharing-1.0",
        "url": "https://github.com/bitext/customer-support-llm-chatbot-training-dataset",
        "mapping": "opt-in mapped subset; publication must preserve CDLA-Sharing terms and attribution",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build auto-mapped external DialogPilot intent eval data")
    parser.add_argument("--source", choices=("banking77", "clinc150-oos", "bitext"), required=True)
    parser.add_argument("--max-per-label", type=int, default=20)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--accept-cdla-sharing", action="store_true")
    args = parser.parse_args()
    max_per_label = max(1, args.max_per_label)
    builders = {
        "banking77": lambda: build_banking(max_per_label),
        "clinc150-oos": lambda: build_clinc_oos(max_per_label),
        "bitext": lambda: build_bitext(
            max_per_label, accept_cdla_sharing=args.accept_cdla_sharing,
        ),
    }
    cases, source = builders[args.source]()
    output = args.output or Path("data/eval/generated") / f"{args.source}-v1"
    bundle = write_dataset(
        output,
        manifest={
            "dataset_id": f"dialogpilot-external-{args.source}",
            "version": "1.0.0-auto-mapped",
            "status": "auto_mapped",
            "created_at": date.today().isoformat(),
            "description": "External intent pressure-test data; excluded from project-gold metrics by default.",
            "split_policy": {"dev": "upstream train", "heldout": "upstream test"},
            "review_policy": {"gold_status": "human_reviewed", "default_status": "auto_mapped"},
            "sources": [source],
        },
        cases=cases,
    )
    print(json.dumps(bundle.summary(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

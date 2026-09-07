"""Public knowledge catalog configuration loaded once per turn/import snapshot."""
import json
import os
from pathlib import Path
from application.sales_channels import filter_contract


def load_knowledge_filter_contract():
    path = os.environ.get("KNOWLEDGE_FILTER_CATALOG_FILE", "").strip()
    if not path:
        return filter_contract()
    raw = Path(path).read_text(encoding="utf-8")
    if len(raw) > 20000:
        raise ValueError("knowledge catalog configuration exceeds bounds")
    value = json.loads(raw)
    if value is None:
        raise ValueError("configured knowledge catalog must be an object")
    return filter_contract(value)

"""Host-provided business vocabulary; persisted source IDs remain data."""
from collections.abc import Mapping
import hashlib
import json
import re


def validate_sales_channel(value, *, source=False):
    """Storage contract validates ID shape, never a process-global vocabulary."""
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value) or (value == "global" and not source):
        raise ValueError("invalid sales channel ID")
    return value


def filter_contract(value=None):
    """Normalize a bounded host snapshot. Missing configuration exposes no filter."""
    if value is None:
        value = {"catalog_id": "unconfigured", "sales_channels": {}}
    if not isinstance(value, Mapping) or set(value) != {"catalog_id", "sales_channels"}:
        raise ValueError("invalid knowledge filter contract")
    catalog_id, channels = value['catalog_id'], value['sales_channels']
    if not isinstance(catalog_id, str) or not catalog_id.strip() or len(catalog_id) > 128:
        raise ValueError("invalid catalog identity")
    if not isinstance(channels, Mapping) or len(channels) > 32:
        raise ValueError("invalid channel directory")
    for key, label in channels.items():
        validate_sales_channel(key)
        if not isinstance(label, str) or not label.strip() or len(label) > 256:
            raise ValueError("channel meaning required")
    return {"catalog_id": catalog_id, "sales_channels": dict(sorted(channels.items()))}


def filter_contract_fingerprint(value=None):
    return hashlib.sha256(json.dumps(filter_contract(value), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def require_catalog_channel(value, contract=None, *, source=False):
    validate_sales_channel(value, source=source)
    if source and value == "global":
        return value
    if value not in filter_contract(contract)['sales_channels']:
        raise ValueError("sales channel is not supported by this knowledge catalog")
    return value


def sales_channel_schema(contract=None):
    channels = filter_contract(contract)['sales_channels']
    if not channels:
        return None
    return {"type": "string", "enum": list(channels), "description":
        "可选的政策适用购买渠道。" + "；".join(f"{key}＝{label}" for key, label in channels.items())
        + "。按本次问题明确指定的渠道选择；假设性问题使用假设渠道。未知或目录未支持时省略，保留在query中。"
        "配送方式、聊天入口不属于购买渠道；不要用当前订单渠道覆盖用户明确提出的假设。"}

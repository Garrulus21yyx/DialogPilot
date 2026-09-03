"""Governed media and catalog tools used by the Product domain."""
from __future__ import annotations

from typing import Any, Dict, Optional

from application.media_asset import AssetStatus
from mcp.tool_manager import Tool, ToolRisk


def product_tools(asset_store, ocr_provider, catalog):
    async def media_read(
        params: Dict[str, Any], context: Optional[Dict[str, Any]],
    ):
        asset_id = _required(params.get("asset_id"), "asset_id")
        tenant_id = _trusted(context, "tenant_id")
        user_id = _trusted(context, "user_id")
        asset, content = asset_store.get(
            asset_id, tenant_id=tenant_id, user_id=user_id,
        )
        if asset.status is not AssetStatus.SCANNED:
            raise ValueError("asset has not passed security scan")
        artifact = ocr_provider.extract(asset, content)
        parse = artifact.parse_result
        if parse is None:
            raise ValueError("OCR provider omitted ParseResult")
        text = "\n".join(node.text for node in parse.nodes if node.text).strip()
        return {
            "asset_id": asset.asset_id,
            "asset_checksum": asset.checksum,
            "text": text,
            "evidence_ref": parse.parse_result_id,
            "producer": parse.producer,
            "producer_version": parse.producer_version,
        }

    async def catalog_search(
        params: Dict[str, Any], _context: Optional[Dict[str, Any]],
    ):
        return catalog.search(_required(params.get("query"), "query")).to_dict()

    return (
        Tool(
            name="media_read",
            description="读取当前认证用户已通过安全扫描的图片文字并返回可追溯 OCR 证据",
            handler=media_read,
            schema={
                "type": "object",
                "properties": {"asset_id": {"type": "string", "description": "已上传资源 ID"}},
                "required": ["asset_id"],
            },
            allowed_agents=("technical",), risk=ToolRisk.LOW, read_only=True,
            authority="media.visible_text", manifest_version="tool-manifest-v1",
            output_schema_version="media-visible-text-v1",
            preconditions=("authenticated_user", "scanned_asset"),
            idempotency="read_only", retry_policy="safe_read_retry",
            typed_outcomes=("OK", "NOT_FOUND", "NOT_READY", "UNAVAILABLE"),
            output_fields=(
                "asset_id", "asset_checksum", "text", "evidence_ref",
                "producer", "producer_version",
            ),
        ),
        Tool(
            name="catalog_search",
            description="用已提取的型号文字查询当前版本商品目录并返回规范型号",
            handler=catalog_search,
            schema={
                "type": "object",
                "properties": {"query": {"type": "string", "description": "型号或 OCR 文本"}},
                "required": ["query"],
            },
            allowed_agents=("technical",), risk=ToolRisk.LOW, read_only=True,
            authority="product.canonical_model", manifest_version="tool-manifest-v1",
            output_schema_version="product-catalog-match-v1",
            preconditions=("catalog_generation_active",), idempotency="read_only",
            retry_policy="safe_read_retry",
            typed_outcomes=("MATCHED", "NO_MATCH", "AMBIGUOUS", "UNAVAILABLE"),
            output_fields=(
                "status", "canonical_model", "product_name", "catalog_version",
                "source_ref", "candidates",
            ),
        ),
    )


def _trusted(context: Optional[Dict[str, Any]], key: str) -> str:
    return _required((context or {}).get(key), key)


def _required(value: object, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} is required")
    return result

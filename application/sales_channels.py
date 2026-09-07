"""Business-owned sales-channel vocabulary shared by source and query boundaries.

Extend this catalog before ingesting a new purchase channel. Chat transports and
fulfilment methods are separate concepts. ``global`` belongs only to sources.
"""
from types import MappingProxyType

SALES_CHANNELS = MappingProxyType({
    "web": "官网购买：商家官方网站下单渠道",
    "store": "实体门店购买：线下门店下单渠道",
})


def validate_sales_channel(value, *, source=False):
    if not isinstance(value, str) or (value not in SALES_CHANNELS and not (source and value == "global")):
        raise ValueError("unsupported sales channel")
    return value


def sales_channel_schema():
    return {"type": "string", "enum": list(SALES_CHANNELS), "description":
        "可选的政策适用购买渠道。" + "；".join(f"{key}＝{label}" for key, label in SALES_CHANNELS.items())
        + "。按本次问题明确指定的渠道选择；假设性问题使用假设渠道。未知或目录未支持时省略，保留在query中。"
        "配送方式、聊天入口不属于购买渠道；不要用当前订单渠道覆盖用户明确提出的假设。"}

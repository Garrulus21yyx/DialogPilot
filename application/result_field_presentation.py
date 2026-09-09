"""Render registered output fields without creating business inferences."""
from decimal import Decimal, InvalidOperation
import json

from application.agent_result import FactRecord, FactSourceKind
from application.capability_registry import CapabilityRegistryBundle


def result_fields_text(fact: FactRecord, registry: CapabilityRegistryBundle | None, *, locale: str) -> str | None:
    if registry is None or fact.source_kind is not FactSourceKind.VERIFIED_STATE:
        return None
    definition = next((tool for tool in registry.tools if tool.tool_id == fact.producer_id
                       and tool.authority == fact.requirement_id
                       and tool.output_schema_version == fact.producer_version), None)
    if definition is None or not definition.result_fields:
        return None
    data = json.loads(fact.value_json)
    lines = []
    for field in definition.result_fields:
        value = data
        for key in field.path:
            if not isinstance(value, dict) or key not in value:
                return None
            value = value[key]
        label = field.label_en if locale == 'en' else field.label
        if value is None:
            rendered = 'Not provided' if locale == 'en' else '未提供'
        elif field.format == 'text':
            if not isinstance(value, (str, int, float, bool)):
                return None
            rendered = str(value)
        else:
            currency = field.currency
            if field.currency_path:
                currency = data
                for key in field.currency_path:
                    if not isinstance(currency, dict) or key not in currency:
                        return None
                    currency = currency[key]
                if not isinstance(currency, str) or not currency.strip():
                    return None
            if isinstance(value, bool):
                return None
            try:
                amount = Decimal(str(value)) / field.scale
            except (InvalidOperation, ValueError):
                return None
            if not amount.is_finite():
                return None
            rendered = f'{amount:f} {currency}'
            if field.format == 'charge_delta':
                direction = ('Refund' if amount < 0 else 'Additional charge' if amount > 0 else 'No difference') if locale == 'en' else (
                    '应退差额' if amount < 0 else '应补差额' if amount > 0 else '无差额')
                rendered = f'{direction} {abs(amount):f} {currency}'
        lines.append(f'  {label}: {rendered}')
    return '\n'.join(lines)

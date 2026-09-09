"""Exact members of a user-approved operation set, independent of reply wording."""
from dataclasses import asdict, dataclass
import hashlib
import json

from application.entity_binding import EntityBinding
from application.work_item import ArgumentValue


@dataclass(frozen=True)
class ApprovalOperation:
    action_ref: str
    operation_key: str
    target_entity_ref: str
    target_entity_version: str
    arguments: tuple[ArgumentValue, ...]
    argument_bindings: tuple[EntityBinding, ...] = ()

    def __post_init__(self):
        if not all(isinstance(value, str) and value.strip() for value in (
            self.action_ref, self.operation_key, self.target_entity_ref, self.target_entity_version)):
            raise ValueError("approval operation requires exact identity and target version")
        object.__setattr__(self, "arguments", tuple(self.arguments))
        object.__setattr__(self, "argument_bindings", tuple(self.argument_bindings))
        values = {arg.name: arg.value_json for arg in self.arguments}
        if len(values) != len(self.arguments):
            raise ValueError("approval operation has duplicate arguments")
        if len({binding.field_name for binding in self.argument_bindings}) != len(self.argument_bindings):
            raise ValueError("approval operation has duplicate bindings")
        if any(values.get(binding.field_name) != binding.value_json for binding in self.argument_bindings):
            raise ValueError("approval binding differs from operation arguments")

    def view(self):
        return {"action_ref": self.action_ref, "operation_key": self.operation_key,
                "target_entity_ref": self.target_entity_ref,
                "target_entity_version": self.target_entity_version,
                "arguments": {arg.name: arg.value for arg in self.arguments}}


def approval_scope_key(operations):
    """Exact presentation identity; members are independent, not a write DAG."""
    operations = tuple(operations)
    if not operations or len({op.operation_key for op in operations}) != len(operations):
        raise ValueError("approval scope requires unique operations")
    if len(operations) == 1:
        return operations[0].operation_key
    payload = json.dumps([asdict(op) for op in operations], ensure_ascii=False,
                         sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "approval-scope:v1:" + hashlib.sha256(payload.encode()).hexdigest()


class ApprovalScope:
    """One processing contract for single- and multi-operation decisions.

    The existing persisted first member is retained in place; additional members
    have the identical operation contract. Consumers always iterate operations.
    No second grant store or alternate execution path is introduced.
    """

    @property
    def operations(self):
        return (ApprovalOperation(self.action_ref, self.operation_key,
            self.target_entity_ref, self.target_entity_version,
            self.arguments, self.argument_bindings), *self.additional_operations)

    @property
    def scope_key(self):
        return approval_scope_key(self.operations)

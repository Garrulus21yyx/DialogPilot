"""Exact members of a user-approved operation set, independent of reply wording."""
from dataclasses import asdict, dataclass
import hashlib
import json

from application.entity_binding import EntityBinding
from application.work_item import ArgumentValue, WorkControlBinding


@dataclass(frozen=True)
class ApprovalOperation:
    action_ref: str
    operation_key: str
    target_entity_ref: str
    target_entity_version: str
    arguments: tuple[ArgumentValue, ...]
    argument_bindings: tuple[EntityBinding, ...] = ()
    origin_work_item_id: str | None = None
    control: WorkControlBinding | None = None
    depends_on: tuple[str, ...] = ()

    def __post_init__(self):
        if not all(isinstance(value, str) and value.strip() for value in (
            self.action_ref, self.operation_key, self.target_entity_ref, self.target_entity_version)):
            raise ValueError("approval operation requires exact identity and target version")
        object.__setattr__(self, "arguments", tuple(self.arguments))
        object.__setattr__(self, "argument_bindings", tuple(self.argument_bindings))
        object.__setattr__(self, "depends_on", tuple(self.depends_on))
        if self.origin_work_item_id is not None and not self.origin_work_item_id.strip():
            raise ValueError("operation origin must be absent or nonblank")
        if (len(set(self.depends_on)) != len(self.depends_on)
                or self.operation_key in self.depends_on
                or any(not dependency.strip() for dependency in self.depends_on)):
            raise ValueError("operation dependencies must be distinct other operations")
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
    """Exact grant identity, including each origin and approved dependency."""
    operations = tuple(operations)
    if not operations or len({op.operation_key for op in operations}) != len(operations):
        raise ValueError("approval scope requires unique operations")
    known = set()
    for operation in operations:
        if not set(operation.depends_on) <= known:
            raise ValueError("approval operations must be in dependency order")
        known.add(operation.operation_key)
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
        origin = next((item for item in self.suspended_work_items
                       if item.work_item_id == self.origin_work_item_id), None)
        return (ApprovalOperation(self.action_ref, self.operation_key,
            self.target_entity_ref, self.target_entity_version,
            self.arguments, self.argument_bindings, self.origin_work_item_id,
            self.control or (origin.control if origin else None)), *self.additional_operations)

    @property
    def origin_controls(self):
        return tuple(dict.fromkeys(op.control for op in self.operations if op.control is not None))

    def validate_origins(self):
        """Every explicitly attributed member matches its stored task envelope."""
        tasks = {item.work_item_id: item for item in self.suspended_work_items}
        for operation in self.operations:
            if operation.origin_work_item_id is not None:
                origin = tasks.get(operation.origin_work_item_id)
                if origin is None or origin.control != operation.control:
                    raise ValueError("approval member differs from its originating objective")
        if self.additional_operations and any(op.origin_work_item_id is None for op in self.operations):
            raise ValueError("multi-operation approval requires every member's origin")

    @property
    def scope_key(self):
        return approval_scope_key(self.operations)

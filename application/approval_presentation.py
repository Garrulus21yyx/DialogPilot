"""Render concrete confirmation terms from the runtime-owned proposal."""
from collections.abc import Mapping


def _label(name):
    return str(name).replace("_", " ").replace(".", " ").strip().capitalize()


def _fields(value, indent="  "):
    if isinstance(value, Mapping):
        lines = []
        for name, item in value.items():
            if isinstance(item, (Mapping, list, tuple)):
                lines.append(f"{indent}- {_label(name)}:")
                lines.extend(_fields(item, indent + "  "))
            else:
                lines.append(f"{indent}- {_label(name)}: {item}")
        return lines
    if isinstance(value, (list, tuple)):
        lines = []
        for index, item in enumerate(value, 1):
            if isinstance(item, (Mapping, list, tuple)):
                lines.append(f"{indent}{index}.")
                lines.extend(_fields(item, indent + "  "))
            else:
                lines.append(f"{indent}{index}. {item}")
        return lines
    return [f"{indent}{value}"]


def render_approval_scope(operations, *, locale, action_semantics=(), registry=None):
    """One generic scope card; no tool execution or model-written permission.

    Labels may improve display; values come from immutable prepared arguments.
    Values are not interpreted as prices or promises about future operations.
    """
    labels = {row["action_ref"]: row.get("display_name") for row in action_semantics}
    lines = ["请确认以下操作（均尚未执行）：" if locale == "zh-CN" else
             "Please confirm these changes (none has been executed):"]
    for index, op in enumerate(operations, 1):
        title = labels.get(op["action_ref"]) or _label(op["action_ref"].split(":")[0])
        lines.append(f"{index}. {title}")
        preparation = registry.action(op["action_ref"]).preparation if registry else None
        version_field = preparation.target_version_argument if preparation else None
        lines.extend(_fields({name: value for name, value in op["arguments"].items()
                              if name != version_field}))
    lines.append("此次确认仅覆盖以上清单；其他待办仍保留。是否批准以上操作？" if locale == "zh-CN" else
                 "This confirmation covers only the changes listed above; other requested work remains pending. Do you approve these changes?")
    return "\n".join(lines)

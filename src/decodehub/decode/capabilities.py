"""Runtime capability matrix derived from adapter and protocol registries."""

from __future__ import annotations

from typing import Any

from ..acquisition.adapters import SPECS
from .bindings import all_bindings
from .plugins import load_plugins
from .presentation import all_presentations
from .registry import get_registry
from .schema import EVENT_SCHEMA_VERSION, known_event_fields


def protocol_capabilities() -> list[dict[str, Any]]:
    load_plugins()
    nodes = get_registry()
    presentations = {item.protocol: item for item in all_presentations()}
    slicer = {name: field.doc for name, field in nodes["slicer"].PARAMS.items()
              if field.doc}
    result: list[dict[str, Any]] = []
    for binding in all_bindings():
        node = nodes[binding.node_type]
        presentation = presentations.get(binding.protocol)
        params = {name: field.doc for name, field in node.PARAMS.items() if field.doc}
        if binding.precond_node_type:
            params.update({
                name: field.doc
                for name, field in nodes[binding.precond_node_type].PARAMS.items()
                if field.doc and name not in params
            })
        for role in binding.roles:
            params.setdefault(role, f"{role} 角色显式指定通道名（覆盖自动映射）")
        params.update(binding.tool_params_doc)
        if not binding.analog_direct:
            params.update({name: f"{doc}（模拟源切片时）"
                           for name, doc in slicer.items() if name not in params})
        result.append({
            "protocol": binding.protocol,
            "node_type": binding.node_type,
            "roles": list(binding.roles),
            "optional_roles": list(binding.optional_roles),
            "require_any": [list(group) for group in binding.require_any],
            "needs": dict(binding.needs),
            "hint": binding.hint,
            "params": params,
            "presentation": presentation.protocol if presentation else None,
            "preview_kinds": list(presentation.preview_kinds) if presentation else [],
            "event_fields": sorted(known_event_fields(binding.protocol)),
            "schema_version": EVENT_SCHEMA_VERSION,
        })
    return result


def _format_capabilities(*, planned: bool) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for key, spec in SPECS.items():
        if (spec.load is None) != planned:
            continue
        result[key] = {
            "description": spec.description,
            "options": {
                option.name: {
                    "type": option.type,
                    "description": option.doc,
                    "required": option.required,
                }
                for option in spec.options
            },
            **({"planned_note": spec.planned_note} if planned else {}),
        }
    return result


def capability_matrix() -> dict[str, Any]:
    return {
        "schema_version": EVENT_SCHEMA_VERSION,
        "protocols": protocol_capabilities(),
        "formats": _format_capabilities(planned=False),
        "planned_formats": _format_capabilities(planned=True),
    }


def protocol_catalog() -> dict[str, dict[str, Any]]:
    return {
        item["protocol"]: {
            key: item[key] for key in ("roles", "params", "needs", "hint")
        }
        for item in protocol_capabilities()
    }

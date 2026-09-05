"""事件发布语言的版本与可扩展注册表（阶段 C）。"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields
from typing import Iterable

EVENT_SCHEMA_VERSION = "1.0"
REPORT_SCHEMA_VERSION = "1.0"

_KNOWN_KINDS: set[str] = set()
_KNOWN_FIELDS: dict[str, set[str]] = {}
_KNOWN_ERRORS: set[str] = {
    "nack", "parity", "framing", "break", "truncated", "spurious-start",
    "baud-uncertain", "clock-stretch", "no-address", "bus-free", "crc",
    "spurious", "no-cs", "cs-midword", "partial-byte", "ambiguous-stop-bit",
    "after-tbit", "ambiguous", "unknown-ccc", "hdr", "incomplete-daa", "unsupported",
    "warn", "preamble",
}


def register_kinds(kinds: Iterable[str]) -> None:
    for kind in kinds:
        if not isinstance(kind, str) or "." not in kind:
            raise ValueError(f"事件 kind 必须是 protocol.name 形式: {kind!r}")
        if kind in _KNOWN_KINDS:
            raise ValueError(f"事件 kind 重复注册: {kind}")
        _KNOWN_KINDS.add(kind)


def known_kinds() -> frozenset[str]:
    return frozenset(_KNOWN_KINDS)


def register_event_fields(protocol: str, fields: Iterable[str]) -> None:
    if not isinstance(protocol, str) or not protocol:
        raise ValueError(f"字段注册要求非空 protocol: {protocol!r}")
    bucket = _KNOWN_FIELDS.setdefault(protocol, set())
    for name in fields:
        if not isinstance(name, str) or not name:
            raise ValueError(f"事件字段必须是非空字符串: {name!r}")
        if name in bucket:
            raise ValueError(f"事件字段重复注册: {protocol}.{name}")
        bucket.add(name)


def known_event_fields(protocol: str | None = None):
    if protocol is None:
        return {p: frozenset(names) for p, names in _KNOWN_FIELDS.items()}
    return frozenset(_KNOWN_FIELDS.get(protocol, ()))


def register_error_codes(codes: Iterable[str]) -> None:
    for code in codes:
        if not isinstance(code, str) or not code:
            raise ValueError(f"错误码必须是非空 str: {code!r}")
        _KNOWN_ERRORS.add(code)


def known_error_codes() -> frozenset[str]:
    return frozenset(_KNOWN_ERRORS)


def validate_errors(errors: Iterable[str]) -> None:
    """校验错误码，阻止拼写错误进入发布语言。"""
    for code in errors:
        if not isinstance(code, str) or not code:
            raise ValueError(f"事件 errors 必须是非空字符串: {code!r}")
        if code not in _KNOWN_ERRORS:
            raise ValueError(f"未知事件错误码: {code!r}；请先 register_error_codes")


def validate_event(event) -> None:
    if event.kind not in _KNOWN_KINDS:
        raise ValueError(f"未知事件 kind: {event.kind!r}；请先在 Presentation 注册")
    if event.schema_version != EVENT_SCHEMA_VERSION:
        raise ValueError(
            f"事件 schema_version={event.schema_version!r} 不兼容；当前为 {EVENT_SCHEMA_VERSION!r}"
        )
    registered = _KNOWN_FIELDS.get(event.kind.split(".", 1)[0])
    if registered:
        declared = {item.name for item in dataclass_fields(event)}
        declared -= {"kind", "t_start", "t_end", "label", "errors", "ann_class", "schema_version"}
        unknown = declared - registered
        if unknown:
            raise ValueError(
                f"事件字段未注册: {event.kind}: {', '.join(sorted(unknown))}"
            )
    validate_errors(event.errors)

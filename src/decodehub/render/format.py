"""事件 → Markdown 表 / JSON / CSV（图文配对的文本侧，ADR-006）。

协议特化呈现（中文类型名/内容列/CSV 专有列）查 decode/presentation.py
注册表（ADR-013）：本模块只依赖 DecodedEvent 基础字段——新增协议在
protocols/<p>/present.py 注册，此处零改动；未注册 kind 走 fallback
（类型=原文、内容=label、CSV 协议列留空）。

导出格式（ADR-019）：`EXPORT_FORMAT_SPECS` 是唯一登记点——key/ext/导出器/
描述同处一处；config.EXPORT_FORMATS、export_events 工具 enum、headless
runner 的遍历全部派生。新增导出格式 = 在此登记一个 ExportFormatSpec。
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from typing import Callable

from ..decode.events import DecodeReport, DecodedEvent
from ..decode.presentation import all_presentations, presentation_of


def fmt_t(seconds: float) -> str:
    """秒 → 工程单位（自动 ns/µs/ms/s）。"""
    s = float(seconds)
    for unit, scale in (("s", 1), ("ms", 1e-3), ("µs", 1e-6), ("ns", 1e-9)):
        if abs(s) >= scale or unit == "ns":
            return f"{s / scale:.4g} {unit}"
    return f"{s:.4g} s"


def events_markdown(events: list[DecodedEvent], start_index: int = 1) -> str:
    """编号事件表（编号与时序图 span 编号一一对应）。"""
    if not events:
        return "（无事件）"
    rows = ["| # | t_start | Δt | 类型 | 内容 | 状态 |",
            "|---|---------|-----|------|------|------|"]
    for i, ev in enumerate(events, start_index):
        status = "OK" if not ev.errors else "ERR:" + ",".join(ev.errors)
        pres = presentation_of(ev.kind)
        kind_cn = pres.kind_cn.get(ev.kind, ev.kind) if pres else ev.kind
        detail = pres.detail_fn(ev) if pres else ev.label
        rows.append(
            f"| {i} | {fmt_t(ev.t_start)} | {fmt_t(ev.t_end - ev.t_start)} "
            f"| {kind_cn} | {detail} | {status} |"
        )
    return "\n".join(rows)


def report_json(report: DecodeReport) -> str:
    return json.dumps(report.to_json(), ensure_ascii=False, indent=2)


def _csv_union_columns() -> list[str]:
    """全部已注册协议专有列的并集（按注册顺序、列名先到先得——表头稳定）。"""
    names: list[str] = []
    for p in all_presentations():
        for name, _fn in p.csv_columns:
            if name not in names:
                names.append(name)
    return names


def report_csv_rows(report: DecodeReport) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    proto_cols = _csv_union_columns()
    w.writerow(["idx", "t_start", "t_end", "duration_s", "kind", "label", "ann_class", "errors",
                *proto_cols, "schema_version"])
    for i, ev in enumerate(report.events, 1):
        ev.to_dict()  # validate the publication contract before exporting
        pres = presentation_of(ev.kind)
        fns = dict(pres.csv_columns) if pres else {}
        w.writerow([
            i, f"{ev.t_start:.9g}", f"{ev.t_end:.9g}", f"{ev.t_end - ev.t_start:.9g}",
            ev.kind, ev.label, ev.ann_class, ";".join(ev.errors),
            *(fns[name](ev) if name in fns else "" for name in proto_cols), ev.schema_version,
        ])
    return buf.getvalue()


# ---- 导出格式注册表（ADR-019：单一登记点，消费方全部派生） -------------------

@dataclass(frozen=True)
class ExportFormatSpec:
    """一种导出格式：键、扩展名、一句话描述、DecodeReport → 文本。"""

    key: str
    ext: str
    description: str
    exporter: Callable[[DecodeReport], str]


EXPORT_FORMAT_SPECS: dict[str, ExportFormatSpec] = {}


def _register(spec: ExportFormatSpec) -> None:
    if spec.key in EXPORT_FORMAT_SPECS:
        raise ValueError(f"导出格式重复注册: {spec.key}")
    EXPORT_FORMAT_SPECS[spec.key] = spec


_register(ExportFormatSpec("csv", "csv", "表格（公共列 + 协议专有列并集）", report_csv_rows))
_register(ExportFormatSpec("json", "json", "机器可读（DecodeReport.to_json）", report_json))
_register(ExportFormatSpec("md", "md", "Markdown 事件表（图文配对）",
                           lambda r: events_markdown(r.events)))

EXPORT_FORMAT_KEYS: tuple[str, ...] = tuple(EXPORT_FORMAT_SPECS)


def export_report(fmt: str, report: DecodeReport) -> str:
    """fmt → 导出文本；未知格式报 DecodehubError（列出可用键）。"""
    try:
        return EXPORT_FORMAT_SPECS[fmt].exporter(report)
    except KeyError:
        from ..shared.errors import DecodehubError

        raise DecodehubError(
            f"未知导出格式 {fmt!r}；可用: {list(EXPORT_FORMAT_KEYS)}"
        ) from None

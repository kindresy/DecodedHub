"""适配器注册表（ADR-018）：格式键 → AdapterSpec，单一登记点。

SPECS 的插入顺序 = 嗅探优先序（docs/40-acquisition.md 规则 1–6）；
`load=None` 的条目为延后支持格式。SUPPORTED_FORMATS / PLANNED_FORMATS /
options_properties / 必填校验全部由 SPECS 派生——别处不再维护第二份格式清单。

新增一个采集格式的全部动作：
  1. 写 adapters/<fmt>.py：load(path, options) -> Capture + SPEC（嗅探匹配器/
     options 声明/目录描述）；
  2. 在下方 SPECS 元组登记一行（位置即嗅探优先级）。
"""

from ...shared.errors import DecodehubError, IngestError
from .generic_csv import SPEC as _generic_csv
from .kingst_bin import SPEC as _kingst_bin
from .kingst_csv import SPEC as _kingst_csv
from .kingst_kvdat import SPEC as _kingst_kvdat
from .mcu_adc_bin import SPEC as _mcu_adc_bin
from .mcu_adc_csv import SPEC as _mcu_adc_csv
from .mho98_csv import SPEC as _mho98_csv
from .mho98_npz import SPEC as _mho98_npz
from .planned import SPECS as _PLANNED_SPECS
from .saleae_csv import SPEC as _saleae_csv
from .sigrok_sr import SPEC as _sigrok_sr
from .spec import AdapterSpec

# 嗅探优先序（解析/延后混合排列；sniff=None 的条目在遍历中被跳过）
_SALEAE_SAL, _SALEAE_BINARY, _SALEAE_DATA_TABLE = _PLANNED_SPECS

SPECS: dict[str, AdapterSpec] = {}
for _s in (
    _SALEAE_SAL,        # 规则 1: .sal / zip 工程包（延后）
    _sigrok_sr,         # 规则 1b: .sr / Sigrok ZIP 会话
    _kingst_kvdat,      # 规则 2: kvdat 魔数
    _SALEAE_BINARY,     # 规则 3: <SALEAE> 魔数（延后）
    _mho98_npz,         # 规则 4: npz 键 t_s/v_V
    _mho98_csv,         # 规则 5: 文本表头（依次）
    _kingst_csv,
    _kingst_bin,        # （不可嗅探，仅显式 format=）
    _saleae_csv,
    _SALEAE_DATA_TABLE,
    _mcu_adc_csv,
    _generic_csv,
    _mcu_adc_bin,       # 规则 6: 偶数大小裸二进制兜底
):
    if _s.key in SPECS:
        raise DecodehubError(f"适配器格式键重复: {_s.key}")
    SPECS[_s.key] = _s

# ---- 派生清单（唯一事实来源 = SPECS）----------------------------------------

SUPPORTED_FORMATS: dict[str, str] = {
    k: s.description for k, s in SPECS.items() if s.load is not None
}
PLANNED_FORMATS: dict[str, str] = {
    k: s.description for k, s in SPECS.items() if s.load is None
}


def get_spec(format_key: str) -> AdapterSpec:
    try:
        return SPECS[format_key]
    except KeyError:
        raise DecodehubError(
            f"未知格式键 {format_key!r}；可用: {sorted(SUPPORTED_FORMATS)}"
        ) from None


def resolve_spec(format_key: str) -> AdapterSpec:
    """get_spec + 延后格式守卫：load 必可用，否则抛延后说明。"""
    spec = get_spec(format_key)
    if spec.load is None:
        raise DecodehubError(
            f"格式 {format_key} 在当前版本延后支持：{spec.planned_note}（ADR-007）"
        )
    return spec


def get_adapter(format_key: str):
    """兼容入口：返回 load(path, options)；延后/未知格式在此统一报错。"""
    return resolve_spec(format_key).load


def validate_options(spec: AdapterSpec, options: dict | None) -> None:
    """声明式前置校验：required 选项缺失即 IngestError（解析前报错）。"""
    opts = options or {}
    missing = [o for o in spec.options if o.required and not opts.get(o.name)]
    if missing:
        detail = "；".join(f"{o.name}（{o.doc}）" for o in missing)
        raise IngestError(f"{spec.key} 缺少必填 options: {detail}")


def options_line(format_key: str) -> str:
    """capabilities 用的选项清单；`*` 后缀 = 必填。"""
    return "、".join(
        o.name + ("*" if o.required else "") for o in SPECS[format_key].options
    )


def options_properties() -> dict[str, dict]:
    """全部已声明选项的 JSON-schema properties（lock_source/add_source 用）。

    同名选项的 doc 去重合并；required 选项标注其格式键。
    """
    out: dict[str, dict] = {}
    docs: dict[str, list[str]] = {}
    required_by: dict[str, list[str]] = {}
    for s in SPECS.values():
        for o in s.options:
            entry = out.setdefault(o.name, {"type": o.type})
            if o.doc and o.doc not in docs.setdefault(o.name, []):
                docs[o.name].append(o.doc)
            if o.required and s.key not in required_by.setdefault(o.name, []):
                required_by[o.name].append(s.key)
    for name, entry in out.items():
        desc = "；".join(docs.get(name, []))
        if required_by.get(name):
            desc += ("；" if desc else "") + f"必填于 {'/'.join(required_by[name])}"
        if desc:
            entry["description"] = desc
    return out

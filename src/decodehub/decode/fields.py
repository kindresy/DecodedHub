"""payload 字段规格引擎（ADR-016）：一种语言描述，同一套算法结算。

六原语覆盖报文小字段切分——分段（序列+重复）、类别（switch 分发）、
动态长度（size 表达式）正是协议描述语言（PDL）的核心原语，本模块是
其极简实现（语法精神同 Kaitai Struct 子集，纯 dict 可经 JSON/MCP 下发）：

  1. 序列        seq: [{id, type}]；type ∈ u8..u64/s8..s64/f32/f64/b1..b64/
                 bytes/str/自定义类型名（types 表）
  2. 重复        repeat: "expr"(repeat_expr 计数) | "eos"(至流尾) | "until"
  3. 动态长度    size: 表达式 | size_eos: true | terminator: 哨兵字节（不含）
  4. 类别分发    switch_on: 判别字段 id + cases: {"0x01": 类型名, "*": 缺省}
  5. 计算与校验  value: 表达式（不消耗字节）；valid: {eq: 表达式} /
                 contents: "A5 5A"；crc: 预设（crc8/crc16_ccitt_false/
                 crc16_xmodem/crc16_modbus/crc32/sum8/xor8）、内联参数
                 （width/poly/init/refin/refout/xorout，Rocksoft™ 模型）
                 或具名函数 {"fn": 名字}；over="prefix" 覆盖本层起点到
                 本字段前；表达式支持 + - * / % << >> & | ^ 比较 and/or、
                 len(x)、root./parent. 跨层引用
  6. 辅助        enum 命名 / if 条件字段 / endian（顶层缺省 be，字段可覆盖）/
                 process 具名变换钩子（register_field_fn 注册在受信侧，
                 规格只携带名字——永不下发/内联可执行代码）/ doc 文档

边界约定：位域（bN）共享位游标、MSB 在前；非位域字段对齐到字节边界；
位级动态长度不支持（与 Kaitai 同款的已知弱项）。CRC 已内建三种写法
（预设/内联参数/具名函数）；跨任意字节区间的 over:{from,to} 与流式
半包粘包属已知边界。

错误模型：规格写错 → 编译期 FieldSpecError（fail fast，注册/建图即暴露）；
数据不符 → FieldView.errors 字段（"truncated"/"incomplete"/"valid"/"crc"/
"no-case"/"no-progress"/"expr"），不中断其余字段的呈现（ADR-004 同款哲学）。
"""

from __future__ import annotations

import ast
import json
import logging
import struct as _struct
from dataclasses import asdict, dataclass, field as dc_field
from typing import Any, Callable, Mapping

from ..shared.errors import DecodehubError, FieldSpecError
from .events import DecodedEvent
from .presentation import Presentation, register_presentation
from .schema import validate_event


@dataclass
class FieldView:
    """一个已结算的小字段：偏移/宽度/值/枚举名/呈现提示/子结构/错误。

    value 永远是原始值（机器可读）；display/scale/unit 是规格声明的呈现提示，
    供格式化出人话（bcd 版本号、十进制、物理单位），JSON 导出时一并带出。
    """

    id: str
    offset_bits: int
    width_bits: int
    kind: str  # uint|int|float|bytes|str|struct|value
    value: Any = None
    enum_label: str | None = None
    display: str | None = None  # hex(缺省)|dec|bcd
    scale: float | None = None  # 显示值 = value * scale
    unit: str | None = None
    children: list["FieldView"] = dc_field(default_factory=list)
    errors: list[str] = dc_field(default_factory=list)


@dataclass
class FieldSetEvent(DecodedEvent):
    """字段切分结果事件（kind = "fields.split"）：源帧事件 + 规格结算出的字段树。"""

    spec: str = ""          # 规格名（register_fields）或 "inline"
    source_kind: str = ""   # 载荷来源事件的 kind（如 i2c.transfer）
    fields: list[FieldView] = dc_field(default_factory=list)

    def to_dict(self) -> dict:
        validate_event(self)
        d = asdict(self)

        def conv(v: Any) -> Any:
            if isinstance(v, bytes):
                return v.hex()
            if isinstance(v, list):
                return [conv(x) for x in v]
            if isinstance(v, dict):
                return {k: conv(x) for k, x in v.items()}
            return v

        return {k: conv(v) for k, v in d.items()}


# ================================================================ 规格编译 ===

_SCALARS: dict[str, tuple[int, str]] = {
    # type → (位宽, kind)
    **{f"u{b}": (b, "uint") for b in (8, 16, 24, 32, 64)},
    **{f"s{b}": (b, "int") for b in (8, 16, 24, 32, 64)},
    **{f"f{b}": (b, "float") for b in (32, 64)},
}


@dataclass(frozen=True)
class FieldDecl:
    """编译后的字段声明（表达式已 parse 成白名单 AST）。"""

    id: str
    kind: str  # scalar|bytes|str|struct|value（scalar 的具体数值 kind 看 base）
    base: str = ""
    width_bits: int = 0
    endian: str | None = None       # None = 继承上下文
    size: ast.expr | None = None    # bytes/str 字节数（表达式）
    size_eos: bool = False
    terminator: int | None = None
    repeat: str | None = None       # expr|eos|until
    repeat_expr: ast.expr | None = None
    until: ast.expr | None = None
    switch_on: str | None = None
    cases: Mapping[int, str] = dc_field(default_factory=dict)  # int → 类型名
    default_case: str | None = None
    if_cond: ast.expr | None = None
    valid_eq: ast.expr | None = None
    contents: bytes | None = None
    crc: tuple[Mapping, str] | None = None  # (算法参数, over)：参数化校验（CRC/校验和）
    process: str | None = None              # 具名变换钩子（代码住受信侧，规格只带名字）
    enum_map: Mapping[int, str] = dc_field(default_factory=dict)
    encoding: str = "ascii"
    display: str | None = None  # 呈现提示：hex(缺省)|dec|bcd
    scale: float | None = None  # 显示值 = value * scale
    unit: str | None = None
    value_expr: ast.expr | None = None
    doc: str = ""


@dataclass(frozen=True)
class CompiledType:
    name: str
    seq: tuple[FieldDecl, ...]


@dataclass(frozen=True)
class CompiledSpec:
    root: CompiledType
    types: Mapping[str, CompiledType]
    endian: str = "be"  # 顶层缺省字节序：仪器/网络序


def _compile_expr(src: Any, known: set[str], where: str) -> ast.expr:
    """表达式 → 白名单 AST（编译期拒绝一切越界的求值能力）。"""
    try:
        tree = ast.parse(str(src), mode="eval")
    except SyntaxError as e:
        raise FieldSpecError(f"{where}: 表达式语法错误 {src!r}（{e}）") from e

    def check(n: ast.expr) -> None:
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return
        if isinstance(n, ast.Name):
            if n.id not in known and n.id not in ("root", "parent"):
                raise FieldSpecError(
                    f"{where}: 表达式 {src!r} 引用了未知名字 {n.id!r}；"
                    f"可用: {sorted(known)}（或 root./parent. 跨层引用）")
            return
        if isinstance(n, ast.Attribute):
            base = n
            while isinstance(base, ast.Attribute):
                base = base.value
            if not (isinstance(base, ast.Name) and base.id in ("root", "parent")):
                raise FieldSpecError(f"{where}: 属性引用仅支持 root.xxx / parent.xxx")
            return
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult,
                ast.Div, ast.FloorDiv, ast.Mod, ast.LShift, ast.RShift, ast.BitAnd,
                ast.BitOr, ast.BitXor)):
            check(n.left)
            return check(n.right)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd, ast.Invert)):
            return check(n.operand)
        if isinstance(n, ast.Compare) and all(isinstance(o, (ast.Eq, ast.NotEq, ast.Lt,
                ast.LtE, ast.Gt, ast.GtE)) for o in n.ops):
            check(n.left)
            return all(check(c) for c in n.comparators)
        if isinstance(n, ast.BoolOp):
            return all(check(v) for v in n.values)
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "len" and len(n.args) == 1 and not n.keywords):
            return check(n.args[0])
        raise FieldSpecError(f"{where}: 表达式 {src!r} 含不允许的语法 {type(n).__name__}")

    check(tree.body)
    return tree.body


def _key_int(k: Any, where: str) -> int:
    """enum/cases 的整数键：int(k, 0)；前导零等非法字面量编译期拒绝。"""
    try:
        return int(k, 0) if isinstance(k, str) else int(k)
    except (TypeError, ValueError) as e:
        raise FieldSpecError(
            f"{where}: 整数键 {k!r} 无法解析（int(k,0)，禁前导零）") from e


def _contents_bytes(v: Any, where: str) -> bytes:
    if isinstance(v, str):
        try:
            return bytes.fromhex(v.replace(" ", ""))
        except ValueError as e:
            raise FieldSpecError(f"{where}: contents 十六进制串非法（{e}）") from e
    if isinstance(v, (list, tuple)) and all(isinstance(x, int) for x in v):
        return bytes(v)
    raise FieldSpecError(f"{where}: contents 需要十六进制串或字节数组")


# ------------------------------------------------------------ 校验算法目录 ---
# CRC 是参数化算法族（Rocksoft™ 模型）：width/poly/init/refin/refout/xorout
# 全是数据——预设覆盖常用模型，非标模型直接给参数，不需要携带代码。

_CRC_PRESETS: dict[str, dict] = {
    "sum8": {"width": 8, "simple": "sum8"},
    "xor8": {"width": 8, "simple": "xor8"},
    "crc8": {"width": 8, "poly": 0x07, "init": 0x00, "refin": False,
             "refout": False, "xorout": 0x00},
    "crc16_ccitt_false": {"width": 16, "poly": 0x1021, "init": 0xFFFF,
                          "refin": False, "refout": False, "xorout": 0x0000},
    "crc16_xmodem": {"width": 16, "poly": 0x1021, "init": 0x0000,
                     "refin": False, "refout": False, "xorout": 0x0000},
    "crc16_modbus": {"width": 16, "poly": 0x8005, "init": 0xFFFF,
                     "refin": True, "refout": True, "xorout": 0x0000},
    "crc32": {"width": 32, "poly": 0x04C11DB7, "init": 0xFFFFFFFF,
              "refin": True, "refout": True, "xorout": 0xFFFFFFFF},
}

_CRC_PARAM_KEYS = ("width", "poly", "init", "refin", "refout", "xorout", "simple")


def _crc_compute(data: bytes, p: Mapping) -> int:
    if "named" in p:  # 具名校验函数（代码住受信侧，规格只带名字）
        return _CHECK_FNS[p["named"]](data)
    if p.get("simple"):
        if p["simple"] == "sum8":
            return sum(data) & 0xFF
        v = 0
        for b in data:
            v ^= b
        return v & 0xFF
    width, mask = p["width"], (1 << p["width"]) - 1
    top = 1 << (p["width"] - 1)
    crc = p["init"] & mask
    for byte in data:
        if p["refin"]:
            byte = int(f"{byte:08b}"[::-1], 2)
        crc ^= byte << (p["width"] - 8)
        for _ in range(8):
            crc = ((crc << 1) ^ p["poly"]) & mask if crc & top else (crc << 1) & mask
    if p["refout"]:
        crc = int(f"{crc:0{width}b}"[::-1], 2)
    return crc ^ p["xorout"]


def _compile_crc(v: Any, width_bits: int, where: str) -> tuple[Mapping, str] | None:
    """crc 声明 → (参数, over)。over v1 仅 "prefix"：本层起点到本字段之前。"""
    if v is None:
        return None
    if not isinstance(v, Mapping):
        raise FieldSpecError(f"{where}: crc 需为 dict")
    if width_bits == 0 or width_bits % 8:
        raise FieldSpecError(f"{where}: crc 仅支持整字节标量字段")
    over = v.get("over", "prefix")
    if over != "prefix":
        raise FieldSpecError(f'{where}: crc.over v1 仅支持 "prefix"')
    algo = v.get("algo")
    if "fn" in v:  # 具名校验钩子：预设/参数覆盖不了的算法，代码在受信侧注册
        name = str(v["fn"])
        if name not in _CHECK_FNS:
            raise FieldSpecError(
                f"{where}: 未注册的校验函数 {name!r}；可用: {sorted(_CHECK_FNS)}"
                f"（register_check_fn 注册——代码住受信侧，规格只带名字）")
        if width_bits == 0 or width_bits % 8:
            raise FieldSpecError(f"{where}: crc 仅支持整字节标量字段")
        return {"named": name}, over
    if isinstance(algo, str) and algo in _CRC_PRESETS:
        params = dict(_CRC_PRESETS[algo])
    else:
        src = algo if isinstance(algo, Mapping) else v
        if not isinstance(src, Mapping) or ("width" not in src and "simple" not in src):
            raise FieldSpecError(
                f"{where}: 未知 crc 算法 {algo!r}；可用预设: {sorted(_CRC_PRESETS)}，"
                f"或内联参数 {list(_CRC_PARAM_KEYS)}")
        params = {"poly": 0, "init": 0, "refin": False, "refout": False,
                  "xorout": 0, "simple": None}
        params.update({k: src[k] for k in _CRC_PARAM_KEYS if k in src})
        if "width" not in params:
            raise FieldSpecError(f"{where}: crc 需要指定 width（或使用预设算法）")
    if params.get("width") != width_bits:
        raise FieldSpecError(
            f"{where}: crc 算法宽度 {params.get('width')} 与字段位宽 {width_bits} 不符")
    return params, over


# ------------------------------------------------------------ 具名钩子注册表 ---
# 真正写不成表达式的变换（私有扰码/解密/自定义打包）——代码注册在受信侧
# （协议包内、随代码版本走），规格只引用名字：永不下发/内联可执行代码。

_FIELD_FNS: dict[str, Callable[[bytes], bytes]] = {}


def register_field_fn(name: str, fn: Callable[[bytes], bytes]) -> None:
    """注册具名字节变换（process 钩子）；重名覆盖并告警。"""
    if name in _FIELD_FNS:
        logging.getLogger(__name__).warning("field_fn 覆盖注册: %s", name)
    _FIELD_FNS[name] = fn


_CHECK_FNS: dict[str, Callable[[bytes], int]] = {}


def register_check_fn(name: str, fn: Callable[[bytes], int]) -> None:
    """注册具名校验函数（crc.fn 钩子）：签名 (bytes) -> int，
    返回值须与字段位宽一致；代码住受信侧（协议包内、随版本走），重名覆盖并告警。"""
    if name in _CHECK_FNS:
        logging.getLogger(__name__).warning("check_fn 覆盖注册: %s", name)
    _CHECK_FNS[name] = fn


def _compile_process(v: Any, where: str) -> str | None:
    if v is None:
        return None
    name = str(v)
    if name not in _FIELD_FNS:
        raise FieldSpecError(
            f"{where}: 未注册的 process 钩子 {name!r}；可用: {sorted(_FIELD_FNS)}"
            f"（register_field_fn 注册——代码住受信侧，规格只带名字）")
    return name


def _compile_field(d: Mapping, known: set[str], where: str) -> FieldDecl:
    if not isinstance(d, Mapping) or "id" not in d:
        raise FieldSpecError(f"{where}: 字段须为含 id 的 dict: {d!r}")
    fid = str(d["id"])
    where = f"{where}.{fid}"

    if "value" in d:  # 计算字段（原语 5；不消耗字节、不参与重复）
        return FieldDecl(id=fid, kind="value",
                         value_expr=_compile_expr(d["value"], known, where),
                         doc=d.get("doc", ""))

    # 重复 / 条件（原语 2；各字段类别通用）。until 条件引用字段自身 → 先注册自名。
    repeat = d.get("repeat")
    if repeat is not None and repeat not in ("expr", "eos", "until"):
        raise FieldSpecError(f"{where}: repeat 仅支持 expr/eos/until")
    repeat_expr = _compile_expr(d["repeat_expr"], known, where) if "repeat_expr" in d else None
    until = _compile_expr(d["until"], known | {fid}, where) if "until" in d else None
    if_cond = _compile_expr(d["if"], known, where) if "if" in d else None
    if repeat == "expr" and repeat_expr is None:
        raise FieldSpecError(f"{where}: repeat=expr 需要 repeat_expr")

    ftype = d.get("type")
    decl_endian = d.get("endian")
    if decl_endian is not None and decl_endian not in ("be", "le"):
        raise FieldSpecError(f"{where}: endian 仅支持 be/le")
    if (d.get("crc") is not None or d.get("process") is not None) and (
            ftype not in _SCALARS and ftype not in ("bytes", "str")):
        raise FieldSpecError(f"{where}: crc/process 仅支持整字节标量或 bytes/str 字段")

    def _valid() -> ast.expr | None:
        v = d.get("valid")
        if v is None:
            return None
        if not isinstance(v, Mapping) or "eq" not in v:
            raise FieldSpecError(f"{where}: valid 需为 {{eq: 表达式}}")
        return _compile_expr(v["eq"], known | {fid}, where)

    def _hints() -> tuple[str | None, float | None, str | None]:
        """呈现提示（原语⑥）：display/scale/unit——人怎么看声明在规格里。"""
        display = d.get("display")
        if display is not None and display not in ("hex", "dec", "bcd"):
            raise FieldSpecError(f"{where}: display 仅支持 hex/dec/bcd")
        scale = d.get("scale")
        if scale is not None:
            scale = float(scale)
            if not scale > 0:
                raise FieldSpecError(f"{where}: scale 必须为正数")
        return display, scale, (str(d["unit"]) if "unit" in d else None)

    if "switch_on" in d:  # 类别分发（原语 4）
        if ftype is not None:
            raise FieldSpecError(f"{where}: switch 字段不应再声明 type")
        disc = str(d["switch_on"])
        if disc not in known:
            raise FieldSpecError(f"{where}: switch_on {disc!r} 不是已声明的字段")
        raw_cases = d.get("cases")
        if not isinstance(raw_cases, Mapping) or not raw_cases:
            raise FieldSpecError(f"{where}: switch 字段需要非空 cases")
        cases: dict[int, str] = {}
        default_case: str | None = None
        for k, tname in raw_cases.items():
            if k == "*":
                default_case = str(tname)
            else:
                cases[_key_int(k, where)] = str(tname)
        return FieldDecl(id=fid, kind="struct", switch_on=disc, cases=cases,
                         default_case=default_case, repeat=repeat,
                         repeat_expr=repeat_expr, until=until, if_cond=if_cond,
                         doc=d.get("doc", ""))

    if ftype in ("bytes", "str"):
        contents = _contents_bytes(d["contents"], where) if "contents" in d else None
        if d.get("crc") is not None:
            raise FieldSpecError(f"{where}: crc 仅支持整字节标量字段（bytes/str 用 process 变换）")
        if "valid" in d:
            raise FieldSpecError(f"{where}: bytes/str 的内容断言用 contents（valid.eq 仅标量）")
        has_size = "size" in d or d.get("size_eos") or "terminator" in d or contents is not None
        if not has_size:
            raise FieldSpecError(f"{where}: bytes/str 需要 size / size_eos / terminator / contents 之一")
        if "size" in d:
            size: ast.expr | None = _compile_expr(d["size"], known, where)
        elif contents is not None:  # contents 隐含定长
            size = ast.parse(str(len(contents)), mode="eval").body
        else:
            size = None
        term = None
        if "terminator" in d:
            term = (int(d["terminator"], 0) if isinstance(d["terminator"], str)
                    else int(d["terminator"]))
            if not 0 <= term <= 255:
                raise FieldSpecError(f"{where}: terminator 需在 0..255，得到 {term}")
        return FieldDecl(
            id=fid, kind=str(ftype), base=str(ftype), size=size,
            size_eos=bool(d.get("size_eos")),
            terminator=term,
            contents=contents,
            process=_compile_process(d.get("process"), where),
            encoding=str(d.get("encoding", "ascii")),
            repeat=repeat, repeat_expr=repeat_expr, until=until, if_cond=if_cond,
            doc=d.get("doc", ""),
        )

    if ftype in _SCALARS:
        width, kind = _SCALARS[ftype]
        if d.get("process") is not None:
            raise FieldSpecError(f"{where}: process 仅支持 bytes/str 字段")
        display, scale, unit = _hints()
        enum_map: dict[int, str] = {}
        if "enum" in d:
            for k, label in d["enum"].items():
                enum_map[_key_int(k, where)] = str(label)
        return FieldDecl(
            id=fid, kind=kind, base=str(ftype), width_bits=width,
            endian=decl_endian, enum_map=enum_map, valid_eq=_valid(),
            crc=_compile_crc(d.get("crc"), width, where),
            display=display, scale=scale, unit=unit,
            repeat=repeat, repeat_expr=repeat_expr, until=until, if_cond=if_cond,
            doc=d.get("doc", ""),
        )

    if isinstance(ftype, str) and ftype.startswith("b") and ftype[1:].isdigit():
        display, scale, unit = _hints()
        return FieldDecl(id=fid, kind="uint", base=ftype,
                         width_bits=int(ftype[1:]), valid_eq=_valid(),
                         display=display, scale=scale, unit=unit,
                         repeat=repeat, repeat_expr=repeat_expr, until=until,
                         if_cond=if_cond, doc=d.get("doc", ""))

    if isinstance(ftype, str):  # 自定义类型名（types 表）；名字存在性由 compile_spec 终检
        return FieldDecl(id=fid, kind="struct", base=ftype,
                         repeat=repeat, repeat_expr=repeat_expr, until=until,
                         if_cond=if_cond, doc=d.get("doc", ""))

    raise FieldSpecError(f"{where}: 缺少/非法 type {ftype!r}（标量 {sorted(_SCALARS)}、"
                         f"bytes/str、b1..b64 位域、switch_on、value、或 types 表类型名）")


def _compile_seq(seq: Any, known_outer: set[str], where: str) -> tuple[CompiledType, set[str]]:
    if not isinstance(seq, list) or not seq:
        raise FieldSpecError(f"{where}: seq 需要非空字段数组")
    known = set(known_outer)
    seen: set[str] = set()
    decls: list[FieldDecl] = []
    for i, d in enumerate(seq):
        if isinstance(d, Mapping) and "id" in d and str(d["id"]) in seen:
            raise FieldSpecError(f"{where}[{i}]: 字段 id 重复: {d['id']!r}")
        decl = _compile_field(d, known, f"{where}[{i}]")
        decls.append(decl)
        seen.add(str(d["id"]))
        known.add(str(d["id"]))
    return CompiledType(name=where, seq=tuple(decls)), known


def compile_spec(spec: Mapping) -> CompiledSpec:
    """dict 规格 → 编译产物（所有表达式/类型引用在此 fail fast）。"""
    if not isinstance(spec, Mapping):
        raise FieldSpecError(f"规格须为 dict/Mapping，得到 {type(spec).__name__}")
    if "seq" not in spec:
        raise FieldSpecError("规格缺少顶层 seq")
    endian = spec.get("endian", "be")
    if endian not in ("be", "le"):
        raise FieldSpecError(f"endian 仅支持 be/le，得到 {endian!r}")

    types_raw = spec.get("types", {})
    if not isinstance(types_raw, Mapping):
        raise FieldSpecError("types 需为 类型名 → 规格的 dict")
    root_ids = {str(f["id"]) for f in spec["seq"]
                if isinstance(f, Mapping) and "id" in f}

    root, _ = _compile_seq(spec["seq"], set(), "root")
    types: dict[str, CompiledType] = {}
    for tname, tspec in types_raw.items():
        if tname in _SCALARS or tname in ("bytes", "str"):
            raise FieldSpecError(f"类型名 {tname!r} 与内建类型冲突")
        if not isinstance(tspec, Mapping) or "seq" not in tspec:
            raise FieldSpecError(f"类型 {tname} 需为含 seq 的 dict")
        types[str(tname)], _ = _compile_seq(tspec["seq"], root_ids, f"type:{tname}")

    # 类型引用环检测（环会让解析器无限递归）——含自环与间接环
    deps: dict[str, set[str]] = {}
    for tname, ctype in types.items():
        refs = set()
        for d in ctype.seq:
            if d.kind == "struct":
                if d.base:
                    refs.add(d.base)
                refs.update(d.cases.values())
                if d.default_case:
                    refs.add(d.default_case)
        deps[tname] = {r for r in refs if r in types}

    color: dict[str, int] = dict.fromkeys(types, 0)

    def _visit(t: str, path: list[str]) -> None:
        color[t] = 1
        for r in sorted(deps[t]):
            if color[r] == 1:
                raise FieldSpecError(f"types 循环引用: {' → '.join([*path, t, r])}")
            if color[r] == 0:
                _visit(r, [*path, t])
        color[t] = 2

    for t in types:
        if color[t] == 0:
            _visit(t, [])

    # 类型引用终检（root 与全部 types 的具名引用都必须落在 types 表内）
    for ctype in (root, *types.values()):
        for d in ctype.seq:
            if d.kind != "struct":
                continue
            refs = list(d.cases.values()) + ([d.default_case] if d.default_case else [])
            if d.switch_on is None and d.base:
                refs.append(d.base)
            for t in refs:
                if t not in types:
                    raise FieldSpecError(f"{ctype.name}.{d.id}: 未知类型 {t!r}"
                                         f"（须定义在 types 表）")
    return CompiledSpec(root=root, types=types, endian=endian)


# ================================================================ 表达式求值 ===

class _Truncated(Exception):
    """读越界（内部哨兵 → 转为字段错误）。"""


class _ExprFail(Exception):
    """运行期表达式求值失败（未知名字/除零等 → 转为字段错误）。"""


def _resolve(name: str, scopes: list[dict]) -> Any:
    for s in reversed(scopes):  # 内层优先
        if name in s:
            return s[name]
    raise _ExprFail(f"未知名字 {name!r}")


def _eval(n: ast.expr, scopes: list[dict]) -> Any:
    if isinstance(n, ast.Constant):
        return n.value
    if isinstance(n, ast.Name):
        if n.id == "root":
            return scopes[0]
        if n.id == "parent":
            return scopes[-2] if len(scopes) > 1 else scopes[-1]
        return _resolve(n.id, scopes)
    if isinstance(n, ast.Attribute):
        base = _eval(n.value, scopes)
        if not isinstance(base, dict):
            raise _ExprFail(f"对非结构值取属性 .{n.attr}")
        if n.attr not in base:
            raise _ExprFail(f"跨层引用 {n.attr!r} 不存在")
        return base[n.attr]
    if isinstance(n, ast.BinOp):
        a, b = _eval(n.left, scopes), _eval(n.right, scopes)
        op = type(n.op)
        try:
            if op is ast.Add:
                return a + b
            if op is ast.Sub:
                return a - b
            if op is ast.Mult:
                return a * b
            if op is ast.Div:
                return a / b
            if op is ast.FloorDiv:
                return a // b
            if op is ast.Mod:
                return a % b
            if op in (ast.LShift, ast.RShift):
                if not (isinstance(b, int) and not isinstance(b, bool)
                        and 0 <= b <= 4096):  # 巨位移会造出巨整数（内存/呈现爆炸）
                    raise _ExprFail("移位量需为 0..4096 的整数")
                return (a << b) if op is ast.LShift else (a >> b)
            if op is ast.BitAnd:
                return a & b
            if op is ast.BitOr:
                return a | b
            if op is ast.BitXor:
                return a ^ b
        except _ExprFail:
            raise
        except (TypeError, ValueError, OverflowError, ZeroDivisionError) as e:
            raise _ExprFail(str(e)) from e
    if isinstance(n, ast.UnaryOp):
        v = _eval(n.operand, scopes)
        if isinstance(n.op, ast.USub):
            return -v
        if isinstance(n.op, ast.UAdd):
            return +v
        return ~v
    if isinstance(n, ast.Compare):
        a = _eval(n.left, scopes)
        for o, c in zip(n.ops, n.comparators):
            b = _eval(c, scopes)
            t = type(o)
            try:
                if t is ast.Eq:
                    ok = a == b
                elif t is ast.NotEq:
                    ok = a != b
                elif t is ast.Lt:
                    ok = a < b
                elif t is ast.LtE:
                    ok = a <= b
                elif t is ast.Gt:
                    ok = a > b
                else:
                    ok = a >= b
            except TypeError as e:
                raise _ExprFail(f"比较失败: {e}") from e
            if not ok:
                return False
            a = b
        return True
    if isinstance(n, ast.BoolOp):
        is_and = isinstance(n.op, ast.And)
        last = _eval(n.values[0], scopes)
        for v in n.values[1:]:
            if is_and and not last:
                return last
            if not is_and and last:
                return last
            last = _eval(v, scopes)
        return last
    if isinstance(n, ast.Call):  # 编译期已限定 len(x)
        v = _eval(n.args[0], scopes)
        try:
            return len(v)
        except TypeError as e:
            raise _ExprFail("len() 需要 bytes/str/数组") from e
    raise _ExprFail(f"不支持的表达式节点 {type(n).__name__}")


# ================================================================ 解析器 ===

class _Cursor:
    """载荷位游标：位域共享位游标，字节读取对齐。"""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0  # bit

    def remaining_bits(self) -> int:
        return len(self.data) * 8 - self.pos

    def align(self) -> None:
        self.pos = (self.pos + 7) & ~7

    def read_bits(self, n: int) -> int:
        if n > self.remaining_bits():
            raise _Truncated()
        v = 0
        for _ in range(n):
            byte = self.data[self.pos >> 3]
            v = (v << 1) | ((byte >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return v

    def read_bytes(self, n: int) -> bytes:
        self.align()
        if n * 8 > self.remaining_bits():
            raise _Truncated()
        out = self.data[self.pos // 8: self.pos // 8 + n]
        self.pos += n * 8
        return out


def _to_int(v: Any) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise _ExprFail(f"需要整数，得到 {v!r}")
    return v


class _Parser:
    def __init__(self, spec: CompiledSpec, payload: bytes):
        self.spec = spec
        self.cur = _Cursor(payload)
        self._starts = [0]  # 各层结构载荷起点（字节）——crc over=prefix 的覆盖范围

    def parse_root(self) -> list[FieldView]:
        views, _ = self._seq(self.spec.root, [{}])
        return views

    # ------------------------------------------------------------- 序列 ---

    def _seq(self, ctype: CompiledType, scopes: list[dict]) -> tuple[list[FieldView], bool]:
        views: list[FieldView] = []
        scope = scopes[-1]
        for decl in ctype.seq:
            if decl.if_cond is not None:
                try:
                    if not _eval(decl.if_cond, scopes):
                        continue
                except _ExprFail as e:
                    views.append(FieldView(id=decl.id, offset_bits=self.cur.pos,
                                           width_bits=0, kind=decl.kind,
                                           errors=["expr:" + str(e)]))
                    return views, True
            try:
                view = self._field(decl, scopes)
            except _Truncated:
                views.append(FieldView(id=decl.id, offset_bits=self.cur.pos,
                                       width_bits=0, kind=decl.kind,
                                       errors=["truncated"]))
                return views, True
            views.append(view)
            if "truncated" in view.errors or "incomplete" in view.errors:
                return views, True
            # 具值字段绑定进作用域供后续表达式引用；struct 不绑定（其字段只在子作用域）
            if decl.kind != "struct":
                scope[decl.id] = view.value
        return views, False

    # ------------------------------------------------------------- 字段分派 ---

    def _field(self, decl: FieldDecl, scopes: list[dict]) -> FieldView:
        if decl.kind == "value":
            pos = self.cur.pos
            try:
                v = _eval(decl.value_expr, scopes)
            except _ExprFail as e:
                return FieldView(id=decl.id, offset_bits=pos, width_bits=0,
                                 kind="value", errors=["expr:" + str(e)])
            return FieldView(id=decl.id, offset_bits=pos, width_bits=0,
                             kind=("float" if isinstance(v, float) else "uint"), value=v)
        if decl.repeat:
            return self._repeat(decl, scopes)
        if decl.kind == "struct":
            return self._struct(decl, scopes)
        if decl.kind in ("bytes", "str"):
            return self._blob(decl, scopes)
        return self._scalar(decl, scopes)

    # ------------------------------------------------------------- 标量 ---

    def _scalar(self, decl: FieldDecl, scopes: list[dict]) -> FieldView:
        crc_data = None
        if decl.crc is not None:  # 覆盖范围 = 本层起点到本字段之前（读取前取快照）
            self.cur.align()
            crc_data = self.cur.data[self._starts[-1]: self.cur.pos // 8]
        if decl.base.startswith("b"):
            pos = self.cur.pos
            raw = self.cur.read_bits(decl.width_bits)
        else:
            self.cur.align()
            pos = self.cur.pos
            raw_bytes = self.cur.read_bytes(decl.width_bits // 8)
            endian = decl.endian or self.spec.endian
            order = ">" if endian == "be" else "<"
            if decl.kind == "float":
                raw = _struct.unpack(f"{order}{'f' if decl.width_bits == 32 else 'd'}",
                                     raw_bytes)[0]
            elif decl.width_bits == 24:
                raw = int.from_bytes(raw_bytes,
                                     "big" if endian == "be" else "little",
                                     signed=(decl.kind == "int"))
            else:
                fmt = {8: "B", 16: "H", 32: "I", 64: "Q"}[decl.width_bits]
                raw = _struct.unpack(f"{order}{fmt}", raw_bytes)[0]
                if decl.kind == "int":
                    raw = raw - (1 << decl.width_bits) \
                        if raw >= (1 << (decl.width_bits - 1)) else raw
        view = FieldView(id=decl.id, offset_bits=pos, width_bits=decl.width_bits,
                         kind=decl.kind, value=raw,
                         enum_label=decl.enum_map.get(raw) if decl.kind == "uint" else None,
                         display=decl.display, scale=decl.scale, unit=decl.unit)
        if decl.valid_eq is not None:
            scopes[-1].setdefault(decl.id, raw)
            try:
                if raw != _eval(decl.valid_eq, scopes):  # eq = 字段值 == 表达式值
                    view.errors.append("valid")
            except _ExprFail as e:
                view.errors.append("expr:" + str(e))
        if decl.crc is not None:
            if _crc_compute(crc_data, decl.crc[0]) != raw:
                view.errors.append("crc")
        return view

    # ------------------------------------------------------------- 载荷块 ---

    def _blob(self, decl: FieldDecl, scopes: list[dict]) -> FieldView:
        self.cur.align()
        pos = self.cur.pos
        if decl.size is not None:
            try:
                n = _to_int(_eval(decl.size, scopes))
            except _ExprFail as e:
                return FieldView(id=decl.id, offset_bits=pos, width_bits=0,
                                 kind=decl.kind, errors=["expr:" + str(e)])
            if n < 0:
                return FieldView(id=decl.id, offset_bits=pos, width_bits=0,
                                 kind=decl.kind, errors=["expr:size < 0"])
            raw = self.cur.read_bytes(n)
        elif decl.size_eos:
            raw = self.cur.read_bytes(self.cur.remaining_bits() // 8)
        else:  # terminator（不含哨兵本身）
            start = self.cur.pos // 8
            idx = self.cur.data.find(bytes([decl.terminator]), start)
            if idx < 0:
                return FieldView(id=decl.id, offset_bits=pos, width_bits=0,
                                 kind=decl.kind, errors=["truncated"])
            raw = self.cur.data[start:idx]
            self.cur.pos = (idx + 1) * 8
        if decl.process is not None:  # 具名变换：线格式 → 逻辑格式
            raw = _FIELD_FNS[decl.process](raw)
        view = FieldView(id=decl.id, offset_bits=pos,
                         width_bits=self.cur.pos - pos, kind=decl.kind,
                         value=raw.decode(decl.encoding, "replace")
                         if decl.kind == "str" else raw)
        if decl.contents is not None and raw != decl.contents:
            view.errors.append("valid")
        return view

    # ------------------------------------------------------------- 结构/分发 ---

    def _struct(self, decl: FieldDecl, scopes: list[dict]) -> FieldView:
        self.cur.align()
        pos = self.cur.pos
        if decl.switch_on is not None:
            try:
                disc = _to_int(_resolve(decl.switch_on, scopes))
            except _ExprFail as e:
                return FieldView(id=decl.id, offset_bits=pos, width_bits=0,
                                 kind="struct", errors=["expr:" + str(e)])
            tname = decl.cases.get(disc, decl.default_case)
            if tname is None:
                return FieldView(id=decl.id, offset_bits=pos, width_bits=0,
                                 kind="struct", errors=["no-case"])
        else:
            tname = decl.base
        ctype = self.spec.types.get(tname)
        if ctype is None:
            return FieldView(id=decl.id, offset_bits=pos, width_bits=0, kind="struct",
                             errors=[f"expr:未知类型 {tname!r}"])
        child_scope: dict = {}
        self._starts.append(self.cur.pos // 8)
        try:
            views, truncated = self._seq(ctype, scopes + [child_scope])
        finally:
            self._starts.pop()
        view = FieldView(id=decl.id, offset_bits=pos,
                         width_bits=self.cur.pos - pos, kind="struct", children=views)
        if truncated:
            view.errors.append("incomplete")
        return view

    # ------------------------------------------------------------- 重复 ---

    def _repeat(self, decl: FieldDecl, scopes: list[dict]) -> FieldView:
        pos = self.cur.pos
        children: list[FieldView] = []

        def one() -> tuple[FieldView, int]:
            before = self.cur.pos
            if decl.kind == "struct":
                v = self._struct(decl, scopes)
            elif decl.kind in ("bytes", "str"):
                v = self._blob(decl, scopes)
            else:
                v = self._scalar(decl, scopes)
            return v, self.cur.pos - before

        def broken(v: FieldView) -> bool:
            return "truncated" in v.errors or "incomplete" in v.errors

        def finish() -> FieldView:
            view = FieldView(id=decl.id, offset_bits=pos,
                             width_bits=self.cur.pos - pos, kind=decl.kind,
                             children=children)
            if any(broken(c) or "no-progress" in c.errors for c in children):
                view.errors.append("incomplete")
            return view

        if decl.repeat == "expr":
            try:
                n = _to_int(_eval(decl.repeat_expr, scopes))
            except _ExprFail as e:
                return FieldView(id=decl.id, offset_bits=pos, width_bits=0,
                                 kind=decl.kind, errors=["expr:" + str(e)])
            if n < 0:
                return FieldView(id=decl.id, offset_bits=pos, width_bits=0,
                                 kind=decl.kind, errors=["expr:repeat < 0"])
            for _ in range(n):
                try:
                    v, adv = one()
                except _Truncated:
                    children.append(FieldView(id=decl.id, offset_bits=self.cur.pos,
                                              width_bits=0, kind=decl.kind,
                                              errors=["truncated"]))
                    break
                if adv == 0 and not broken(v):  # 零推进防死循环（no-case/纯 value 等）
                    v.errors.append("no-progress")
                    children.append(v)
                    break
                children.append(v)
                if broken(v):
                    break
        elif decl.repeat == "eos":
            min_bits = decl.width_bits if decl.kind in ("uint", "int", "float") else 8
            while self.cur.remaining_bits() >= min_bits:
                try:
                    v, adv = one()
                except _Truncated:
                    children.append(FieldView(id=decl.id, offset_bits=self.cur.pos,
                                              width_bits=0, kind=decl.kind,
                                              errors=["truncated"]))
                    break
                children.append(v)
                if broken(v):
                    break
                if adv == 0:  # 零推进防死循环
                    v.errors.append("no-progress")
                    break
        else:  # until（含满足条件的末项）
            while True:
                try:
                    v, adv = one()
                except _Truncated:
                    children.append(FieldView(id=decl.id, offset_bits=self.cur.pos,
                                              width_bits=0, kind=decl.kind,
                                              errors=["truncated"]))
                    break
                children.append(v)
                scopes[-1][decl.id] = v.value
                if broken(v):
                    break
                try:
                    if _eval(decl.until, scopes):
                        break
                except _ExprFail as e:
                    v.errors.append("expr:" + str(e))
                    break
                if adv == 0:  # 条件已为假还要再来一轮 → 零推进即死循环
                    v.errors.append("no-progress")
                    break

        return finish()


def parse_payload(spec: Mapping | CompiledSpec, payload: bytes) -> list[FieldView]:
    """同一套算法：规格（dict 或编译产物）× 载荷 → 字段树。"""
    compiled = spec if isinstance(spec, CompiledSpec) else compile_spec(spec)
    return _Parser(compiled, bytes(payload)).parse_root()


# ================================================================ 命名注册表 ===

_FIELDS: dict[str, CompiledSpec] = {}


def register_fields(name: str, spec: Mapping) -> CompiledSpec:
    """注册具名规格（编译期即 fail fast）；重名抛 FieldSpecError。"""
    if name in _FIELDS:
        raise FieldSpecError(f"字段规格重复注册: {name}")
    compiled = compile_spec(spec)
    _FIELDS[name] = compiled
    return compiled


def get_fields(name: str) -> CompiledSpec:
    if name not in _FIELDS:
        raise FieldSpecError(f"未知字段规格 {name!r}；可用: {sorted(_FIELDS)}")
    return _FIELDS[name]


def all_field_specs() -> tuple[str, ...]:
    return tuple(_FIELDS)


# ================================================================ 载荷提取 ===

def _bits_to_bytes(bits: list[int]) -> bytes:
    """位列表 → 字节（首 bit = 首字节 MSB，尾部补零对齐）。"""
    out = bytearray()
    for i, b in enumerate(bits):
        if i % 8 == 0:
            out.append(0)
        out[-1] |= (b & 1) << (7 - (i % 8))
    return bytes(out)


def _uart_payload(ev: DecodedEvent) -> bytes | None:
    if ev.kind != "uart.frame":
        return None
    width_bits = getattr(ev, "data_bits", 8)
    return (ev.value & ((1 << width_bits) - 1)).to_bytes(max(1, (width_bits + 7) // 8), "big")


def _i2c_payload(ev: DecodedEvent) -> bytes | None:
    data = getattr(ev, "data_bytes", None)
    return bytes(data) if data else None


def _spi_payload(ev: DecodedEvent) -> bytes | None:
    words = getattr(ev, "words", None)
    mosi = [w[0] for w in words if w[0] is not None]
    return bytes(mosi) if mosi else None


def _uplink_payload(ev: DecodedEvent) -> bytes | None:
    bits = getattr(ev, "data_bits", None)
    return _bits_to_bytes(bits) if bits else None


def _downlink_payload(ev: DecodedEvent) -> bytes | None:
    bits = getattr(ev, "bits", None)
    return _bits_to_bytes(bits) if bits else None


_PAYLOAD_EXTRACTORS: dict[str, Callable[[DecodedEvent], bytes | None]] = {
    "i2c": _i2c_payload,
    "uart": _uart_payload,
    "spi": _spi_payload,
    "uplink": _uplink_payload,
    "downlink": _downlink_payload,
}


def register_payload_extractor(family: str, fn: Callable[[DecodedEvent], bytes | None]) -> None:
    """注册协议族 → 载荷提取器（覆盖式——预留协议侧五件套迁移），覆盖时告警。"""
    if family in _PAYLOAD_EXTRACTORS:
        logging.getLogger(__name__).warning("payload 提取器覆盖注册: %s", family)
    _PAYLOAD_EXTRACTORS[family] = fn


def resolve_spec(spec: Mapping | CompiledSpec | str) -> tuple[CompiledSpec, str]:
    if isinstance(spec, str):
        if spec.lstrip().startswith("{"):  # MCP 传输层以字符串承载 JSON 的宽容路径
            try:
                return compile_spec(json.loads(spec)), "inline"
            except json.JSONDecodeError as e:
                raise FieldSpecError(f"内联规格 JSON 解析失败: {e}") from e
        return get_fields(spec), spec
    if isinstance(spec, CompiledSpec):
        return spec, "inline"
    return compile_spec(spec), "inline"


def split_one(ev: DecodedEvent, compiled: CompiledSpec, spec_label: str,
               protocol: str) -> FieldSetEvent | None:
    family = protocol or ev.kind.split(".")[0]
    extractor = _PAYLOAD_EXTRACTORS.get(family)
    if extractor is None:
        return None
    payload = extractor(ev)
    if not payload:
        return None
    fields = parse_payload(compiled, payload)
    return FieldSetEvent(
        kind="fields.split", t_start=ev.t_start, t_end=ev.t_end,
        label=" ".join(format_field(f) for f in fields),
        ann_class="data", spec=spec_label, source_kind=ev.kind, fields=fields,
    )


def split_events(events: list[DecodedEvent], spec: Mapping | CompiledSpec | str,
                 protocol: str = "",
                 kinds: list[str] | tuple[str, ...] = ()) -> list[FieldSetEvent]:
    """事件流 → 字段切分事件（便捷入口；图内请用 field_split 节点）。"""
    compiled, label = resolve_spec(spec)
    out = []
    for ev in events:
        if kinds and ev.kind not in kinds:
            continue
        fse = split_one(ev, compiled, label, protocol)
        if fse is not None:
            out.append(fse)
    return out


# ================================================================ 呈现约定 ===

def _bcd_str(v: int) -> str:
    """BCD 版本号：0x0200 → "2.00"，0x0210 → "2.10"（USB bcdUSB 惯例）。"""
    digits = f"{v:X}"
    if len(digits) % 2:
        digits = "0" + digits
    return f"{int(digits[:-2] or '0')}.{digits[-2:]}"


def _int_text(x: int, pad: int = 0) -> str:
    """呈现护栏：巨整数不给 str()/格式化（int_max_str_digits 会炸）。"""
    if x.bit_length() > 512:
        return f"<int {x.bit_length()}bit>"
    return f"0x{x:0{pad}X}" if pad else str(x)


def format_field(f: FieldView) -> str:
    """单行格式化（label/CSV 用）：枚举给名字、按提示出人话、bytes 附可打印 ASCII。"""
    v = f.value
    if f.kind == "struct":
        inner = " ".join(format_field(c) for c in f.children[:4])
        if len(f.children) > 4:
            inner += f" …(+{len(f.children) - 4})"
        text = "{" + inner + "}"
    elif v is None:
        text = "?"
    elif f.enum_label:
        text = f.enum_label
    elif isinstance(v, bool):
        text = str(v)
    elif f.kind == "float":
        text = f"{v * (f.scale or 1):g}"
    elif f.kind == "int":
        x = v * (f.scale or 1)
        text = f"{x:g}" if isinstance(x, float) else _int_text(x)
    elif f.kind == "bytes":
        text = v.hex()
        if v and all(32 <= b < 127 for b in v):
            text += f" '{v.decode('ascii')}'"
    elif f.kind == "str":
        text = v
    elif f.display == "bcd":
        text = _bcd_str(v)
    elif f.display == "dec" or f.scale is not None or f.width_bits == 0:
        x = v * (f.scale or 1)
        text = f"{x:g}" if isinstance(x, float) else _int_text(x)
    else:
        pad = max(1, (f.width_bits + 3) // 4)
        text = _int_text(v * (f.scale or 1), pad) if f.scale else f"0x{v:0{pad}X}"
    if f.unit and not f.enum_label:
        text = f"{text} {f.unit}"
    if f.errors:
        text += "!" + ",".join(f.errors)
    return f"{f.id}={text}"


def format_detail(ev: DecodedEvent) -> str:
    """Markdown 内容列：换行缩进的字段树（给人看）；struct 展开为子行。"""
    lines: list[str] = []

    def walk(fields: list[FieldView], depth: int) -> None:
        pad = "\u00a0\u00a0" * depth
        tick = "└ " if depth else ""
        for f in fields:
            if f.kind == "struct":
                lines.append(f"{pad}{tick}{f.id} ({len(f.children)} 项)")
                walk(f.children, depth + 1)
            else:
                lines.append(f"{pad}{tick}{format_field(f)}")

    walk(list(getattr(ev, "fields", [])), 0)
    return "<br>".join(lines)


_PRES_REGISTERED = False


def register_fields_presentation() -> None:
    """注册 fields 呈现约定。由 decode/__init__ 在协议族呈现**之后**调用——
    CSV 并集列序契约（ADR-013）：既有协议列序不变，新族列追加在尾。"""
    global _PRES_REGISTERED
    if _PRES_REGISTERED:
        return
    register_presentation(Presentation(
        protocol="fields",
        kind_cn={"fields.split": "字段"},
        detail_fn=format_detail,
        event_fields=("spec", "source_kind", "fields"),
        csv_columns=(("fields", format_detail), ("source_kind", lambda ev: ev.source_kind),
                     ("spec", lambda ev: ev.spec)),
        plot_family=False,  # 字段树不往时序图里添 span
        preview_kinds=("fields.split",),
    ))
    _PRES_REGISTERED = True

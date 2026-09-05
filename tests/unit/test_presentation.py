"""呈现注册表测试（ADR-013）：注册/重复注册/fallback/CSV 并集序/预览并集。"""

import pytest

from decodehub.decode import presentation as ps
from decodehub.decode.events import (DecodeReport, DecodedEvent, I2cEvent,
                                     UartEvent)
from decodehub.decode.presentation import (Presentation, all_preview_kinds,
                                           presentation_of,
                                           register_presentation)
from decodehub.render.format import _csv_union_columns, events_markdown, report_csv_rows


@pytest.fixture
def clean_registry():
    """注册表快照恢复：测试注册的协议不泄漏到其他用例。"""
    saved = dict(ps._PRESENTATIONS)
    yield
    ps._PRESENTATIONS.clear()
    ps._PRESENTATIONS.update(saved)


def _ev(kind, label, **kw):
    base = dict(kind=kind, t_start=0.0, t_end=1e-4, label=label)
    base.update(kw)
    return DecodedEvent(**base)


def test_protocol_presentations_registered_on_import():
    """导入 decode 包即完成注册（同 registry 先例）；族前缀匹配。

    注册顺序稳定（CSV 并集列序依赖）：协议族在前，fields（ADR-016）追加在尾。
    """
    # 相对断言（多 agent 并行防语义冲突）：既有协议族列序冻结，fields 恒在尾
    protos = [p.protocol for p in ps.all_presentations()]
    assert protos[:-1] == ["uart", "i2c", "spi", "uplink", "downlink", "i3c"]
    assert protos[-1] == "fields"
    for kind, proto in [("uart.frame", "uart"), ("uart.warn", "uart"),
                        ("i2c.transfer", "i2c"), ("i2c.addr", "i2c"),
                        ("spi.word", "spi"), ("uplink.frame", "uplink"),
                        ("downlink.packet", "downlink"), ("downlink.warn", "downlink"),
                        ("fields.split", "fields")]:
        assert presentation_of(kind).protocol == proto
    assert presentation_of("nosuch.kind") is None


def test_duplicate_registration_raises(clean_registry):
    register_presentation(Presentation(protocol="custom", kind_cn={}))
    with pytest.raises(ValueError, match="重复注册"):
        register_presentation(Presentation(protocol="uart", kind_cn={}))
    with pytest.raises(ValueError, match="重复注册"):
        register_presentation(Presentation(protocol="custom", kind_cn={}))


def test_markdown_registered_and_fallback():
    """注册 kind → 中文名 + 内容列；未注册 kind → 原文 + label（现状 fallback）。"""
    evs = [
        UartEvent(kind="uart.frame", t_start=0.0, t_end=1e-4, label="8N1", value=0x41),
        UartEvent(kind="uart.made-up", t_start=1e-4, t_end=2e-4, label="未登记 kind"),
        DecodedEvent(kind="mystery.thing", t_start=2e-4, t_end=3e-4, label="未知协议事件"),
    ]
    table = events_markdown(evs)
    assert "| 1 | 0 ns | 100 µs | UART | 8N1 'A' | OK |" in table
    assert "| 2 | 100 µs | 100 µs | uart.made-up | 未登记 kind | OK |" in table  # 族注册但 kind 未登记 → 原文
    assert "| 3 | 200 µs | 100 µs | mystery.thing | 未知协议事件 | OK |" in table  # 完全未注册 → 原文 + label


def test_csv_header_union_matches_current_order():
    """并集列序 = 注册序先到先得：既有列序不变，下行新列与 fields 列（ADR-016）追加在尾。"""
    cols = _csv_union_columns()
    assert cols[:12] == ["value_or_address", "read", "data_bytes", "acks",
                         "mosi", "miso", "word_bits", "pream_ok", "confidence",
                         "fc_hz", "slot", "frame"]
    assert cols[-3:] == ["fields", "source_kind", "spec"]  # fields 三列恒在尾
    header = report_csv_rows(DecodeReport(protocol="x", params={}, events=[]))
    assert header.splitlines()[0].startswith(
        "idx,t_start,t_end,duration_s,kind,label,ann_class,errors,"
        "value_or_address,read,data_bytes,acks,mosi,miso,word_bits,"
        "pream_ok,confidence,fc_hz,slot,frame,")
    assert header.splitlines()[0].endswith(",fields,source_kind,spec,schema_version")


def test_csv_rows_fill_only_own_protocol_columns():
    rep = DecodeReport(protocol="mix", params={}, events=[
        UartEvent(kind="uart.frame", t_start=0.0, t_end=1e-4, label="F", value=0x41),
        I2cEvent(kind="i2c.transfer", t_start=1e-4, t_end=2e-4, label="W",
                 address=0x51, read=False, data_bytes=[0x12, 0x34], acks=[True, False]),
    ])
    rows = report_csv_rows(rep).strip().splitlines()
    assert len(rows) == 3
    n_cols = len(rows[0].split(","))  # 相对断言基准 = 实际表头列数
    uart = rows[1].split(",")
    assert uart[8] == "65" and uart[9:-1] == [""] * (n_cols - 10)
    assert uart[-1] == "1.0"
    i2c = rows[2].split(",")
    assert i2c[8] == "81" and i2c[9] == "False" and i2c[10] == "12 34" and i2c[11] == "AN"
    assert i2c[12:-1] == [""] * (n_cols - 13)
    assert i2c[-1] == "1.0"


def test_csv_rejects_unregistered_kind_instead_of_silent_fallback():
    report = DecodeReport(protocol="x", params={}, events=[
        DecodedEvent(kind="mystery.x", t_start=0.0, t_end=1e-4, label="M"),
    ])
    with pytest.raises(ValueError, match="kind"):
        report_csv_rows(report)


def test_all_preview_kinds_union_contains_downlink():
    kinds = all_preview_kinds()
    assert kinds[:-1] == ("uart.frame", "i2c.transfer", "i2c.addr",
                          "spi.transfer", "spi.word", "uplink.frame",
                          "downlink.packet", "i3c.transfer", "i3c.addr",
                          "i3c.ccc", "i3c.daa")
    assert kinds[-1] == "fields.split"


def test_default_detail_is_label():
    p = Presentation(protocol="z")
    assert p.detail_fn(_ev("z.k", "原样")) == "原样"
    assert p.plot_family is True and p.csv_columns == () and p.preview_kinds == ()

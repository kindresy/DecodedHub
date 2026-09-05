"""适配器注册表一致性（ADR-018）：spec 单一登记点的结构不变量。

注册表是格式知识的唯一事实来源；这里的守卫保证：登记遗漏、派生清单漂移、
必填声明笔误都在单元测试期暴露，而不是在用户摄取时。
"""

import numpy as np
import pytest

from decodehub.acquisition import load_capture
from decodehub.acquisition.adapters import (
    PLANNED_FORMATS,
    SPECS,
    SUPPORTED_FORMATS,
    get_adapter,
    get_spec,
    options_line,
    options_properties,
    validate_options,
)
from decodehub.acquisition.sniff import sniff
from decodehub.shared import DecodehubError, IngestError, PlannedFormatError

_OPTION_TYPES = {"string", "number", "integer", "boolean"}


class TestRegistryInvariants:
    def test_every_supported_format_is_registered(self):
        """登记遗漏防线：支持格式一个不能少（曾漏 kingst_bin）。"""
        assert set(SUPPORTED_FORMATS) == {
            "kingst_csv", "kingst_bin", "kingst_kvdat",
            "mho98_csv", "mho98_npz",
            "mcu_adc_csv", "mcu_adc_bin",
            "saleae_csv", "sigrok_sr", "generic_csv",
        }
        assert set(PLANNED_FORMATS) == {"saleae_sal", "saleae_binary", "saleae_data_table"}
        assert set(SUPPORTED_FORMATS) | set(PLANNED_FORMATS) == set(SPECS)

    def test_spec_shape(self):
        for s in SPECS.values():
            assert s.description, s.key
            assert (s.load is None) == bool(s.planned_note), s.key  # 延后格式必须给出去路
            if s.sniff is not None:
                assert s.sniff_hint, s.key  # 未命中要能进 tried 诊断
            for o in s.options:
                assert o.type in _OPTION_TYPES, (s.key, o.name)

    def test_sniffable_planned_raise_immediately(self, tmp_path):
        import zipfile

        p = tmp_path / "x.sal"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("meta.json", "{}")
        with pytest.raises(PlannedFormatError):
            sniff(p)

    def test_planned_data_table_sniffs_to_planned_error(self, tmp_path):
        p = tmp_path / "t.csv"
        p.write_text("name,start_time,duration\n", encoding="utf-8")
        with pytest.raises(PlannedFormatError):
            sniff(p)

    def test_planned_via_explicit_format(self, tmp_path):
        p = tmp_path / "x.csv"
        p.write_text("1023\n1024\n", encoding="utf-8")
        with pytest.raises(DecodehubError) as ei:
            load_capture(p, format_key="saleae_data_table")
        assert "延后" in str(ei.value)

    def test_get_adapter_unknown_key(self):
        with pytest.raises(DecodehubError) as ei:
            get_adapter("nope")
        assert "未知格式键" in str(ei.value)


class TestDerivedOptions:
    def test_properties_cover_all_declared(self):
        props = options_properties()
        for s in SPECS.values():
            for o in s.options:
                assert o.name in props, (s.key, o.name)
                assert props[o.name]["type"] == o.type

    def test_required_marked_in_schema_and_line(self):
        desc = options_properties()["sample_rate"]["description"]
        assert "必填于 kingst_bin/mcu_adc_bin" in desc
        assert options_line("mcu_adc_bin").startswith("sample_rate*")
        # mcu_adc_csv 的 sample_rate 是条件必填（单列时），不算硬必填
        assert "*" not in options_line("mcu_adc_csv").split("、")[0]

    def test_capabilities_line(self):
        from decodehub.app.services import capabilities_text

        text = capabilities_text()
        assert "选项带 * 为必填" in text
        assert "sample_rate*、n_channels、device" in text  # kingst_bin 行


class TestValidateOptions:
    def test_missing_required_fails_before_parse(self, tmp_path):
        p = tmp_path / "x.bin"
        np.array([1, 2], dtype="<u2").tofile(p)
        with pytest.raises(IngestError) as ei:
            load_capture(p)  # 嗅探 → mcu_adc_bin → 前置校验
        assert "sample_rate" in str(ei.value)

    def test_optional_spec_passes_validation(self):
        validate_options(get_spec("kingst_kvdat"), {})

    def test_optional_fields_are_not_forced(self, tmp_path):
        p = tmp_path / "k.csv"
        p.write_text("Time[s], 通道 0\n0, 1\n0.001, 0\n", encoding="utf-8")
        cap = load_capture(p)  # kingst_csv 无必填项
        assert cap.meta.format_key == "kingst_csv"
        assert cap.meta.sample_rate is None

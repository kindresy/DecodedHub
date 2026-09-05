"""参数路由派生化（ADR-021）：每个解码的全部参数皆可配；未知参数拒绝。"""

from __future__ import annotations

import numpy as np
import pytest

from decodehub.app import services
from decodehub.app.session import SessionState
from decodehub.decode.bindings import ProtocolBinding, auto_map_channels, get_binding
from decodehub.decode.synth import analogify, encode_i2c, encode_spi, encode_uart, save_kingst_csv
from decodehub.shared.errors import ProtocolLockError
from decodehub.shared.waves import Capture, CaptureMeta


def _digital_state(tmp_path, wave):
    csv = tmp_path / "cap.csv"
    save_kingst_csv(wave, csv)
    st = SessionState()
    services.ingest(st, str(csv), None, None)
    return st


def _analog_state(wave, fs=10_000_000.0):
    ch = analogify(wave, name=wave.channels[0], fs=fs)
    st = SessionState()
    from decodehub.acquisition.project import Project, SourceEntry
    st.project = Project()
    st.project.add(SourceEntry(alias="cap", capture=Capture(
        meta=CaptureMeta(source_kind="synth", format_key="synth"),
        analog=[ch], capture_id="synth-analog")))
    return st


class TestAlternativeRoleMapping:
    def test_spi_accepts_miso_only_and_prefixed_display_names(self):
        assert auto_map_channels(
            ["la:SPI 0 SCK", "la:SPI 0 MISO"], get_binding("spi"), {}
        ) == {"clk": "la:SPI 0 SCK", "miso": "la:SPI 0 MISO"}

    def test_spi_rejects_clock_without_either_data_role(self):
        with pytest.raises(ProtocolLockError, match="至少需要其一"):
            auto_map_channels(
                ["CLK", "GPIO"],
                get_binding("spi"),
                {"mosi": None, "miso": None, "cs": None},
            )

    def test_explicit_channel_is_reserved_before_heuristics(self):
        assert auto_map_channels(
            ["D0", "CLK", "MOSI"], get_binding("spi"), {"clk": "D0"}
        ) == {"clk": "D0", "mosi": "MOSI", "miso": "CLK"}

    def test_role_tokens_do_not_match_arbitrary_substrings(self):
        binding = ProtocolBinding(
            protocol="test",
            node_type="spi_decode",
            roles=("cs",),
            role_aliases={"cs": frozenset({"cs", "nss"})},
        )
        assert auto_map_channels(["focus", "bus NSS"], binding, {}) == {
            "cs": "bus NSS"
        }


class TestProtocolParamsRouted:
    def test_uart_all_params(self, tmp_path):
        st = _digital_state(tmp_path, encode_uart(b"AB", baud=115200, seed=1))
        services.lock_protocol(st, "uart", {
            "baud": 115200, "data_bits": 8, "parity": "N",
            "stop_bits": 1, "invert": False, "bit_order": "lsb",
        }, None)
        services.run_decode(st, None, None)
        params = next(iter(st.reports.values())).params
        assert params["baud"] == 115200 and params["bit_order"] == "lsb"

    def test_i2c_stretch_warn(self, tmp_path):
        st = _digital_state(tmp_path, encode_i2c(
            [{"addr": 0x51, "read": False, "data": [0x2A]}], freq=400e3))
        services.lock_protocol(st, "i2c", {"stretch_warn_s": 0.002}, None)
        services.run_decode(st, None, None)
        assert next(iter(st.reports.values())).params["stretch_warn_s"] == 0.002

    def test_spi_all_params(self, tmp_path):
        st = _digital_state(tmp_path, encode_spi(
            [0xA5, 0x5A], cpol=1, cpha=1, word_bits=8))
        services.lock_protocol(st, "spi", {
            "cpol": 1, "cpha": 1, "word_bits": 8,
            "bit_order": "msb", "cs_active": "low",
        }, None)
        services.run_decode(st, None, None)
        params = next(iter(st.reports.values())).params
        assert params["cpol"] == 1 and params["cs_active"] == "low"

    def test_analog_source_slicer_params(self):
        st = _analog_state(encode_uart(b"AB", baud=115200, seed=1))
        services.lock_protocol(st, "uart", {"threshold": 1.2, "hysteresis": 0.3}, None)
        services.run_decode(st, None, None)  # 切片路径跑通即可
        assert "cap|uart" in st.reports

    def test_uplink_all_params(self):
        st = _analog_state(encode_uart(b"AB", baud=115200, seed=1), fs=10_000_000.0)
        services.lock_protocol(st, "uplink", {
            "profile": "default", "chip_s": 1e-6, "pn_word": 0x3DA60E45,
            "pn_len": 31, "pream": "001", "data_bits": 5,
            "invert": False, "unipolar": False, "msb_first": True,
        }, None)
        assert "cap|uplink" in st.locks


class TestUnknownParamsRejected:
    def test_uart_typo(self, tmp_path):
        st = _digital_state(tmp_path, encode_uart(b"AB", baud=115200, seed=1))
        with pytest.raises(ProtocolLockError, match="'buad'") as ei:
            services.lock_protocol(st, "uart", {"baud": 115200, "buad": 9600}, None)
        assert "baud" in str(ei.value)  # 可配参数列表给出正确拼写

    def test_i2c_slicer_param_rejected(self, tmp_path):
        """数字源上的 i2c 不经过 slicer：threshold 不可配。"""
        st = _digital_state(tmp_path, encode_i2c(
            [{"addr": 0x51, "read": False, "data": [0x2A]}], freq=400e3))
        with pytest.raises(ProtocolLockError, match="'threshold'"):
            services.lock_protocol(st, "i2c", {"threshold": 1.2}, None)

    def test_uplink_foreign_param_rejected(self):
        st = _analog_state(encode_uart(b"AB", baud=115200, seed=1))
        with pytest.raises(ProtocolLockError, match="'fc_nominal'"):
            services.lock_protocol(st, "uplink", {"fc_nominal": 263e3}, None)

    def test_role_override_still_works(self, tmp_path):
        st = _digital_state(tmp_path, encode_uart(b"AB", baud=115200, seed=1))
        services.lock_protocol(st, "uart", {"rx": "TX"}, None)
        assert st.locks["cap|uart"].channel_map["rx"] == "TX"


class TestParamsCommand:
    def test_list_one(self, capsys):
        from decodehub.cli.main import main
        assert main(["params", "uart"]) == 0
        out = capsys.readouterr().out
        assert "baud" in out and "data_bits" in out and "stop_bits" in out

    def test_list_all_and_unknown(self, capsys):
        from decodehub.cli.main import main
        assert main(["params"]) == 0
        assert "downlink" in capsys.readouterr().out
        assert main(["params", "nope"]) == 1

    def test_slicer_params_listed_for_sliced_protocols(self, capsys):
        """模拟源上的数字协议经 slicer 切片：目录补列切片参数（params 与可配集合同源）。"""
        from decodehub.decode.bindings import get_binding
        from decodehub.decode.registry import get_registry
        slicer_keys = set(get_registry()["slicer"].PARAMS)
        for proto, c in services.PROTOCOL_CATALOG.items():
            if get_binding(proto).analog_direct:
                assert slicer_keys.isdisjoint(c["params"])  # 模拟直达不经切片
            else:
                assert slicer_keys <= set(c["params"])
        from decodehub.cli.main import main
        assert main(["params", "uart"]) == 0
        out = capsys.readouterr().out
        assert "threshold" in out and "模拟源切片时" in out


class TestPipelineChainParams:
    def test_all_node_params_configurable(self, tmp_path):
        """链节点参数同样全量可配（扁平写法，多个 PARAMS 键）。"""
        st = _digital_state(tmp_path, encode_uart(b"AB", baud=115200, seed=1))
        services.lock_protocol(st, "uart", {"baud": 115200}, None)
        services.bind_pipeline(st, "win", "uart", [{
            "type": "event_filter", "kinds": ["uart.frame"],
            "t_min": 0.0, "has_errors": False,
        }])
        services.run_decode(st, None, None)
        lock = st.locks["cap|win"]
        assert lock.graph.nodes["pipe0_event_filter"].params["t_min"] == 0.0
        assert lock.graph.nodes["pipe0_event_filter"].params["has_errors"] is False

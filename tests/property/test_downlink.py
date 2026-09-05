"""下行 DBPSK 测试（ADR-011）：上行为锚的偏移解析 + 跨源/同源双通道 + 参数全量可配。"""

import random

import numpy as np
import pytest

from decodehub.app import services
from decodehub.app.session import SessionState
from decodehub.decode.synth import encode_downlink, encode_uplink
from decodehub.shared import Capture, CaptureMeta
from decodehub.shared.errors import ProtocolLockError
from decodehub.shared.waves import AnalogChannel

SEED = 20260904
PERIOD = 1.0 / 60.0
SYM_S = 31 * 1e-6


def _capture(analog, cid):
    return Capture(meta=CaptureMeta(source_kind="synth", format_key="synth"),
                   analog=list(analog), capture_id=cid)


def _mk_project(n_frames=3, snr=None, seed=1):
    """上行帧（guard + n）与下行包（6 槽，末槽恒载波）双通道，同 t0。"""
    rng = random.Random(seed)
    frames = [tuple(rng.randrange(2) for _ in range(5)) for _ in range(n_frames)]
    ul = encode_uplink([(0, 1, 0, 1, 0)] + frames, fs=10e6, period_s=PERIOD,
                       env_amp=0.5, snr_db=None, seed=seed)
    # 下行锚在上行帧网格（delta 自校准，锚点只需落在同一 60Hz 网格周期内）
    anchors = [0.37e-6 + (f + 1) * PERIOD + 0.5 * SYM_S for f in range(-1, n_frames)]
    truth = []
    for _ in anchors:
        slots = [tuple(rng.randrange(2) for _ in range(16)) for _ in range(5)]
        slots.append((0,) * 16)  # 末槽恒载波（真实固件行为；接收端据此定位用户周期）
        truth.append(slots)
    dl = encode_downlink(anchors, truth, fs=10e6, delta_s=850e-6,
                         snr_db=snr, seed=seed + 1)
    if snr is not None:
        pass  # snr 已在 encode_downlink 内注入
    return ul, dl, frames, truth


class TestDownlinkRoundTrip:
    def test_cross_source(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ul, dl, frames, truth = _mk_project(n_frames=3, seed=11)
        st = SessionState()
        from decodehub.acquisition.project import Project, SourceEntry
        st.project = Project()
        st.project.add(SourceEntry(alias="ch1", capture=_capture([ul], "ch1")))
        st.project.add(SourceEntry(alias="ch2", capture=_capture([dl], "ch2")))

        services.lock_protocol(st, "uplink", {}, source="ch1")
        plan, _g = services.lock_protocol(st, "downlink", {}, source="ch2")
        # 图结构：上行子图（前缀克隆）+ 本源 apick + 扇入
        assert "ul_uplink_decode" in plan and "downlink_decode" in plan
        types = [n.type for n in st.locks["ch2|downlink"].graph.nodes.values()]
        assert types.count("analog_pick") == 2  # 上行子图 + 下行本源
        assert st.locks["ch2|downlink"].source_inputs == {"ul_apick": "ch1", "apick": "ch2"}

        services.run_decode(st, {}, None)
        rep = st.reports["ch2|downlink"]
        packets = [e for e in rep.events if e.kind == "downlink.packet"]
        assert len(packets) >= 3 * 6
        # 帧编号为网格索引（可能整体偏移）；按槽值匹配帧映射
        def _val(bits16):
            v = 0
            for b in bits16:
                v = (v << 1) | b
            return v
        by_frame = {}
        for p in packets:
            by_frame.setdefault(p.frame, {})[p.slot] = p
        frames_dec = sorted(f for f, d in by_frame.items() if 0 in d and 5 in d)
        off = frames_dec[0]  # 解码帧号 = 网格索引，真值 f 对应解码 f+off
        for f in range(len(truth)):
            got = by_frame.get(f + off, {})
            for k in range(5):
                pk = got.get(k)
                if pk is None:
                    continue
                assert pk.value == _val(truth[f][k]), (f, k)
            pk5 = got.get(5)
            if pk5 is not None:
                # 恒载波槽：差分读出为全 0 或全 1（极性两极，见 data_hex_inv）
                assert pk5.value in (0x0000, 0xFFFF)

    def test_same_source_two_channels(self, tmp_path, monkeypatch):
        """单源双通道（示波器一次采集两通道）：上行与下行同源。"""
        monkeypatch.chdir(tmp_path)
        ul, dl, frames, truth = _mk_project(n_frames=2, seed=23)
        st = SessionState()
        from decodehub.acquisition.project import Project, SourceEntry
        st.project = Project()
        cap = Capture(meta=CaptureMeta(source_kind="synth", format_key="synth"),
                      analog=[ul, dl], capture_id="scope")
        st.project.add(SourceEntry(alias="scope", capture=cap))
        services.lock_protocol(st, "uplink", {"rx": "CH1"}, source="scope")
        services.lock_protocol(st, "downlink", {"rx": "CH2", "uplink_source": "scope"},
                               source="scope")
        services.run_decode(st, {}, None)
        packets = [e for e in st.reports["scope|downlink"].events
                   if e.kind == "downlink.packet"]
        assert len(packets) >= 2 * 6

    def test_render_and_export(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ul, dl, *_ = _mk_project(n_frames=2, seed=31)
        st = SessionState()
        from decodehub.acquisition.project import Project, SourceEntry
        st.project = Project()
        st.project.add(SourceEntry(alias="ch1", capture=_capture([ul], "ch1")))
        st.project.add(SourceEntry(alias="ch2", capture=_capture([dl], "ch2")))
        services.lock_protocol(st, "uplink", {}, source="ch1")
        services.lock_protocol(st, "downlink", {}, source="ch2")
        summary = services.run_decode(st, {}, None)
        png, table = services.render_timing(st, None, None, 60, 150, source="ch2")
        assert png.exists() and "下行·包" in table
        # run_decode 摘要 preview 含下行包（ADR-013：preview 注册表修复 downlink 缺席）
        assert "下行·包" in summary
        import json
        p = services.export_events(st, "json", None, source="ch2")
        data = json.loads(p.read_text())
        assert data["protocol"] == "downlink"
        # CSV 协议专有列（ADR-013 注册表）：下行行填充 value/fc_hz/slot/frame/confidence
        # （fields 三列由 ADR-016 追加在并集尾部，下行列位置不变）
        csv_p = services.export_events(st, "csv", None, source="ch2")
        lines = csv_p.read_text().strip().splitlines()
        assert ",pream_ok,confidence,fc_hz,slot,frame," in lines[0]
        assert lines[0].endswith(",fields,source_kind,spec,schema_version")
        pk = next(l for l in lines[1:] if "downlink.packet" in l).split(",")
        assert pk[8] != "" and pk[16] != "" and pk[17] != "" and pk[18] != "" and pk[19] != ""


class TestDownlinkConfigurable:
    def test_custom_carrier_shape(self, tmp_path, monkeypatch):
        """载波/每 bit 周期数/包长/槽位结构全部可配（ADR-011）。"""
        monkeypatch.chdir(tmp_path)
        rng = random.Random(5)
        frames = [tuple(rng.randrange(2) for _ in range(5)) for _ in range(2)]
        ul = encode_uplink([(1, 0, 1, 0, 1)] + frames, fs=10e6, period_s=PERIOD, seed=7)
        anchors = [0.37e-6 + (f + 1) * PERIOD + 0.5 * SYM_S for f in range(-1, 2)]
        truth = []
        for _ in anchors:
            slots = [tuple(rng.randrange(2) for _ in range(12)) for _ in range(1)]
            slots.append((0,) * 12)
            truth.append(slots)
        dl = encode_downlink(anchors, truth, fs=10e6, fc=300e3,
                             cycles_per_bit=8, delta_s=500e-6,
                             slot_period=PERIOD / 2, seed=9)
        st = SessionState()
        from decodehub.acquisition.project import Project, SourceEntry
        st.project = Project()
        st.project.add(SourceEntry(alias="ch1", capture=_capture([ul], "ch1")))
        st.project.add(SourceEntry(alias="ch2", capture=_capture([dl], "ch2")))
        services.lock_protocol(st, "uplink", {}, source="ch1")
        services.lock_protocol(st, "downlink", {
            "fc_nominal": 300e3, "cycles_per_bit": 8, "n_bits": 13,
            "slot_offsets_us": [0, 8333], "frame_hz": 60,  # 与 synth slot_period=帧周期/2 一致
        }, source="ch2")
        services.run_decode(st, {}, None)
        packets = [e for e in st.reports["ch2|downlink"].events
                   if e.kind == "downlink.packet"]
        assert len(packets) >= 2 * 2
        expect_vals = {int("".join(map(str, slots[0])), 2) for slots in truth}
        slot0_vals = {pk.value for pk in packets if pk.slot == 0}
        assert expect_vals <= slot0_vals  # 槽 0 数据全部解出

    def test_uplink_configurable(self):
        """上行协议形状可配：同长度换 m 序列字 + 数据位数 5→8（ADR-011）。

        约束（vendored DSP 的物理前提，见 ADR-011）：PN 字必须具 m 序列级
        近零旁瓣自相关；PN **长度**改动需配套策略档案（默认策略按 31-chip
        实机调参），故长度固定在 31 验证。
        """
        from decodehub.decode.protocols.uplink.decode import UplinkDecodeNode, UplinkPrecondNode

        def _mseq(taps, n, seed=1):
            reg, out = seed, []
            for _ in range(n):
                fb = bin(reg & taps).count("1") & 1
                out.append(reg & 1)
                reg = (reg >> 1) | (fb << (n - 1))
            return out

        alt_word = int("".join(map(str, _mseq(0b00101, 31, 0b0101))), 2)  # 同本原多项式、不同相位（循环移位）
        assert alt_word != 0x3DA60E45
        rng = random.Random(3)
        data = [tuple(rng.randrange(2) for _ in range(8)) for _ in range(3)]
        ch = encode_uplink([(0, 0, 0, 0, 0, 0, 0, 1)] + data, fs=10e6, period_s=PERIOD,
                           pn_word=alt_word, data_bits_n=8, seed=13)
        pre = UplinkPrecondNode().run({"in": [ch]}, {"channel": "CH1", "profile": "default"})
        ev = UplinkDecodeNode().run({"in": pre["out"]}, {
            "channel": "CH1", "profile": "default",
            "pn_word": alt_word, "data_bits": 8,
        })["out"]
        values = [e.value for e in ev if e.kind == "uplink.frame"]
        expected = [int("".join(str(b) for b in d), 2) for d in data]
        assert values == ([1] + expected)[: len(values)]  # 首帧为 guard（值 1）
        assert len(values) >= 3
        assert all(e.pream_ok for e in ev if e.kind == "uplink.frame")


class TestDownlinkGuards:
    def test_requires_uplink_lock(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ul, dl, *_ = _mk_project(n_frames=1, seed=41)
        st = SessionState()
        from decodehub.acquisition.project import Project, SourceEntry
        st.project = Project()
        st.project.add(SourceEntry(alias="ch1", capture=_capture([ul], "ch1")))
        st.project.add(SourceEntry(alias="ch2", capture=_capture([dl], "ch2")))
        with pytest.raises(ProtocolLockError, match="uplink"):
            services.lock_protocol(st, "downlink", {}, source="ch2")

    def test_t0_mismatch_rejected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ul, dl, *_ = _mk_project(n_frames=1, seed=43)
        dl_shifted = AnalogChannel(name=dl.name, samples=dl.samples, t0=dl.t0 + 5e-3,
                                   dt=dl.dt)
        st = SessionState()
        from decodehub.acquisition.project import Project, SourceEntry
        st.project = Project()
        st.project.add(SourceEntry(alias="ch1", capture=_capture([ul], "ch1")))
        st.project.add(SourceEntry(alias="ch2", capture=_capture([dl_shifted], "ch2")))
        services.lock_protocol(st, "uplink", {}, source="ch1")
        with pytest.raises(ProtocolLockError, match="同一次采集|同触发"):
            services.lock_protocol(st, "downlink", {}, source="ch2")

    def test_real_silent_ch2_honest(self, data_dir):
        """真实双通道采集：CH2 当时静默（capture.md）→ 诚实拒绝，不输出伪包。"""
        if not (data_dir / "uplink24ms_ch1.npz").exists():
            pytest.skip("真实采集不在库（15MB，见 .gitignore）")
        from decodehub.acquisition import load_capture

        st = SessionState()
        from decodehub.acquisition.project import Project, SourceEntry
        st.project = Project()
        st.project.add(SourceEntry(
            alias="ch1", capture=load_capture(data_dir / "uplink24ms_ch1.npz")))
        st.project.add(SourceEntry(
            alias="ch2", capture=load_capture(data_dir / "uplink24ms_ch2.npz")))
        services.lock_protocol(st, "uplink", {}, source="ch1")
        services.lock_protocol(st, "downlink", {}, source="ch2")
        msg = services.run_decode(st, {}, source="ch2")
        rep = st.reports["ch2|downlink"]
        packets = [e for e in rep.events if e.kind == "downlink.packet"]
        assert packets == []
        assert any(e.kind == "downlink.warn" for e in rep.events)

    def test_profile_persists_downlink(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DECODEHUB_PROFILES_DIR", str(tmp_path / "profiles"))
        ul, dl, *_ = _mk_project(n_frames=1, seed=47)
        st = SessionState()
        from decodehub.acquisition.project import Project, SourceEntry
        st.project = Project()
        st.project.add(SourceEntry(alias="ch1", capture=_capture([ul], "ch1")))
        st.project.add(SourceEntry(alias="ch2", capture=_capture([dl], "ch2")))
        services.lock_protocol(st, "uplink", {"pn_word": 0x3DA60E45}, source="ch1")
        services.lock_protocol(st, "downlink",
                               {"slot_offsets_us": [1970, 4748, 7525, 10303, 13081, 15858],
                                "fc_nominal": 263e3},
                               source="ch2")
        services.save_profile(st, "tp-duo", "上行+下行")
        import json
        data = json.loads((tmp_path / "profiles" / "tp-duo.json").read_text())
        locks = {l["source"]: l for l in data["locks"]}
        assert locks["ch1"]["params"]["pn_word"] == 0x3DA60E45
        assert locks["ch2"]["params"]["fc_nominal"] == 263e3
        assert len(locks["ch2"]["params"]["slot_offsets_us"]) == 6

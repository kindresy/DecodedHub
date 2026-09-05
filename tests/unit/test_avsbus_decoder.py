"""AVSBus 三线被动解码器回归。"""

from decodehub.decode.protocols.avsbus.decode import AvsBusDecodeNode
from decodehub.decode.protocols.avsbus.encode import encode_avsbus
from decodehub.shared.waves import DigitalWave


def _params(**overrides):
    params = {name: decl.default for name, decl in AvsBusDecodeNode.PARAMS.items()}
    params.update(overrides)
    return params


def test_avsbus_read_and_write_fields_and_crc():
    wave = encode_avsbus([
        {"cmd": 3, "cmd_group": 0, "cmd_data_type": 0, "select": 2,
         "cmd_data": 0xFFFF, "response_data": 0x0ABC,
         "slave_ack": 0, "status_resp": 0},
        {"cmd": 0, "cmd_group": 0, "cmd_data_type": 0, "select": 1,
         "cmd_data": 0x0555, "response_data": 0xFFFF,
         "slave_ack": 0, "status_resp": 0},
    ], fs=1_000_000, idle_clocks=2)
    events = AvsBusDecodeNode().run({"in": wave}, _params())["out"]
    frames = [e for e in events if e.kind == "avsbus.frame"]
    assert len(frames) == 2
    read, write = frames
    assert read.command == "read"
    assert read.cmd == 3 and read.cmd_data_type == 0 and read.select == 2
    assert read.cmd_data == 0xFFFF and read.response_data == 0x0ABC
    assert read.main_crc_ok and read.response_crc_ok and not read.errors
    assert write.command == "write_commit"
    assert write.cmd_data == 0x0555 and write.slave_ack == 0


def test_avsbus_target_status_and_response_data_layout_roundtrip():
    wave = encode_avsbus([{
        "cmd": 3, "cmd_data": 0xFFFF, "response_data": 0x1234,
        "status_resp": 1, "slave_ack": 0,
    }])
    frame = [e for e in AvsBusDecodeNode().run({"in": wave}, _params())["out"]
             if e.kind == "avsbus.frame"][0]
    assert frame.response_data == 0x1234
    assert frame.status_resp == 1
    assert "status-response" in frame.errors


def test_avsbus_bad_crc_and_invalid_start_are_events_not_exceptions():
    wave = encode_avsbus([{"cmd": 3, "cmd_data_type": 2, "select": 1,
                           "cmd_data": 0x1111, "response_data": 0x2222}],
                         fs=2_000_000)
    # Flip one MData bit after generation; decoder must keep the frame and report it.
    from decodehub.shared.waves import DigitalWave
    segs = [(0.0, wave.initial)]
    for t, lv in zip(wave.edges_t, wave.edges_levels):
        segs.append((float(t), int(lv)))
    # Locate first data transition and toggle the logical mdata level in a snapshot.
    for i, (t, lv) in enumerate(segs):
        if i > 1:
            segs[i] = (t, lv ^ 0b010)
            break
    bad = DigitalWave.from_segments(list(wave.channels), wave.initial, segs[1:], wave.t_end,
                                    t_start=wave.t_start, sample_rate=wave.sample_rate,
                                    n_samples=wave.n_samples)
    events = AvsBusDecodeNode().run({"in": bad}, _params())["out"]
    assert any(e.kind == "avsbus.frame" and e.errors for e in events)


def test_avsbus_target_mode_does_not_validate_corrupted_controller_bits():
    wave = encode_avsbus([{"cmd": 3, "cmd_data": 0xFFFF, "response_data": 0x1234}])
    segs = [(float(t), int(lv)) for t, lv in zip(wave.edges_t, wave.edges_levels)]
    # Corrupt the first controller data snapshot while keeping the target side intact.
    for i, (t, lv) in enumerate(segs):
        if i > 1:
            segs[i] = (t, lv ^ 0b010)
            break
    corrupted = DigitalWave.from_segments(list(wave.channels), wave.initial, segs,
                                           wave.t_end, sample_rate=wave.sample_rate,
                                           n_samples=wave.n_samples)
    frames = [e for e in AvsBusDecodeNode().run(
        {"in": corrupted}, _params(mode="target"))["out"] if e.kind == "avsbus.frame"]
    assert frames and "start-code" not in frames[0].errors


def test_avsbus_resync_discards_partial_prefix_and_restarts_frame_boundary():
    valid = encode_avsbus([{"cmd": 0, "cmd_data": 0x0555}], fs=1_000_000)
    from decodehub.shared.waves import DigitalWave
    segs = []
    t = 0.0
    # Five incomplete clocks before a legal 34-clock resync burst.
    for _ in range(5):
        t += 0.5e-6
        segs.append((t, 0b100))
        t += 0.5e-6
        segs.append((t, 0b101))
        t += 0.5e-6
        segs.append((t, 0b100))
    for _ in range(34):
        t += 0.5e-6
        segs.append((t, 0b110))
        t += 0.5e-6
        segs.append((t, 0b111))
        t += 0.5e-6
        segs.append((t, 0b110))
    shift = t
    segs.extend((shift + float(et), int(el))
                for et, el in zip(valid.edges_t, valid.edges_levels))
    combined = DigitalWave.from_segments(list(valid.channels), 0b110, segs,
                                          t_end=shift + valid.t_end,
                                          sample_rate=valid.sample_rate,
                                          n_samples=int((shift + valid.t_end) * valid.sample_rate))
    events = AvsBusDecodeNode().run({"in": combined}, _params())["out"]
    frames = [e for e in events if e.kind == "avsbus.frame"]
    assert len(frames) == 1 and frames[0].cmd_data == 0x0555
    assert not [e for e in events if e.kind == "avsbus.warn"]


def test_avsbus_resync_burst_is_not_sliced_into_fake_frames():
    wave = encode_avsbus([], fs=1_000_000)
    # Build 34 idle-high data clock cycles, the specified resync sequence.
    from decodehub.shared.waves import DigitalWave
    segs = []
    t = 0.0
    for _ in range(34):
        t += 0.5e-6
        segs.append((t, 0b110))
        t += 0.5e-6
        segs.append((t, 0b111))
        t += 0.5e-6
        segs.append((t, 0b110))
    resync = DigitalWave.from_segments(["clock", "mdata", "sdata"], 0b110, segs, t_end=t,
                                        sample_rate=1_000_000, n_samples=int(t * 1_000_000))
    events = AvsBusDecodeNode().run({"in": resync}, _params())["out"]
    assert [e.kind for e in events] == ["avsbus.resync"]


def test_avsbus_encoder_clock_period_matches_declared_sample_rate():
    wave = encode_avsbus([{"cmd": 0}], fs=1_000_000)
    times, levels = wave.edge_stream("clock")
    rises = times[levels == 1]
    assert len(rises) >= 3
    assert max(abs(float(d) - 1e-6) for d in rises[1:32] - rises[:31]) < 1e-12

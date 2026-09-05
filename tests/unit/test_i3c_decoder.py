from decodehub.decode.protocols.i3c.decode import I3cDecodeNode
from decodehub.decode.synth import encode_i3c
from decodehub.shared import DigitalWave


def decode(wave, **params):
    defaults = {name: decl.default for name, decl in I3cDecodeNode.PARAMS.items()}
    defaults.update(params)
    return I3cDecodeNode().run({"in": wave}, defaults)["out"]


def test_repeated_start_is_one_transfer():
    wave = encode_i3c([
        {"addr": 0x50, "read": False, "data": [0x10], "repeat_next": True},
        {"addr": 0x50, "read": True, "data": [0xBE]},
    ])
    events = decode(wave, mode="sdr")
    assert any(event.kind == "i3c.repeat-start" for event in events)
    transfers = [event for event in events if event.kind == "i3c.transfer"]
    assert len(transfers) == 1
    assert transfers[0].data_bytes == [0x10, 0xBE]
    assert transfers[0].read is True


def test_legacy_i2c_mode_preserves_ack_semantics():
    wave = encode_i3c([
        {"addr": 0x33, "read": False, "data": [0xA5], "mode": "legacy_i2c"}
    ])
    transfer = [event for event in decode(wave, mode="legacy_i2c")
                 if event.kind == "i3c.transfer"][0]
    assert transfer.mode == "legacy_i2c"
    assert transfer.acks == [True, True]
    assert transfer.parity_ok == [None]


def test_parity_failure_is_an_event_error():
    wave = encode_i3c([{"addr": 0x33, "read": False, "data": [0x00],
                        "bad_parity": [True]}])
    events = decode(wave, mode="sdr")
    data = [event for event in events if event.kind == "i3c.data"][0]
    assert "parity" in data.errors
    transfer = [event for event in events if event.kind == "i3c.transfer"][0]
    assert "parity" in transfer.errors


def test_truncated_frame_is_reported():
    wave = encode_i3c([{"addr": 0x20, "read": False, "data": [0x77]}])
    cut = wave.t_end - 50e-6 - 0.25 / 100e3
    from decodehub.shared import DigitalWave
    keep = wave.edges_t < cut
    truncated = DigitalWave(
        channels=wave.channels,
        initial=wave.initial,
        t_start=wave.t_start,
        edges_t=wave.edges_t[keep],
        edges_levels=wave.edges_levels[keep],
        t_end=cut,
    )
    events = decode(truncated, mode="sdr")
    transfer = [event for event in events if event.kind == "i3c.transfer"][0]
    assert "truncated" in transfer.errors


def test_auto_mode_marks_address_only_as_ambiguous():
    wave = encode_i3c([{"addr": 0x20, "read": False, "data": []}])
    events = decode(wave, mode="auto")
    transfer = [event for event in events if event.kind == "i3c.transfer"][0]
    assert transfer.mode == "unknown"
    assert transfer.errors == ["ambiguous"]


def test_address_nack_is_preserved_as_transfer_error():
    wave = encode_i3c([{"addr": 0x20, "read": False, "acks": [False]}])
    transfer = [event for event in decode(wave, mode="sdr")
                if event.kind == "i3c.transfer"][0]
    assert transfer.acks == [False]
    assert "nack" in transfer.errors


def test_auto_bus_profile_does_not_guess_bus_free_violation():
    # In auto profile the capture cannot prove whether Pure Bus or Mixed Bus
    # timing rules apply, so no deterministic T_BUF warning is emitted.
    wave = encode_i3c([
        {"addr": 0x20, "data": [], "repeat_next": False},
        {"addr": 0x21, "data": []},
    ], gap_s=0.0)
    events = decode(wave, mode="sdr", bus_profile="auto")
    assert not [event for event in events if "bus-free" in event.errors]


def test_hdr_entry_is_explicitly_unsupported():
    wave = encode_i3c([{"addr": 0x7E, "mode": "hdr-ddr", "data": [0xAA]}])
    events = decode(wave, mode="auto")
    unsupported = [event for event in events if event.kind == "i3c.unsupported"]
    assert unsupported and "hdr" in unsupported[0].errors
    assert not [event for event in events if event.kind == "i3c.transfer"]
    after_hdr = [event for event in events if event.t_start > unsupported[0].t_end]
    assert not [event for event in after_hdr
                if event.kind in {"i3c.repeat-start", "i3c.addr", "i3c.stop"}]


def test_auto_private_read_stays_ambiguous():
    wave = encode_i3c([{"addr": 0x22, "read": True, "data": [0xAA]}])
    transfer = [event for event in decode(wave, mode="auto")
                if event.kind == "i3c.transfer"][0]
    assert transfer.mode == "unknown"
    assert "ambiguous" in transfer.errors


def test_tbit_zero_then_more_data_is_reported():
    wave = encode_i3c([{
        "addr": 0x22, "read": True, "data": [0xAA, 0xBB], "t_bits": [0, 1]
    }])
    events = decode(wave, mode="sdr")
    data = [event for event in events if event.kind == "i3c.data"]
    assert "after-tbit" in data[-1].errors
    transfer = [event for event in events if event.kind == "i3c.transfer"][0]
    assert "after-tbit" in transfer.errors


def test_private_byte_that_matches_direct_ccc_code_is_not_ccc():
    wave = encode_i3c([{"addr": 0x22, "read": False, "data": [0x90]}])
    events = decode(wave, mode="sdr")
    assert not [event for event in events if event.kind == "i3c.ccc"]


def test_repeated_start_inside_daa_round_closes_incomplete_round():
    wave = encode_i3c([{
        "addr": 0x7E,
        "daa": {"pid": 0x123456789ABC, "bcr": 0x12, "dcr": 0x34, "address": 0x22},
    }])
    scl_t, scl_lv = wave.edge_stream("SCL")
    rises = scl_t[scl_lv == 1]
    falls = scl_t[scl_lv == 0]
    insert = None
    for rise in rises:
        following = falls[falls > rise]
        if 4e-4 < rise < 7e-4 and following.size and wave.level_at("SDA", float(rise)) == 1:
            insert = (float(rise) + (float(following[0]) - float(rise)) / 3.0)
            break
    assert insert is not None
    snapshot = wave.level_at("SCL", insert) | (wave.level_at("SDA", insert) << 1)
    broken = DigitalWave.from_segments(
        list(wave.channels), wave.initial,
        list(zip(wave.edges_t.tolist(), wave.edges_levels.tolist())) + [(insert, snapshot & ~2)],
        t_end=wave.t_end,
    )
    events = decode(broken, mode="sdr")
    assert any(event.kind == "i3c.repeat-start" for event in events)
    assert any(event.kind == "i3c.daa" and "incomplete-daa" in event.errors
               for event in events)


def test_daa_assignment_followed_directly_by_stop_is_not_corrupted():
    source = encode_i3c([{
        "addr": 0x7E,
        "daa": {"pid": 0x123456789ABC, "bcr": 0x12, "dcr": 0x34, "address": 0x22},
    }])
    # Keep the first target's assignment ACK, discard the encoder's final
    # probe, and append a legal STOP conditioning sequence.
    reference = decode(source, mode="sdr")
    assignment_end = [event.t_end for event in reference if event.kind == "i3c.daa"][0]
    half = 1.0 / (2 * 100e3)
    keep = source.edges_t <= assignment_end + 1e-15
    segments = list(zip(source.edges_t[keep].tolist(), source.edges_levels[keep].tolist()))
    current = int(source.edges_levels[keep][-1])
    segments.extend([
        (assignment_end + half, current & ~1),
        (assignment_end + 2 * half, current | 1),
        (assignment_end + 3 * half, current | 1 | 2),
    ])
    stopped = DigitalWave.from_segments(
        list(source.channels), source.initial, segments,
        t_end=assignment_end + 4 * half,
    )
    events = decode(stopped, mode="sdr")
    daa = [event for event in events if event.kind == "i3c.daa"]
    assert len(daa) == 1
    assert daa[0].pid == 0x123456789ABC and daa[0].address == 0x22
    assert "incomplete-daa" not in daa[0].errors

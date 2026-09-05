from decodehub.decode.synth import encode_i3c
from decodehub.decode.protocols.i3c.decode import I3cDecodeNode


def run_i3c(wave, **params):
    norm = {}
    for name, decl in I3cDecodeNode.PARAMS.items():
        norm[name] = decl.coerce(params[name]) if name in params else decl.default
    norm.setdefault("scl", wave.channels[0])
    norm.setdefault("sda", wave.channels[1])
    return I3cDecodeNode().run({"in": wave}, norm)["out"]


def test_i3c_encoder_is_available():
    wave = encode_i3c([{"addr": 0x2A, "read": False, "data": [0x12, 0x34]}])
    events = run_i3c(wave, mode="sdr")
    transfers = [event for event in events if event.kind == "i3c.transfer"]
    assert len(transfers) == 1
    assert transfers[0].address == 0x2A
    assert transfers[0].data_bytes == [0x12, 0x34]
    assert transfers[0].parity_ok == [True, True]
    assert transfers[0].t_bits == [None, None]


def test_i3c_read_t_bits():
    wave = encode_i3c([{"addr": 0x2A, "read": True, "data": [0xAB, 0xCD]}])
    transfers = [event for event in run_i3c(wave, mode="sdr")
                 if event.kind == "i3c.transfer"]
    assert transfers[0].data_bytes == [0xAB, 0xCD]
    assert transfers[0].t_bits == [1, 0]


def test_broadcast_ccc_and_unknown_command():
    known = run_i3c(encode_i3c([
        {"addr": 0x7E, "read": False, "ccc": 0x06, "data": [0x2A]}
    ]), mode="sdr")
    ccc = [event for event in known if event.kind == "i3c.ccc"][0]
    assert ccc.ccc == 0x06 and ccc.ccc_name == "RSTDAA"
    assert ccc.errors == []

    unknown = run_i3c(encode_i3c([
        {"addr": 0x7E, "read": False, "ccc": 0x99}
    ]), mode="sdr")
    ccc = [event for event in unknown if event.kind == "i3c.ccc"][0]
    assert ccc.ccc == 0x99 and "unknown-ccc" in ccc.errors


def test_direct_ccc_read_remains_unclassified_without_broadcast_context():
    events = run_i3c(encode_i3c([{
        "addr": 0x22,
        "read": True,
        "ccc": 0x8D,
        "data": [0x12, 0x34],
    }]), mode="sdr")
    assert not [event for event in events if event.kind == "i3c.ccc"]
    transfer = [event for event in events if event.kind == "i3c.transfer"][0]
    assert transfer.data_bytes == [0x8D, 0x12, 0x34]


def test_daa_metadata_is_retained():
    events = run_i3c(encode_i3c([{
        "addr": 0x7E,
        "read": False,
        "daa": {"pid": 0x123456789ABC, "bcr": 0x12, "dcr": 0x34, "address": 0x22},
    }]), mode="sdr")
    daa = [event for event in events if event.kind == "i3c.daa"][0]
    assert daa.pid == 0x123456789ABC
    assert daa.bcr == 0x12 and daa.dcr == 0x34
    assert daa.address == 0x22


def test_daa_arbitration_decodes_each_64_bit_target_record():
    events = run_i3c(encode_i3c([{
        "addr": 0x7E,
        "daa": [
            {"pid": 0x123456789ABC, "bcr": 0x12, "dcr": 0x34, "address": 0x22},
            {"pid": 0xABCDEF012345, "bcr": 0x56, "dcr": 0x78, "address": 0x23},
        ],
    }]), mode="auto")
    daa = [event for event in events if event.kind == "i3c.daa"]
    assert [(event.pid, event.address, event.bcr, event.dcr) for event in daa] == [
        (0x123456789ABC, 0x22, 0x12, 0x34),
        (0xABCDEF012345, 0x23, 0x56, 0x78),
    ]
    transfer = [event for event in events if event.kind == "i3c.transfer"][0]
    assert "nack" not in transfer.errors  # final 7Eh/R NACK ends DAA normally


def test_truncated_daa_is_marked():
    wave = encode_i3c([{
        "addr": 0x7E,
        "read": False,
        "daa": {"pid": 0x123456789ABC, "bcr": 0x12, "dcr": 0x34},
    }])
    # Cut inside the continuous PID/BCR/DCR field, before the assignment
    # address is complete.
    cut = 8e-4
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
    daa = [event for event in run_i3c(truncated, mode="sdr")
           if event.kind == "i3c.daa"][0]
    assert "truncated" in daa.errors

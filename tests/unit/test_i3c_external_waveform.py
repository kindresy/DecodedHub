from pathlib import Path

import pytest

from decodehub.acquisition.adapters.kingst_csv import load
from decodehub.decode.protocols.i3c.decode import I3cDecodeNode


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data" / "external" / "i3c" / "ExampleWaveform.csv"


def test_downloaded_i3c_waveform_is_ingestable_and_reaches_i3c_decoder():
    if not FIXTURE.is_file():
        pytest.skip("optional external I3C fixture absent; see tests/data/external/i3c/README.md")
    capture = load(FIXTURE, {"sample_rate": 500_000_000})
    assert capture.digital is not None
    assert capture.digital.channels == ("scl", "sda")
    assert capture.digital.n_edges > 1_000

    params = {name: decl.default for name, decl in I3cDecodeNode.PARAMS.items()}
    params.update({"scl": "scl", "sda": "sda", "mode": "auto"})
    events = I3cDecodeNode().run({"in": capture.digital}, params)["out"]
    assert any(event.kind == "i3c.daa" for event in events)
    assert any(event.kind == "i3c.unsupported" and "hdr" in event.errors
               for event in events)

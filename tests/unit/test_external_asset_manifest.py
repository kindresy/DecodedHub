from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).parents[1] / "data" / "external"

EXPECTED = {
    "uart/hello_world_8n1_115200.sr": "e72b96efdb30ef33e1a7a2a4e1bd814cd47d5474b67a1d5adb68d4c5ad47ab24",
    "i2c/rtc_ds1307_200khz.sr": "963b2d1d34a7a1dedbbe04c59b70de305d4c19f8ae9789a8029ce2495795b96d",
    "spi/spi_0x5a_cpol0_cpha0.sr": "bf27e3dcf9dec7455aa28aa2766fdc7edfdc9c07014e9ad6d134f0229a6a5dff",
    "spi/max7219.sr": "fb740e70746d268ee697d86eaaa2502727c4d5abdfaeb6a5f7dd0df37fb26325",
    "i3c/ExampleWaveform.sr": "2429e287671243a39ae26b12aff062b3c82944d0527a4830b5c40db72c3f7ca9",
    "i3c/ExampleWaveform.csv": "42c2ec35d7da641467f941c66f3e20362e53e8fd0f3103655b29e880233332ca",
    "i3c/i3c_sdr_smoke.csv": "1dd3e28520b3652c47fb41a56f69d56a94c16453863a6a800babb3712aefca6b",
    "avsbus/avsbus_smoke.csv": "4b7e66f9fbcf39bbae589595c6ec7e3e791a6af724f1906e2e0adc9e90cf819c",
}


def test_external_assets_match_manifest() -> None:
    for relative, expected in EXPECTED.items():
        blob = (ROOT / relative).read_bytes()
        assert hashlib.sha256(blob).hexdigest() == expected

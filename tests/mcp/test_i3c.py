from __future__ import annotations

from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from decodehub.decode.synth import encode_i3c, save_kingst_csv
from decodehub.mcp_server.server import build_server


def _text(result) -> str:
    return "".join(item.text for item in result.content if item.type == "text")


@pytest.fixture
def capture(tmp_path: Path) -> Path:
    wave = encode_i3c([{"addr": 0x2A, "read": False, "data": [0x12, 0x34]}])
    path = tmp_path / "i3c.csv"
    save_kingst_csv(wave, path)
    return path


@pytest.mark.anyio
async def test_i3c_mcp_lock_decode_export_and_render(capture, tmp_path):
    async with create_connected_server_and_client_session(build_server()) as client:
        result = await client.call_tool("list_capabilities", {})
        assert "i3c" in _text(result)

        await client.call_tool("lock_source", {
            "path": str(capture),
            "options": {"alias": "bus"},
        })
        result = await client.call_tool("lock_protocol", {
            "protocol": "i3c",
            "params": {"mode": "sdr"},
        })
        assert "scl" in _text(result) and "sda" in _text(result)

        result = await client.call_tool("run_decode", {})
        assert "W 0x2A" in _text(result)

        csv_path = tmp_path / "events.csv"
        result = await client.call_tool("export_events", {
            "format": "csv",
            "path": str(csv_path),
        })
        assert csv_path.is_file()
        assert "parity" in csv_path.read_text(encoding="utf-8")
        assert "已导出" in _text(result)

        result = await client.call_tool("render_timing", {})
        assert any(item.type == "image" for item in result.content)

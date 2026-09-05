#!/usr/bin/env python
"""stdio 进程级冒烟：真实启动 decodehub-mcp，验证握手/能力/工具列表/一次调用。

用法: .venv/bin/python scripts/stdio_smoke.py
"""

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def python_executable(root: Path = ROOT) -> str:
    local = root / ".venv" / "bin" / "python"
    return str(local) if local.is_file() else sys.executable


def main() -> None:
    proc = subprocess.Popen(
        [python_executable(), "-m", "decodehub.mcp_server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, cwd=ROOT,
    )
    msg_id = 0

    def send(method: str, params: dict | None = None, notification: bool = False) -> None:
        nonlocal msg_id
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notification:
            msg_id += 1
            msg["id"] = msg_id
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()

    def read_until(predicate, timeout=15.0) -> list[dict]:
        deadline = time.time() + timeout
        msgs: list[dict] = []
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            m = json.loads(line)
            msgs.append(m)
            if predicate(m):
                return msgs
        return msgs

    # 1) initialize（声明 tools.listChanged 检查在结果里）
    send("initialize", {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "smoke", "version": "0"},
    })
    init = [m for m in read_until(lambda m: m.get("id") == 1) if "result" in m]
    assert init, "initialize 无响应"
    caps = init[0]["result"]["capabilities"]
    assert caps.get("tools", {}).get("listChanged") is True, caps
    print("✅ initialize: tools.listChanged =", caps["tools"]["listChanged"])
    send("notifications/initialized", notification=True)

    # 2) 初始工具列表 = 4
    send("tools/list")
    res = [m for m in read_until(lambda m: m.get("id") == 2) if "result" in m]
    names = sorted(t["name"] for t in res[0]["result"]["tools"])
    assert names == ["get_session", "list_capabilities", "list_profiles", "lock_source",
                         "open_project", "reset_session"], names
    print("✅ 初始工具(6):", ", ".join(names))

    # 3) 调用 list_capabilities
    send("tools/call", {"name": "list_capabilities", "arguments": {}})
    res = [m for m in read_until(lambda m: m.get("id") == 3) if "result" in m]
    text = res[0]["result"]["content"][0]["text"]
    assert "kingst_kvdat" in text and "uart" in text
    print("✅ list_capabilities 正常（包含格式与协议目录）")

    proc.stdin.close()
    proc.terminate()
    print("\nSTDIO SMOKE PASSED")


if __name__ == "__main__":
    main()

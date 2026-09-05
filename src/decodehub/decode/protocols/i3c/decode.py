"""I3C Basic v1.1.1 SDR decoder.

Only logical SCL/SDA levels are available in DigitalWave. The node therefore
decodes the observable SDR bit protocol and reports ambiguity instead of
inventing controller/target drive ownership.
"""

from __future__ import annotations

from typing import Any

from ....shared.waves import DigitalWave
from ...events import I3cEvent
from ...graph import Param
from ...registry import register

T_BUF = 1.3e-6
T_BUF_PURE = 38.4e-9

CCC_NAMES: dict[int, str] = {
    0x00: "ENEC",
    0x01: "DISEC",
    0x02: "ENTAS0",
    0x03: "ENTAS1",
    0x04: "ENTAS2",
    0x05: "ENTAS3",
    0x06: "RSTDAA",
    0x07: "ENTDAA",
    0x08: "DEFSLVS",
    0x09: "SETMWL",
    0x0A: "SETMRL",
    0x8B: "GETMWL",
    0x8C: "GETMRL",
    0x8D: "GETPID",
    0x8E: "GETBCR",
    0x8F: "GETDCR",
    0x20: "ENTHDR0",
    0x21: "ENTHDR1",
    0x22: "ENTHDR2",
    0x23: "ENTHDR3",
    0x24: "ENTHDR4",
    0x25: "ENTHDR5",
    0x26: "ENTHDR6",
    0x27: "ENTHDR7",
    0x90: "GETSTATUS",
    0x91: "GETACCCR",
}
READ_CCCS = {0x8B, 0x8C, 0x8D, 0x8E, 0x8F, 0x90, 0x91}


def _odd_parity(byte: int) -> int:
    return 1 ^ (int(byte).bit_count() & 1)


def _hex(values: list[int]) -> str:
    return " ".join(f"{value:02X}" for value in values)


def _transfer_label(tr: dict[str, Any]) -> str:
    address = tr["address"]
    if address is None:
        return f"?[{_hex(tr['data'])}]"
    if tr["mode"] == "ccc":
        name = tr["ccc_name"] or "CCC"
        return f"CCC {name} [{_hex(tr['data'])}]"
    if tr["mode"] == "daa":
        return f"DAA [{_hex(tr['data'])}]"
    direction = "R" if tr["read"] else "W"
    return f"{direction} 0x{address:02X} [{_hex(tr['data'])}]"


@register
class I3cDecodeNode:
    TYPE = "i3c_decode"
    INPUTS = {"in": "digital"}
    OUTPUTS = {"out": "events"}
    PARAMS = {
        "scl": Param("str", default="", doc="SCL 通道名（空 = 第 1 通道）"),
        "sda": Param("str", default="", doc="SDA 通道名（空 = 第 2 通道）"),
        "mode": Param("enum", default="auto",
                      choices=("auto", "sdr", "legacy_i2c"),
                      doc="解码模式：auto、I3C SDR 或 legacy I2C"),
        "bus_profile": Param("enum", default="auto",
                              choices=("auto", "pure", "mixed"),
                              doc="总线空闲规则：auto 不作确定性告警，pure/mixed 使用对应阈值"),
        "stretch_warn_s": Param("float_pos", default=1e-3,
                                doc="SCL 低电平持续超过该值告警（秒）"),
    }

    def run(self, inputs: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        wave: DigitalWave = inputs["in"]
        names = list(wave.channels)
        scl = params.get("scl") or (names[0] if names else "")
        sda = params.get("sda") or (names[1] if len(names) > 1 else "")
        mode = str(params.get("mode") or "auto")
        bus_profile = str(params.get("bus_profile") or "auto")
        stretch_warn_s = float(params.get("stretch_warn_s", 1e-3))
        if mode not in {"auto", "sdr", "legacy_i2c"}:
            raise ValueError(f"未知 I3C 解码模式: {mode!r}")
        if bus_profile not in {"auto", "pure", "mixed"}:
            raise ValueError(f"未知 I3C bus_profile: {bus_profile!r}")
        for channel in (scl, sda):
            if channel not in names:
                raise ValueError(f"通道 {channel!r} 不存在；可用: {names}")
        if scl == sda:
            raise ValueError("SCL 与 SDA 不能是同一通道")

        scl_t, scl_lv = wave.edge_stream(scl)
        sda_t, sda_lv = wave.edge_stream(sda)
        scl_rises = [float(t) for t, level in zip(scl_t, scl_lv) if int(level) == 1]
        scl_falls = [float(t) for t, level in zip(scl_t, scl_lv) if int(level) == 0]
        events: list[I3cEvent] = []

        in_transfer = False
        tr: dict[str, Any] = {}
        bit_count = 0
        shift = 0
        byte_t0 = wave.t_start
        fall_idx = 0
        last_stop_t: float | None = None

        def sda_at(t: float) -> int:
            return wave.level_at(sda, t)

        def scl_at(t: float) -> int:
            return wave.level_at(scl, t)

        def new_transfer(t: float) -> dict[str, Any]:
            return {
                "t0": t,
                "address": None,
                "expect_address": True,
                "read": None,
                "mode": "unknown" if mode == "auto" else mode,
                "data": [],
                "acks": [],
                "parity": [],
                "t_bits": [],
                "errors": set(),
                "ccc": None,
                "ccc_name": None,
                "daa_pending": False,
                "daa_state": "idle",
                "daa_bits": [],
                "daa_assign_bits": [],
                "daa_current": [],
                "daa_target_t0": None,
                "daa_expected_nack": False,
                "daa_completion": None,
                "daa_post_completion_rise": False,
                "read_ended": False,
                "pid": None,
                "bcr": None,
                "dcr": None,
                "unsupported": False,
            }

        def append_event(
            kind: str,
            t0: float,
            t1: float,
            label: str,
            *,
            errors: list[str] | None = None,
            ann_class: str = "data",
            **fields: Any,
        ) -> I3cEvent:
            event = I3cEvent(
                kind, t0, t1, label,
                errors=list(errors or []),
                ann_class=ann_class,
                mode=fields.pop("mode", tr.get("mode", "unknown")),
                address=fields.pop("address", tr.get("address")),
                read=fields.pop("read", tr.get("read")),
                data_bytes=list(fields.pop("data_bytes", [])),
                acks=list(fields.pop("acks", [])),
                parity_ok=list(fields.pop("parity_ok", [])),
                t_bits=list(fields.pop("t_bits", [])),
                ccc=fields.pop("ccc", tr.get("ccc")),
                ccc_name=fields.pop("ccc_name", tr.get("ccc_name")),
                pid=fields.pop("pid", tr.get("pid")),
                bcr=fields.pop("bcr", tr.get("bcr")),
                dcr=fields.pop("dcr", tr.get("dcr")),
            )
            events.append(event)
            return event

        def emit_daa_completion(extra_errors: list[str] | None = None) -> None:
            completion = tr.get("daa_completion")
            if not completion:
                return
            errors = list(completion["errors"])
            for error in extra_errors or []:
                if error not in errors:
                    errors.append(error)
            append_event(
                "i3c.daa", completion["t0"], completion["t1"],
                f"DAA PID=0x{completion['pid']:012X} -> 0x{completion['address']:02X}",
                errors=errors,
                ann_class="data" if not errors else "warn",
                mode="daa", address=completion["address"], read=True,
                data_bytes=list(completion["data"]), acks=[completion["ack"]],
                parity_ok=[completion["parity_ok"]], pid=completion["pid"],
                bcr=completion["bcr"], dcr=completion["dcr"], t_bits=[],
            )
            tr["address"] = completion["address"]
            tr["pid"], tr["bcr"], tr["dcr"] = (
                completion["pid"], completion["bcr"], completion["dcr"]
            )
            tr["daa_completion"] = None
            tr["daa_state"] = "await_address"
            tr["daa_post_completion_rise"] = False

        def finish_daa(end_t: float, truncated: bool = False,
                       stop_boundary: bool = False) -> None:
            if not tr.get("daa_pending") or tr.get("daa_state") in {"idle", "done", "await_address"}:
                return
            if tr.get("daa_state") == "completed_pending":
                emit_daa_completion(["ambiguous-stop-bit"] if stop_boundary else None)
                return
            daa_data = list(tr["daa_current"])
            raw_bits = tr["daa_bits"]
            for offset in range(0, len(raw_bits) - 7, 8):
                value = 0
                for bit in raw_bits[offset:offset + 8]:
                    value = (value << 1) | bit
                if len(daa_data) < 8:
                    daa_data.append(value)
            errors: list[str] = ["incomplete-daa"]
            tr["errors"].add("incomplete-daa")
            if truncated:
                errors.append("truncated")
            if stop_boundary:
                errors.append("ambiguous-stop-bit")
                tr["errors"].add("ambiguous-stop-bit")
            append_event(
                "i3c.daa",
                tr.get("daa_target_t0") or tr["t0"],
                end_t,
                f"DAA PID={tr['pid'] if tr['pid'] is not None else '?'}",
                errors=errors,
                ann_class="data" if not errors else "warn",
                mode="daa",
                address=tr.get("address"),
                data_bytes=daa_data,
                t_bits=[],
                pid=tr.get("pid"),
                bcr=tr.get("bcr"),
                dcr=tr.get("dcr"),
            )
            tr["daa_pending"] = False
            tr["daa_state"] = "idle"

        def finish_transfer(end_t: float, truncated: bool = False) -> None:
            if not in_transfer or tr.get("unsupported"):
                return
            errors = set(tr["errors"])
            if truncated:
                errors.add("truncated")
            if any(ack is False and not (tr.get("daa_expected_nack") and i == len(tr["acks"]) - 1)
                   for i, ack in enumerate(tr["acks"])):
                errors.add("nack")
            if any(ok is False for ok in tr["parity"]):
                errors.add("parity")
            if mode == "auto" and tr["mode"] == "unknown":
                errors.add("ambiguous")
            ordered_errors = sorted(errors)
            append_event(
                "i3c.transfer",
                tr["t0"],
                end_t,
                _transfer_label(tr),
                errors=ordered_errors,
                ann_class="err" if ordered_errors else "data",
                mode=tr["mode"],
                address=tr["address"],
                read=tr["read"],
                data_bytes=tr["data"],
                acks=tr["acks"],
                parity_ok=tr["parity"],
                t_bits=tr["t_bits"],
                ccc=tr["ccc"],
                ccc_name=tr["ccc_name"],
                pid=tr["pid"],
                bcr=tr["bcr"],
                dcr=tr["dcr"],
            )

        def handle_start(t: float) -> None:
            nonlocal in_transfer, tr, bit_count, shift, last_stop_t
            threshold = {"pure": T_BUF_PURE, "mixed": T_BUF}.get(bus_profile)
            if threshold is not None and last_stop_t is not None and t - last_stop_t < threshold:
                append_event("i3c.warn", last_stop_t, t, "总线空闲违例",
                             errors=["bus-free"], ann_class="warn")
            if in_transfer:
                if tr.get("unsupported"):
                    # HDR payload is intentionally opaque. Ignore any SDA
                    # transitions until STOP instead of feeding them into the
                    # SDR START/address FSM.
                    bit_count, shift = 0, 0
                    return
                if tr.get("daa_pending") and tr.get("daa_state") in {"payload", "assignment"}:
                    # A repeated START during a DAA round terminates the
                    # malformed/incomplete round before opening the next
                    # address phase.
                    finish_daa(t)
                    tr["daa_pending"] = True
                    tr["daa_state"] = "await_address"
                    tr["daa_bits"] = []
                    tr["daa_assign_bits"] = []
                    tr["daa_current"] = []
                    tr["daa_target_t0"] = None
                elif tr.get("daa_pending") and tr.get("daa_state") == "completed_pending":
                    emit_daa_completion()
                append_event("i3c.repeat-start", t, t, "Sr", ann_class="start")
                tr["expect_address"] = True
                tr["read_ended"] = False
            else:
                append_event("i3c.start", t, t, "S", ann_class="start")
                tr = new_transfer(t)
                in_transfer = True
            bit_count, shift = 0, 0

        def handle_stop(t: float) -> None:
            nonlocal in_transfer, bit_count, shift, last_stop_t
            if in_transfer:
                # A controller may lower and raise SCL while preparing SDA for
                # STOP. That conditioning edge looks like one leading zero
                # bit, but it is not a partial data word.
                if bit_count and not (
                    bit_count == 1 and not tr["expect_address"] and shift == 0
                ):
                    tr["errors"].add("partial-byte")
                    append_event("i3c.warn", byte_t0, t, "不完整字节",
                                 errors=["partial-byte"], ann_class="warn")
                finish_daa(t, stop_boundary=bool(
                    tr.get("daa_pending")
                    and (
                        tr.get("daa_state") in {"payload", "assignment"}
                        or (
                            tr.get("daa_state") == "completed_pending"
                            and not tr.get("daa_post_completion_rise")
                        )
                    )
                ))
                finish_transfer(t)
                append_event("i3c.stop", t, t, "P", ann_class="stop")
                in_transfer = False
            else:
                append_event("i3c.stop", t, t, "P(孤立)",
                             errors=["spurious"], ann_class="warn")
            bit_count, shift = 0, 0
            last_stop_t = t

        # Both streams are already sorted. Merge with two cursors so memory
        # stays O(E) and equal-time SDA edges win over SCL sampling.
        sda_idx = scl_idx = 0
        while sda_idx < len(sda_t) or scl_idx < len(scl_rises):
            take_sda = scl_idx >= len(scl_rises) or (
                sda_idx < len(sda_t) and float(sda_t[sda_idx]) <= scl_rises[scl_idx]
            )
            if take_sda:
                t, kind, payload = float(sda_t[sda_idx]), "sda", int(sda_lv[sda_idx])
                sda_idx += 1
            else:
                t, kind, payload = float(scl_rises[scl_idx]), "scl_rise", None
                scl_idx += 1
            if kind == "sda":
                if in_transfer and tr.get("unsupported"):
                    # HDR payload has no SDR exit detector yet. Keep the
                    # remainder opaque through capture end: interpreting an
                    # SDA-high transition as STOP (or a later fall as START)
                    # would create fabricated SDR transfers.
                    continue
                if scl_at(t) != 1:
                    continue
                if payload == 0:
                    handle_start(t)
                else:
                    handle_stop(t)
                continue

            while fall_idx < len(scl_falls) and scl_falls[fall_idx] < t:
                falling = scl_falls[fall_idx]
                if t - falling > stretch_warn_s:
                    append_event("i3c.warn", falling, t, "时钟拉伸",
                                 errors=["clock-stretch"], ann_class="warn")
                fall_idx += 1
            if not in_transfer:
                continue
            sampled = sda_at(t)
            if tr.get("unsupported"):
                continue

            # ENTDAA's PID/BCR/DCR and dynamic-address fields are a
            # continuous bit stream. Consume them before the normal 8+1 SDR
            # accumulator, otherwise every ninth arbitration bit would be
            # mistaken for a parity/T bit.
            if tr.get("daa_pending") and not tr["expect_address"]:
                if tr["daa_state"] == "payload":
                    if not tr["daa_bits"]:
                        tr["daa_target_t0"] = t
                    tr["daa_bits"].append(sampled)
                    if len(tr["daa_bits"]) == 64:
                        raw = tr["daa_bits"]
                        values: list[int] = []
                        for offset in range(0, 64, 8):
                            value = 0
                            for b in raw[offset:offset + 8]:
                                value = (value << 1) | b
                            values.append(value)
                        tr["daa_current"] = values
                        tr["pid"] = int.from_bytes(bytes(values[:6]), "big")
                        tr["bcr"], tr["dcr"] = values[6], values[7]
                        tr["daa_assign_bits"] = []
                        tr["daa_state"] = "assignment"
                    continue
                if tr["daa_state"] == "assignment":
                    tr["daa_assign_bits"].append(sampled)
                    if len(tr["daa_assign_bits"]) == 9:
                        bits = tr["daa_assign_bits"]
                        address = 0
                        for b in bits[:7]:
                            address = (address << 1) | b
                        parity_ok = bits[7] == _odd_parity(address)
                        assigned_ack = bits[8] == 0
                        daa_errors: list[str] = []
                        if not parity_ok:
                            daa_errors.append("parity")
                            tr["errors"].add("parity")
                        if not assigned_ack:
                            daa_errors.append("nack")
                            tr["errors"].add("nack")
                        tr["daa_completion"] = {
                            "t0": tr["daa_target_t0"] or t,
                            "t1": t,
                            "address": address,
                            "data": list(tr["daa_current"]),
                            "pid": tr["pid"],
                            "bcr": tr["bcr"],
                            "dcr": tr["dcr"],
                            "parity_ok": parity_ok,
                            "ack": assigned_ack,
                            "errors": daa_errors,
                        }
                        tr["daa_state"] = "completed_pending"
                        tr["daa_post_completion_rise"] = False
                        tr["daa_bits"] = []
                        tr["daa_assign_bits"] = []
                    continue
                if tr["daa_state"] == "completed_pending":
                    # A legal DAA round is followed by Sr or STOP. A rising
                    # SCL edge before either boundary is STOP conditioning;
                    # remember it so a subsequent STOP is not ambiguous.
                    tr["daa_post_completion_rise"] = True
                    continue
                # await_address/done: wait for a repeated START and address.
                continue

            if bit_count < 8:
                if bit_count == 0:
                    byte_t0 = t
                shift = (shift << 1) | sampled
                bit_count += 1
                continue

            byte = shift
            bit_count, shift = 0, 0
            if tr["expect_address"]:
                ack = sampled == 0
                tr["address"] = byte >> 1
                tr["read"] = bool(byte & 1)
                tr["expect_address"] = False
                tr["acks"].append(ack)
                daa_final_probe = (
                    tr.get("daa_pending") and tr["daa_state"] == "await_address"
                    and tr["address"] == 0x7E and tr["read"] and not ack
                )
                if not ack and not daa_final_probe:
                    tr["errors"].add("nack")
                label = f"0x{tr['address']:02X} {'R' if tr['read'] else 'W'}"
                append_event(
                    "i3c.addr", byte_t0, t, label,
                    errors=[] if ack or daa_final_probe else ["nack"],
                    ann_class="start" if ack or daa_final_probe else "warn",
                    mode=tr["mode"],
                    address=tr["address"],
                    read=tr["read"],
                    acks=[ack],
                )
                if tr.get("daa_pending") and tr["daa_state"] == "await_address":
                    if tr["address"] == 0x7E and tr["read"] and ack:
                        tr["daa_state"] = "payload"
                        tr["daa_bits"] = []
                        tr["daa_assign_bits"] = []
                        tr["daa_current"] = []
                    elif tr["address"] == 0x7E and tr["read"] and not ack:
                        # A NACK on 7Eh/R is the normal end-of-arbitration
                        # probe, not a failed data transfer.
                        tr["daa_expected_nack"] = True
                        tr["daa_state"] = "done"
                continue

            # A passive two-wire capture cannot distinguish a private data
            # byte from a direct CCC defining byte. Only the reserved 7Eh
            # broadcast header opens an unambiguous CCC context.
            ccc_defining = not tr["data"] and tr["address"] == 0x7E
            tr["data"].append(byte)
            tr["acks"].append(None)
            tr["parity"].append(None)
            tr["t_bits"].append(None)
            is_read = bool(tr["read"]) and not ccc_defining
            if tr["mode"] == "legacy_i2c" or (mode == "legacy_i2c"):
                ack = sampled == 0
                tr["acks"][-1] = ack
                label = f"0x{byte:02X} ({'ACK' if ack else 'NAK'})"
                if not ack:
                    tr["errors"].add("nack")
                append_event(
                    "i3c.data", byte_t0, t, label,
                    errors=[] if ack else ["nack"],
                    ann_class="ack" if ack else "warn",
                    mode="legacy_i2c", address=tr["address"], read=tr["read"],
                    data_bytes=[byte], acks=[ack], parity_ok=[None], t_bits=[None],
                )
                tr["mode"] = "legacy_i2c"
                continue

            if is_read:
                after_tbit = tr.get("read_ended", False)
                if mode == "sdr":
                    tr["mode"] = "sdr"
                elif tr["mode"] not in {"ccc", "daa"}:
                    tr["mode"] = "unknown"
                tr["t_bits"][-1] = sampled
                if sampled == 0:
                    tr["read_ended"] = True
                if after_tbit:
                    tr["errors"].add("after-tbit")
                append_event(
                    "i3c.data", byte_t0, t, f"0x{byte:02X} T{sampled}",
                    errors=["after-tbit"] if after_tbit else [],
                    ann_class="warn" if after_tbit else "data", mode=tr["mode"],
                    address=tr["address"],
                    read=True, data_bytes=[byte], acks=[None],
                    parity_ok=[None], t_bits=[sampled],
                )
                continue

            parity_ok = sampled == _odd_parity(byte)
            if mode == "sdr" or tr["mode"] in {"ccc", "daa"}:
                tr["mode"] = tr["mode"] if tr["mode"] in {"ccc", "daa"} else "sdr"
            else:
                tr["mode"] = "unknown"
            tr["parity"][-1] = parity_ok
            data_errors = [] if parity_ok else ["parity"]
            if not parity_ok:
                tr["errors"].add("parity")
            append_event(
                "i3c.data", byte_t0, t, f"0x{byte:02X} ({'P' if parity_ok else 'P!'} )",
                errors=data_errors, ann_class="data" if parity_ok else "warn",
                mode="ccc" if ccc_defining else tr["mode"],
                address=tr["address"], read=False,
                data_bytes=[byte], acks=[None], parity_ok=[parity_ok],
                t_bits=[None],
            )

            if ccc_defining:
                tr["ccc"] = byte
                tr["ccc_name"] = CCC_NAMES.get(byte)
                tr["mode"] = "ccc"
                if tr["ccc_name"] is None:
                    tr["errors"].add("unknown-ccc")
                append_event(
                    "i3c.ccc", byte_t0, t,
                    tr["ccc_name"] or f"CCC 0x{byte:02X}",
                    errors=[] if tr["ccc_name"] else ["unknown-ccc"],
                    ann_class="start" if tr["ccc_name"] else "warn",
                    mode="ccc", address=tr["address"], read=False,
                    data_bytes=[byte], ccc=byte, ccc_name=tr["ccc_name"],
                )
                if byte == 0x07:
                    tr["daa_pending"] = True
                    tr["mode"] = "daa"
                    tr["daa_state"] = "await_address"
                elif byte in READ_CCCS:
                    tr["read"] = True
                elif byte in {0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27}:
                    tr["mode"] = "unsupported"
                    tr["errors"].add("hdr")
                    tr["unsupported"] = True
                    append_event(
                        "i3c.unsupported", byte_t0, t,
                        f"{tr['ccc_name']}（HDR）",
                        errors=["hdr"], ann_class="warn", mode="unsupported",
                        address=tr["address"], read=False, data_bytes=[byte],
                        ccc=byte, ccc_name=tr["ccc_name"],
                    )

        if in_transfer:
            if bit_count:
                tr["errors"].add("partial-byte")
                append_event("i3c.warn", byte_t0, wave.t_end, "不完整字节",
                             errors=["partial-byte"], ann_class="warn")
            finish_daa(wave.t_end, truncated=True)
            finish_transfer(wave.t_end, truncated=True)

        events.sort(key=lambda event: (event.t_start, event.t_end, event.kind))
        return {"out": events}

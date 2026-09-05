"""AVSBus 呈现注册。"""

from ...events import AvsBusEvent
from ...presentation import Presentation, register_presentation


def _data(ev: AvsBusEvent):
    return f"0x{ev.cmd_data:04X} / 0x{ev.response_data:04X}"


def _crc(ev: AvsBusEvent):
    return f"{'OK' if ev.main_crc_ok else 'BAD'}/{'OK' if ev.response_crc_ok else 'BAD'}"


register_presentation(Presentation(
    protocol="avsbus",
    kind_cn={
        "avsbus.frame": "AVSBus·帧",
        "avsbus.resync": "AVSBus·重同步",
        "avsbus.warn": "AVSBus!",
    },
    detail_fn=lambda ev: ev.label,
    event_fields=("mode", "raw_mdata", "raw_sdata", "start_code", "cmd", "command",
                  "cmd_group", "cmd_data_type", "select", "cmd_data", "response_data",
                  "slave_ack", "status_resp", "main_crc", "response_crc", "main_crc_ok",
                  "response_crc_ok"),
    csv_columns=(
        ("command", lambda ev: ev.command),
        ("cmd_data_type", lambda ev: f"0x{ev.cmd_data_type:X}"),
        ("select", lambda ev: f"0x{ev.select:X}"),
        ("cmd_data", _data),
        ("slave_ack", lambda ev: ev.slave_ack),
        ("status_resp", lambda ev: f"0x{ev.status_resp:02X}"),
        ("crc", _crc),
        ("raw_mdata", lambda ev: f"0x{ev.raw_mdata:08X}"),
        ("raw_sdata", lambda ev: f"0x{ev.raw_sdata:08X}"),
    ),
    preview_kinds=("avsbus.frame", "avsbus.warn", "avsbus.resync"),
))

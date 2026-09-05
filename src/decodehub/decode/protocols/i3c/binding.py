"""I3C protocol binding (SDR two-wire digital captures)."""

from __future__ import annotations

from ...bindings import ProtocolBinding, register_binding


register_binding(ProtocolBinding(
    protocol="i3c",
    node_type="i3c_decode",
    roles=("scl", "sda"),
    needs={"min_digital": 2},
    hint="I3C SDR 两线总线；缺省第 1/2 个数字通道作 SCL/SDA",
    decoder_params=("mode", "bus_profile", "stretch_warn_s"),
))

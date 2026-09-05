"""SPI 协议绑定（ADR-014）：角色/需求/参数路由/图模板声明。"""

from __future__ import annotations

from ...bindings import ProtocolBinding, register_binding

register_binding(ProtocolBinding(
    protocol="spi",
    node_type="spi_decode",
    roles=("clk", "mosi", "miso", "cs"),
    optional_roles=("mosi", "miso", "cs"),
    require_any=(("mosi", "miso"),),
    needs={"min_digital": 2},
    hint="四线同步总线（MISO 可省）；缺省第 1/2/3/4 个数字通道作 CLK/MOSI/MISO/CS",
    decoder_params=("cpol", "cpha", "word_bits", "bit_order", "cs_active"),
))

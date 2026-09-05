"""AVSBus binding（阶段 B）。"""

from ...bindings import ProtocolBinding, register_binding

register_binding(ProtocolBinding(
    protocol="avsbus",
    node_type="avsbus_decode",
    roles=("clock", "mdata", "sdata"),
    needs={"min_digital": 3},
    hint="PMBus/SMIF AVSBus 三线同步总线；缺省第 1/2/3 个数字通道作 Clock/MData/SData",
    decoder_params=("mode", "frame_bits"),
))

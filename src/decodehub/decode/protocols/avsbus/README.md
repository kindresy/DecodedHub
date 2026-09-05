# AVSBus

这是 PMBus/SMIF Adaptive Voltage Scaling Bus 的离线被动解码器。输入为
`AVS_Clock`、`AVS_MData`、`AVS_SData` 三条数字通道；每 32 个时钟输出一个
controller/target 子帧组合事件，解析 StartCode、Cmd、CmdGroup、CmdDataType、
Select、CmdData、TargetAck、StatusResp、CRC-3 和原始 32-bit 字。CRC 使用
`x³+x+1`，校验错误、保留位、状态和重同步均作为事件错误/告警输出。

公开 Quick Guide 将 voltage、transition rate、current、temperature、power mode、
status、version 等 CmdDataType 列出；具体制造商扩展仍保留数值而不臆测名称。
当前实现针对 SDR/逻辑数字波形，不声称解码 HDR 电气所有权或模拟边沿质量。

协议字段/时序依据 PMBus 当前规范索引及公开 AVSBus Quick Guide；CRC 实现与
OpenPOWER OCC 的 AVSBus 参考代码交叉校验：

- [PMBus current specifications](https://pmbus.org/current-specifications/)
- [AVSBus Quick Guide rev. 1.4 mirror](https://www.scribd.com/document/1022002667/AVSBus-Quick-Guide-rev-1-4-2021-04)
- [OpenPOWER OCC AVSBus implementation](https://github.com/open-power/occ/blob/master/src/occ_405/pss/avsbus.c)

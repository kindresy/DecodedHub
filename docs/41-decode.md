# 41 · 解码上下文（Decode）—— 图引擎与协议算法

> 状态：已定案 · C3 核心域 · 算法 essence 参照 sigrok libsigrokdecode（UART/I2C/SPI pd.py）与 Saleae Analyzer 数据模型，独立实现

## 1. 图引擎

见 `30-architecture.md` 的端口/节点契约/验证/求值规范。本文件补充**节点目录**与**协议算法**。

### 节点目录（v1）

| type              | 输入端口                         | 输出端口           | 参数                                                                                                                          | 语义                                                                                                   |      |
| ----------------- | ---------------------------- | -------------- | --------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- | ---- |
| `digital_pick`    | `in: capture`                | `out: digital` | `channels: list[str]`（空=全部数字通道）                                                                                             | 抽取数字子集（`select` 重掩码）                                                                                 |      |
| `analog_pick`     | `in: capture`                | `out: analog`  | `channels: list[str]`（空=全部）                                                                                                 | 抽取模拟通道列表                                                                                             |      |
| `slicer`          | `in: analog`                 | `out: digital`；`threshold: scalar`（切片所用阈值，供应用层回写 meta） | `threshold: float?`、`hysteresis: float=0.2×幅值`、`names: list[str]?`                                                          | Schmitt 切片（**向量化滞回**：仅对越过上/下阈值的定态样本做相邻差分，死区样本继承前态；O(n) 时间、O(定态数) 内存）；threshold 缺省 = (Vmin+Vmax)/2；每路模拟→1 个数字通道（名字继承或由 names 指定）；阈值经 scalar 端口输出、应用层回写 `meta.threshold_v`        |      |
| `uart_decode`     | `in: digital`                | `out: events`  | `rx: str`（角色→通道名映射，图模板已解析为具体名）、`baud: float                                                                                 | "auto"`、`data_bits: 5..9=8`、`parity: N/O/E`、`stop_bits: 1/1.5/2`、`invert: bool`、`bit_order: lsb/msb` | §3.1 |
| `i2c_decode`      | `in: digital`                | `out: events`  | `scl, sda: str`、`stretch_warn_s: float=1e-3`                                                                                | §3.2                                                                                                 |      |
| `spi_decode`      | `in: digital`                | `out: events`  | `clk: str`、`cs/miso/mosi: str?`、`cpol: 0/1`、`cpha: 0/1`、`word_bits: 1..32=8`、`bit_order: lsb/msb`、`cs_active: low/high=low` | §3.3                                                                                                 |      |
| `i3c_decode`      | `in: digital`                | `out: events`  | `scl, sda: str`、`mode: auto/sdr/legacy_i2c`、`bus_profile: auto/pure/mixed`                                                   | I3C Basic SDR：奇校验/T-bit、CCC、ENTDAA、HDR 明确标记 unsupported                                             |      |
| `avsbus_decode`   | `in: digital`                | `out: events`  | `clk, mdata, sdata: str`、`sample_edge: rising/falling`                                                                       | 每 32 clocks 解 controller/target 子帧、命令字段、状态与 CRC-3；错误后按帧边界恢复                                  |      |
| `event_filter`    | `in: events`                 | `out: events`  | `kinds: list[str]?`、`t_min/t_max: float?`、`has_errors: bool?`                                                               | 时间窗 + 类型 + 错误过滤                                                                                      |      |
| `uplink_precond`  | `in: analog`                 | `out: analog`  | `channel: str`、`profile: str`、`chip_s: float?`                                                                              | 上行 DSSS 预条件：抽取到 ~12 样点/chip + 1ms 滑动均值 HPF 剥离 60Hz 包络（ADR-010）                                          |      |
| `uplink_decode`   | `in: analog`                 | `out: events`  | 同上 + `invert/unipolar/msb_first/pn_word/pn_len/pream/data_bits`                                                             | 上行 DSSS：PN 相关解扩 → 梳齿符号 → 能量分段 → 帧同步 → 码片速率仲裁（§3.4）；纯噪声诚实拒绝                                        |      |
| `downlink_decode` | `in: analog`, `sync: events` | `out: events`  | `channel/profile/fc_nominal/cycles_per_bit/n_bits/slot_offsets_us/frame_hz/invert`                                          | 下行 DBPSK：以上行帧为锚的槽位包（§3.5）；**首个双输入解码节点（扇入）**                                                       |      |

角色名（`rx/scl/sda/...`）在**图模板构建期**（应用层）就被解析为具体通道名写入节点参数——图内不存在运行期名字查找，保证图的纯函数性与可序列化。

## 2. 事件模型（发布语言）

```python
@dataclass
class DecodedEvent:                 # 一切解码器的事件基类
    kind: str                       # "uart.frame" | "i2c.start" | "i2c.transfer" | "spi.word" | ...
    t_start: float                  # 秒
    t_end: float
    label: str                      # 短人类可读（"0x55"、"W 0x51"、"ACK"）
    errors: list[str] = []          # "parity"/"framing"/"break"/"truncated"/"nack"/...
    ann_class: str = "data"         # 渲染提示: start/stop/data/ack/warn/err

@dataclass
class UartEvent(DecodedEvent):      # kind="uart.frame"
    value: int = 0
    parity: str = "N"; data_bits: int = 8

@dataclass
class I2cEvent(DecodedEvent):
    # kind ∈ i2c.start / i2c.repeat-start / i2c.stop / i2c.addr / i2c.data / i2c.transfer
    address: int | None = None; is_10bit: bool = False
    read: bool | None = None
    data_bytes: list[int] = []; acks: list[bool] = []   # transfer 级
    byte_index: int = 0                                  # data 级

@dataclass
class SpiEvent(DecodedEvent):       # kind ∈ spi.word / spi.transfer
    mosi: int | None = None; miso: int | None = None
    word_bits: int = 8; words: list[tuple[int, int]] = []  # transfer 级 [(mosi,miso),…]

@dataclass
class DecodeReport:
    protocol: str; params: dict
    events: list[DecodedEvent]      # 全局时间有序（Saleae 不变量）
    node_id: str; wall_ms: float
    def counts(self) -> dict        # kind → 数量；errors 总数
```

I2C 同时输出细粒度事件（start/addr/data，供绘图标注）与**传输级汇总事件**（`i2c.transfer`：地址、方向、数据、逐字节 ACK，供表格）——这是 sigrok "多注释行"经验。SPI 同理（`spi.word` + `spi.transfer`）。

## 3. 协议模块与编解码原理（ADR-012）

> **一协议一目录**：`src/decodehub/decode/protocols/<协议>/{decode.py, encode.py, binding.py, present.py, README.md}`（绑定声明见 ADR-014：角色/需求/参数路由/锚依赖；图模板统一由 `decode/bindings.py` 构建）。
> 编码器（合成/往返测试的编码方向）与解码器同目录成对；**编解码原理文档随代码走**
> （波形模型、发送侧编码、接收侧算法、参数、事件、测试锚点），本节只留索引。

| 协议 | 模块 | 原理文档 | 数据通路 |
|---|---|---|---|
| UART | `protocols/uart/` | [README](../src/decodehub/decode/protocols/uart/README.md) | digital（跳变流） |
| I2C | `protocols/i2c/` | [README](../src/decodehub/decode/protocols/i2c/README.md) | digital（2ch 位域） |
| SPI | `protocols/spi/` | [README](../src/decodehub/decode/protocols/spi/README.md) | digital（2–4ch） |
| I3C | `protocols/i3c/` | [README](../src/decodehub/decode/protocols/i3c/README.md) | digital（SCL/SDA；Basic SDR） |
| AVSBus | `protocols/avsbus/` | [README](../src/decodehub/decode/protocols/avsbus/README.md) | digital（clock/MData/SData） |
| 上行 DSSS | `protocols/uplink/` | [README](../src/decodehub/decode/protocols/uplink/README.md) | **analog 直达**（含 vendored dsss.py） |
| 下行 DBPSK | `protocols/downlink/` | [README](../src/decodehub/decode/protocols/downlink/README.md) | **analog + events 扇入**（含 vendored dpsk.py） |

共性裁决：解码全部**跳变/事件驱动**（ADR-005）；解码错误是事件字段（ADR-004）；
上/下行协议形状参数全量可配、默认值 = 档案预设而非常量（ADR-011）；
公共辅助（模拟通道挑选/均匀性校验）统一走 `protocols/_shared.py`，协议间不得私有互导（ADR-012）；
图模板/角色/参数路由经协议绑定声明（ADR-014），应用层只做会话编排。

I3C 当前识别 ENEC、DISEC、ENTAS0–3、RSTDAA、ENTDAA、DEFSLVS、SETMWL、
SETMRL、GETMWL、GETMRL、GETPID、GETBCR、GETDCR、ENTHDR0–7、GETSTATUS、
GETACCCR。被动两线波形无法证明未知私有传输必为 I3C，`mode=auto` 会诚实标为
ambiguous；HDR-DDR/TSP/TSL/BT 只发 unsupported 事件，不推测电气驱动所有权。

所有事件与报告携带 `schema_version="1.0"`。JSON 在序列化时执行 kind、协议
字段和错误码校验；CSV 同样输出版本列；Markdown 对未知第三方事件保留可读
兜底，方便排障但不把它误当成已通过机器契约。

## 4. 合成波形发生器（`synth.py`，测试矢量的编码方向）

```
encode_uart(bytes, baud, nd, parity, stop, invert, order,
            jitter_ui=0, drift_ppm=0, idle_gap_bits=…) → DigitalWave
encode_i2c(transactions[(addr,rw,bytes…)], freq,
           stretch_s=0, repeat_start=…) → DigitalWave(2ch: scl,sda)
encode_spi(words, freq, cpol, cpha, cs_every=…) → DigitalWave(2..4ch)
analogify(digital, v_low=0, v_high=3.3, rise_s=…, noise_σ=…) → AnalogChannel
encode_uplink(frames_data, fs, ppm/snr_db/env/period_s=16.67ms,
              pn_word/pn_len/pream/data_bits_n…) → AnalogChannel
  （上行 DSSS：PN 扩频 NRZ + 60Hz 包络 + 噪声 + 时钟偏差注入）
encode_downlink(frame_starts, packets_per_frame, fs, fc/delta_s/slot_period/
              cycles_per_bit/snr_db…) → AnalogChannel
  （下行 DBPSK：以上行帧为锚的槽位包；1=相位翻转）
```

注入轴：边沿抖动（UI 单位）、收发时钟漂移（ppm）、模拟化噪声/摆率/过冲——支撑鲁棒性属性测试（往返零误差 + 带扰动容差）。

## 5. 测试矩阵摘要

完整矩阵（U1–U10 / I1–I6 / S1–S8 / E1 / G1）见 `60-testing.md`。核心不变量：

1. **往返**：`decode(encode(bytes, cfg), cfg) == bytes`（对随机字节流 × 配置矩阵）。
2. **时间有序**：任何解码输出事件按 `t_start` 全序。
3. **部分结果**：截断/坏片段不中断解码，恢复点后事件完整。
4. **图级**：环/类型失配/缺输入/未知参数在构建期被拒。

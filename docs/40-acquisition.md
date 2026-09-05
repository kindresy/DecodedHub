# 40 · 采集归一化上下文（Acquisition）

> 状态：已定案 · C2 · 调研依据：本机真实样本（kingstvis/rigol mho98 的 data/）+ Saleae 官方格式文档

## 职责

把外部采集系统导出的文件翻译为 `Capture`（统一信号模型）。外部格式的全部怪癖止步于本上下文（防腐层）。

## 格式目录（v1 支持的全部）

| 格式键 | 来源 | 结构要点 | 时间表示 | 已知怪癖 |
|---|---|---|---|---|
| `kingst_csv` | Kingst VIS 导出 | 表头 `Time[s], 通道 0, …`；**跳变行**（任一通道变化才出一行，值为全通道快照） | 相对秒，触发=0，9 位小数 | 分隔符是 `", "`（逗号+空格）；通道名随软件语言（`通道 N`/`Channel N`）→ 按列位置解析；**文件不含采样率**（可选参数补） |
| `kingst_bin` | Kingst VIS 导出 | 裸 `uint16 LE` 逐采样位域，bit i = 通道 i | t = k / rate | 无头、无采样率（参数必填或默认 200 MHz）；大小恒为 n×2 字节 |
| `kingst_kvdat` | Kingst VIS 工程 | XML `<settings>` 前导（变长）+ `\n` + 魔数 `kvdat\0\0\0` + u64×4（n_samples、sample_rate、trigger_pos、n_channels）+ 每通道 16 字节描述（u32 常量 0x00442323、u8 通道号、u8 初始电平、u16 保留、u64 记录数）+ 记录流 5 字节（u32 位置索引 + u8 flag≡0）；**末条记录 = (n_samples, 0) 终结符** | 索制：t = pos/rate | 自描述（率/深度/初始电平齐全），最紧凑；XML 前导按字节扫描魔数定位 |
| `mho98_csv` | RIGOL MHO98 MCP 导出 | 行1 `# MHO98 waveform source=… mode=… points=…`；行2 `# xincrement=… xorigin=… xreference=… yincrement=… yorigin=… yreference=…`；行3 表头 `t_s,v_V`；数据行 | 相对秒，触发≈中心（有负时间） | `skiprows=3`；缩放已应用（若按码值则 `v=(code−yorigin−yreference)×yincrement`） |
| `mho98_npz` | 同上（RAW 全内存） | NPZ，键 `t_s`(f64) + `v_V`(f64)，观测到 1k…50M 点 | 相对秒 | 压缩 npz 不可 mmap；读一次转 float32；均匀时只留 `(t0,dt)` |
| `mcu_adc_csv` | MCU 固件串口记录 | 变体：`time_ms,adc_raw` / `millis,value` / `voltage` 单列 | ms 或 s；单列时采样率参数必填 | 原始码值需 `vref`/`bits` 换算（`raw_scale=vref/2^bits`） |
| `mcu_adc_bin` | 同上 | 裸 `uint16 LE` 采样 dump | t = k / rate | 采样率参数必填；大小 % 2 == 0 |
| `saleae_csv` | Saleae Logic 2 数字 CSV | 表头 `Time [s],Channel 0,…`（注意空格）；跳变行 + 全通道快照 | 相对秒 | 与 kingst_csv 同构但分隔符为 `,`、表头带空格 |
| `sigrok_sr` | Sigrok session | ZIP 中 `metadata` + 单设备 `logic-*` 分段；探针名、采样率、`unitsize` 1–4 自描述 | t = k / rate | 自动按分段序号拼接；多 device/capturefile、缺 metadata、空数据或非整样本均拒绝 |
| `generic_csv` | 通用模拟（含 RIGOL 面板导出） | 表头含 `x`/`t` 列 + 一个/多个电压列（`CH1` 等）；或无表头数值列 | s 或 ms | 用于兜底；多行头的 RIGOL DS1000Z 风格跳过 `#`/文本行 |

## 嗅探规则（有序，全部失败才报 `UnknownFormatError`，消息列出尝试过的规则）

> ADR-018 起，规则以各格式 `AdapterSpec.sniff` 匹配器的形式随适配器登记
> （`adapters/<fmt>.py`），本表描述判定语义与优先序；遍历器在 `sniff.py`。

1. `.sal` 或 zip 魔数 `PK\x03\x04`（且 zip 内含 `meta.json`）→ v1 明确报"规划中"错误（诚实优于半解析）。
   `.sr` + ZIP 魔数 → `sigrok_sr`；适配器进一步验证 `metadata`。
2. 前 8KB 内（跳过可选 XML 前导）出现 `kvdat\x00\x00\x00` → `kingst_kvdat`。
3. 前 8 字节 `<SALEAE>` → v1 报"规划中"（数字 bin v0/v1 与模拟 bin 规格已备档于 ADR-007，暂不实现）。
4. `.npz` 且键 ⊇ {`t_s`,`v_V`} → `mho98_npz`。
5. 文本嗅探（前 3 行，容忍 BOM）：
   - 行1 以 `# MHO98 waveform` 开头 → `mho98_csv`；
   - 表头以 `Time[s],` 开头（", " 分隔）→ `kingst_csv`；
   - 表头以 `Time [s],` 开头 → `saleae_csv`；
   - 表头 `name,…,start_time,…` → v1 报"规划中"（`saleae_data_table`）；
   - 表头匹配 `(?i)^(t(ime)?_?(ms|s)?|millis)\s*,` 或单/双数值列 → `mcu_adc_csv`；
   - 表头含 `x`/`t` 列 + `CH\d|ch\d|volt` 列 → `generic_csv`。
6. 二进制、大小为偶、无其他命中 → `mcu_adc_bin`（需采样率参数；无参数则报错并提示）。

`kingst_bin` 与 `mcu_adc_bin` 同为裸 u16 流、嗅探不可区分——前者不设匹配器，
只能显式 `format="kingst_bin"`；兜底命中一律按 `mcu_adc_bin` 处理。

## 登记契约（ADR-018）

格式知识的单一登记点 = `adapters/__init__.py` 的 `SPECS`（插入序即嗅探优先序）。
**新增一个格式的全部动作**：

1. 写 `adapters/<fmt>.py`：`load(path, options) -> Capture` + `SPEC = AdapterSpec(...)`
   （嗅探匹配器、`sniff_hint` 诊断串、`options` 声明、目录描述一句话同处一处）；
2. `SPECS` 元组登记一行（位置 = 嗅探优先级）。

以下全部**派生**自 SPECS，不许再手写第二份：`SUPPORTED_FORMATS` / `PLANNED_FORMATS`、
capabilities 的每格式选项明细（`options_line`）、MCP lock_source/add_source 的
options JSON schema（`options_properties`）、required 选项的解析前校验
（`validate_options`）。一致性由 tests/unit/test_adapter_registry.py 守护
（登记完整性、派生覆盖、必填前置报错）。

`kingst_kvdat` 额外读取真实 KingstVIS 3.6.x XML：恢复设备型号、稀疏物理
通道和用户通道名；对已验证的 `SpiAnalyzer` 15 项参数向量恢复 CLK/MOSI/
MISO/CS、模式、位序、字长和 CS 极性。`lock_protocol("spi")` 先应用这些保存
值，再应用调用者显式参数，所以工程文件可直接离线复现 GUI 设置且仍允许覆盖。

## 统一模型字段（C1 `shared/waves.py`，权威定义）

```python
class TimeBase(Enum):
    TRIGGER_RELATIVE = "trigger_relative"   # Kingst/MHO98：触发点 = 0
    ABSOLUTE = "absolute"                    # 有 epoch 锚点

@dataclass
class CaptureMeta:
    source_kind: str            # "kingst"|"mho98"|"mcu_adc"|"saleae"|"generic"
    format_key: str             # 上表格式键
    device: str | None
    source_files: list[str]
    captured_at: datetime | None
    time_base: TimeBase
    trigger_t: float | None
    sample_rate: float | None   # None = 未知（kingst_csv 未补参数时）
    probe_attenuation: float = 1.0
    threshold_v: float | None   # LA 阈值 / 切片所用阈值（回写）
    extra: dict = field(...)    # 前导参数、kvdat XML 等原始佐证

@dataclass
class DigitalWave:              # 多通道数字 IR（位域跳变表）
    channels: tuple[str, ...]   # 有序通道名（保持源顺序）
    initial: int                # t_start 时刻的位域快照
    t_start: float = 0.0
    edges_t: np.ndarray         # f64[E] 严格递增
    edges_levels: np.ndarray    # u32[E] 每次跳变后的位域快照
    t_end: float                # 采集结束（含）
    sample_rate: float | None = None   # 源采样率提示（仅元信息）
    n_samples: int | None = None
```

关键操作：`select(names)`（子集重掩码）、`edge_stream(name) -> (times, levels)`（单通道跳变）、`level_at(name, t)`（二分查电平）、`to_bool_array(name)`（重物化，测试/绘图用）、`from_bool_array(...)`（压缩构造）、`from_segments(...)`（快照段构造入口；`t_start` 显式声明采集起点，可为负——构造后不可再突变）。

```python
@dataclass
class AnalogChannel:
    name: str
    units: str = "V"
    t0: float = 0.0
    dt: float | None = None     # 均匀步长；None 则必须有 times
    times: np.ndarray | None = None
    samples: np.ndarray         # float32，物理单位
    raw_scale: float | None = None   # 码值→电压（mcu_adc）
    raw_offset: float = 0.0

@dataclass
class Capture:
    meta: CaptureMeta
    digital: DigitalWave | None = None
    analog: list[AnalogChannel] = field(default_factory=list)
    capture_id: str = ""        # 摄取时分配（来源文件名+内容摘要），制品目录用
```

## 各源转换注记

- **kingst_kvdat → DigitalWave**：t = pos/sample_rate；记录流按通道分块（每通道：描述头 + record_count + 记录），通道归属直接可知；把各通道跳变时刻合并、按时间排序，快照 = 前快照异或该通道位；初始位域 = 各通道 u8 初始电平拼装；丢弃 (n_samples, 0) 终结符；率/深度取自头。**一致性保险**：单测用真实 `probe_100k.kvdat` 解出的位流与 `probe_100k_all.bin` 逐位比对（两文件同源采集）。
- **kingst_csv / saleae_csv → DigitalWave**：首行（t=0 行）→ `initial`；其余行 → edges。n_samples 未知 → 用 `t_end × rate` 估计或留空。
- **kingst_bin → DigitalWave**：u16 位域流 → `np.flatnonzero(np.diff(stream))` 压缩；通道名缺省 `D0..D15`（导出时可能只含部分通道——与 CSV 列对齐问题由用户参数 `channels` 解决，默认全 16）。
- **mho98_csv / npz → AnalogChannel**：CSV 从 `#` 前导取 points/xincrement；npz 判 `np.ptp(np.diff(t_s))` 近零 → 均匀化存 `(t0,dt)`，否则保留 times。
- **mcu_adc_* → AnalogChannel**：`raw_scale` 路径保留原始码值与换算系数；CSV 时间列 ms→s 归一。
- **切片回写**：`slicer` 经 `threshold` scalar 输出端口给出实际所用阈值（含缺省计算值），应用层在图外写回 `meta.threshold_v`（图内保持纯函数），`render_analog` 按其标注阈值线。

## 多源工程（Project，ADR-008 v1.2）

一个实验中多台采集器同时采集时，会话持有 `Project` 容器；**各源独立分析**
（每源独立协议锁与解码，见 50-mcp-gateway 的 source 参数）：

```python
@dataclass
class SourceEntry:
    alias: str                    # 源别名（缺省 = 文件名 slug）
    capture: Capture
    offset: float = 0.0           # 库级字段（merged 用；工具层不暴露）
    t_wall: datetime | None       # 同上

@dataclass
class Project:
    entries: list[SourceEntry]
    def merged(self) -> Capture   # 库能力：合并到公共时间轴（记忆化）
```

**库能力（不暴露于工具层，v1.2 裁决）**：`merged()` 把多源合并为单一 Capture——
多源通道名加 `别名:` 前缀（单源向后兼容）、数字跳变流归并重排位（≤32 通道）、
模拟通道平移、**同时刻容差归并**（相差 ≤ max(1e-12, 1e-12·|t|) 秒视为同一物理时刻，
规避浮点 ulp 伪先后造成的 I2C 伪 START）。单测锚定（含跨设备混合解码与 CSV 往返回归）。
**不暴露的原因**：用户环境 PC 时间戳 ≥百 ms 误差且不拆总线到多设备——合并无可信偏移来源。

## 内存与性能

- 均匀时间轴永不物化数组（`(t0, dt, n)`）；50M 点模拟 = 200 MB(float32)，可接受，>10M 时呈现层走 min/max 包络抽取。
- 数字路径一切 O(跳变数)：活跃总线 1M 采样典型 ~10³–10⁴ 跳变，解码毫秒级。
- npz 压缩文件一次性读入后立即释放句柄。

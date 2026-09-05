# 60 · 测试策略

> 状态：已定案 · 测试金字塔：单元 → 属性（合成往返）→ 集成（真实样本）→ MCP 冒烟

## 分层

| 层 | 位置 | 内容 | 依赖 |
|---|---|---|---|
| 单元 | `tests/unit/` | 内核操作（select/level_at/压缩往返）、嗅探规则表、各适配器、图验证五规则、求值缓存/部分求值 | 仅 `shared/acquisition/decode`，无 IO（样本在 `tests/data/`） |
| 属性 | `tests/property/` | 合成→解码往返矩阵（下表）；`hypothesis` 风格由自写随机循环实现（少依赖） | `decode/synth.py` |
| 集成 | `tests/integration/` | 真实样本全链路：kingst CSV/bin/kvdat → Capture → （KV/BIN 位流一致性比对）；demo 场景端到端 | `tests/data/` 真实文件副本 |
| MCP | `tests/mcp/` | in-memory client-server 会话：初始化能力宣告、阶段过滤（6/11/18）、lock 后 list_changed、门禁错误、READY 全工具走通含 ImageContent | `mcp.shared.memory` |

## 往返测试矩阵（属性层）

生成器轴：随机字节流（含 0x00/0xFF 边界值）、baud ∈ {9600, 115200, 1M, 3M}、数据位 5–9、校验 N/O/E、停止 1/1.5/2、抖动 ≤0.2 UI、漂移 ≤2%、噪声（模拟路径）。

| # | 用例 | 断言 |
|---|---|---|
| U1 | 8N1 往返 × baud 矩阵 | 字节零误差；帧数一致 |
| U2 | 数据位 5/6/7/9（9bit 值 >0xFF） | 零误差 |
| U3 | 奇偶 O/E；注入校验错（翻转校验位） | 正确解码 + `parity` 错误事件 |
| U4 | 停止 1.5/2；注入 framing（拉低停止位） | framing 错误事件 |
| U5 | BREAK（低 ≥ 整帧） | break 事件 + 后续恢复 |
| U6 | 背靠背帧（无空闲隙） | 全部解出 |
| U7 | 反相线 + MSB 先 | 零误差（参数正确时） |
| U8 | auto-baud vs 真实 baud | 估计误差 ≤2%；解码零误差 |
| U9 | 抖动/漂移/噪声扫描 | 容差内零误差 |
| U10 | 模拟路径（analogify + slicer） | 零误差；阈值回写 meta |
| I1 | 写传输（逐字节 ACK）× 100k/400k | 地址/数据/ACK 全对 |
| I2 | 读传输 + 末字节 NACK | NACK 正确标记 |
| I3 | 重复 START | repeat-start 事件；传输不误闭合 |
| I4 | 10-bit 地址 | 地址重组正确 |
| I5 | 时钟拉伸 5ms | 解码不受影响（可选 WARN） |
| I6 | 总线空闲违例 + 字节中 SDA 抖动 | WARN 事件，不断流 |
| S1–S4 | CPOL/CPHA 四模式同一词 | 四模式解码一致 |
| S5 | 单词 CS vs 多词突发 | transfer.words 有序完整 |
| S6 | 词长 1–32 bit × MSB/LSB | 零误差 |
| S7 | 缺 CS / 缺 MISO | 降级模式 + WARN |
| S8 | 词中 CS 翻转 | cs-midword WARN + 词复位 |
| C1–C6 | I3C SDR、legacy I2C、奇校验/T-bit、CCC、DAA、HDR/ambiguity | 语义正确；不支持模式明确事件化；坏帧可恢复 |
| A1–A5 | AVSBus controller/target、命令字段、CRC-3、状态、截断/重同步 | 32-clock 帧语义与参考 CRC 一致；错误不中断后续帧 |
| E1 | 采集尾截断 | truncated 事件 + 前序完整 |
| G1 | 图级：环/类型失配/缺输入/未知参数 | 构建期拒绝（异常消息含规则名） |

## 真实样本（`tests/data/`，自本机采集目录复制的小文件）

| 文件 | 来源 | 用途 |
|---|---|---|
| `kingst_probe.csv` | kingstvis `data/probe.csv`（62KB, 2 通道） | kingst_csv 适配器 + 通道名（中文）处理 |
| `kingst_probe_all.csv` | `probe_all.csv`（185KB, 16 通道） | 16 通道位域正确性 |
| `kingst_100k.bin` | `probe_100k_all.bin`（200KB） | kingst_bin → DigitalWave |
| `kingst_100k.kvdat` | `probe_100k.kvdat`（25KB） | kvdat 解析 + **与 bin 位流逐位一致**断言 |
| `mho98_ch1_norm.csv` | rigol `data/ch1_norm_*.csv` | mho98_csv 前导解析 |
| `uplink24ms_ch1.npz` / `ch2.npz` | rigol mho98 真实采集 | 上行黄金（0x01）+ 下行静默诚实拒绝 |

（mcu_adc / saleae 等其余样本由各测试以 tmp_path 自造，不入库。）

## 外部可再分发波形

`tests/data/external/` 保存 UART、I2C、SPI、I3C、AVSBus 的真实或来源明确的
回归资产；逐文件来源、许可证、SHA-256、通道/参数和预期语义见
[`test-assets.md`](test-assets.md)。Sigrok `.sr` 覆盖 metadata/probe/rate、
多段 logic 拼接及 UART/I2C/SPI 离线解码；I3C 同一来源同时保留 CSV 与 `.sr`
并锚定 DAA/HDR 事件；AVSBus 使用确定性公开协议向量 CSV。

无明确再分发条款的三个社区 KVDAT 不入 Git。若其合法本地副本位于相邻
`decodehub-code-e127559/tests/data/external/`，测试自动执行 KingstVIS 3.6.x
解析和保存 SPI 设置端到端断言；缺失时只跳过这两项，不伪造测试数据。

## MCP 冒烟断言

1. `initialize` 结果宣告 `tools.listChanged == true`。
2. 初始 `tools/list` 恰好 6 个（DISCOVERY 集）。
3. `lock_source`（对合成 kingst CSV）后：客户端收到 `notifications/tools/list_changed`；重新 list 得 **11** 个（含 add_source / lock_protocol）；返回文本包含解锁工具名。
4. 越权调用 `run_decode`（在 DISCOVERY）→ 错误消息包含 `lock_protocol` 引导。
5. `lock_protocol(uart)` → **18** 工具；`run_decode` → 计数摘要；`render_timing` 返回内容含 `image/png` ImageContent 与表文本。
6. `reset_session` → 回 6 工具 + list_changed。
7. 多源并行（ADR-008 v1.2）：`add_source` ×2 → 各源独立 `lock_protocol`（可不同协议）→ `run_decode()` 一次并行解码全部源（分节摘要）→ `get_events/render_timing/export_events` 按 source 取数（多源缺省得到引导错误）→ `unlock_protocol` 单源不影响他源。

## 多源合并测试（`tests/unit/test_project_multi_source.py` + MCP 多源）

| 用例 | 断言 |
|---|---|
| 单源向后兼容 | 无前缀、零偏移返回原对象 |
| 双源命名空间与时间轴 | 通道 `别名:` 前缀；t_start/t_end = min/max（平移后） |
| 偏移增量与缓存失效 | set_offsets 未提及源保持原值；merged 记忆化 |
| 模拟平移 + 命名 | t0 平移、名字带前缀 |
| >32 数字通道 | 明确报错 |
| 墙钟对齐 | 差值即偏移；缺 t_wall 报错 |
| **CSV 往返回归** | 浮点 ulp 不得拆裂同时翻转（曾致大量伪 START） |
| 前缀角色映射 | `别名:scl` 等前缀名按后缀匹配（库能力 merged 用） |
| 跨设备混合解码 | SCL 源 A + SDA 源 B（偏移对齐）→ I2C 完整解出（**库能力**，工具层不暴露） |

## 覆盖率与门槛

- 回归门槛：`pytest tests/` 全过 + `scripts/stdio_smoke.py` 进程级冒烟；app/mcp_server 以冒烟路径覆盖为主。
- 所有公共 dataclass 字段在文档（40/41）与代码间保持同名——文档漂移作为 review 检查项。
- 下游特性逐提交测试和最终门禁结果见 [`integration-test-summary.md`](integration-test-summary.md)。

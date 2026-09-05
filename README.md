# decoded-all-in-one（decodehub）

> 采集数据归一化 + 通信协议解码 + 可视化 的 **MCP 平台**。
> 逻辑分析仪（Kingst / Saleae CSV / Sigrok SR）· 示波器（RIGOL MHO98）· MCU ADC —— 一次摄取，统一解码。

[设计文档（DDD）](docs/00-vision.md) · [架构](docs/30-architecture.md) · [MCP 渐进式暴露](docs/50-mcp-gateway.md) · [Headless CLI](docs/70-headless-cli.md) · [ADR](docs/adr/)

## 它解决什么

| 痛点                       | 方案                                                                                                                    |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------- |
| 每种采集器导出格式都不同，脚本一次性       | 格式嗅探 + 10 个适配器 → 统一信号模型（`DigitalWave` 位域跳变 IR / `AnalogChannel` 紧凑时间轴）                                                |
| **多台采集器同时采集，各自孤立**       | **多源并行分析（Project）**：`add_source` 追加各设备导出 → **每源独立锁协议**（可不同协议）→ `run_decode()` 一次并行解码全部 → 按源取事件/渲染/导出（制品目录天然隔离）；追加源零破坏 |
| 解析复杂多变，函数式写法失控           | **图（DAG）节点流水线**：类型化端口、构建期五规则验证、拉式记忆化求值、`inspect_graph` 可检视                                                            |
| MCP 工具 schema 淹没 LLM 上下文 | **三阶段渐进式暴露**：初始 6 工具 → 锁定数据源后 11 → 锁定协议后 18（`tools/list_changed` 实测生效 + 服务端门禁兜底）                                      |
| **IO/仪器固定的重复调试，每次重新配置**  | **工程档案（Profile）**：`save_profile` 固化源定义+协议锁（通道角色钉死）→ 之后 `open_project(files)` 一步直达 READY；接线错误在打开时即被防线拦截                |
| **团队/CI 的解码仍要 LLM 在场**       | **项目配置 + headless CLI（ADR-015）**：`decodehub.toml`（glob 批量 + 导出/渲染管线）→ `decodehub run` 一条命令出 index/summary；`diff` 做事件流回归对比                        |
| 解码结果难读                   | 图文配对：时序图帧 span 编号 ↔ Markdown 事件表；JSON/CSV 导出落盘                                                                        |

## 快速开始

```bash
cd decoded_all_in_one
.venv/bin/pip install -e .        # 已装则跳过
.venv/bin/python examples/demo.py # 全链路演示（合成→嗅探→解码→图表）
.venv/bin/python -m pytest tests/ # 回归测试
```

## 无仪器冷启动

手边没有仪器、也没有采集文件？用内置合成器造一份 UART 采集，5 分钟走完
**配置 → 校验 → 解码 → 报告** 全流程（多总线合成见
`examples/complex-project/make_captures.py`）：

```bash
.venv/bin/python - <<'EOF'
from pathlib import Path
from decodehub.decode.synth import encode_uart, save_kingst_csv

Path("captures").mkdir(exist_ok=True)
wave = encode_uart(b"Hello decodehub", baud=115200, idle_bits=2.0, seed=1)
save_kingst_csv(wave, Path("captures/hello.csv"))
print("已生成 captures/hello.csv")
EOF
```

写一份最小 `decodehub.toml`（内联解码定义，无需档案）：

```toml
version = 1
[runs.main]
[runs.main.decode.sources.la]
format = "kingst_csv"
[runs.main.decode.locks.la]
protocol = "uart"
params = { baud = 115200 }
[runs.main.captures]
la = "captures/hello.csv"
[runs.main.export]
formats = ["csv", "md"]
```

```bash
decodehub validate    # 校验配置与采集绑定（不解码）
decodehub run         # → reports/main/hello/ + index.md + summary.json
```

打开 `reports/main/index.md` 看运行汇总；`reports/main/hello/decoded.json`
是机器可读的全量事件（diff/CI 的输入），`events.md` 是人读的事件表。
之后把手里的真实采集文件路径换进 `[runs.main.captures]` 即可。

## 重复调试（IO/仪器固定时推荐）

```
# 首次：配置好源与协议后固化
save_profile(name="gizmo-v3", description="v3 主板")

# 之后每次抓取：两步
open_project(profile="gizmo-v3", files={"la": "…/probe.kvdat", "scope": "…/ch1.csv"})
run_decode()                                   # 全部已锁源并行解码
```

档案是 `profiles/gizmo-v3.json`（纯 JSON，可提交进固件仓库、团队共享）。

## 项目化 / CI（headless CLI，无需 LLM）

定型后的解码用 `decodehub.toml` 描述，一条命令批量复跑（ADR-015，[完整参考](docs/70-headless-cli.md)）：

```toml
version = 1
[runs.main]
profile = "gizmo-v3"          # 引用档案；也可 [runs.main.decode] 内联定义
[runs.main.captures]
la = "captures/*.kvdat"       # glob 批量；单文件别名自动广播
[runs.main.export]
formats = ["csv", "md"]
[runs.main.render]
timing = true
```

```bash
decodehub validate            # 校验配置/档案/采集绑定（CI 首道防线）
decodehub run                 # 批量解码 → reports/main/<采集集>/ + index.md
decodehub diff reports/main/001_old/decoded.json reports/main/001_new/decoded.json
                              # 事件流回归对比（忽略时间戳；不同则退出码 1）
```

档案/配置可提交进固件仓库进 code review；`schemas/profile.v1.schema.json`
供 IDE 校验补全。

## 接入 MCP 客户端

```json
{
  "mcpServers": {
    "decodehub": {
      "command": "<decodehub工程目录>/.venv/bin/python",
      "args": ["-m", "decodehub.mcp_server"]
    }
  }
}
```

典型会话（工具随阶段解锁）：

```
list_capabilities()
lock_source(path="…/probe.kvdat")            # 嗅探+归一化 → 解锁 describe_capture 等
add_source(path="…/scope_ch1.csv", options={"alias": "scope"})
describe_capture()                            # 分源通道统计
lock_protocol(protocol="i2c", source="la")    # LA 锁 I2C
lock_protocol(protocol="uart", source="scope")# 示波器锁 UART（各源独立）
run_decode()                                  # 一次并行解码全部已锁源
get_events(source="la", kind="i2c.transfer")  # 按源分页表格
render_timing(source="la", t_min=0, t_max=0.001)  # PNG（内联+落盘）+ 配对表
export_events(format="csv", source="scope")   # out/<capture_id>/events.csv（按源隔离）
```

## 支持矩阵（v1）

- **格式（10）**：kingst_csv / kingst_bin / kingst_kvdat · mho98_csv / mho98_npz（含扩频突发采集）· mcu_adc_csv / mcu_adc_bin · saleae_csv（数字）· sigrok_sr（`.sr` 会话）· generic_csv（模拟兜底）
- **协议（7）**：UART（auto-baud、5–9 位、奇偶、1/1.5/2 停止、反相）· I2C（7/10-bit、重复起始、时钟拉伸容忍）· SPI（四模式、CS 帧化、1–32 位词、单向数据线降级）· I3C（Basic SDR、CCC/DAA、legacy I2C）· AVSBus（32-clock controller/target 子帧、CRC-3）· **上行 DSSS + 下行 DBPSK**（模拟直达：PN 相关解扩、码片速率自动估计、纯噪声诚实拒绝；下行以上行帧为锚做偏移解析——**跨节点扇入 + 跨源图注入**的示范；协议形状参数全量可配，ADR-010/011）
- **图表**：数字时序图（帧着色+编号）、模拟波形+阈值叠加图

能力清单不是手工常量：`list_capabilities`、CLI 和 MCP 参数约束都从适配器、
协议插件、绑定、节点参数、呈现规则与事件 schema 在运行时派生。事件/报告的
发布 schema 当前为 `1.0`。

## 工程结构（DDD）

```
src/decodehub/
├── shared/      C1 信号内核（统一模型，零依赖）
├── acquisition/ C2 采集归一化（嗅探 + 适配器防腐层）
├── decode/      C3 核心域（DAG 图引擎 + 通用节点 + protocols/ 一协议一目录）
│   └── protocols/<协议>/  decode.py + encode.py + README.md（编解码原理，随代码走）
├── render/      C4 呈现（表格/图表/导出/制品）
├── app/         应用层（会话状态机 + 用例编排）
└── mcp_server/  C5 网关（三阶段渐进式工具暴露）
```

依赖规则：`shared` ← `acquisition|decode|render` ← `app` ← `mcp_server`；matplotlib 只在 render，mcp SDK 只在 mcp_server。

## 扩展

- **新协议**：协议目录五件套（`decode.py` 解码 + `encode.py` 编码 + `binding.py` 绑定声明 + `present.py` 呈现 + `README.md` 原理）→ 注册 `PluginDescriptor` → 往返测试。外部包通过 Python entry point 组 `decodehub.protocols` 投放，插件 API 版本为 `1`；引擎/网关/应用层不需硬编码协议名。
- **新格式**：`adapters/<name>.py` 声明 `AdapterSpec` + 在 `SPECS` 中登记 + 样本测试；嗅探、选项校验和能力目录自动派生。
- 详见 [docs/30-architecture.md 扩展指南](docs/30-architecture.md)。

可再分发的真实波形、来源/许可证/SHA-256 和预期结果见
[测试资产清单](docs/test-assets.md)；本次下游集成的逐项测试证据见
[集成测试概要](docs/integration-test-summary.md)。

## 已知边界（ADR-007）

Saleae `.sal`/二进制、CAN/1-Wire、甘特/统计图延后；I3C 当前仅被动 Basic SDR（不解析 HDR 电气所有权）；裸 u16 bin 无魔数需显式 `format`；单会话/进程（stdio 语义）。

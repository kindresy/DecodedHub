# 30 · 架构（Architecture）

> 状态：已定案 · 战术设计与运行时

## 分层与目录

```
decoded_all_in_one/
├── pyproject.toml              # 发行名 decodehub；入口 decodehub-mcp
├── README.md
├── docs/                       # 本文档集（DDD）
├── examples/                   # demo.py：无 MCP 的全链路演示；样本生成
├── src/decodehub/
│   ├── shared/                 # C1 信号内核
│   │   ├── waves.py            # DigitalWave / AnalogChannel / Capture / CaptureMeta
│   │   └── errors.py           # DecodehubError 体系（领域错误基类）
│   ├── acquisition/            # C2 采集归一化
│   │   ├── sniff.py            # 格式嗅探（有序规则表）
│   │   ├── adapters/           # 一格式一文件：kingst_csv/bin/kvdat、mho98_csv/npz、
│   │   │                       #   mcu_adc_csv/bin、saleae_csv、sigrok_sr、generic_csv
│   │   └── service.py          # IngestService：path(+options) → Capture
│   ├── decode/                 # C3 解码（核心域）
│   │   ├── graph.py            # Graph/NodeSpec/Edge/PortType、验证、记忆化求值
│   │   ├── registry.py         # NODE_REGISTRY：type → Node 类；注册装饰器
│   │   ├── events.py           # DecodedEvent 家族 + DecodeReport
│   │   ├── schema.py           # 事件/报告 schema 1.0 与字段/错误校验
│   │   ├── plugins.py          # 插件 API v1：内置描述符 + entry point 发现
│   │   ├── capabilities.py     # 从各注册表派生运行时能力矩阵
│   │   ├── nodes/
│   │   │   ├── picks.py        # digital_pick / analog_pick
│   │   │   ├── slicer.py       # analog → digital（滞回阈值）
│   │   │   ├── uart.py         # uart_decode（含 auto-baud）
│   │   │   ├── i2c.py          # i2c_decode
│   │   │   ├── spi.py          # spi_decode
│   │   │   └── filters.py      # event_filter
│   │   └── synth.py            # 合成波形发生器（测试矢量，也可当节点外的工具函数）
│   ├── render/                 # C4 呈现
│   │   ├── format.py           # events → markdown 表 / JSON / CSV
│   │   ├── plots.py            # 时序图 / 模拟叠加图（matplotlib Agg）
│   │   └── artifacts.py        # 制品登记与 out/<capture_id>/ 约定
│   ├── app/                    # 应用层
│   │   ├── session.py          # SessionState：三阶段状态机
│   │   └── services.py         # 用例编排：ingest/describe/build_graph/decode/render
│   └── mcp_server/
│       ├── tools.py            # ToolSpec 表（分阶段）+ handlers
│       ├── server.py           # 低层 Server 装配：list_tools 过滤 / call_tool 分发 / 门禁
│       └── __main__.py         # python -m decodehub.mcp_server
└── tests/
    ├── unit/                   # 每上下文独立单测
    ├── property/               # 合成→解码往返属性测试（测试矩阵见 60-testing.md）
    ├── integration/            # 真实样本 + 全链路
    └── mcp/                    # in-memory client 会话冒烟
```

## 运行时数据流（一次典型会话）

```
LLM                    C5 网关                应用层                 C2/C3/C4
 │ list_capabilities     │                      │                      │
 │ ────────────────────▶ │ 阶段=DISCOVERY        │                      │
 │ ◀── 源/协议目录 ────── │                      │                      │
 │ lock_source(path) ──▶ │ ─▶ ingest ─────────▶ sniff+adapter ─▶ Capture
 │                       │ send_tool_list_changed                      │
 │ ◀── 采集摘要 + 新工具名│                      │                      │
 │ describe_capture ───▶ │ ─▶ describe ───────▶ 通道统计                │
 │ lock_protocol(uart,params) ─▶ build_graph ─▶ GraphSpec（含通道自动映射）
 │                       │ send_tool_list_changed                      │
 │ run_decode ─────────▶ │ ─▶ execute ────────▶ 图求值（pull+memo）─▶ DecodeReport
 │ render_timing ──────▶ │ ─▶ render ─────────▶ PNG + Markdown 表 + 制品登记
 │ ◀── [ImageContent, TextContent(表+制品清单)]                          │
```

## 图引擎规范（C3 核心）

### 端口类型

| PortType  | 载荷                    | 产生者 → 消费者                                            |
| --------- | --------------------- | ---------------------------------------------------- |
| `capture` | `Capture`             | （源，v1 由应用层直接注入）→ `digital_pick` / `analog_pick`      |
| `digital` | `DigitalWave`         | `digital_pick` / `slicer` → 解码器 / `event_filter` 不消费 |
| `analog`  | `list[AnalogChannel]` | `analog_pick` → `slicer`                             |
| `events`  | `list[DecodedEvent]`  | 解码器 / `event_filter` → 呈现层（应用层读取）                    |
| `scalar`  | `float/int/str`       | `slicer.threshold`（切片阈值，应用层回写 meta 后随图表标注）；`baud_measure` 等预留 |

模拟 → 数字的唯一路径是 `slicer`（端口类型严格匹配，无隐式转换——验证期强制）。

### 节点契约

```python
class Node(Protocol):
    TYPE: str                              # 注册键，如 "uart_decode"
    INPUTS: Mapping[str, str]              # 端口名 → PortType
    OUTPUTS: Mapping[str, str]
    PARAMS: Mapping[str, ParamSpec]        # 参数名 → (默认值, 校验/文档)
    def run(self, inputs, params) -> dict[str, Any]: ...
```

节点是纯函数：禁止文件 I/O、全局状态、随机数（合成发生器作为例外位于 `synth.py`，且不注册为节点，供测试与 examples 直接调用）。
`@register` 在注册期即校验契约完整性（TYPE/INPUTS/OUTPUTS/PARAMS/run）——缺失当场报错，而非建图/求值时才以 AttributeError 暴露。

### 图规范与验证（构建期，全部可检错误）

1. 边引用的节点与端口必须存在；
2. 端口类型严格相等；
3. 每个输入端口至多一条入边；
4. 无环（DFS 三色标记）；
5. params 通过 `PARAMS` 校验（未知参数名报错，防止拼写错误静默失效）；缺省值经同一 coerce 路径逐次物化——声明中的可变默认（如 list）不跨节点共享实例。

### 求值（运行期）

- **拉式记忆化**：`evaluate(graph, target_ids)`，递归下降即拓扑序；只计算目标的祖先。
- 缓存键 = 节点 id（图在一次会话内不可变；参数变化 = 新图）。
- 错误包装为 `NodeError(node_id, message, got_inputs)`：用户能看到"哪个阶段、喂了什么、为何失败"。节点实现只抛领域异常（ValueError/KeyError），**不得自行构造 NodeError**——节点不知道自己的 spec id，包装与 id 注入是引擎的职责。
- **解码错误是事件不是异常**：解码器永远返回完整事件列表，坏片段成为带 `errors` 标记的事件，解码在下一个恢复点（下一起始位 / START 条件 / CS 边沿）继续。截断的采集产生 `truncated` 事件。

### 通道自动映射

`lock_protocol` 依据协议绑定（ADR-014）声明的角色（UART: `rx`；I2C: `scl,sda`；SPI: `clk,[miso],[mosi],[cs]`）在 `Capture` 中自动选通道（映射算法在 `decode/bindings.py`——域内启发式）：优先匹配常见名（`rx/tx/scl/sda/clk/sck/mosi/miso/cs`、`通道 N`/`channel N`/`D N` 的数字），否则取前 N 个数字通道，并在返回的"解码计划"中明示映射结果供 LLM 确认/覆盖（`lock_protocol(params={"rx": "通道 3"})`）。

## 会话状态机（应用层）

```
DISCOVERY ──lock_source(成功)──▶ SOURCE_LOCKED ──lock_protocol(成功)──▶ READY
    ▲                                  │unlock_protocol │                  │
    └──────────── reset_session ───────┴────────────────┴──────────────────┘
```

- `SessionState`: `stage / project / locks / reports / artifacts / memos`（locks/reports/memos 以 `源|协议` 键——一源可多协议锁）。
- READY 阶段 `run_decode(overrides)` 允许改解码参数（如换 baud）→ 重建图并重求值；会话按锁保留图求值 memo（`evaluate(memo=…)`），重建时 type+params 未变的上游节点（pick/slice）直接命中缓存，参数变化的节点自动淘汰（`_inherit_memo`）。
- 制品目录 `out/<capture_id>/`，文件名确定性（无时间戳），重复渲染幂等覆盖。

## 错误模型（跨层约定）

| 层 | 异常 | MCP 侧表现 |
|---|---|---|
| 领域 | `UnknownFormatError(tried_rules)` | `INVALID_PARAMS` + 可解释消息 |
| 领域 | `GraphValidationError(rule, detail)` | `INVALID_PARAMS` + 指明违反的规则 |
| 节点运行 | `NodeError(node_id, cause)` | `INVALID_PARAMS` + 节点上下文（NodeError 属 DecodehubError——均为调用方可修正的领域错误；`INTERNAL_ERROR` 仅用于非领域异常） |
| 门禁 | `StageGateError(tool, stage_needed, stage_now)` | `INVALID_PARAMS` + "先调用 lock_source/lock_protocol" |
| 解码数据错 | `errors` 字段挂在事件上 | 正常结果中的 WARN 行，**不是**错误 |

## 扩展指南

### 插件拓扑与解耦边界

```
外部包 entry point: decodehub.protocols       内置 PluginDescriptor
                    \                            /
                     +---- load_plugins() ------+
                                  |
                    导入协议模块并验证 API v1
                                  |
        +-------------------------+-------------------------+
        |                         |                         |
 ProtocolBinding            Node registry             Presentation
 角色/图模板/参数路由       纯解码算法与端口契约       表格/CSV/时序图约定
        +-------------------------+-------------------------+
                                  |
                       capability_matrix()
                                  |
                   App / CLI / MCP / JSON schema
```

平台只依赖稳定契约，不依赖具体协议实现。插件加载后必须同时提供绑定、节点和
呈现注册，节点契约会立即校验；重复协议名、API 版本不兼容或三者缺一都在启动
阶段失败。能力矩阵再与采集 `AdapterSpec`、事件 schema 合并，因此新增协议
不会要求在 CLI/MCP 中维护另一份枚举。

**新增协议**（ADR-012/013/014：一协议一目录，预计 ~350 行 + 文档 + 测试）：
1. `decode/protocols/<proto>/decode.py` 实现 `Node`（INPUTS/OUTPUTS/PARAMS/run，参数必附 doc——目录文案由它派生），事件继承 `DecodedEvent`；
2. `decode/protocols/<proto>/encode.py` 合成编码器（往返测试的编码方向）；
3. `decode/protocols/<proto>/binding.py` 协议绑定（角色/通道数需求/参数路由/锚依赖声明，ADR-014）；
4. `decode/protocols/<proto>/present.py` 注册呈现约定（ADR-013：中文名/内容列/CSV 专有列/是否上时序图/preview kind）；
5. `decode/protocols/<proto>/README.md` **编解码原理文档**（波形模型/发送侧/接收侧/参数/事件/测试锚点）；
6. 暴露 `PluginDescriptor(protocol, module, node_type, version="1")`；内置协议加入
   `BUILTIN_PLUGINS`，外部发行包声明 `decodehub.protocols` entry point；
7. `tests/property/` 往返测试，并用事件 schema 契约测试固定发布字段。

**协议客制图**（可选，ADR-022）：通用时序图表达不了的"协议自身最佳显示"
（星座图/眼图/包结构图……）→ 新建 `render/contrib/<proto>.py` 自注册
`(protocol, graph_kind) → RenderRoute`——投放即生效（pkgutil 发现，零共享
文件改动），通用路由自动兜底；轻量差异（仍是时序图、只调标注/样式）优先
扩展现有 present.py 数据级约定，不急于开客制模块。

引擎、网关、呈现、**应用层**零改动——图模板由 `decode/bindings.py` 的
`build_lock_graph` 按绑定统一构建（数字/切片/模拟直达/跨源扇入四形态），
工具目录和协议参数 schema 从能力矩阵派生，
新协议事件自动获得 JSON/Markdown/CSV/图表渲染（渲染只依赖 `DecodedEvent` 基础字段）。

**新增采集格式**：
1. `adapters/<name>.py`：`load(path, options) -> Capture` + `AdapterSpec`；
2. 在 `adapters.SPECS` 按嗅探优先级登记（魔数优先，文本头靠后）；
3. `tests/unit/test_sniff.py` 加样本。

**MCP 新工具**：`mcp_server/tools.py` 的 `ToolSpec` 表加条目并标注所属阶段；网关自动获得门禁与列表过滤能力。

## 关键非功能决策

- **性能**：数字路径 O(跳变数)；模拟样本 float32；均匀时间轴不物化 times 数组；绘图对模拟做每像素 min/max 包络抽取；slicer 为向量化滞回（O(n) 时间、O(定态数) 峰值内存——不物化逐采样候选，50M 点模拟切片不膨胀）。
- **上下文经济**：工具 schema 精简（枚举内嵌于 description）；事件分页（`get_events(limit/offset)`）；大结果引导用 `export_events` 落盘。
- **进程模型**：stdio 单会话/进程；stdout 仅协议流量，日志走 stderr（logging）。

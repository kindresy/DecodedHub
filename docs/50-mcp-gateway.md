# 50 · MCP 网关（渐进式工具暴露）

> 状态：已定案 · C5 · 技术底座已对 mcp 1.29.1 端到端实测（含 in-tool 通知与客户端自动重取）

## 机制（三层保险）

1. **每会话工具过滤**：低层 `mcp.server.Server` 的 `@server.list_tools()` 处理器按 `SessionState.stage` 返回该阶段的 `ToolSpec` 集（非 FastMCP——其全局 ToolManager 无法做每会话差异，且 `run()` 不传 `NotificationOptions`，`listChanged` 会被宣告为 false）。
2. **列表变更通知**：锁定动作在工具处理器内 `await ctx.session.send_tool_list_changed()`；初始化时 `create_initialization_options(notification_options=NotificationOptions(tools_changed=True))` 宣告能力。
3. **服务端门禁**：`call_tool` 分发前校验"工具属于当前阶段"；即使客户端缓存了过期列表（部分客户端不响应 list_changed，如旧版 Claude Desktop / Codex），调用也会得到可操作的引导错误而非执行。

兜底：`lock_*` 的返回文本**总是列出刚解锁的工具名**，LLM 在客户端尚未刷新列表时也能据文本继续（或直接得到门禁引导）。

## 运行时能力目录

`list_capabilities` 不维护协议/格式硬编码副本。启动时先加载内置及
`decodehub.protocols` 外部 entry points，再从 `ProtocolBinding`、节点参数、
呈现注册、事件 schema 和采集 `AdapterSpec` 组合出能力矩阵。因而 CLI、MCP
说明和锁协议参数约束会同时看见 UART/I2C/SPI/I3C/AVSBus/uplink/downlink
七个协议与十个已实现格式；计划中的 Saleae 格式单独列出且不能被误调用。

MCP 的 `lock_protocol.protocol` schema 由这个矩阵生成 enum，其他可扩展选择器
保留自由字符串，避免外部插件被网关层挡住。能力结果带事件 schema 版本 `1.0`。

## 已知 SDK 坑位与对策（实测来源）

| 坑 | 对策 |
|---|---|
| `list_tools` 处理器可能被以 `req=None` 调用（工具 schema 缓存刷新路径） | 处理器签名带默认参；一律经 `server.request_context`（contextvar）取会话 |
| 无 session_id 字段 | 以 `id(ctx.session)`（会话对象身份）为键持有 `SessionState` |
| 无会话结束回调 | stdio 单会话/进程，进程退出即清理；状态字典随 lifespan 归零 |
| stdout 是协议通道 | 全部日志走 stderr（logging） |

## 三阶段工具目录

### 阶段 DISCOVERY（初始，6 个）

| 工具 | 参数 | 行为 |
|---|---|---|
| `list_capabilities` | — | 列出支持的源格式（键、所需参数、通道语义）与协议（角色、参数 schema、典型用法）；附当前会话阶段与建议动作。自描述入口。 |
| `lock_source` | `path`、`format?`（默认 auto 嗅探）、`options?`（如 kingst_bin 的 `sample_rate`、mcu_adc 的 `sample_rate/vref/bits`） | 嗅探 + 摄取 → `Capture`；返回采集摘要（源、通道数、采样率/时长、数字/模拟通道名表）+ 解锁的工具名；发 `tools/list_changed`。失败：`UnknownFormatError` 全文（尝试过的规则）。 |
| `get_session` | — | 阶段、锁定内容摘要、可用动作、制品清单。 |
| `reset_session` | — | 清空回到 DISCOVERY；发 list_changed。 |
| `list_profiles` | — | 列出工程档案（ADR-009；IO/仪器固定的重复调试入口）。 |
| `open_project` | `profile`、`files={别名:路径}` | 按档案一步开工程：摄取各源 + 应用全部协议锁 → 直达 READY；通道角色钉死校验（接线防线）。发 list_changed。 |

### 阶段 SOURCE_LOCKED（追加 5 个，共 11）

| 工具 | 参数 | 行为 |
|---|---|---|
| `describe_capture` | — | 分源描述：每源格式/时长/通道统计（数字跳变·活动率；模拟 Vpp/极值）与协议锁状态。 |
| `add_source` | `path`、`format?`、`options?`（`alias/sample_rate/vref/bits…`） | 追加采集源（可重复；**不影响已锁协议**，各源独立分析）。 |
| `lock_protocol` | `protocol`、`source?`、`params?` | **按源**锁定协议并自动映射通道；各源可锁不同协议。多源时 `source` 必填（缺省得到引导性错误）。返回解码计划；发 list_changed。 |
| `unlock_protocol` | `source?` | 解除某源协议锁；全部解除回 SOURCE_LOCKED。 |
| `save_profile` | `name`、`description?` | 把当前源定义 + 协议锁固化为 `profiles/<name>.json`（可 git/共享）。 |

### 阶段 READY（追加 7 个，共 18；均带 `source` 参数，唯一源可省）

| 工具 | 参数 | 行为 |
|---|---|---|
| `run_decode` | `overrides?`（如 `{"baud": 115200}`；改参数即重建解码节点，上游命中缓存） | 执行图；返回事件计数摘要（按 kind/错误数）+ 首屏预览（前 20 事件表）。 |
| `get_events` | `source?`、`kind?`、`t_min/t_max?`、`has_errors?`、`limit=50`、`offset=0` | 按源分页事件（Markdown 表）。 |
| `export_events` | `format=json/csv/md`、`source?`、`path?` | 按源落盘 `out/<capture_id>/events.<fmt>`（制品目录按源天然隔离）。 |
| `render_timing` | `source?`、`t_min/t_max?`、`max_frames=60`、`dpi?` | 该源数字时序图 PNG（内联 ImageContent）+ 编号对应 Markdown 表 + 制品清单。 |
| `render_analog` | `source?`、`channel?`、`t_min/t_max?` | 该源模拟波形图（仅模拟源，否则引导性错误）。 |
| `inspect_graph` | `source?` | 该源解码 DAG（节点/边/参数）文本形式。 |
| `redecode` | `params`、`source?` | `run_decode(overrides)` 的别名语义化入口。 |

> `run_decode` 与 `redecode` 合并为一个实现，两个名字降低 LLM 误用率；如嫌冗余可在配置中只启用其一。

## 错误翻译表

| 领域错误 | MCP 表现 |
|---|---|
| `UnknownFormatError` | `INVALID_PARAMS`，消息含 `tried_rules` |
| `GraphValidationError` | `INVALID_PARAMS`，指明规则与详情 |
| `StageGateError` | `INVALID_PARAMS`，"该工具需 X 阶段；当前 Y；下一步调用 lock_*" |
| `NodeError` | `INVALID_PARAMS`，含 node_id 与输入摘要（DecodehubError 家族统一；非领域异常才译 INTERNAL_ERROR） |
| 事件级解码错误 | 正常结果中的 WARN 行（非错误） |

## 客户端接入

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

亦提供 console script `decodehub-mcp`。工作目录约定：制品写入 CWD 下 `out/`（MCP 客户端的工作目录即用户可见位置）。

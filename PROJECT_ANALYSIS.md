# Financial Analysis Market 项目工程分析

## 1. 项目概述

Financial Analysis Market（简析）是一个面向 A 股研究的本地化工作台，当前版本为 `0.1.0`。项目将行情、财务、公告、新闻、技术指标、规则评分、五日统计预测、多 Agent 研究和多轮对话整合在同一个 Web 页面中。

项目的主要特点是：

- 后端仅使用 Python 标准库，无第三方运行时依赖。
- 前端使用原生 HTML、CSS 和 JavaScript，不需要 Node.js 或前端构建。
- 通过 `Context` 注册和获取服务，以事件中心发布领域事件。
- 同时使用确定性规则模型和 Kimi 大模型，二者职责分离。
- 提供两种独立的多 Agent 模式：并行专家研究和顺序多轮对话。
- 对话过程通过 NDJSON 流输出公开消息和 Trace 事件，支持实时展示、历史切换和 JSON 导出。

本项目用于数据研究与学习，不构成投资建议。评分、估值区间和五日预测均为模型结果，不应被理解为收益承诺或买卖指令。

## 2. 技术栈与运行依赖

| 层次 | 技术 |
| --- | --- |
| 语言 | Python 3.11+ |
| Web 服务 | `http.server.ThreadingHTTPServer` |
| HTTP 客户端 | `urllib.request` |
| 数据模型 | `dataclasses` |
| 并发 | `ThreadPoolExecutor`、`threading.Lock` |
| 大模型 | Kimi Chat Completions API，结构化 JSON Schema 输出 |
| 前端 | 原生 HTML、CSS、JavaScript、SVG |
| 实时传输 | NDJSON，每个 JSON 对象占一行 |
| 打包 | `setuptools`、`pyproject.toml` |
| 外部数据 | 东方财富为主，腾讯行情为备用 |

项目没有数据库、Web 框架、前端框架或可观测性平台依赖。会话、缓存和 Trace 默认都保存在当前服务进程内。

## 3. 快速开始

### 3.1 配置模型

仅使用规则模型时，不需要配置 API Key。需要多 Agent 功能时，设置：

```bash
export KIMI_API_KEY="你的 API Key"
```

也支持 `MOONSHOT_API_KEY`。其他可选配置见“配置说明”。

### 3.2 启动 Web 服务

在仓库根目录执行：

```bash
python3 -m financial_analysis_market --web
```

默认访问地址：

```text
http://127.0.0.1:8000
```

可指定监听地址和端口：

```bash
python3 -m financial_analysis_market --web --host 127.0.0.1 --port 8001
```

### 3.3 使用 CLI

```bash
# 单次股票分析，默认启用 Kimi 多专家研究
python3 -m financial_analysis_market 贵州茅台

# 仅运行确定性规则和统计模型
python3 -m financial_analysis_market 贵州茅台 --no-ai

# 输出完整 JSON
python3 -m financial_analysis_market 贵州茅台 --no-ai --json

# 股票池筛选
python3 -m financial_analysis_market --screen --limit 20
```

安装为命令行工具后，也可以使用：

```bash
python3 -m pip install -e .
fam 贵州茅台 --no-ai
fam --web
```

多 Agent 连续对话目前通过 Web 页面和 `/api/chat/stream` 提供，CLI 尚未提供交互式对话命令。

## 4. 工程目录与模块职责

```text
financial_analysis_market/
├── pyproject.toml                    # 包信息、命令行入口和静态资源打包
├── PROJECT_ANALYSIS.md               # 本文档
└── financial_analysis_market/
    ├── __init__.py                   # 导出 AnalysisEngine、create_engine 和版本号
    ├── __main__.py                   # python -m 入口
    ├── cli.py                        # CLI 参数解析与文本/JSON 输出
    ├── engine.py                     # 用例编排与服务组装
    ├── runtime.py                    # 精简服务注册表和事件中心
    ├── web.py                        # HTTP 路由、JSON API、NDJSON 流和静态资源
    ├── models.py                     # 股票、行情、财报、公告、新闻等不可变模型
    ├── errors.py                     # 可展示业务异常、数据源异常和模型异常
    ├── providers/
    │   └── eastmoney.py              # 股票搜索、行情、财报、板块、公告和新闻适配
    ├── analysis.py                   # 技术面、历史表现、基本面摘要和风险规则
    ├── scoring.py                    # 好股票、好价格、好时机三维评分
    ├── forecast.py                   # 五日统计预测和多因子修正
    ├── screener.py                   # 大市值候选池并发筛选与缓存
    ├── kimi.py                       # Kimi API 客户端、结构化响应校验和重试
    ├── agents.py                     # 五专家并行研究与主编汇总
    ├── conversation.py               # 五角色顺序对话、会话状态和公开消息交接
    ├── tracing.py                    # Trace、Span、公开消息和用量事件记录
    └── static/
        ├── index.html                # 研究工作台与对话工作台
        ├── app.css                   # 页面布局、消息气泡和 Trace 时间线样式
        └── app.js                    # 数据渲染、筛选、图表、流式对话和会话恢复
```

## 5. 总体架构

```text
浏览器原生 UI
  ├─ 股票分析、历史、估值、预测和筛选视图
  └─ 多 Agent 对话 + Trace 时间线
                 │ JSON / NDJSON
                 ▼
web.py：ThreadingHTTPServer
                 │
                 ▼
engine.py：AnalysisEngine
  ├─ analyze() ───────┐
  ├─ screen()         │
  └─ chat()           │
                      ▼
             Context 服务注册表
  ┌──────────────┬──────────────┬──────────────┐
  ▼              ▼              ▼              ▼
数据提供层      确定性分析层     AI 研究层       对话与追踪层
Eastmoney       StockAnalyzer   ResearchTeam    ConversationService
Tencent 备用    ThreeFactorScorer KimiClient     TraceRecorder
                WeeklyForecaster
                StockScreener
```

`create_engine()` 是组合根，负责创建 provider、分析器、评分器、预测器、筛选器、Kimi 客户端、研究团队和对话服务，并注册到 `Context`。`AnalysisEngine` 不直接硬编码各服务的构造过程，而是通过服务名获取依赖。

`Context.emit()` 当前用于发布 `analysis/started`、`stock/resolved`、`market/loaded`、`analysis/completed`、筛选事件以及并行研究事件。它属于进程内同步事件中心，不是分布式消息总线。

## 6. 核心业务流程

### 6.1 单股票分析流程

`AnalysisEngine.analyze()` 的执行顺序如下：

1. 根据名称或六位代码解析 A 股。
2. 获取实时行情和最多 320 根历史日线。
3. 获取最近四期财务报告；财务接口失败时降级为空数据并记录警告。
4. 获取公告、行业板块和个股新闻；各数据源可独立降级。
5. 获取三大指数和行业热点环境。
6. 计算技术指标、历史统计和基本面摘要。
7. 计算三维评分和公告标题事件结果。
8. 生成未来五个工作日的统计预测、日内区间和不确定性区间。
9. 汇总规则风险、公告风险和预测风险。
10. 可选运行五专家并行研究与主编汇总。
11. 返回包含股票、行情、历史、财务、评分、预测、AI 结论、数据来源和警告的完整字典。

单次分析的输出既可以由 CLI 打印，也可以由 Web API 序列化为 JSON。

### 6.2 五专家并行研究

`agents.py` 中的 `ResearchTeam` 是一种“并行专家 + 主编”模式：

```text
                       ┌─ 基本面 Agent ─┐
                       ├─ 估值 Agent ───┤
事实快照与规则结果 ────┼─ 市场 Agent ───┼──→ 主编 Agent → 综合研究结论
                       ├─ 公告 Agent ───┤
                       └─ 风险 Agent ───┘
```

主要行为：

- 五个专家基于各自需要的事实子集独立调用模型，默认最多三个并发 worker。
- 主编读取五个专家的公开结构化结果，形成标题、摘要、优势、风险、观察项和研究结论。
- 模型不能修改确定性三维评分和五日预测。
- 单个专家失败时记录降级，主编可继续使用其他专家结果。
- 主编失败时使用规则结果生成兜底摘要。
- 输入事实快照经过 SHA-256 摘要标识，成功结果缓存 600 秒。
- Token 用量按专家和主编累加，事件通过 `Context` 发布。

该模式适合一次性生成股票研究报告，不负责连续追问。

### 6.3 五角色顺序对话

`conversation.py` 中的 `ConversationService` 是另一种独立模式：

```text
用户问题
   │
   ▼
研究主持 Agent
   │ 公开任务与研究重点
   ▼
基本面 Agent
   │ 公开回应 + handoff
   ▼
市场 Agent
   │ 回应上位观点 + handoff
   ▼
风险审查 Agent
   │ 反证、矛盾与风险边界
   ▼
主编 Agent
   │
   ▼
面向用户的最终回复
```

与并行研究不同，顺序对话中的后续 Agent 会读取 `current_turn_messages`，因此能够回应前一位 Agent，而不是仅生成五份独立报告。

每轮提问执行五次模型调用，角色顺序固定。模型输入包括：

- 当前股票事实快照。
- 最近的用户与主编历史消息。
- 当前用户问题。
- 本轮已经产生的公开 Agent 消息和交接事项。

首轮对话通过 `analyze(query, use_ai=False)` 构建事实快照，避免重复运行并行专家团队。后续追问复用该快照，因此行情和财报不会自动刷新；需要新数据时应新建会话。

单个角色失败时，服务记录失败 Span，发送公开降级消息并继续后续角色。主编失败时返回基于规则评分的兜底答案，整轮状态标记为 `degraded`。

### 6.4 股票池筛选

`StockScreener` 按总市值选取前 N 只非 ST A 股作为精简候选池，N 范围为 5～50，默认 20。

- 默认最多五个并发 worker 获取候选股票数据。
- 单只失败会增加失败计数，不阻断其他股票。
- 全部失败时抛出 `DataSourceError`。
- 输出综合及各维度 TOP 6、符合条件股票和前端筛选所需字段。
- 三维分数均不低于 65 的股票进入 `qualified`，最多展示十只。
- 结果按候选池大小缓存 600 秒。

候选池存在明显的大市值样本偏差，不代表全市场扫描结果。

## 7. 确定性分析模型

### 7.1 技术与历史分析

`StockAnalyzer` 计算：

- MA5、MA20、MA60、MA244。
- EMA12、EMA26、MACD DIF、DEA 和柱值。
- RSI14、ATR14。
- 5、20、60、120、244 日动量或收益。
- 20 日支撑与压力。
- 日波动率和按 244 个交易日折算的年化波动率。
- 52 周高低点、价格位置、最大回撤。
- 样本内年度首末收盘价收益、60 日上涨占比、最佳和最差单日。

技术信号由价格相对 MA20、MA5 与 MA20 的关系、DIF 与 DEA 的关系组成，分为偏强、偏弱和震荡。

### 7.2 三维评分

`ThreeFactorScorer` 的综合权重为：

| 维度 | 权重 | 主要因素 |
| --- | ---: | --- |
| 好股票 | 45% | 年化 ROE、营收和利润成长、毛利率、现金含量、多期稳定性 |
| 好价格 | 35% | 分行业 PE、PB、盈利法与净资产法估值参考 |
| 好时机 | 20% | 趋势、动量、RSI、波动、板块强弱、公告标题事件 |

行业模板包括金融、消费、医药、科技、周期、制造和综合。不同模板使用不同 PE/PB 参考区间；金融行业不横向比较毛利率和现金流。

缺失子因子不会直接按零分参与，而是在有效因子上重新归一化权重；整个维度没有有效因子时得零分。

综合结论规则：

- 三个维度均不低于 70：`三好共振`。
- 综合分不低于 70：`重点观察`。
- 综合分不低于 55：`等待更好条件`。
- 其他：`谨慎研究`。

公告只分析近 90 日标题关键词。风险关键词优先于利好关键词，未命中事件按中性处理，不能替代公告全文。

### 7.3 五日预测

`WeeklyForecaster` 使用以下基准和修正：

- 5、20、60 日对数收益动量。
- AR(1) 均值回复，系数限制在 `-0.35～0.35`。
- 微弱的星期季节性。
- 行业周期、市场热点、大盘环境、公司公告、新闻舆情和业绩动量修正。
- 因子总调整上限及随预测日衰减。
- ATR 与历史波动估计日内高低价和振幅。
- 基于波动率、预测 horizon 和事件不确定性的约 80% 收盘区间。

预测日期按后续工作日推算，未接入交易所法定节假日和临时休市日历。输出应被理解为基准统计情景，不是实际价格承诺。

## 8. Kimi 客户端设计

`KimiClient` 对模型 API 做了集中封装：

- 使用 Chat Completions 接口。
- 使用 JSON Schema 约束结构化输出。
- 系统提示要求只能使用输入事实，禁止编造数据、公告全文和收益承诺。
- 检查空结果、长度截断、非 JSON 和非对象响应。
- 对 429、部分 5xx 和连接异常进行一次重试。
- 统一转换鉴权、权限、限流和连接错误。
- 返回内容、Token 用量、模型名和请求 ID。

当前模型调用以完整结构化响应为单位，不做逐 Token 推理或文本流式输出。对话页面中的“实时”指 Agent 步骤和公开消息级实时更新。

## 9. 对话会话与链路追踪

### 9.1 会话状态

每个 `ChatSession` 保存：

- Session ID 和股票查询条件。
- 首轮构建的股票事实快照。
- 最近 16 条用户与主编消息。
- 最近 12 轮完整公开对话、Trace 事件、状态和 Token 用量。
- 用于串行化同一会话提问的锁。

服务默认最多保留 24 个会话，采用有序字典淘汰最久未使用的会话。所有状态都在内存中，服务重启后无法恢复；浏览器 `localStorage` 只保存 Session ID，不是会话数据库。

不同会话可以并行处理，同一会话的提问通过会话锁串行执行，避免历史消息交叉写入。

### 9.2 Trace 与 Span 层级

每轮提问创建独立 Trace，根 Span 下包含一个数据上下文 Span 和五个 Agent Span：

```text
Trace：一轮多 Agent 对话
└─ 根 Span
   ├─ 数据快照 Span
   ├─ 研究主持 Span
   ├─ 基本面 Span
   ├─ 市场 Span
   ├─ 风险审查 Span
   └─ 主编 Span
```

每个事件带有：

- `sequence`：轮次内递增序号。
- `trace_id`：一轮对话的追踪标识。
- `session_id`：跨轮会话标识。
- `span_id` 与 `parent_span_id`：调用层级。
- `timestamp`：上海时区 ISO 时间。
- `event`、`name`、`status`。
- 可选的 Agent、耗时、Token、模型请求 ID 和公开消息。

主要事件类型：

| 事件 | 含义 |
| --- | --- |
| `trace/started` | 新一轮对话开始 |
| `span/started` | 数据或 Agent 步骤开始 |
| `span/completed` | 步骤完成，包含耗时和用量 |
| `span/failed` | 步骤失败，包含可展示错误 |
| `user/message` | 用户向研究主持发送公开消息 |
| `agent/message` | Agent 向下一个角色或用户发送公开消息 |
| `trace/completed` | 整轮完成或部分降级 |
| `trace/failed` | 整轮无法完成 |

Trace 只记录公开消息和调用元数据，不记录或展示模型思维链，也不把完整模型提示词和事实快照写入前端 Trace。

### 9.3 前端投影与导出

浏览器消费 NDJSON 后形成两个视图：

- 公开对话视图：展示用户消息、Agent 间消息、交接事项和主编最终回复。
- Trace 时间线：展示事件顺序、Span 层级、状态、耗时、Token 和请求 ID。

每轮 Trace 都可以单独切换查看，并可导出为 JSON。刷新页面后，前端根据 Session ID 请求会话快照，重建消息和轨迹。

## 10. HTTP API

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/healthz` | 健康检查 |
| GET | `/api/capabilities` | 并行研究和顺序对话的模型可用性 |
| POST | `/api/analyze` | 单股票完整分析 |
| POST | `/api/screen` | 股票池筛选 |
| POST | `/api/chat/stream` | 多 Agent 对话，NDJSON 流式响应 |
| GET | `/api/chat/session?id=...` | 获取会话、历史轮次和 Trace 快照 |

### 10.1 单股票分析

```json
{
  "query": "贵州茅台",
  "use_ai": true
}
```

### 10.2 股票池筛选

```json
{
  "limit": 20
}
```

### 10.3 多 Agent 对话

新会话：

```json
{
  "message": "请讨论这家公司当前最值得跟踪的一点",
  "query": "贵州茅台",
  "session_id": null
}
```

继续追问：

```json
{
  "message": "进一步分析刚才提到的风险",
  "query": "贵州茅台",
  "session_id": "chat-..."
}
```

响应类型为 `application/x-ndjson`。事件示例：

```json
{"type":"trace","trace":{"event":"span/started","name":"市场 Agent处理中","status":"running"}}
{"type":"trace","trace":{"event":"agent/message","message":{"agent":"市场 Agent","target":"风险审查 Agent","content":"..."}}}
{"type":"done","session_id":"chat-...","trace_id":"trace-...","status":"completed"}
```

请求格式错误在响应头发送前返回 HTTP 400。流开始后的业务或模型错误通过 `type=error` 事件返回，因此调用方不能只依赖 HTTP 状态码判断整轮是否成功。

## 11. 数据源、缓存与降级

### 11.1 数据源

东方财富提供：

- A 股名称和代码搜索。
- 实时行情和历史 K 线。
- 最近四期业绩报表。
- 行业板块行情、指数和热点。
- 公司公告和个股新闻搜索。

实时行情与历史行情在主源失败后可以使用腾讯备用接口。财务、公告和新闻没有同等备用源，失败时通过警告和中性因子降级。

### 11.2 重试与缓存

- Provider HTTP 请求默认超时 12 秒，最多三次尝试，并逐步增加等待时间。
- 行业板块数据缓存 600 秒。
- 市场环境数据缓存 300 秒。
- 股票池筛选结果缓存 600 秒。
- 成功的并行专家研究结果缓存 600 秒。
- 多 Agent 对话不缓存模型答案，但复用会话内股票事实快照。

### 11.3 降级原则

项目优先保留确定性分析能力：AI 未配置或失败时，规则评分、历史统计、筛选和统计预测仍可使用。数据源局部失败时，相关因子按中性或数据不足处理，并在结果中记录警告。

## 12. 配置说明

| 环境变量 | 必填 | 说明 |
| --- | --- | --- |
| `KIMI_API_KEY` | AI 功能必填 | Kimi API Key |
| `MOONSHOT_API_KEY` | 可替代上一项 | Moonshot API Key |
| `KIMI_BASE_URL` | 否 | 自定义 API 地址，默认根据 Key 类型选择 Kimi Coding 或 Moonshot 地址 |
| `KIMI_MODEL` | 否 | 默认 `k3.1-pro` 或 `kimi-k3.1-pro`，取决于 Key 类型 |
| `KIMI_REASONING_EFFORT` | 否 | `low`、`high`、`max`，默认 `low` |
| `KIMI_TIMEOUT` | 否 | 单次模型请求超时秒数，默认 90 |

API Key 只应通过环境变量或本地未跟踪配置提供，不应写入源码、文档、Trace 或提交到仓库。

## 13. 错误处理与并发模型

业务异常分为：

- `InvalidStockQuery`：股票输入无效或匹配不唯一。
- `DataSourceError`：外部数据源不可用或数据不足。
- `ModelError`：模型响应、连接或结构化内容异常。
- `FinancialAnalysisError`：可安全展示的业务异常基类。

普通 JSON API 将输入错误映射为 400，数据源错误映射为 502，未知错误记录服务端日志并返回 500。

Web 服务使用多线程，研究专家和候选池筛选使用线程池。共享缓存和会话字典有全局锁，单个对话会话另有独立锁。当前没有请求取消、任务队列、背压、跨进程锁或分布式限流，因此更适合本地单用户研究，而非直接作为高并发公共服务部署。

## 14. 工程优势

1. **依赖精简**：标准库即可完成数据获取、分析、Web 服务和流式对话，部署成本低。
2. **规则与模型分工明确**：模型解释事实和公开讨论，不直接改写确定性评分与预测。
3. **局部失败可降级**：数据、专家、主编和对话角色均有明确的降级路径。
4. **两种 Agent 拓扑各负其责**：并行研究强调信息覆盖，顺序对话强调观点交接和连续追问。
5. **追踪面向用户可理解**：公开展示角色、消息、状态和用量，而不是暴露冗长思维链。
6. **前端无需构建**：静态资源直接由 Python 服务提供，打包时随 wheel 一起发布。
7. **数据结构清晰**：外部数据先转换成不可变 dataclass，再进入分析和模型层。

## 15. 当前限制与改进建议

### 15.1 自动化测试缺失

当前仓库没有既有测试目录和测试配置。核心评分、指标、预测、Provider 解析、Agent 降级和 NDJSON 协议主要依赖运行验证，回归保护不足。

建议优先增加：

- 技术指标、评分边界和缺失数据的单元测试。
- 预测日期、波动区间和因子上限测试。
- Provider 响应 fixture 与主备切换测试。
- 五角色消息交接、失败降级、会话淘汰和锁行为测试。
- HTTP 流式协议、会话恢复和前端事件投影测试。

### 15.2 会话与 Trace 不持久化

服务重启或会话被淘汰后，浏览器保存的 Session ID 会失效。对话快照也会随时间变旧。

建议增加 SQLite 持久化、会话过期策略、显式“刷新事实快照”操作，以及数据时间戳提示。

### 15.3 缺少请求取消与刷新控制

长时间模型调用期间，前端不能主动取消当前轮次；断开后服务端也不一定立即停止后续模型请求。

建议引入取消令牌、客户端断连检测和每轮预算控制，并允许用户停止当前讨论。

### 15.4 Token 成本与延迟

顺序对话每轮固定调用五次模型，每个角色都会接收事实快照和公开历史。连续追问时成本和等待时间会累积。

建议按问题路由必要角色、压缩历史上下文、对稳定事实使用摘要，并保留“完整五角色讨论”和“轻量追问”两种模式。

### 15.5 研究数据边界

- 公告和新闻主要使用标题或摘要关键词，未解析公告全文与财务附注。
- 预测未使用交易所日历，也未覆盖所有突发事件和交易规则。
- 候选池按大市值选择，存在样本偏差。
- 数据来自第三方公开接口，可用性、字段约定和使用条款需要持续核对。

这些限制已在页面和部分输出中提示，后续仍应优先完善原文证据链和交易日历。

### 15.6 本地服务安全边界

当前服务没有用户认证、权限隔离和 CSRF 防护，默认绑定 `127.0.0.1` 是合理的本地部署方式。不应在未增加安全控制的情况下直接暴露到公网。

## 16. 推荐演进顺序

1. 建立核心单元测试和 HTTP 协议回归测试。
2. 增加会话持久化、事实快照刷新和对话取消。
3. 优化上下文大小，引入轻量追问与动态角色路由。
4. 完善公告全文、财务附注和交易日历证据。
5. 根据实际部署需求增加任务队列、限流、认证和外部可观测性导出。
6. 在保持标准库精简优势的前提下，按真实需求评估是否引入成熟 Web、数据库或前端依赖。

## 17. 验证与交付检查

当前工程可使用以下命令进行基础检查：

```bash
python3 -m compileall -q financial_analysis_market
node --check financial_analysis_market/static/app.js
python3 -m pip wheel . --no-deps -w dist
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/api/capabilities
```

多 Agent 对话已验证真实行情与真实 Kimi 五角色完整链路；HTTP NDJSON、会话恢复和打包静态资源也已通过脚本检查。由于项目没有既有自动化测试，上述检查不能替代持续集成中的回归测试。

## 18. 总结

该项目已经形成一个完整的本地 A 股研究工作台闭环：外部数据进入统一模型后，由确定性分析器生成可重复的规则结果，再由两类多 Agent 系统分别完成并行研究和顺序讨论，最后通过原生 Web UI、CLI 和 JSON/NDJSON API 输出。

下一阶段的重点不应只是增加更多 Agent，而应优先补足测试、持久化、取消与上下文成本控制，并加强原始公告和财务附注证据，从而在保持工程精简的同时提高可靠性和可维护性。

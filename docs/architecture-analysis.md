# OpenCode Agent Framework — 深度架构分析与扩展设计方案

> 基于源码全量遍历，所有结论均有文件路径 + 行号支撑

---

# 第一阶段：OpenCode 架构分析

## 1. Runtime 体系

### 1.1 Session Runtime

**运行模式**：同一个进程内并存两条路径——V1（旧）和 V2（新，event-sourced）。

| | V1 路径 | V2 路径 |
|---|---|---|
| **入口** | `packages/opencode/src/session/prompt.ts` | `packages/core/src/session/runner/llm.ts` |
| **数据源** | `MessageTable` + `PartTable` | `SessionMessageTable`（事件溯源） |
| **模型客户端** | AI SDK (`streamText`) | `@opencode-ai/llm` 原生客户端 |
| **并发控制** | `run-state.ts` 互斥 Fiber | `run-coordinator.ts` FIFO 序列化 |
| **启停标志** | `experimentalEventSystem` flag | `OPENCODE_EXPERIMENTAL_EVENT_SYSTEM` |

**Session 生命周期**：

```
create()                           # prompt.ts:709
  → SessionID.descending()         # ses_<ulid>
  → Info { id, parentID, agent, model, ... }
  → EventV2Bridge → SessionV1.Event.Created

fork()                             # prompt.ts:733
  → 复制消息到新 ID
  → 追加 (fork #N) 后缀

prompt()                           # prompt.ts:1155
  → createUserMessage()            # 解析文件/Agent/MCP
  → loop() → runLoop()             # 主循环
    → processor.process()          # LLM 流 + 工具执行
    → 返回 "compact"|"stop"|"continue"
```

**销毁**：`remove()` 先递归取消子 session (`parentID` 自引用)，再删除消息。

### 1.2 Agent Runtime

**入口**：[agent.ts:84](packages/opencode/src/agent/agent.ts#L84) `Agent.Service`

Agent 定义为 `Info` 结构体（[agent.ts:35-55](packages/opencode/src/agent/agent.ts#L35-L55)）：
- `name`, `mode` ("primary"|"subagent"|"all"), `native`, `hidden`
- `permission`: `PermissionV1.Ruleset`
- `model?`: `{modelID, providerID}`
- `prompt?`: 自定义 system prompt
- `temperature?`, `topP?`, `steps?`

**7 种内置 Agent**：

| Agent | Mode | 功能 |
|---|---|---|
| `build` (L141) | primary | 默认，全部工具可用 |
| `plan` (L156) | primary | 只读+plan 文件可写 |
| `general` (L182) | subagent | 通用子代理 |
| `explore` (L196) | subagent | 代码搜索 |
| `compaction` (L219) | primary(hidden) | 上下文压缩 |
| `title` (L234) | primary(hidden) | 标题生成 |
| `summary` (L250) | primary(hidden) | 对话摘要 |

**动态 Agent 创建**：`generate()` 使用 `PROMPT_GENERATE` + `generateObject()` 从自然语言生成 Agent 配置（identifier + whenToUse + systemPrompt）。

### 1.3 Task Runtime

**入口**：[task.ts:81](packages/opencode/src/tool/task.ts#L81) `TaskTool`

本质是**有状态子线程管理器**：
```
TaskTool.execute(params, ctx):
  1. agent.get(params.subagent_type)          # 查 Agent
  2. sessions.create({parentID, title, permission})  # 创建子 session
  3. background.start({id, run})              # 启动后台 Job
  4. 如果是 foreground: acquireUseRelease
     → race(background.wait, background.waitForPromotion)
     → 返回结果或 promote 到 background
```

**Background Job** 系统 ([job.ts](packages/opencode/src/background/job.ts))：
- `start()`: 创建 Active 记录，fork fiber
- `extend()`: 续接已有 job（继续发送上下文）
- `wait()`: 等待完成
- `promote()`: 转为后台运行
- `cancel()`: 取消

### 1.4 Tool Runtime

**双注册表架构**：

| | App Registry (V1) | Core Registry (V2) |
|---|---|---|
| **文件** | `packages/opencode/src/tool/registry.ts` | `packages/core/src/tool/registry.ts` |
| **Context** | 富: sessionID, messageID, agent, abort, `ask()` | 简: sessionID, agent, assistantMessageID, toolCallID |
| **生命周期** | 静态（Effect 生命周期） | Scope-based（离开 Scope 自动注销） |
| **输出** | `ExecuteResult {title, output, metadata}` | `ToolOutput` + `ToolOutputStore` |

**15 个 App 级工具**：read, write, edit, bash, glob, grep, task, webfetch, websearch, question, skill, todowrite, apply_patch, lsp, plan_exit

**12 个 Core 级工具**（V2）：同上但不含 task, lsp, plan_exit, repo_clone, repo_overview（后两者在 TODO 列表）

## 2. Prompt System

### 完整 Pipeline

```
用户输入
  ↓
Agent.prompt（如果有自定义）OR SystemPrompt.provider(model)  # system.ts:24
  ↓
environment()                                                 # system.ts:54
  model identity + <env> + <available_references>
  ↓
skills()                                                      # system.ts:92
  <available_skills> XML
  ↓
prepare() 合并                                               # request.ts:56
  system = [base | agent_prompt, environment, skills, user.system].join("\n")
  ↓
resolveTools() 注入 tool definitions                         # request.ts:148
  ↓
toModelMessages() 注入历史消息                                # message-v2.ts:145
  ↓
LLM.stream(request)                                          # processor.ts:960
```

### 7 套 Prompt 模板

按模型自动路由（[system.ts:24-37](packages/opencode/src/session/system.ts#L24-L37)）：

| 匹配条件 | 模板文件 | 行数 |
|---|---|---|
| gpt-4 / o1 / o3 | beast.txt | 148 行，自主 Agent 风格 |
| gpt + codex | codex.txt | 80 行，OpenCode 品牌 |
| gpt (其他) | gpt.txt | 98 行 |
| gemini- | gemini.txt | 156 行，安全导向 |
| claude | anthropic.txt | 106 行 |
| trinity | trinity.txt | 98 行 |
| 默认 | default.txt | 96 行 |

## 3. Context Engine

### 压缩机制

**V2 Core** ([compaction.ts](packages/core/src/session/compaction.ts))：
- Token 估算：`4 chars = 1 token`
- 触发条件：`estimate(request) > context - max(output, buffer)`
- 方式：Head/Tail 分割 → LLM 生成摘要 → 插入 `<conversation-checkpoint>`

**V1 App** ([compaction.ts](packages/opencode/src/session/compaction.ts))：
- 621 行，支持 prune（擦除旧工具输出）
- 摘要模板：Goal / Constraints / Progress / Key Decisions / Next Steps / Critical Context / Relevant Files

### 上下文来源

| 来源 | 实现 |
|---|---|
| 历史消息 | `SessionMessageTable` 加载，compaction 后过滤 |
| 文件附件 | `createUserMessage()` 中的 resolvePart → Read |
| MCP | `MCPCatalog.convertTool()` → 动态 tool |
| Tool Result | 直接拼接进 assistant content |
| 系统环境 | `environment()` 注入 `<env>` 块 |
| 项目引用 | `Reference.Service` 注入 `<available_references>` |

## 4. Agent Loop

### V1 主循环（当前主力）

```python
# prompt.ts runLoop() L1184-1437
while True:
    status = "busy"
    msgs = filterCompactedMessages(sessionID)
    user, assistant, finished, tasks = latest(msgs)

    # 退出条件
    if finished and finish != "tool-calls" and no_pending_tools:
        break

    step += 1
    model = resolve_model(providerID, modelID)

    # 处理待办
    task = tasks.pop()
    if subtask:   handleSubtask(); continue
    if compaction: compaction.process(); continue
    if overflow:  compaction.create(); continue

    agent = get_agent(user.agent)
    is_last_step = step >= agent.steps

    # 创建 assistant 消息
    msg = create_message("assistant")

    # 构建请求
    tools = resolveTools(agent, session, model)
    system = [skills, environment, instructions]
    model_msgs = toModelMessages(msgs, model)

    # 流 + 工具循环
    result = processor.process({
        system, messages: model_msgs, tools,
        model, agent, permission
    })

    if structured_output or result == "stop": break
    if result == "compact": compact(); continue
```

### V2 Agent Loop（新架构）

```python
# runner/llm.ts run() L349 + runTurn() L335 + runTurnAttempt() L164

def run(sessionID, force):
    fail_interrupted_tools(sessionID)
    while has_pending_work:
        step = 1
        while needs_continuation:
            result = runTurnAttempt(sessionID, promotion, step)
            needs_continuation = result.needs_continuation
            step = result.step + 1
        # check queue for next work item

def runTurnAttempt(sessionID, promotion, step):
    agent = select_agent(session.agent)
    system = load_system_context(agent)     # skills + env + references
    promote_pending_input(promotion)        # inject user messages
    model = resolve_model(session)          # catalog + auth
    entries = load_history(sessionID)       # from SessionMessageTable
    tools = materialize_tools(permissions)  # registry → definitions
    request = build_request(model, system, entries, tools)

    # Compaction check (may throw → restart turn)
    compact_if_needed()

    # Stream + settle tools in FiberSet
    llm.stream(request).forEach(event):
        publish(event)                      # event-sourced持久化
        if tool_call and not provider_executed:
            FiberSet.run(settle_tool(call)) # 并行执行

    return { needs_continuation, step }
```

## 5. Memory

### 现状：无长期记忆系统

| 类型 | 是否存在 | 说明 |
|---|---|---|
| 短期记忆 | 部分 | Session 消息持久化 (SQLite) + Compaction 摘要 |
| 长期记忆 | **无** | 无项目知识库、用户偏好存储 |
| 向量检索 | **无** | 无 embedding 生成、无 Qdrant/Milvus/Chroma |
| Redis | **无** | console/cloud 有 Upstash Redis 用于限流，非记忆 |
| 持久化 | SQLite | Drizzle ORM + WAL 模式 |

### 等价"记忆"机制

1. **Session 事件溯源**：所有对话作为事件持久化到 `SessionMessageTable`
2. **Compaction 摘要**：LLM 生成的结构化摘要（Goal/Progress/Decisions/Next Steps）
3. **Session Context Epoch**：系统上下文基线快照，变更时重新计算
4. **Skill 系统**：Markdown 文件作为可注入知识
5. **项目配置**：`.opencode/config.*` 持久化用户设置

---

# 第二阶段：知识库

## 模块索引

### Runtime
- **职责**：Session/Agent/Task/Tool 生命周期管理
- **核心文件**：
  - `packages/opencode/src/session/prompt.ts` — V1 Agent Loop
  - `packages/core/src/session/runner/llm.ts` — V2 Agent Loop
  - `packages/core/src/session/execution/local.ts` — 执行路由
  - `packages/core/src/session/run-coordinator.ts` — 并发控制
- **依赖**：Effect-TS (Fiber/Scope/Layer), SQLite (Drizzle)
- **关键调用链**：`prompt() → loop() → runLoop() → processor.process() → llm.stream()`

### Agent
- **职责**：Agent 定义、权限、生命周期
- **核心文件**：`packages/opencode/src/agent/agent.ts`
- **关键函数**：`get()`, `list()`, `defaultInfo()`, `generate()`

### Prompt
- **职责**：System Prompt 组装、模型适配
- **核心文件**：
  - `packages/opencode/src/session/system.ts` — 模板选择 + 环境注入
  - `packages/opencode/src/session/llm/request.ts` — 完整请求组装
  - `packages/opencode/src/session/prompt/*.txt` — 7 套 Prompt 模板

### Tool
- **职责**：工具注册、执行、输出截断
- **核心文件**：
  - `packages/opencode/src/tool/registry.ts` — V1 注册表
  - `packages/core/src/tool/registry.ts` — V2 注册表（Scope-based）
  - `packages/core/src/tool/tool.ts` — Tool.make() / settle()
  - `packages/core/src/tool/builtins.ts` — 内置工具列表

### Provider
- **职责**：模型目录、API 适配、消息变换
- **核心文件**：
  - `packages/opencode/src/provider/provider.ts` — 2479 行，模型解析核心
  - `packages/opencode/src/provider/transform.ts` — 1544 行，Provider 消息变换
  - `packages/core/src/plugin/models-dev.ts` — models.dev 目录拉取

### MCP
- **职责**：MCP 协议实现、OAuth、工具代理
- **核心文件**：
  - `packages/opencode/src/mcp/index.ts` — 963 行，MCP 服务核心
  - `packages/opencode/src/mcp/catalog.ts` — 工具转换
  - `packages/opencode/src/mcp/oauth-provider.ts` — OAuth 2.0

### Memory
- **职责**：上下文压缩（目前唯一的"记忆"机制）
- **核心文件**：
  - `packages/core/src/session/compaction.ts` — V2 压缩
  - `packages/opencode/src/session/compaction.ts` — V1 压缩 + Prune

### Context
- **职责**：上下文组装、裁剪、Token 控制
- **核心文件**：
  - `packages/core/src/system-context/index.ts` — SystemContext 统一接口
  - `packages/opencode/src/session/overflow.ts` — 溢出检测
  - `packages/core/src/util/token.ts` — Token 估算

### Skill
- **职责**：可加载知识模块
- **核心文件**：
  - `packages/opencode/src/skill/index.ts` — 367 行，Skill 发现+加载
  - `packages/core/src/skill/guidance.ts` — 系统提示注入
  - `packages/core/src/tool/skill.ts` — skill 工具

---

# 第三阶段：方案映射

逐模块分析你的方案在当前 OpenCode 中的支持情况。

## 3.1 Agent Runtime Core

**支持情况**：完全支持

**原因**：Effect-TS (Fiber + Scope + Layer) 提供完整资源生命周期、并发控制、依赖注入。`run-coordinator.ts` 提供 FIFO 序列化执行。V2 `SessionRunner` 的 `runTurn / runTurnAttempt` 提供 Agent 生命周期。

**关键源码依据**：
- [run-coordinator.ts:22-102](packages/core/src/session/run-coordinator.ts#L22-L102) — 并发序列化
- [runner/llm.ts:164-314](packages/core/src/session/runner/llm.ts#L164-L314) — runTurnAttempt
- [runner/llm.ts:349-377](packages/core/src/session/runner/llm.ts#L349-L377) — 主调度

**改造成本**：低
**推荐方案**：直接复用 Effect Layer 体系。新增模块作为新 Service 注册进 Layer 树。

## 3.2 Executive Agent

**支持情况**：部分支持

**原因**：`build` Agent 是事实上的"Executive"——接收用户意图，调用工具，处理结果。但**没有显式计划生成 + 任务图分解 + 监督执行**的能力。当前是反应式对话，不是规划式编排。

**关键源码依据**：
- [agent.ts:141-155](packages/opencode/src/agent/agent.ts#L141-L155) — build agent 定义
- [prompt.ts:1184-1437](packages/opencode/src/session/prompt.ts#L1184-L1437) — runLoop 无计划阶段

**改造成本**：中
**推荐方案**：
```
新增 ExecutiveAgent (extends Agent.Info)
  ↓
  接受用户输入后先调用 plan() → 生成 TaskGraph
  ↓
  分配给 Worker Pool 执行
  ↓
  汇总结果
```
复用现有 `build` Agent 的 Permission + Tool 基础设施，新增 `plannerPrompt` 和 TaskGraph 生成逻辑。

## 3.3 Task Graph (DAG)

**支持情况**：不支持

**原因**：当前只有扁平的 task list（TaskTool 中的 tasks 栈），没有 DAG 依赖关系。`handleSubtask()` 顺序处理，无并发调度、无依赖解析。

**关键源码依据**：
- [task.ts:18-22](packages/opencode/src/tool/task.ts#L18-L22) — TaskPromptOps 接口
- [prompt.ts:259-340](packages/opencode/src/session/prompt.ts#L259-L340) — handleSubtask 顺序执行

**改造成本**：中
**推荐方案**：全新模块
```typescript
// 核心数据结构
interface TaskNode {
  id: string
  taskType: "search" | "code" | "test" | "document"
  dependencies: string[]   // TaskNode.id[]
  spec: {
    description: string
    prompt: string
    requiredCapabilities: string[]
    quality: "low" | "medium" | "high"
    budget: "low" | "medium" | "high"
  }
  status: "pending" | "running" | "completed" | "failed"
  result?: TaskResult
}
```

## 3.4 Worker Pool

**支持情况**：完全支持

**原因**：Subagent session + `task` tool + Background Job 系统已提供：创建 worker session → 执行 → 销毁。支持 foreground/background 模式。FiberSet 支持并行工具执行。

**关键源码依据**：
- [task.ts:92-333](packages/opencode/src/tool/task.ts#L92-L333) — Worker 创建/执行/销毁
- [job.ts](packages/opencode/src/background/job.ts) — 后台 Job 生命周期
- [runner/llm.ts:241-260](packages/core/src/session/runner/llm.ts#L241-L260) — FiberSet 并行

**改造成本**：低
**推荐方案**：扩展 subagent 类型

```
现有:  general, explore
新增:  code, test, document
```

复用现有 `task` tool 的 session create → background.start → wait 流程。

## 3.5 Capability Router

**支持情况**：不支持

**原因**：当前模型选择逻辑是：Agent.model（可选）→ Session.model → 默认 model。没有基于 task capability + quality + budget 的路由。`ProviderTransform` 处理消息变换但不做路由决策。

**关键源码依据**：
- [task.ts:167-170](packages/opencode/src/tool/task.ts#L167-L170) — 模型选择：agent.model ?? 父消息 model
- [agent.ts:45-49](packages/opencode/src/agent/agent.ts#L45-L49) — Agent.model 可选字段

**改造成本**：低（核心逻辑纯代码实现，不依赖 LLM）
**推荐方案**：

```typescript
// 新增：packages/core/src/router/
interface ModelCapability {
  modelID: string
  providerID: string
  costPer1MTokens: number
  speedScore: number      // 1-10
  contextWindow: number
  capabilities: string[]  // ["planning", "code_generation", "search", ...]
}

function route(requirement: CapabilityRequirement, models: ModelCapability[]): ModelRef {
  // 评分公式：纯代码，不用 LLM
  return models
    .map(m => ({
      model: m,
      score: matchCap(m.capabilities, requirement.capabilities) * 50
            + matchQuality(m, requirement.quality) * 20
            + matchCost(m, requirement.budget) * 20
            + matchSpeed(m) * 10
    }))
    .sort((a, b) => b.score - a.score)[0].model
}
```

## 3.6 Model Registry

**支持情况**：完全支持

**原因**：[models-dev.ts:95-128](packages/core/src/plugin/models-dev.ts#L95-L128) 已有完整模型注册表：
- `cost`：分层定价（input/output/cache read/write/tiers/context_over_200k）
- `capabilities`：tools, input modalities, output modalities
- `limit.context`：上下文窗口大小
- `variants`：reasoning effort 变体
- `family`, `status`, `release_date`

**缺失**：缺少语义 capability tag（planning, code_generation 等）。

**关键源码依据**：
- [models-dev.ts:46-98](packages/core/src/plugin/models-dev.ts#L46-L98) — Model schema
- [models-dev.ts:95-128](packages/core/src/plugin/models-dev.ts#L95-L128) — 模型注册

**改造成本**：低
**推荐方案**：在现有 `Model.Capabilities` 上追加 `tags: string[]` 字段，或在 config 中维护 capability → model 映射表（声明式，非 LLM）。

## 3.7 Context Manager 三层隔离

**支持情况**：部分支持

**原因**：
- Session 层隔离：✓（parent-child session hierarchy）
- Task 层隔离：✗（没有 task context filter）
- Worker 层隔离：✗（subagent 拿完整的 parent 上下文 + 自己的 system prompt，没有精简）

**关键源码依据**：
- [subagent-permissions.ts:14-27](packages/opencode/src/agent/subagent-permissions.ts#L14-L27) — 权限派生
- [task.ts:142-158](packages/opencode/src/tool/task.ts#L142-L158) — 子 session 创建
- [prompt.ts:259-340](packages/opencode/src/session/prompt.ts#L259-L340) — handleSubtask 注入上下文

**改造成本**：中
**推荐方案**：Worker 创建时注入精简上下文

```
WorkerContext = TaskSpec.relevantFiles   // 只包含任务需要的文件
             + TaskSpec.prompt           // 任务描述
             + SystemPrompt.minimal      // 最小化系统提示
// NOT: 完整对话历史
// NOT: 不相关的文件内容
```


## 3.9 Reflection Agent

**支持情况**：不支持

**原因**：无触发机制、无 Critic Worker、无修正反馈循环。

**改造成本**：中
**推荐方案**：作为 Task Graph 的 post-condition check

```
TaskGraph.execute():
  每个 TaskNode 完成后:
    if result.status == "failed":
      create CriticTask { depends_on: [failed_node] }
      CriticTask 检查输出、修正
```

## 3.10 Worker Agent 模型绑定分离

**支持情况**：部分支持

**原因**：Agent.model 是 optional 的，可以不绑定。但没有 Capability Router 来自动选择模型。当前是静态 fallback 链：agent.model → session.model → default.model。

**改造成本**：低（依赖 Capability Router 实现）
**推荐方案**：移除所有 Worker Agent 的 `model` 字段，完全由 Router 决策。保留 optional model override 作为用户手动覆盖的 escape hatch。

---

# 第四阶段：最终架构设计

## 总体架构图

```
                        User
                         │
                         ▼
              ┌──────────────────────┐
              │   Executive Agent    │  复用 Agent.Service
              │   (强模型, 长期存活) │  + 新增 Plan 能力
              └──────────┬───────────┘
                         │ user intent
                         ▼
              ┌──────────────────────┐
              │    TaskGraph         │  全新模块
              │    (DAG Generator)   │  LLM 生成 DAG
              └──────────┬───────────┘
                         │ TaskNode[]
                         ▼
              ┌──────────────────────┐
              │  Capability Router   │  全新模块
              │  (纯代码, 非 LLM)    │  评分公式
              └──────────┬───────────┘
                         │ model selection
                         ▼
              ┌──────────────────────┐
              │    Worker Pool       │  扩展现有 Subagent
              │  Search/Code/Test/   │  TaskTool + FiberSet
              │  Document            │
              └──────────┬───────────┘
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
    ┌──────────┐  ┌──────────┐  ┌──────────┐
    │  Tool    │  │  MCP     │  │ Context  │  全部复用
    │  Layer   │  │  Layer   │  │  Engine  │
    └──────────┘  └──────────┘  └──────────┘
          │              │              │
          └──────────────┼──────────────┘
                         ▼
              ┌──────────────────────┐
              │   OpenCode Runtime   │  完全复用
              │   Effect-TS Layer    │
              │   Session/Agent/Tool │
              │   Event Store/SQLite │
              └──────────────────────┘
```

## 模块详细设计

### 模块 1：Executive Agent（扩展现有）

```
继承：Agent.Service
新增：Plan mode — 输出不是工具调用，而是 TaskGraph
入口：packages/opencode/src/agents/executive/
  ├── executive-agent.ts   # ExecutiveAgent 定义
  ├── planner-prompt.txt    # 计划生成 Prompt
  └── task-graph.ts         # DAG 数据结构
```

**Planner Prompt 设计**：
```
你是一个 Task Planner。收到用户需求后，输出 JSON DAG：
{
  "nodes": [
    {
      "id": "scan",
      "type": "search",
      "description": "扫描项目文件结构",
      "dependencies": [],
      "requiredCapabilities": ["search"],
      "quality": "low"
    },
    {
      "id": "analyze",
      "type": "code",
      "description": "分析架构",
      "dependencies": ["scan"],
      "requiredCapabilities": ["code_generation", "reasoning"],
      "quality": "high"
    }
  ]
}
```

### 模块 2：TaskGraph Engine（全新）

```
入口：packages/core/src/task-graph/
  ├── task-graph.ts         # DAG 数据结构 + 拓扑排序
  ├── task-scheduler.ts     # 依赖解析 + 并发执行
  └── task-graph-executor.ts # 完整执行框架
```

**核心逻辑**：
```typescript
class TaskGraphExecutor {
  execute(nodes: TaskNode[]):
    // 1. 拓扑排序
    const sorted = topologicalSort(nodes)

    // 2. 按层级并发执行
    for (const level of topologicalLevels(sorted)):
      await Effect.all(level.map(n => executeNode(n)))
        .pipe(Effect.withConcurrency(4))

  executeNode(node: TaskNode):
    // 1. Router 选模型
    const model = Router.route(node.spec)

    // 2. 创建 Worker Session
    const session = await sessions.create({
      parentID: executiveSessionID,
      title: node.spec.description,
      agent: mapTypeToAgent(node.taskType),
      permission: buildPermission(node),
    })

    // 3. 注入精简上下文（不是完整历史！）
    const prompt = buildWorkerContext(node)

    // 4. 执行
    const result = await sessions.prompt({
      sessionID: session.id,
      model,
      parts: [{ type: "text", text: prompt }],
    })

    // 5. 销毁 Worker
    await sessions.close(session.id)

    return result
}
```

### 模块 3：Capability Router（全新）

```
入口：packages/core/src/router/
  ├── router.ts             # 核心路由逻辑
  ├── model-capabilities.ts # 模型能力标注
  └── scoring.ts            # 评分公式
```

**模型能力标注**（声明式配置）：
```typescript
// config/models.yaml 或 TypeScript 静态配置
const MODEL_CAPABILITIES = {
  "anthropic/claude-opus-4-6": {
    capabilities: ["planning", "architecture", "reasoning", "code_generation"],
    speedScore: 7,
    costTier: "high"
  },
  "openai/gpt-5": {
    capabilities: ["planning", "code_generation", "reasoning", "search"],
    speedScore: 8,
    costTier: "high"
  },
  "deepseek/deepseek-v4": {
    capabilities: ["code_generation", "search", "test"],
    speedScore: 6,
    costTier: "low"
  },
  "qwen/qwen3-8b": {
    capabilities: ["search", "document"],
    speedScore: 9,
    costTier: "low"
  }
}
```

### 模块 4：Worker Pool（扩展现有）

**扩展 Subagent 类型**：

| Agent Type | 现有 | 新增 |
|---|---|---|
| Search Worker | `explore` subagent | 完善 |
| Code Worker | `general` subagent | 拆分出 `code` |
| Test Worker | 无 | 新增 `test` |
| Document Worker | `summary` (hidden) | 新增 `document` |

```
新增：packages/opencode/src/agents/worker/
  ├── code-worker.ts        # CodeAgent 定义
  ├── test-worker.ts        # TestAgent 定义
  └── document-worker.ts    # DocumentAgent 定义
```

### 模块 5：Context Manager（扩展现有）

```
新增：packages/core/src/context/
  ├── worker-context.ts     # Worker 上下文构建器
  └── context-filter.ts     # 上下文过滤器
```

**Worker Context 构建逻辑**：
```typescript
function buildWorkerContext(task: TaskNode): string {
  return [
    `Task: ${task.spec.description}`,
    `Goal: ${task.spec.prompt}`,
    ``,
    `Relevant Context:`,
    ...task.spec.relevantFiles.map(f => `  - ${f.path}`),
    ``,
    `Previous Results:`,
    ...task.dependencies.flatMap(depId => {
      const dep = taskGraph.get(depId)
      if (!dep?.result) return []
      return [`  [${depId}]: ${dep.result.summary}`]
    }),
  ].join("\n")
}
```

## 数据流图

```
1. User Input: "重构这个 Qt 项目"

2. Executive Agent
   └→ plan() → LLM 调用 (Claude Opus / GPT-5)
      └→ 输出 TaskGraph JSON

3. TaskGraph Engine
   └→ 拓扑排序 → 分层
      Level 0: [scan_project]           ← 无依赖，可并发
      Level 1: [analyze_architecture]   ← 依赖 scan
      Level 2: [generate_refactor_plan,
                 generate_test_plan]    ← 依赖 analyze，可并发
      Level 3: [execute_refactor,
                execute_tests]          ← 依赖各自 plan
      Level 4: [verify_build]          ← 依赖 execute_refactor + execute_tests

4. For each Level:
   For each Node:
     Router.route(node.spec)           → 选模型
     WorkerPool.execute(node)          → 创建 Worker → 执行 → 销毁
     Node.result = result              → 写回 DAG

5. Executive Agent
   └→ 汇总所有 Node.result
      └→ 生成最终报告 → 返回用户
```

## 文件结构

```
packages/opencode/src/
├── agents/
│   ├── agent.ts                  # 现有 - 扩展 Agent 类型
│   ├── executive/
│   │   ├── executive-agent.ts    # 新增 - Executive Agent
│   │   ├── planner-prompt.txt    # 新增 - 计划生成 Prompt
│   │   └── task-graph.ts         # 新增 - DAG 类型定义
│   └── worker/
│       ├── code-worker.ts        # 新增
│       ├── test-worker.ts        # 新增
│       ├── document-worker.ts    # 新增
│       └── worker-context.ts     # 新增 - Worker 上下文构建

packages/core/src/
├── task-graph/
│   ├── task-graph.ts             # 新增 - DAG 数据结构
│   ├── task-scheduler.ts         # 新增 - 调度器
│   └── task-graph-executor.ts    # 新增 - 执行框架
├── router/
│   ├── router.ts                 # 新增 - 核心路由
│   ├── model-capabilities.ts     # 新增 - 能力标注
│   └── scoring.ts                # 新增 - 评分
├── context/
│   ├── worker-context.ts         # 新增 - Worker 上下文
│   └── context-filter.ts         # 新增 - 上下文过滤
├── session/
│   ├── runner/                   # 现有 - 复用 V2 Runner
│   ├── compaction.ts             # 现有 - 复用
│   └── ...                       # 现有
├── tool/
│   ├── registry.ts               # 现有 - 复用
│   ├── tool.ts                   # 现有 - 复用
│   └── task.ts                   # 现有 - 扩展支持 TaskGraph
└── provider/
    ├── provider.ts               # 现有 - 复用
    └── transform.ts              # 现有 - 复用
```

## 实施路线图

### Phase 1 — 最小可运行版本（2-3 周）

**目标**：DAG + Router + 两个 Worker 类型

- [ ] `packages/core/src/task-graph/` — DAG 数据结构 + 拓扑排序 + 基础调度器
- [ ] `packages/core/src/router/` — 静态能力标注 + 评分公式
- [ ] `packages/opencode/src/agents/worker/code-worker.ts` — Code Worker Agent 定义
- [ ] `packages/opencode/src/agents/executive/task-graph.ts` — Executive 输出 TaskGraph 的 JSON Schema

**关键设计决定**：
- Executive 先用 `build` Agent + custom system prompt（不做独立的 Executive Agent）
- Worker 先用 `general` subagent（不做类型拆分）
- Router 先做静态配置文件（不做 UI）

### Phase 2 — 多 Agent（1-2 周）

- [ ] Worker 类型拆分：`code`, `test`, `document`, `search`
- [ ] Worker Context 精简（不是完整历史）
- [ ] TaskGraph 并行执行（FiberSet 并发）
- [ ] Executive Agent 独立 Prompt 模板

### Phase 3 — 记忆系统（2-3 周）

- [ ] 基于现有 Compaction 摘要构建项目记忆
- [ ] Session 间知识传递（跨 session 检索历史摘要）
- [ ] 用户偏好持久化（config 扩展）

### Phase 4 — 自主规划（2 周）

- [ ] Reflection Agent（失败触发 Critic）
- [ ] TaskGraph 动态调整（根据中间结果重新规划）
- [ ] Capability 标注自动更新（从 models.dev 数据自动推断）

### Phase 5 — 生产化（持续）

- [ ] 成本追踪仪表板（复用 Provider 现有 cost 数据）
- [ ] Lane Queue 并发控制（参考 OpenClaw 的设计）
- [ ] 消息平台适配（Telegram/Slack/微信）
- [ ] 持久运行模式（Session 事件循环 7×24）
- [ ] 向量检索（Qdrant 集成）用于跨 session 语义搜索

---

## 关键复用清单

| 现有能力 | 复用方式 | 节省工作量 |
|---|---|---|
| Effect-TS Layer/Fiber/Scope | 直接作为 Agent Runtime | **完全不用写 Runtime** |
| Agent.Service (create/list/get) | Executive + Worker 的基础 | 只需加 Prompt 和类型 |
| TaskTool + BackgroundJob | Worker Pool 的执行引擎 | **不用写调度框架** |
| SessionRunner (V2) | Agent Loop 的执行器 | **不用写 LLM 调用层** |
| ToolRegistry + Materialization | 所有工具立即可用 | 15+ 工具零成本 |
| Provider + ModelsDev | Model Registry 基础 | 只需加 capability tag |
| Compaction + Context Epoch | 上下文管理基础 | 只需加 Worker 层过滤 |
| Permission System | Worker 权限管控 | 三级权限零成本 |
| MCP Client | 外部工具扩展 | 完整 OAuth + Streamable HTTP |
| Skill System | 可注入项目知识 | Markdown 文件立即可用 |
| Event Store (SQLite) | 所有状态持久化 | **不用引入新数据库** |

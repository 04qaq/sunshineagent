# Claude Code MultiAgent 架构深度解读

> 基于 Anthropic Claude Code v2.1.88 完整源码分析，揭示生产级 AI Agent 协作系统背后的设计原理与实现细节。

---

## 一、为什么需要 MultiAgent？

先思考单 Agent 的工作流程。假设用户说"给我的 Express 项目加 JWT 鉴权"：

```
单 Agent 模式（串行）：
  Step 1: 阅读项目结构（Grep/Glob/Read 所有路由、中间件、配置）
  Step 2: 设计方案（基于读到的内容，设计 JWT 中间件链、token 策略）
  Step 3: 实现方案（写代码、改配置、跑测试）
  ── 三步必须串行，每一步都在累积上下文
  ── 到 Step 3 时，上下文可能已超 100K tokens
  ── 模型开始在压缩过的旧信息上工作，遗漏边缘情况

MultiAgent 模式（并行 + 隔离）：
  Explore Agent（并行）── 搜索所有路由和现有鉴权代码 ──┐
  Plan Agent（并行）  ── 设计 JWT 方案架构            ──┤
                                                         ├──→ 主 Agent 整合

  主 Agent ── 拿到两份报告的摘要，上下文保持干净          │
                                                         │
  Implement Agent（后台）── 按方案 + 研究结果写代码 ─────┘
  ── 各 Agent 上下文独立，报告精简，主 Agent 只看到结论
```

**核心优势**：

| 维度 | 单 Agent | MultiAgent |
|------|----------|------------|
| 并行度 | 串行执行 | 独立子任务可并行 |
| 上下文经济 | 全部内容累积在同一窗口 | 每个 Agent 上下文独立，只传摘要 |
| 工具安全 | 拥有全部工具权限 | 可按角色裁剪工具集 |
| 专注度 | 一个 prompt 处理所有逻辑 | 每个 Agent 专注一件事 |

---

## 二、MultiAgent 的三种形态

Claude Code 的多 Agent 机制有三种形态，本节先做概念性介绍，后文会对每种形态做深入的架构分析。

### 2.1 父子型（Parent-Child）

父 Agent 在执行中途主动调用 `AgentTool` 创建子 Agent（SubAgent），子 Agent 有独立的上下文和精简的工具箱，完成任务后将结果返回给父 Agent，父 Agent 继续执行。

这是最基础的多 Agent 模式——**所有 Agent 共用同一个 `query()` / `queryLoop()` 函数，差异全在于通过 `createSubagentContext()` 构造的隔离上下文。** 详细实现见第三章。

### 2.2 主从型（Coordinator-Worker）

通过 `CLAUDE_CODE_COORDINATOR_MODE=1` 环境变量启用。有一个专门的**协调者（Coordinator）**负责与用户对话、将任务拆分为子任务、分发给 Worker 执行、合成 Worker 的结果后汇报。Worker 之间不直接通信，全部通过 Coordinator 调度。

Coordinator 本身不执行工具——它只做编排和用户沟通。详细实现见第四章。

### 2.3 平级协作型（Agent Swarm）

通过 `TeamCreate` + `AgentTool(name="xxx", team_name="xxx")` 创建多个平级 Team Member，通过 `SendMessage` 工具通信。当前代码中 Team 仍由 Lead Agent 管理 TeamFile，是主从混合模式。可视为完全去中心化协作的雏形。

### 2.4 三种形态对比

| 维度 | 父子型 | 主从型 | Swarm |
|------|--------|--------|-------|
| 父是否执行工具 | 是，自己也用工具 | 否，只编排 | 无固定父角色 |
| 子 Agent 选择 | 模型通过 subagent_type | Coordinator 决定 | 自由创建 |
| 任务分发 | prompt 一次性给 | 可多次 SendMessage 渐进 | SendMessage 自由通信 |
| 通信拓扑 | 星形（父↔子） | 星形（Coordinator↔Worker） | 网状 |
| 适用场景 | 简单 delegate | 复杂多步任务 | 长期协作团队 |

---

## 三、父子型（Parent-Child）架构详解

### 3.1 总体架构

```
主 Agent Loop (queryLoop)
  │
  ├── 模型输出 tool_use: AgentTool(subagent_type="Explore", ...)
  │
  ├── AgentTool.call()
  │   ├── 1. 选择 AgentDefinition
  │   ├── 2. 解析工具集 resolveAgentTools()
  │   ├── 3. 决定 sync/async
  │   ├── 4. 构建 runAgent 参数
  │   │
  │   ├── [同步路径] 父阻塞等待
  │   │   └── for await (msg of runAgent({isAsync:false}))
  │   │       └── query() → queryLoop()
  │   │       → 返回结果给父
  │   │
  │   └── [异步路径] 父不等待
  │       └── registerAsyncAgent() → void runAsyncAgentLifecycle()
  │       → 返回 {status:"async_launched", agentId}
  │       → 子 Agent 独立循环，完成后 enqueueAgentNotification()
```

**核心设计原则**：子 Agent 和主 Agent **调用同一个 `query()` / `queryLoop()` 函数**。没有"子 Agent 专用循环"，差异全在于传入的 `toolUseContext` 参数——通过 `createSubagentContext()` 隔离上下文。

### 3.2 内置 Agent 类型

| Agent | 触发场景 | 工具 | 特点 |
|-------|---------|------|------|
| **general-purpose** | 默认，无 `subagent_type` 指定时 | `['*']` 全部 | 通用型 |
| **Explore** | 调查代码库、找文件 | `['Read', 'Grep', 'Glob']` | 只读，省略 CLAUDE.md |
| **Plan** | 架构设计、方案规划 | `['Read', 'Grep', 'Glob']` | 只读，省 Token |
| **claude-code-guide** | 回答"Claude 能做什么"类问题 | 专项工具集 | 知识型 |
| **statusline-setup** | 配置状态栏 | 配置相关 | 配置型 |
| **verification** | 代码审查验证 | 测试相关 | 验证型 |

Explore/Plan Agent 的两个关键优化：

```typescript
// Explore/Plan 省略 CLAUDE.md — 它们是只读研究型 Agent，不需要
// commit/lint 规范。主 Agent 有完整 CLAUDE.md，负责理解其输出。
// 节省 ~5-15 Gtok/week 舰队级成本。
const shouldOmitClaudeMd = agentDefinition.omitClaudeMd && !override?.userContext

// Explore/Plan 不需要 gitStatus（最多 40KB，标注为"可能过期"）—
// 需要 git 信息时它们自己跑 git status 获取最新数据。
// 节省 ~1-3 Gtok/week。
const isReadOnlyAgent = agentType === 'Explore' || agentType === 'Plan'
```

### 3.3 Agent 定义系统

每个 Agent 通过 `AgentDefinition` 类型定义：

```typescript
type BaseAgentDefinition = {
  agentType: string              // 模型用 subagent_type 选择
  whenToUse: string              // 提示模型何时使用此 Agent
  tools?: string[]               // 允许的工具列表，['*'] = 全部
  disallowedTools?: string[]     // 禁止的工具
  permissionMode?: PermissionMode // 独立权限模式（acceptEdits/bubble/plan）
  model?: string                 // 模型选择，或 'inherit'
  hooks?: HooksSettings          // 生命周期钩子（SubagentStart/SubagentStop）
  mcpServers?: AgentMcpServerSpec[]  // Agent 专属 MCP 服务器
  background?: boolean           // 始终后台运行
  omitClaudeMd?: boolean         // 省略 CLAUDE.md（Explore/Plan 使用）
  maxTurns?: number              // 最大 Agent 轮次
  skills?: string[]              // 预加载的技能
  isolation?: 'worktree' | 'remote' // 文件系统隔离级别
}
```

Agent 有三个来源，按优先级后者覆盖前者：

```
built-in < plugin < userSettings < projectSettings < flagSettings < policySettings
```

### 3.4 工具箱精简 —— 三层过滤模型

子 Agent 不能拥有父 Agent 的全部工具。这既是安全考虑也是 Token 经济考虑——少了 40 个工具定义，系统提示词就少几千 Token，每次 API 调用都能省成本。

#### 第一层：全局禁止列表

所有子 Agent 都不能使用的工具：

```typescript
export const ALL_AGENT_DISALLOWED_TOOLS = new Set([
  TASK_OUTPUT_TOOL_NAME,         // TaskOutput — 不管理后台任务
  EXIT_PLAN_MODE_V2_TOOL_NAME,   // ExitPlanMode — 不能替父退出计划模式
  ENTER_PLAN_MODE_TOOL_NAME,     // EnterPlanMode — 不能替父进入计划模式
  AGENT_TOOL_NAME,               // AgentTool — 防止无限递归派发
  ASK_USER_QUESTION_TOOL_NAME,   // AskUserQuestion — 异步子 Agent 不能弹窗
  TASK_STOP_TOOL_NAME,           // TaskStop — 不管理任务
  WORKFLOW_TOOL_NAME,            // 防止工作流递归
])
```

**设计原理**：这些工具是"协调者"的能力——子 Agent 是"执行者"。让子 Agent 拥有 `AgentTool` 会导致无限递归；让它有 `AskUserQuestion` 会导致后台 Agent 弹出没人能看到的交互窗口。

#### 第二层：Agent 类型专属约束

按 Agent 类型分白名单和黑名单两种模式。

**异步 Agent 白名单**：

```typescript
export const ASYNC_AGENT_ALLOWED_TOOLS = new Set([
  FileReadTool, WebSearchTool, TodoWriteTool, GrepTool,
  WebFetchTool, GlobTool, ...SHELL_TOOL_NAMES,  // Bash, PowerShell
  FileEditTool, FileWriteTool, NotebookEditTool,
  SkillTool, SyntheticOutputTool, ToolSearchTool,
  EnterWorktreeTool, ExitWorktreeTool,
])
// 白名单外的一律不可用——异步 Agent 是"限权"模式
```

**自定义 Agent 黑名单**：

```typescript
export const CUSTOM_AGENT_DISALLOWED_TOOLS = new Set([
  ...ALL_AGENT_DISALLOWED_TOOLS,  // 继承第一层
])
// 比白名单宽松——自定义 Agent 默认有大部分工具，只排除危险项
```

过滤逻辑：

```typescript
export function filterToolsForAgent({ tools, isBuiltIn, isAsync }) {
  return tools.filter(tool => {
    if (tool.name.startsWith('mcp__')) return true    // MCP 工具始终放行
    if (ALL_AGENT_DISALLOWED_TOOLS.has(tool.name)) return false  // 第一层
    if (!isBuiltIn && CUSTOM_AGENT_DISALLOWED_TOOLS.has(tool.name)) return false  // 第二层
    if (isAsync && !ASYNC_AGENT_ALLOWED_TOOLS.has(tool.name)) return false  // 第二层白名单
    return true
  })
}
```

#### 第三层：Agent 自身声明

在 Agent 定义的 `tools` 和 `disallowedTools` 字段中声明：

```typescript
export function resolveAgentTools(agentDefinition, availableTools, isAsync) {
  const filtered = filterToolsForAgent({...})
  const allowed = filtered.filter(t => !disallowedSet.has(t.name))
  if (tools === undefined || tools[0] === '*') return allowed
  return tools.filter(name => availableMap.has(name))
}
```

#### 实际过滤效果示例

以"自定义 Agent、后台异步模式、tools: ['Read', 'Grep', 'Bash']"为例：

```
父 Agent 完整工具池 (60+ tools)
  → 第一层: 排除 7 个
  → 第二层 ASYNC: 白名单只留 16 个
  → 第三层 tools: 只留 ['Read', 'Grep', 'Bash']
  = 最终工具箱: 3 个工具
```

### 3.5 上下文隔离 —— 逐字段决策

这是 SubAgent 架构最精妙的部分。核心函数 `createSubagentContext()` 对 `ToolUseContext` 的 20+ 个字段逐一做独立决策：

#### 类别一：必须克隆 —— "各用各的"

```typescript
readFileState: cloneFileStateCache(parent.readFileState)
contentReplacementState: cloneContentReplacementState(parent.xxx)
nestedMemoryAttachmentTriggers: new Set()
loadedNestedMemoryPaths: new Set()
dynamicSkillDirTriggers: new Set()
discoveredSkillNames: new Set()
```

#### 类别二：子控制器 —— "父死了子也得死"

```typescript
abortController: createChildAbortController(parent.abortController)
// 父 Ctrl+C → 子自动取消；子 Ctrl+C → 不影响父
```

#### 类别三：No-Op 化 —— "你不能动父的状态"

```typescript
setAppState: shareSetAppState ? parent.setAppState : () => {}
// 默认空函数 — 异步子 Agent 调用 setAppState() 没效果
setResponseLength: share ? parent.setResponseLength : () => {}
setInProgressToolUseIDs: () => {}
```

#### 类别四：UI 回调置空 —— "你不能操作父的界面"

```typescript
addNotification: undefined
setToolJSX: undefined
setStreamMode: undefined
setSDKStatus: undefined
openMessageSelector: undefined
```

#### 类别五：getAppState 包装 —— "你能读，但读到的已经不一样了"

```typescript
getAppState: () => {
  const state = parent.getAppState()
  return {
    ...state,
    toolPermissionContext: {
      ...state.toolPermissionContext,
      shouldAvoidPermissionPrompts: true,  // 不弹权限确认窗口
    },
  }
}
```

#### 类别六：身份重建 —— "你不是父 Agent"

```typescript
agentId: createAgentId()
agentType: "Explore"
queryTracking: { chainId: newUUID(), depth: parent.depth + 1 }
```

#### 类别七：关键透传 —— "必须共享的"

```typescript
// 关键！即使 setAppState 被 no-op，子 Agent 的后台 bash 任务
// 也必须能注册到根 Store，否则进程不会被清理
setAppStateForTasks: parent.setAppStateForTasks ?? parent.setAppState

// 函数式更新，并发安全
updateAttributionState: parent.updateAttributionState
```

#### 隔离效果示意

```
父 Agent:    messages(100+)  readFileState(50文件)  depth=0
                         ↓ 子 Agent 隔离后 ↓
子 Agent:    messages(2条)   readFileState(全新)    depth=1
             看不到父历史      不共享缓存           身份独立
             setAppState→noop                       有自己的transcript
```

#### setAppState 的逃生舱设计

| 方法 | 同步子 Agent | 异步子 Agent | 用途 |
|------|-------------|-------------|------|
| `setAppState` | 透传到父 | **no-op** | UI 状态（消息列表、工具进度） |
| `setAppStateForTasks` | 透传到根 | **透传到根** | 基础设施（bash 任务注册、session hooks） |

异步子 Agent 的 `setAppState` 被设为 no-op，但 `setAppStateForTasks` 永不被 no-op——确保子 Agent 启动的后台 bash 任务能被正确追踪和清理。

### 3.6 同步 vs 异步执行

#### 调度决策

```typescript
const shouldRunAsync = (
  run_in_background === true ||          // 显式指定后台
  selectedAgent.background === true ||   // Agent 定义强制后台
  isCoordinator ||                        // Coordinator 模式
  forceAsync ||                           // Fork gate 强制 async
  assistantForceAsync ||                  // Assistant 模式强制 async
  proactiveModule?.isProactiveActive()    // Proactive 模式
) && !isBackgroundTasksDisabled
```

#### 同步执行

父 Agent Loop **阻塞等待**子 Agent 完成：

```
父 Agent Loop
  → AgentTool.call()
    → for await (msg of runAgent({isAsync:false}))
      → msg 逐条发布为 tool_progress（父 UI 实时显示进度）
    → finalizeAgentTool() → 构造结构化结果
  → 父拿到 tool_result，继续循环
```

同步 Agent 超过 2 秒会注册自动后台化——通过 `autoBackgroundMs=120000`（2分钟），超时后自动切换为后台模式。

#### 异步执行

父 Agent Loop **立即返回 task ID**，不等待。核心函数 `runAsyncAgentLifecycle()` 流程：

```
runAsyncAgentLifecycle()
  ├── for await (msg of makeStream(onCacheSafeParams))
  │     ├── 收集到 agentMessages[]
  │     ├── 实时更新 UI
  │     ├── 进度追踪（ProgressTracker）
  │     └── SDK progress 事件发射
  ├── finalizeAgentTool()          → 统计结果
  ├── completeAsyncAgent()         → 标记 task 完成
  ├── classifyHandoffIfNeeded()    → 安全检查
  └── enqueueAgentNotification()   → 编入父 Agent 消息队列
```

**父 Agent 收到的子 Agent 结果格式**（XML 注入）：

```xml
<task-notification>
  <task-id>agent-a3kf7m2x</task-id>
  <tool-use-id>toolu_01ABcD...</tool-use-id>
  <status>completed</status>
  <summary>Agent "JWT实现" completed</summary>
  <result>实现完成：创建了 3 个文件，修改了 2 个。Commit hash: abc123</result>
  <usage>
    <total_tokens>45231</total_tokens>
    <tool_uses>24</tool_uses>
    <duration_ms>67890</duration_ms>
  </usage>
</task-notification>
```

父 Agent Loop 在下一轮迭代中，通过 `getAttachmentMessages()` → `queuedCommandsSnapshot` 路径摄入此通知。

#### 错误处理与资源清理

子 Agent 出错时会有三种通知——用户主动终止（killed，附带部分结果）、执行错误（failed，附带错误信息）、正常完成（completed）。

每个子 Agent 结束时必须清理 8 项资源，其中 `killShellTasksForAgent` 最关键——如果子 Agent 启动了后台 bash 循环，不清理就会变成僵尸进程。

### 3.7 Agent 间通信

#### 父 → 子

**AgentTool 调用**是最基本的方式——父 Agent 通过 `prompt` 参数传递完整任务。

**SendMessage 工具**用于向已运行的 Agent 发送后续消息：

```typescript
SendMessageTool({
  to: "agent-a1b",       // 子 Agent 的 agentId
  summary: "Fix null pointer",
  message: "在 src/auth/validate.ts:42 处修复空指针..."
})
```

SendMessage 支持的消息类型：

| 消息类型 | 用途 |
|---------|------|
| 纯文本消息 | 发送任务指令或后续继续 |
| `shutdown_request` | 请求 Agent 关闭 |
| `shutdown_response` | 响应关闭请求 |
| `plan_approval_response` | 审批 Plan Agent 的输出 |

**Mailbox 系统**：在 Agent Swarm 模式下，每个 Team Member 有自己的 mailbox，消息通过文件系统持久化。Agent 在自己的 queryLoop 中检查 mailbox。

#### 子 → 父：XML 注入

子 Agent 完成后，通过 `enqueueAgentNotification()` 将结果编码为 `<task-notification>` XML，注入父 Agent 的消息队列。这是单向通道——子 Agent 不能主动"对话"父 Agent，只能汇报自己的状态变更。

#### Resume 机制：跨生命周期的上下文保留

如果 SendMessage 发给了一个已停止的 Agent，框架会自动从磁盘 transcript 恢复它：

```typescript
if (task.status === 'running') {
  queuePendingMessage(agentId, input.message, ...)  // 运行中 → 排队
} else {
  resumeAgentBackground({ agentId, prompt, ... })  // 已停止 → 恢复
}
```

SendMessage 因此成了一种"跨生命周期"的通信——Agent 即使已完成，也能被唤醒继续执行新任务，且保留之前的完整上下文。

### 3.8 Fork Agent —— Prompt Cache 共享

#### 设计目标

Fork 的核心动机是**最大化 Prompt Cache 命中率**。如果父子 Agent 的 API 请求前缀（system prompt + tools + model + messages prefix）字节相同，Cache 就能命中，显著降低 API 成本。

#### 实现机制

```typescript
export const FORK_AGENT = {
  agentType: 'fork',
  tools: ['*'],              // 继承父的全部工具
  model: 'inherit',           // 继承父的模型
  permissionMode: 'bubble',   // 权限冒泡到父终端
}

// 防止递归 fork
export function isInForkChild(messages): boolean { ... }
```

Fork 与普通子 Agent 的三个关键区别——追求"完全相同"：

```typescript
override: {
  systemPrompt: forkParentSystemPrompt,  // ← 父的 system prompt，字节级相同
},
availableTools: toolUseContext.options.tools,  // ← 父的完整工具池
useExactTools: true,  // ← 不调用 resolveAgentTools()，避免序列化差异
```

**占位符结果**是 Cache 命中的核心技术——所有 fork children 的 tool_result 共享相同的占位符文本。只有最后一个 text block（每个 child 的个性化 directive）不同，前面全部从 Cache 读取。

Fork 子 Agent 被注入严格的执行约束（10 条 RULES），确保它们作为执行单元运行而非协调者。输出格式被标准化为 Scope/Result/Key files/Files changed/Issues 五段，方便父 Agent 快速解析。

---

## 四、主从型（Coordinator-Worker）架构详解

这是 Claude Code 中最成熟的 MultiAgent 模式。下面以与父子型同等的深度来讲解它的完整实现。

### 4.1 启用方式与架构概览

通过环境变量启用：

```bash
CLAUDE_CODE_COORDINATOR_MODE=1 node package/cli.js
```

执行流程如下：

```
用户 (User)
  │
  ▼
┌────────────────────────────────────────┐
│  Coordinator (协调者 Agent)              │
│  - 不执行工具，只做编排和用户沟通          │
│  - 合成 Worker 结果，制定精准后续指令      │
│  - 决策：Continue / Spawn / Stop         │
│  - 工具：AgentTool、SendMessage、TaskStop │
└──┬───────────────┬─────────────────────┘
   │               │
   │ AgentTool()   │ AgentTool()
   │ (spawn new)   │ (spawn new)
   ▼               ▼
┌─────────┐   ┌─────────┐
│Worker 1 │   │Worker 2 │   ← 并行执行，互不通信
│ 研究项目 │   │ 研究依赖 │     各自有独立上下文
└────┬────┘   └────┬────┘
     │<task-notification>│
     └────────┬─────────┘
              ▼
       Coordinator（合成结果）
              │
       SendMessage(to="Worker1", spec=...)
              │
              ▼
       ┌─────────┐
       │Worker 1 │   ← 继续执行实现任务
       │ 改代码   │     保留之前的文件上下文
       └────┬────┘
            │<task-notification>
            ▼
       Coordinator → 汇报用户
```

**Coordinator 模式改变的是 Agent Loop 中的"决策层"而非"执行层"。** Worker 的底层实现和普通 SubAgent 完全相同——都是 `runAgent()` → `query()` → `queryLoop()`。区别在于：

| 层面 | 普通父子型 | Coordinator-Worker |
|------|-----------|-------------------|
| 父 Agent 的系统提示词 | 通用的 Claude Code 提示词 | 专用的 Coordinator 系统提示词（约 370 行） |
| 父 Agent 的工具 | 全部可用 | 只有 AgentTool、SendMessage、TaskStop |
| 父是否自己执行 | 是，直接调用 Bash/Read/Write 等 | 否，只做编排 |
| Worker 的选择 | 模型按 subagent_type 字段决定 | Coordinator 按任务需求决定 |

### 4.2 Worker 的定义与生命周期

#### Worker 的工具箱

Coordinator 的系统提示词定义了 Worker 的工具范围：

```typescript
// 从 ASYNC_AGENT_ALLOWED_TOOLS 中剔除内部协调工具
const INTERNAL_WORKER_TOOLS = new Set([
  TEAM_CREATE_TOOL_NAME,    // Worker 不创建团队
  TEAM_DELETE_TOOL_NAME,    // Worker 不删除团队
  SEND_MESSAGE_TOOL_NAME,   // Worker 不直接通信
  SYNTHETIC_OUTPUT_TOOL_NAME,
])

const workerTools = ASYNC_AGENT_ALLOWED_TOOLS
  .filter(name => !INTERNAL_WORKER_TOOLS.has(name))
// 最终约 12-13 个工具：Read, Write, Edit, Bash, Grep, Glob,
// WebSearch, WebFetch, TodoWrite, Skill, ToolSearch, NotebookEdit
```

Worker 的 prompt 生成方式：

```typescript
// Coordinator 在调用 AgentTool 时，必须写自包含的 prompt
// Worker 看不到用户对话，只看到 Coordinator 给的这几百字的 spec
AgentTool({
  subagent_type: "worker",
  prompt: "Fix the null pointer in src/auth/validate.ts:42.
           The user field on Session (src/auth/types.ts:15) is
           undefined when sessions expire. Add null check..."
})
```

#### Worker 的生命周期状态机

```
        AgentTool()
           │
           ▼
       ┌─────────┐
       │ running │ ← Worker 正在执行 agent loop
       └────┬────┘
            │
    ┌───────┼───────────┐
    │       │           │
    ▼       ▼           ▼
┌───────┐ ┌───────┐ ┌──────┐
│completed│ │failed │ │killed│
│ 正常完成│ │执行出错│ │被停止│
└───┬───┘ └───┬───┘ └──┬───┘
    │         │         │
    └─────────┼─────────┘
              │
       SendMessage()  ← 所有终止状态都可被恢复
              │
              ▼
         ┌─────────┐
         │ running │ ← 从 transcript 恢复，上下文完整保留
         └─────────┘
```

**Resume 是关键能力**——Worker 完成后不会被销毁，它的完整对话历史（sidechain transcript）持久化在磁盘上。Coordinator 随时可以 SendMessage 唤醒它，Worker 带着之前的全部上下文继续执行。这在 Coordinator 的系统提示词中被反复强调：

```text
Continue workers whose work is complete via SendMessage to take
advantage of their loaded context.
```

### 4.3 通信机制

Coordinator-Worker 的通信全部是**星形拓扑**——Worker 之间没有直接通道，一切经过 Coordinator。

#### Coordinator → Worker（下游通信）

```
Coordinator 的通信手段：

1. AgentTool() — 创建新 Worker（一次性）
   → Worker 收到 system prompt + 初始 prompt
   → 这是最常用的"冷启动"方式

2. SendMessage(to="<agentId>") — 向已有 Worker 发后续指令
   → 触发 resumeAgentBackground() 或 queuePendingMessage()
   → Worker 在下一轮 tool-round 收到消息
   → 这是 Coordinator 的核心操作——"继续"Worker

3. TaskStop(task_id="<agentId>") — 中止 Worker
   → 设置 Worker 的 abortController
   → Worker 在下一轮检测 aborted 信号
   → 然后 Coordinator 可以用 SendMessage 重新定向
```

Coordinator 系统提示词中关于 SendMessage 的关键指导：

```text
// 继续（Continue）— Worker 刚完成研究，现在给实现指令
SendMessage({ to: "agent-a1b",
  message: "Fix the null pointer in validate.ts:42..." })

// 纠正（Correct）— Worker 测试失败，给纠正指令
SendMessage({ to: "agent-a1b",
  message: "Two tests still failing at lines 58 and 72 —
            update the assertions to match the new error message." })

// 重新定向（Redirect）— TaskStop 后给新方向
TaskStop({ task_id: "agent-x7q" })
SendMessage({ to: "agent-x7q",
  message: "Stop the JWT refactor. Instead, fix the null pointer..." })
```

#### Worker → Coordinator（上游通信）

Worker 只有一个通信出口——**task-notification XML 注入**：

```xml
<task-notification>
  <task-id>agent-a3kf7m2x</task-id>
  <status>completed|failed|killed</status>
  <summary>Agent "描述" completed/failed: reason</summary>
  <result>Worker 最后一条 assistant 消息的文本内容</result>
  <usage>
    <total_tokens>45231</total_tokens>
    <tool_uses>24</tool_uses>
    <duration_ms>67890</duration_ms>
  </usage>
</task-notification>
```

这个 XML 通过消息队列进入父 Agent 的消息流。Coordinator 的系统提示词教它如何解析：

```text
Worker results arrive as user-role messages containing
<task-notification> XML. They look like user messages but are not.
Distinguish them by the <task-notification> opening tag.

The <task-id> value is the agent ID — use SendMessage with that
ID as `to` to continue that worker.
```

#### 通信限制的深层原因

```text
Workers can't see your conversation. Every prompt must be
self-contained with everything the worker needs.
```

这条规则有三个目的：

1. **安全隔离**：Worker 看不到用户对话中的敏感信息
2. **Prompt 质量**：Coordinator 被迫写出具体的、自包含的指令（不能写"based on our discussion"这种模糊引用）
3. **上下文经济**：Worker 的上下文干净——只有 spec + 执行必需的文件内容，没有用户闲聊

### 4.4 协调者的调度决策

Coordinator 的核心智能体现在系统提示词定义的决策规则中。这些不是代码实现的，而是通过提示词工程注入的行为约束。

#### 决策 1：禁止 lazy delegation（合成义务）

```text
// Anti-pattern — 被禁止
"Based on your findings, fix the bug"

// Required — 必须合成具体 spec
"Fix the null pointer in src/auth/validate.ts:42.
 The user field on Session (src/auth/types.ts:15) is undefined
 when sessions expire but the token remains cached.
 Add a null check before user.id — if null, return 401."
```

**设计原理**：如果 Coordinator 只转发 Worker 结果而不理解，那 Coordinator 就是多余的。合成是 Coordinator 存在价值的证明——它必须从 Worker 的发现中提取关键信息，还原到代码上下文中，形成可以独立执行的指令。

#### 决策 2：Continue vs Spawn 决策矩阵

```
| 情况                         | 机制        | 原因                    |
|------------------------------|-------------|------------------------|
| 研究涉及的文件恰好需要修改      | Continue    | Worker 已有文件上下文    |
| 研究范围广但实现只改几个文件    | Spawn fresh | 避免探索噪音干扰实现    |
| 纠正 Worker 的错误             | Continue    | Worker 知道它试了什么   |
| 验证另一个 Worker 的代码       | Spawn fresh | 验证者需独立视角        |
| 第一次尝试用了完全错误的方案    | Spawn fresh | 错误上下文会污染重试    |
| 完全无关的任务                 | Spawn fresh | 无上下文可复用          |
```

**设计原理**：这是一个上下文经济学的决策。Continue 读文件成本为 0（已在上下文），但上下文可能包含噪音；Spawn fresh 上下文干净，但需要重新搜索。核心判断标准是"上下文中有效信息 vs 噪音的比例"。

#### 决策 3：并发策略

```text
- Read-only tasks (research) — run in parallel freely
- Write-heavy tasks (implementation) — one at a time per file area
- Verification can sometimes run alongside implementation
```

Coordinator 被授予根据任务类型管理并发的能力——只读任务不冲突，写任务可能有合并冲突，验证可以和实现重叠。

### 4.5 纠错机制

#### 机制 1：强制合成 = 质量关卡

Coordinator 不能转发——必须理解。这本身就是一道质量关卡。一个连自己 Worker 的发现都说不清的 Coordinator，不具备继续指挥的资格。

#### 机制 2：Continue 出错 Worker，不新建

Worker 报告失败时，系统提示词要求用 SendMessage 继续它——Worker 的上下文中包含失败的完整过程（错误信息、尝试过的方法），继续它能让它快速调整。开新 Worker 要重新铺垫。

但如果失败是由于"方案根因错误"（比如选了不该用的技术栈），则 Spawn fresh——错误方案的上下文会锚定 Worker 在错误方向上。

#### 机制 3：Stop + Continue 纠偏

```text
// 用户改需求后，Coordinator 可以中途纠正 Worker
TaskStop({ task_id: "agent-x7q" })
SendMessage({ to: "agent-x7q",
  message: "Stop the JWT refactor. Instead, fix the null pointer..." })
```

比 kill + spawn new 高效——Worker 已经加载了相关文件。

#### 机制 4：独立 Verification，不用实现者的测试

Verification Worker 被要求用独立的视角测试——不能直接运行实现者写的测试用例、不能假设实现者的断言是正确的、必须测试实现者没想到的边缘情况。

#### 机制 5：防递归结构

- Worker 的工具箱不含 `AgentTool`——它与生俱来不能创建孙 Agent
- Fork Agent 有 `isInForkChild()` 运行时检查作为额外防线
- Coordinator 只有编排工具，不能自己执行代码修改——从工具层面保证了角色分离

---

## 五、总结

### 父子型 vs 主从型 深度对比

| 维度 | 父子型（Parent-Child） | 主从型（Coordinator-Worker） |
|------|----------------------|---------------------------|
| **父 Agent 系统提示词** | 通用 Claude Code 提示词 | 专用 Coordinator 提示词（~370 行） |
| **父 Agent 工具** | 全部可用 | 仅 AgentTool、SendMessage、TaskStop |
| **父是否自己执行** | 是（直接调用 Bash/Read/Write） | 否（只编排，不执行） |
| **子 Agent 类型** | Explore/Plan/general-purpose 等 | 统一使用 worker 类型 |
| **任务分发** | prompt 一次性给定 | 多次 SendMessage 渐进细化 |
| **通信** | 父→子：AgentTool/SendMessage<br>子→父：task-notification XML | 同左 + 更丰富的模式 |
| **纠错方式** | 依赖子 Agent 自身质量 | Coordinator 合成 + Continue/Spawn 决策 |
| **Worker 间通信** | 无 | 无（星形拓扑） |
| **关键设计约束** | 共用 queryLoop | 强制合成义务 |
| **适用场景** | "帮我搜一下这个"，简单 delegate | "重构整个模块"，复杂多步任务 |

### 核心架构原则

1. **同一个 Agent Loop** — 所有 Agent 走相同的 `query()` / `queryLoop()` 函数，差异全在 `toolUseContext` 参数
2. **逐字段隔离** — `createSubagentContext()` 对 20+ 个字段独立决策
3. **三层工具过滤** — 全局禁止 → 类型白/黑名单 → Agent 自身声明
4. **双通道通信** — 父→子：AgentTool/SendMessage/Mailbox；子→父：task-notification XML
5. **Fork 追求 Cache 完美匹配** — 字节级相同的请求前缀，最大化 Prompt Cache 命中率
6. **逃生舱设计** — `setAppState` 可 no-op 但 `setAppStateForTasks` 永远透传
7. **Resume 机制** — sidechain transcript 持久化，已停止 Agent 可跨生命周期恢复
8. **协调者的合成义务** — 主从型独有的质量关口，禁止懒委托

---

*本文基于 Anthropic Claude Code v2.1.88 还原源码分析。*
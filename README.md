# SunshineAgent

一个基于 Python 的 AI Agent 框架，实现了 Multi-Agent 协作系统。

## 特性

- **多 Agent 架构**：支持父子型（Parent-Child）和 Executive（Coordinator-Worker）模式
- **任务图调度**：DAG 依赖图，支持并行执行和拓扑排序
- **上下文隔离**：借鉴 Claude Code 的逐字段隔离设计
- **三层工具过滤**：全局禁止 → Agent 类型约束 → Agent 自身声明
- **Resume 机制**：Worker 完成后可被唤醒继续执行
- **Reflection 重试**：失败时自动分析原因并重试

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│  build Agent (Primary)                                       │
│  - 普通模式：直接执行                                         │
│  - Executive 模式：DAG 规划 + 并行执行                        │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  TaskGraph Engine                                            │
│  - 生成任务依赖图                                             │
│  - 拓扑排序 + 并行调度                                        │
│  - Reflection 重试                                           │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Worker Pool                                                 │
│  - general: 通用任务                                         │
│  - explore: 代码搜索                                         │
│  - code: 代码编写                                            │
│  - test: 测试编写                                            │
│  - document: 文档生成                                        │
└─────────────────────────────────────────────────────────────┘
```

## 快速开始

### 安装

```bash
# 克隆仓库
git clone git@github.com:04qaq/sunshineagent.git
cd sunshineagent

# 安装依赖
pip install -e ".[dev]"
```

### 配置

创建 `.env` 文件：

```bash
# 选择一个 Provider
ANTHROPIC_API_KEY=your-api-key
# 或
OPENAI_API_KEY=your-api-key
# 或
DEEPSEEK_API_KEY=your-api-key
```

### 运行

```bash
# 启动交互式 CLI
sunshine

# 或直接运行
python -m src.cli.main
```

## 使用示例

### 普通模式

```python
from src.tool.task import TaskTool

# 单个子任务
result = await task_tool.execute({
    "description": "搜索认证模块",
    "prompt": "分析 src/auth/ 目录下的所有文件",
    "subagent_type": "explore",
})
```

### Executive 模式

```python
# 复杂多步骤任务
result = await task_tool.execute({
    "description": "重构认证模块",
    "prompt": "重构 src/auth/ 模块并添加测试...",
    "subagent_type": "general",
    "executive": True,  # 启用 Executive 模式
})
```

## 项目结构

```
sunshineagent/
├── src/
│   ├── agent/              # Agent 系统
│   │   ├── agent.py        # AgentInfo 定义
│   │   ├── builtins.py     # 内置 Agent
│   │   ├── loop.py         # Agent Loop
│   │   ├── permissions.py  # 权限系统
│   │   ├── executive.py    # Executive Controller
│   │   └── worker_lifecycle.py  # Worker 生命周期
│   ├── context/            # 上下文管理
│   │   ├── token.py        # Token 估算
│   │   ├── worker_context.py    # Worker 上下文构建
│   │   ├── worker_isolation.py  # 上下文隔离
│   │   └── context_filter.py    # 上下文过滤
│   ├── task_graph/         # 任务图
│   │   └── graph.py        # DAG 数据结构
│   ├── tool/               # 工具系统
│   │   ├── base.py         # 工具基类
│   │   ├── filter.py       # 三层过滤
│   │   └── task.py         # TaskTool
│   ├── provider/           # Provider 系统
│   │   ├── registry.py     # 模型注册
│   │   └── router.py       # 模型路由
│   ├── session/            # Session 系统
│   │   ├── service.py      # Session 服务
│   │   └── compaction.py   # 上下文压缩
│   ├── prompt/             # Prompt 系统
│   │   └── engine.py       # Prompt 组装
│   └── cli/                # CLI 界面
│       └── main.py         # 入口
├── tests/                  # 测试
├── docs/                   # 文档
└── pyproject.toml          # 项目配置
```

## 内置 Agent

| Agent | 类型 | 权限 | 用途 |
|-------|------|------|------|
| `build` | primary | 全部 | 默认主 Agent |
| `plan` | primary | 只读 | 规划模式 |
| `general` | subagent | 默认 | 通用任务 |
| `explore` | subagent | 只读 | 代码搜索 |
| `code` | subagent | 默认 | 代码编写 |
| `test` | subagent | 默认 | 测试编写 |
| `document` | subagent | 只读+写 | 文档生成 |
| `compaction` | hidden | 无 | 上下文压缩 |
| `title` | hidden | 无 | 标题生成 |
| `summary` | hidden | 无 | 对话摘要 |

## 开发

### 运行测试

```bash
# 运行所有测试
pytest

# 运行特定测试
pytest tests/test_agent.py

# 运行并显示覆盖率
pytest --cov=src
```

### 代码检查

```bash
# Lint
ruff check src/

# Format
ruff format src/

# Type check
mypy src/
```

## 参考

- [Claude Code 架构分析](docs/ClaudeCode源码解读-agent架构.md)
- [OpenCode 架构分析](docs/architecture-analysis.md)
- [Worker 隔离计划](docs/worker-isolation-plan.md)

## License

MIT

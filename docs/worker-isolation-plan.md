# Worker 类型扩展与上下文隔离开发计划

## 1. 概述

本计划实现以下功能：
- 添加 test 和 document worker 类型
- 实现 Worker Context 精简机制
- 实现三层隔离（Session, Task, Worker）
- 实现上下文过滤器

## 2. 架构设计

### 2.1 三层隔离模型

```
Session 层 (Parent Session)
├── 完整对话历史
├── 用户偏好
└── 项目上下文

Task 层 (Task Context)
├── 任务描述
├── 相关文件列表
└── 依赖任务结果

Worker 层 (Worker Session)
├── 精简任务描述
├── 相关文件内容（仅必要部分）
└── 依赖结果摘要
```

### 2.2 Worker 类型定义

| Worker | 权限 | 用途 |
|--------|------|------|
| general | default | 通用复杂任务 |
| explore | read_only | 代码搜索探索 |
| code | default + 测试 | 代码编写任务 |
| test | default + 测试 | 测试编写执行 |
| document | read_only + 写文档 | 文档生成任务 |

## 3. 实现步骤

### Phase 1: 添加 Worker 类型定义

**文件修改：**
- `src/agent/builtins.py` - 添加 CODE_AGENT, TEST_AGENT, DOCUMENT_AGENT
- `src/agent/permissions.py` - 添加测试相关权限

**新增 Worker：**

```python
CODE_AGENT = AgentInfo(
    name="code",
    mode="subagent",
    permission=PermissionRuleset(
        allow_bash=True,
        allow_network=True,
        allow_file_write=True,
        deny_tools={"task", "question"},
    ),
    system_prompt="You are a coding worker. Focus on writing clean, testable code.",
)

TEST_AGENT = AgentInfo(
    name="test",
    mode="subagent",
    permission=PermissionRuleset(
        allow_bash=True,
        allow_network=True,
        allow_file_write=True,
        deny_tools={"task", "question"},
        allow_tools={"read", "glob", "grep", "bash", "write", "edit"},
    ),
    system_prompt="You are a testing worker. Write and run tests to verify code quality.",
)

DOCUMENT_AGENT = AgentInfo(
    name="document",
    mode="subagent",
    permission=PermissionRuleset(
        allow_bash=False,
        allow_network=False,
        allow_file_write=True,
        deny_tools={"task", "question", "bash"},
        allow_tools={"read", "glob", "grep", "write"},
    ),
    system_prompt="You are a documentation worker. Generate clear, comprehensive documentation.",
)
```

### Phase 2: 实现上下文过滤器

**新增文件：** `src/context/context_filter.py`

```python
class ContextFilter:
    """上下文过滤器 - 根据 agent 类型和任务需求过滤内容"""
    
    def filter_for_worker(self, messages: list, agent_type: str, 
                          task_description: str) -> list:
        """为 worker 过滤上下文"""
        pass
    
    def extract_relevant_files(self, messages: list, 
                               task_description: str) -> list[str]:
        """从历史消息中提取相关文件路径"""
        pass
    
    def summarize_dependencies(self, dependency_results: dict) -> str:
        """总结依赖任务的结果"""
        pass
```

### Phase 3: 实现 Worker Context Builder

**新增文件：** `src/context/worker_context.py`

```python
class WorkerContextBuilder:
    """Worker 上下文构建器 - 生成精简的任务上下文"""
    
    def build(self, task_spec: dict, agent_type: str,
              dependency_results: dict = None) -> str:
        """构建 worker 上下文"""
        context_parts = [
            f"Task: {task_spec['description']}",
            f"Goal: {task_spec['prompt']}",
            "",
            "Relevant Context:",
        ]
        
        # 添加相关文件
        if 'relevant_files' in task_spec:
            for f in task_spec['relevant_files']:
                context_parts.append(f"  - {f}")
        
        # 添加依赖结果摘要
        if dependency_results:
            context_parts.append("")
            context_parts.append("Previous Results:")
            for dep_id, result in dependency_results.items():
                context_parts.append(f"  [{dep_id}]: {result.get('summary', 'No summary')}")
        
        return "\n".join(context_parts)
```

### Phase 4: 更新 TaskTool 支持新 Worker 类型

**修改文件：** `src/tool/task.py`

主要修改：
1. 更新 `parameters` 添加新的 `subagent_type` 选项
2. 集成 `WorkerContextBuilder`
3. 更新 `_build_worker_context` 方法使用精简上下文

### Phase 5: 实现三层隔离

**Session 层隔离（已有）：**
- Parent-child session 关系
- 完整历史只在 parent session

**Task 层隔离（新增）：**
- 在 task tool 中实现任务级过滤
- 只传递任务相关上下文

**Worker 层隔离（新增）：**
- Worker session 只包含精简上下文
- 不传递完整对话历史

## 4. 文件清单

### 新增文件
1. `src/context/context_filter.py` - 上下文过滤器
2. `src/context/worker_context.py` - Worker 上下文构建器
3. `tests/test_context_filter.py` - 过滤器测试
4. `tests/test_worker_context.py` - Worker 上下文测试

### 修改文件
1. `src/agent/builtins.py` - 添加新 Worker 类型
2. `src/agent/permissions.py` - 添加测试权限配置
3. `src/tool/task.py` - 集成新功能
4. `src/provider/router.py` - 更新路由支持新 Worker 类型

## 5. 测试计划

### 单元测试
- ContextFilter 测试：验证过滤逻辑
- WorkerContextBuilder 测试：验证上下文构建
- 新 Agent 类型测试：验证权限配置

### 集成测试
- TaskTool 端到端测试：验证完整流程
- Worker 隔离测试：验证上下文不泄露

## 6. 验收标准

1. ✅ 支持 general, explore, code, test, document 五种 worker 类型
2. ✅ Worker session 不包含父 session 的完整历史
3. ✅ Worker 只接收任务描述 + 相关文件 + 依赖结果摘要
4. ✅ 不同 worker 类型有合适的权限配置
5. ✅ 所有测试通过

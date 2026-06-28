"""Worker Lifecycle Manager - Worker 生命周期管理和 Resume 机制。

借鉴 Claude Code 的设计：
- Worker 完成后不会被销毁，对话历史持久化
- 可以通过 SendMessage 唤醒已停止的 Worker
- Resume 时保留之前的完整上下文
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from ulid import ULID


class WorkerStatus(StrEnum):
    """Worker 状态。"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"  # 可被 Resume


@dataclass
class WorkerRecord:
    """Worker 记录 - 持久化的 Worker 状态。"""
    worker_id: str
    session_id: str
    agent_type: str
    status: WorkerStatus
    created_at: datetime
    updated_at: datetime

    # 任务信息
    task_id: str = ""
    task_description: str = ""

    # 执行结果
    result: str = ""
    error: str = ""

    # 对话历史（用于 Resume）
    messages: list[dict[str, Any]] = field(default_factory=list)

    # 上下文快照
    context_snapshot: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "worker_id": self.worker_id,
            "session_id": self.session_id,
            "agent_type": self.agent_type,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "task_id": self.task_id,
            "task_description": self.task_description,
            "result": self.result,
            "error": self.error,
            "messages": self.messages,
            "context_snapshot": self.context_snapshot,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkerRecord:
        """从字典创建。"""
        return cls(
            worker_id=data["worker_id"],
            session_id=data["session_id"],
            agent_type=data["agent_type"],
            status=WorkerStatus(data["status"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            task_id=data.get("task_id", ""),
            task_description=data.get("task_description", ""),
            result=data.get("result", ""),
            error=data.get("error", ""),
            messages=data.get("messages", []),
            context_snapshot=data.get("context_snapshot", {}),
        )


class WorkerLifecycleManager:
    """Worker 生命周期管理器。

    职责：
    1. 管理 Worker 的创建、运行、完成、失败状态
    2. 持久化 Worker 的对话历史
    3. 支持 Resume - 唤醒已停止的 Worker 继续执行
    """

    def __init__(self, storage_path: str | None = None):
        self._workers: dict[str, WorkerRecord] = {}
        self._storage_path = storage_path
        self._message_queue: dict[str, list[dict[str, Any]]] = {}  # worker_id -> messages

        # 如果有存储路径，加载已有的 Worker 记录
        if storage_path:
            self._load_from_storage()

    def create_worker(
        self,
        session_id: str,
        agent_type: str,
        task_id: str = "",
        task_description: str = "",
    ) -> WorkerRecord:
        """创建新的 Worker。"""
        worker_id = f"worker-{ULID()}"
        now = datetime.now()

        record = WorkerRecord(
            worker_id=worker_id,
            session_id=session_id,
            agent_type=agent_type,
            status=WorkerStatus.PENDING,
            created_at=now,
            updated_at=now,
            task_id=task_id,
            task_description=task_description,
        )

        self._workers[worker_id] = record
        self._message_queue[worker_id] = []
        self._save_to_storage()

        return record

    def get_worker(self, worker_id: str) -> WorkerRecord | None:
        """获取 Worker 记录。"""
        return self._workers.get(worker_id)

    def get_worker_by_session(self, session_id: str) -> WorkerRecord | None:
        """通过 Session ID 获取 Worker 记录。"""
        for record in self._workers.values():
            if record.session_id == session_id:
                return record
        return None

    def update_status(
        self,
        worker_id: str,
        status: WorkerStatus,
        result: str = "",
        error: str = "",
    ):
        """更新 Worker 状态。"""
        record = self._workers.get(worker_id)
        if record:
            record.status = status
            record.updated_at = datetime.now()
            if result:
                record.result = result
            if error:
                record.error = error
            self._save_to_storage()

    def add_messages(self, worker_id: str, messages: list[dict[str, Any]]):
        """添加消息到 Worker 的对话历史。"""
        record = self._workers.get(worker_id)
        if record:
            record.messages.extend(messages)
            record.updated_at = datetime.now()
            self._save_to_storage()

    def queue_message(self, worker_id: str, message: dict[str, Any]):
        """排队消息（用于 Resume）。"""
        if worker_id not in self._message_queue:
            self._message_queue[worker_id] = []
        self._message_queue[worker_id].append(message)

    def get_pending_messages(self, worker_id: str) -> list[dict[str, Any]]:
        """获取待处理的消息。"""
        messages = self._message_queue.get(worker_id, [])
        self._message_queue[worker_id] = []
        return messages

    def can_resume(self, worker_id: str) -> bool:
        """检查是否可以 Resume。"""
        record = self._workers.get(worker_id)
        if not record:
            return False

        # 只有已完成或失败的 Worker 可以 Resume
        return record.status in (
            WorkerStatus.COMPLETED,
            WorkerStatus.FAILED,
            WorkerStatus.PAUSED,
        )

    def resume_worker(self, worker_id: str) -> WorkerRecord | None:
        """Resume Worker。

        借鉴 Claude Code 的 Resume 机制：
        - 从持久化存储恢复 Worker 状态
        - 保留之前的完整对话历史
        - 设置为 RUNNING 状态
        """
        record = self._workers.get(worker_id)
        if not record or not self.can_resume(worker_id):
            return None

        record.status = WorkerStatus.RUNNING
        record.updated_at = datetime.now()
        self._save_to_storage()

        return record

    def get_all_workers(self, status: WorkerStatus | None = None) -> list[WorkerRecord]:
        """获取所有 Worker。"""
        if status:
            return [r for r in self._workers.values() if r.status == status]
        return list(self._workers.values())

    def _save_to_storage(self):
        """保存到存储。"""
        if not self._storage_path:
            return

        try:
            data = {
                "workers": {k: v.to_dict() for k, v in self._workers.items()},
                "message_queue": self._message_queue,
            }
            with open(self._storage_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass  # 静默失败

    def _load_from_storage(self):
        """从存储加载。"""
        if not self._storage_path:
            return

        try:
            import os
            if not os.path.exists(self._storage_path):
                return

            with open(self._storage_path, encoding="utf-8") as f:
                data = json.load(f)

            for k, v in data.get("workers", {}).items():
                self._workers[k] = WorkerRecord.from_dict(v)

            self._message_queue = data.get("message_queue", {})
        except Exception:
            pass  # 静默失败


class SendMessageTool:
    """SendMessage 工具 - 向已停止的 Worker 发送消息。

    借鉴 Claude Code 的 SendMessage 设计：
    - 支持向运行中的 Worker 发送后续指令
    - 支持 Resume 已停止的 Worker
    - 消息类型：文本、shutdown_request、plan_approval_response
    """

    def __init__(
        self,
        lifecycle_manager: WorkerLifecycleManager,
        resume_callback: Callable[[str], None] | None = None,
    ):
        self._lifecycle = lifecycle_manager
        self._resume_callback = resume_callback

    async def send(
        self,
        worker_id: str,
        message: str,
        message_type: str = "text",
        summary: str = "",
    ) -> dict[str, Any]:
        """发送消息给 Worker。

        Args:
            worker_id: 目标 Worker ID
            message: 消息内容
            message_type: 消息类型 (text/shutdown_request/plan_approval_response)
            summary: 消息摘要

        Returns:
            发送结果
        """
        record = self._lifecycle.get_worker(worker_id)
        if not record:
            return {"success": False, "error": f"Worker {worker_id} not found"}

        # 构建消息
        msg = {
            "type": message_type,
            "content": message,
            "summary": summary,
            "timestamp": datetime.now().isoformat(),
        }

        if record.status == WorkerStatus.RUNNING:
            # 运行中 → 排队
            self._lifecycle.queue_message(worker_id, msg)
            return {
                "success": True,
                "action": "queued",
                "message": "Message queued for running worker",
            }

        elif self._lifecycle.can_resume(worker_id):
            # 已停止 → Resume
            self._lifecycle.queue_message(worker_id, msg)

            if self._resume_callback:
                self._resume_callback(worker_id)

            return {
                "success": True,
                "action": "resumed",
                "message": "Worker resumed with message",
            }

        else:
            return {
                "success": False,
                "error": f"Worker {worker_id} cannot be resumed (status: {record.status})",
            }

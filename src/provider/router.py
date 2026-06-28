"""ModelRouter — 基于能力的子 agent 模型选择。

纯代码路由，不依赖 LLM。参考 OpenCode 的 Capability Router 设计。
"""

import time

from src.provider.registry import ModelInfo, ProviderRegistry

COST_RANK: dict[str, int] = {
    "very-low": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "very-high": 4,
}

CAPABILITY_RANK: dict[str, int] = {
    "very-low": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "very-high": 4,
}

TASK_REQUIREMENTS: dict[str, dict] = {
    "explore": {"min_capability": "low", "prefer_cost": ["very-low", "low", "medium"]},
    "general": {"min_capability": "medium", "prefer_cost": ["medium", "high"]},
    "code": {"min_capability": "medium", "prefer_cost": ["medium", "high"]},
    "test": {"min_capability": "medium", "prefer_cost": ["low", "medium"]},
    "document": {"min_capability": "low", "prefer_cost": ["very-low", "low", "medium"]},
}

UNAVAILABLE_TTL = 300  # 5 分钟后自动恢复


class ModelRouter:
    """为子 agent 选择最优模型。

    - 根据 task 类型匹配能力等级
    - 子模型 cost ≤ 父模型 cost
    - 追踪不可用模型，避免重复调用
    """

    def __init__(self):
        self._unavailable: dict[str, float] = {}  # "provider_id/model_id" → timestamp

    def select(
        self,
        subagent_type: str,
        registry: ProviderRegistry,
        parent_provider_id: str = "",
        parent_model_id: str = "",
    ) -> tuple[str, str] | None:
        """选择一个可用模型。返回 (provider_id, model_id) 或 None。

        选择策略：
        1. 根据任务类型筛选满足能力要求的模型
        2. 排除成本高于父模型的模型
        3. 按任务类型排序（explore/document 低成本优先，code 高能力优先）
        4. 如果无合适模型，返回 None（由调用方处理兜底）
        """
        req = TASK_REQUIREMENTS.get(subagent_type)
        if not req:
            return None

        min_cap = CAPABILITY_RANK.get(req["min_capability"], 0)

        parent_cost = self._resolve_parent_cost(registry, parent_provider_id, parent_model_id)

        candidates: list[tuple[int, int, str, str, ModelInfo]] = []
        for pid in registry.detected_providers:
            p = registry.get_provider(pid)
            if not p:
                continue
            for mid, mi in p.models.items():
                if not self._is_available(pid, mi.model):
                    continue
                cap = CAPABILITY_RANK.get(mi.capability, 0)
                cost = COST_RANK.get(mi.cost, 0)
                if cap < min_cap:
                    continue
                if cost > parent_cost:
                    continue
                candidates.append((cap, cost, pid, mid, mi))

        if not candidates:
            return None

        # 根据 worker 类型选择排序策略
        if subagent_type in ("explore", "document"):
            # 低成本优先：这些任务不需要强模型
            candidates.sort(key=lambda x: (x[1], -x[0]))
        elif subagent_type == "test":
            # 平衡：中等能力，低成本
            candidates.sort(key=lambda x: (x[1], -x[0]))
        else:
            # 高能力优先：general 和 code 需要强模型
            candidates.sort(key=lambda x: (-x[0], x[1]))
        best = candidates[0]
        return (best[2], best[3])

    def select_with_fallback(
        self,
        subagent_type: str,
        registry: ProviderRegistry,
        parent_provider_id: str = "",
        parent_model_id: str = "",
        default_model: str = "",
    ) -> tuple[str, str] | None:
        """选择模型，带兜底策略。

        兜底优先级：
        1. 路由选择（满足能力+成本约束）
        2. 父模型（继承当前会话模型）
        3. 默认模型（配置文件中的 default_model）
        """
        # 1. 路由选择
        result = self.select(subagent_type, registry, parent_provider_id, parent_model_id)
        if result:
            return result

        # 2. 兜底：使用父模型
        if parent_provider_id and parent_model_id:
            return (parent_provider_id, parent_model_id)

        # 3. 兜底：使用默认模型
        if default_model:
            if "/" in default_model:
                parts = default_model.split("/", 1)
                return (parts[0], parts[1])
            # 尝试从 registry 解析
            resolved = registry.resolve(default_model)
            if resolved:
                return (resolved.provider, resolved.model)

        return None

    def mark_unavailable(self, provider_id: str, model_id: str):
        """标记模型暂时不可用。"""
        key = f"{provider_id}/{model_id}"
        self._unavailable[key] = time.time()

    def _is_available(self, provider_id: str, model_id: str) -> bool:
        key = f"{provider_id}/{model_id}"
        ts = self._unavailable.get(key)
        if ts is None:
            return True
        if time.time() - ts > UNAVAILABLE_TTL:
            self._unavailable.pop(key, None)
            return True
        return False

    def _resolve_parent_cost(
        self, registry: ProviderRegistry, provider_id: str, model_id: str
    ) -> int:
        """获取父模型的 cost 等级数值。默认 4 (very-high) 表示无限制。"""
        p = registry.get_provider(provider_id)
        if p:
            mi = p.resolve_model(model_id)
            if mi:
                return COST_RANK.get(mi.cost, 4)
        return 4

    @property
    def unavailable_models(self) -> list[str]:
        now = time.time()
        return [k for k, ts in self._unavailable.items() if now - ts <= UNAVAILABLE_TTL]

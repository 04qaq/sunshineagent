"""CapabilityRouter — 纯代码模型路由。

原则：
  - 强模型规划 + 审查，弱模型执行
  - Worker 代价 ≤ 聊天模型代价
  - 评分：能力匹配(50%) + 质量(20%) + 成本(20%) + 速度(10%)
"""

from src.config.provider import COST_LEVELS
from src.provider.catalog import ModelCatalog, ModelEntry


class CapabilityRouter:
    """按任务类型 + 质量 + 成本限制选择最优模型。"""

    def __init__(self, catalog: ModelCatalog):
        self._catalog = catalog

    def route(self, task_type: str, *, max_cost: str = "very-high") -> ModelEntry | None:
        """为任务选择模型，代价不超过 max_cost。"""
        from src.agent.workers import TASK_REQUIREMENTS

        req = TASK_REQUIREMENTS.get(task_type)
        if req is None:
            req = TASK_REQUIREMENTS.get("general")

        candidates = self._catalog.list_by_cost(max_cost)
        scored = [(m, self._score(m, req)) for m in candidates]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[0][0] if scored else None

    def _score(self, model: ModelEntry, req) -> float:
        cap_score = 0.0
        for cap in req.capabilities:
            if cap in model.tags:
                cap_score += 1.0
        if req.capabilities:
            cap_score = (cap_score / len(req.capabilities)) * 50

        qw = {"high": 1.0, "medium": 0.5, "low": 0.0}.get(req.quality, 0.5)
        quality_score = qw * 20

        cost_level = COST_LEVELS.get(model.cost_tier, 2)
        cost_score = max(0, 20 - cost_level * 5)

        speed_score = min(10, model.max_output_tokens / 32768 * 10)

        return cap_score + quality_score + cost_score + speed_score

    def route_with_fallback(
        self, task_type: str, *, max_cost: str = "very-high"
    ) -> tuple[ModelEntry, ModelEntry]:
        primary = self.route(task_type, max_cost=max_cost)
        fallback = self.route("general", max_cost=max_cost)
        if primary is None:
            primary = fallback
        return primary, fallback

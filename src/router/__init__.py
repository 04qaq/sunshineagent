"""CapabilityRouter — 纯代码模型路由，按任务类型选最合适的模型。

原则：
  - 强模型规划 + 审查，弱模型执行
  - 评分公式：能力匹配(50%) + 质量需求(20%) + 成本偏好(20%) + 速度(10%)
  - 不调 LLM，纯计算
"""

from dataclasses import dataclass, field

from src.provider.catalog import ModelCatalog, ModelEntry


@dataclass
class TaskRequirement:
    """任务所需的能力描述。"""
    task_type: str
    capabilities: list[str] = field(default_factory=list)
    quality: str = "medium"   # "low" | "medium" | "high"
    budget: str = "medium"    # "low" | "medium" | "high"


# ── 任务类型定义 ──────────────────────────────────────────────────────

TASK_REQUIREMENTS: dict[str, TaskRequirement] = {
    "plan": TaskRequirement(
        task_type="plan",
        capabilities=["planning", "architecture", "reasoning"],
        quality="high",
        budget="high",
    ),
    "explore": TaskRequirement(
        task_type="explore",
        capabilities=["search"],
        quality="low",
        budget="low",
    ),
    "code": TaskRequirement(
        task_type="code",
        capabilities=["code_generation", "reasoning"],
        quality="high",
        budget="medium",
    ),
    "test": TaskRequirement(
        task_type="test",
        capabilities=["code_generation", "test"],
        quality="medium",
        budget="low",
    ),
    "review": TaskRequirement(
        task_type="review",
        capabilities=["review", "code_generation", "reasoning"],
        quality="high",
        budget="high",
    ),
    "document": TaskRequirement(
        task_type="document",
        capabilities=["document"],
        quality="low",
        budget="low",
    ),
    "general": TaskRequirement(
        task_type="general",
        capabilities=["code_generation", "reasoning", "general"],
        quality="medium",
        budget="medium",
    ),
}


# ── 模型速度评分（1-10，声明式）─────────────────────────────────────

MODEL_SPEED: dict[str, int] = {
    "claude-opus-4-6": 7,
    "claude-sonnet-4-6": 8,
    "gpt-5": 8,
    "qwen3-8b": 9,
}


# ── 质量和预算的权重映射 ─────────────────────────────────────────────

QUALITY_WEIGHT = {"high": 1.0, "medium": 0.5, "low": 0.0}
BUDGET_WEIGHT = {"high": 0.0, "medium": 0.5, "low": 1.0}


class CapabilityRouter:
    """能力路由器 —— 按任务类型 + 质量 + 预算选择最优模型。"""

    def __init__(self, catalog: ModelCatalog | None = None):
        self._catalog = catalog or ModelCatalog()

    def route(
        self,
        task_type: str,
        *,
        preferred: str | None = None,
        provider: str | None = None,
    ) -> ModelEntry | None:
        """为指定任务选择最优模型。

        Args:
            task_type: 任务类型 (plan/explore/code/test/review/document/general)
            preferred: 用户偏好模型 ID（可选覆盖）
            provider: 限制 provider（可选）

        Returns:
            ModelEntry 或 None
        """
        req = TASK_REQUIREMENTS.get(task_type)
        if req is None:
            req = TASK_REQUIREMENTS["general"]

        candidates = list(self._catalog.MODELS)

        if provider:
            candidates = [m for m in candidates if m.provider_id == provider]

        if preferred:
            match = self._catalog.resolve(
                candidates[0].provider_id if candidates else "", preferred
            )
            if match:
                return match

        scored = [(m, self._score(m, req)) for m in candidates]
        scored.sort(key=lambda x: x[1], reverse=True)

        return scored[0][0] if scored else None

    def _score(self, model: ModelEntry, req: TaskRequirement) -> float:
        """评分模型匹配度。"""
        # 能力匹配（0-50）
        cap_score = 0.0
        for cap in req.capabilities:
            if cap in model.tags:
                cap_score += 1.0
        if req.capabilities:
            cap_score = (cap_score / len(req.capabilities)) * 50

        # 质量权重（0-20）
        quality_score = QUALITY_WEIGHT.get(req.quality, 0.5) * 20

        # 成本偏好（0-20）—— 越便宜得分越高
        cost = model.input_price + model.output_price
        budget = BUDGET_WEIGHT.get(req.budget, 0.5)
        cost_score = budget * (1.0 - min(cost / 10.0, 1.0)) * 20

        # 速度（0-10）
        speed = MODEL_SPEED.get(model.model_id, 5)
        speed_score = (speed / 10.0) * 10

        return cap_score + quality_score + cost_score + speed_score

    def route_with_fallback(
        self,
        task_type: str,
        *,
        preferred: str | None = None,
        provider: str | None = None,
    ) -> tuple[ModelEntry, ModelEntry]:
        """返回主选和备选模型。"""
        primary = self.route(task_type, preferred=preferred, provider=provider)
        fallback = self.route("general", provider=provider)
        if primary is None:
            primary = fallback
        return primary, fallback

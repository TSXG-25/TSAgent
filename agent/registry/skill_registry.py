import numpy as np
from typing import List, Optional

_embedding = None


def _get_embedding():
    global _embedding
    if _embedding is None:
        from agent.embeddings import get_embedding

        _embedding = get_embedding()
    return _embedding


def _embed_query(text: str) -> Optional[np.ndarray]:
    try:
        return np.array(_get_embedding().embed_query(text))
    except Exception:
        return None


class Skill:
    def __init__(
        self,
        name: str,
        description: str,
        planner_hint: str = "",
    ):
        self.name = name
        self.description = description
        self.planner_hint = planner_hint
        self._embedding = None

    @property
    def embedding(self):
        """Lazy load embedding on first access"""
        if self._embedding is None:
            self._embedding = _embed_query(self.description)
        return self._embedding

    def match(self, query: str, query_embedding: Optional[np.ndarray]) -> float:
        if self.embedding is None or query_embedding is None:
            text = f"{self.name} {self.description}".lower()
            query_terms = set(query.lower().split())
            if not query_terms:
                return 0.0
            return len(query_terms.intersection(text.split())) / len(query_terms)
        return np.dot(query_embedding, self.embedding) / (
            np.linalg.norm(query_embedding) * np.linalg.norm(self.embedding)
        )

    def get_system_prompt(self) -> str:
        hint = f"\n{self.planner_hint}" if self.planner_hint else ""
        return f"当前技能：{self.name}。{self.description}{hint}"

    def get_workflow(self) -> Optional[str]:
        return None


class SkillRegistry:
    def __init__(self):
        self.skills: List[Skill] = []

    def register(self, skill: Skill):
        self.skills.append(skill)

    def select(self, user_input: str) -> Optional[Skill]:
        if not self.skills:
            return None

        query_emb = _embed_query(user_input)
        best = max(self.skills, key=lambda s: s.match(user_input, query_emb))
        return best if best.match(user_input, query_emb) > 0.3 else None


skill_registry = SkillRegistry()

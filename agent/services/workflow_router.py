"""WorkflowRouter — Embedding 优先 + LLM 二级路由。

路由策略：
  1. score >= 0.75 → 直接返回 Workflow（高置信度）
  2. 0.45 <= score < 0.75 → LLM 从 Top-3 中选择
  3. score < 0.45 → 返回 None（Planner 兜底）
  4. Top1 - Top2 < 0.08 → 即使 score >= 0.75 也走 LLM（margin 过小）
"""
import numpy as np
import json
from typing import Dict, List, Optional, Tuple, Any
from langchain_core.messages import SystemMessage, HumanMessage
from agent.embeddings import get_embedding
from agent.llm import llm


LLM_ROUTER_PROMPT = """You are a workflow router. Given a user request and candidate workflows, choose the best one.

Return ONLY a JSON object:
- "workflow": the chosen workflow_id, or null if none matches
- "reason": brief explanation

Workflows:
{workflows}

User: {input}
JSON:"""


class WorkflowRouter:
    
    def __init__(self, threshold: float = 0.75, llm_lower: float = 0.45, margin: float = 0.08):
        self._threshold = threshold
        self._llm_lower = llm_lower
        self._margin = margin
        self._embedding = None
        self._index: Dict[str, List[Tuple[str, np.ndarray]]] = {}
        self._workflow_map: Dict[str, Any] = {}
        self._keyword_map: Dict[str, List[str]] = {}
    
    @property
    def embedding(self):
        if self._embedding is None:
            self._embedding = get_embedding()
        return self._embedding
    
    def register_workflow(self, workflow_id: str, examples: List[str], workflow_obj: Any,
                          keywords: Optional[List[str]] = None):
        self._workflow_map[workflow_id] = workflow_obj
        vecs = []
        for ex in examples:
            try:
                vec = np.array(self.embedding.embed_query(ex))
                vecs.append((ex, vec))
            except Exception:
                continue
        if vecs:
            self._index[workflow_id] = vecs
        if keywords:
            self._keyword_map[workflow_id] = keywords
    
    def _embed_query(self, text: str) -> Optional[np.ndarray]:
        try:
            return np.array(self.embedding.embed_query(text))
        except Exception:
            return None
    
    def compute_scores(self, query_vec: np.ndarray) -> List[Tuple[str, float, str]]:
        results = []
        for wf_id, examples in self._index.items():
            best_score = -1.0
            best_example = ""
            for ex, vec in examples:
                norm = np.linalg.norm(query_vec) * np.linalg.norm(vec)
                score = float(np.dot(query_vec, vec) / norm) if norm > 0 else 0
                if score > best_score:
                    best_score = score
                    best_example = ex
            results.append((wf_id, best_score, best_example))
        results.sort(key=lambda x: -x[1])
        return results
    
    def _keyword_match(self, user_input: str) -> Optional[Any]:
        text = user_input.lower()
        for wf_id, keywords in self._keyword_map.items():
            if any(k in text for k in keywords):
                wf = self._workflow_map.get(wf_id)
                if wf:
                    return wf
        return None
    
    def _llm_route(self, user_input: str, scored: List[Tuple[str, float, str]]) -> Tuple[Optional[Any], str]:
        """LLM 二级路由：从 Top-3 候选 Workflow 中选择。"""
        top3 = scored[:3]
        candidates = []
        for wf_id, score, ex in top3:
            wf_obj = self._workflow_map.get(wf_id)
            if wf_obj:
                desc = wf_obj.description if hasattr(wf_obj, 'description') else wf_id
                candidates.append(f"id: {wf_id}\n  description: {desc}\n  score: {score:.2f}\n  match: {ex}")
        
        if not candidates:
            return None, "LLM 路由：无可用候选 Workflow"
        
        workflows_text = "\n\n".join(candidates)
        prompt = LLM_ROUTER_PROMPT.format(workflows=workflows_text, input=user_input)
        
        try:
            response = llm.invoke([SystemMessage(content=prompt)])
            content = response.content.strip()
            # Extract JSON
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            content = content.strip()
            obj = json.loads(content)
            chosen = obj.get("workflow")
            reason = obj.get("reason", "")
            if chosen and chosen in self._workflow_map:
                wf = self._workflow_map[chosen]
                return wf, f"LLM 路由: {chosen} ({reason})"
            return None, f"LLM 路由: 无匹配 ({reason})"
        except Exception as e:
            # LLM 失败时降级到 Top-1
            top_id, top_score, top_ex = scored[0]
            wf = self._workflow_map.get(top_id)
            if wf:
                return wf, f"LLM 路由失败，降级到 Top-1 ({top_id}, {top_score:.2f})"
            return None, f"LLM 路由失败: {e}"
    
    def match(self, user_input: str) -> Tuple[Optional[Any], str]:
        # Stage 0: 关键词兜底
        kw = self._keyword_match(user_input)
        if kw:
            return kw, "关键词匹配"
        
        query_vec = self._embed_query(user_input)
        if query_vec is None:
            return None, "Embedding 不可用"
        
        scored = self.compute_scores(query_vec)
        if not scored:
            return None, "无注册 Workflow"
        
        top_id, top_score, top_ex = scored[0]
        
        # Stage 1: 低置信度 → Planner
        if top_score < self._llm_lower:
            return None, f"Embedding低置信度 ({top_score:.2f})，低于 LLM 路由下限"
        
        # Stage 2: 中高置信度 → 检查是否需要用 LLM
        need_llm = False
        llm_reason = ""
        
        if top_score >= self._threshold:
            # 高置信度，但检查 margin
            if len(scored) >= 2:
                second_score = scored[1][1]
                margin = top_score - second_score
                if margin < self._margin:
                    need_llm = True
                    llm_reason = f"margin={margin:.2f} 过小"
            if not need_llm:
                wf = self._workflow_map.get(top_id)
                if wf:
                    return wf, f"Embedding高置信度 ({top_score:.2f}): {top_ex}"
                return None, f"Workflow '{top_id}' 已注册但对象不可用"
        else:
            # 中置信度 (0.45~0.75)
            need_llm = True
            llm_reason = f"score={top_score:.2f} 中等"
        
        # Stage 3: LLM 二级路由
        return self._llm_route(user_input, scored)


router = WorkflowRouter()
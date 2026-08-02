"""PromptRegistry — 工作流提示词注册中心。"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class PromptTemplate:
    workflow_id: str
    stage_id: str
    system: str = ""
    user: str = ""
    variables: List[str] = field(default_factory=list)


class PromptRegistry:
    _cache: Dict[str, PromptTemplate] = {}
    _base_path = Path(__file__).parent
    
    @classmethod
    def get(cls, workflow_id: str, stage_id: str) -> Optional[PromptTemplate]:
        cache_key = f"{workflow_id}/{stage_id}"
        if cache_key in cls._cache:
            return cls._cache[cache_key]
        
        file_path = cls._base_path / workflow_id / f"{stage_id}.txt"
        if not file_path.exists():
            return None
        
        try:
            content = file_path.read_text(encoding="utf-8")
            template = cls._parse_template(workflow_id, stage_id, content)
            cls._cache[cache_key] = template
            return template
        except Exception:
            return None
    
    @classmethod
    def _parse_template(cls, workflow_id: str, stage_id: str, content: str) -> PromptTemplate:
        system = ""
        user = content
        variables = []
        
        if "---system---" in content:
            parts = content.split("---system---", 1)
            if len(parts) == 2:
                before_system, rest = parts
                if "---user---" in rest:
                    sys_parts = rest.split("---user---", 1)
                    system = sys_parts[0].strip()
                    user = sys_parts[1].strip()
                else:
                    system = rest.strip()
                    user = before_system.strip()
        
        import re
        variables = re.findall(r'\{(\w+)\}', user)
        
        return PromptTemplate(
            workflow_id=workflow_id, stage_id=stage_id,
            system=system, user=user, variables=variables,
        )
    
    @classmethod
    def render(cls, workflow_id: str, stage_id: str, **kwargs) -> str:
        """渲染完整的 prompt（system + user + 注入）。"""
        template = cls.get(workflow_id, stage_id)
        if not template:
            return ""
        
        result = template.system
        if result:
            result += "\n\n"
        result += template.user
        
        for key, value in kwargs.items():
            result = result.replace(f"{{{key}}}", str(value))
        
        return result
    
    @classmethod
    def render_with_context(cls, workflow_id: str, stage_id: str, artifacts: Dict[str, Any]) -> str:
        """从 ExecutionContext 中的 Artifact 自动注入变量。"""
        template = cls.get(workflow_id, stage_id)
        if not template:
            return ""
        
        result = template.system
        if result:
            result += "\n\n"
        result += template.user
        
        # 注入 variables 对应的 artifact 内容
        for var_name in template.variables:
            art = artifacts.get(var_name)
            if art:
                content = art.content if hasattr(art, 'content') else str(art)
                result = result.replace(f"{{{var_name}}}", str(content)[:3000])
        
        return result
    
    @classmethod
    def render_parts(cls, workflow_id: str, stage_id: str, artifacts: Dict[str, Any]) -> tuple:
        """返回 (system, user) 两段注入后的文本。"""
        template = cls.get(workflow_id, stage_id)
        if not template:
            return ("", "")
        
        system = template.system
        user = template.user
        
        for var_name in template.variables:
            art = artifacts.get(var_name)
            if art:
                content = art.content if hasattr(art, 'content') else str(art)
                replacement = str(content)[:3000]
                system = system.replace(f"{{{var_name}}}", replacement)
                user = user.replace(f"{{{var_name}}}", replacement)
        
        return (system, user)
    
    @classmethod
    def clear_cache(cls):
        cls._cache.clear()
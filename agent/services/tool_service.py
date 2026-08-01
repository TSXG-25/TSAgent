# agent/services/tool_service.py
"""Tool Service — 工具访问与智能过滤。

Tool Affordance: 根据 task goal 和 facts 过滤可用工具，
避免 LLM 面对几十个工具时做出错误选择。
"""
from agent.registry.tool_registry import registry


class ToolService:
    @staticmethod
    def get_tool(name: str):
        return registry.get(name)

    @staticmethod
    def get_all_tools():
        return registry.get_all()  # 返回 dict

    @staticmethod
    def get_all_tools_list():
        return registry.get_all_tools()  # 返回 list

    @staticmethod
    def get_tools_by_tag(tag: str):
        return registry.get_by_tag(tag)

    @staticmethod
    def register_tool(func, name=None, category="general", tags=None):
        registry.register(func, name, category, tags)

    @staticmethod
    def rank_tools(goal: str, facts: dict) -> list:
        """Fix: Tool Affordance — 根据任务目标和已有 Facts 过滤工具。
        
        Args:
            goal: 任务描述
            facts: 当前已知 Facts
        
        Returns:
            按相关性排序的工具名列表（最相关在前）
        """
        goal_lower = (goal or "").lower()
        all_tools = registry.get_all()
        all_names = list(all_tools.keys())
        
        scored = []
        for name in all_names:
            score = 0
            
            # 读取类任务 → 优先 read_file
            if any(w in goal_lower for w in ["读取", "读", "阅读", "打开", "查看", "read", "open", "view"]):
                if name == "read_file":
                    score += 10
                if name == "list_directory":
                    score += 5
            
            # 写入/输出类任务 → 优先 write_file, run_python
            if any(w in goal_lower for w in ["写入", "写", "创建", "输出", "write", "create", "output"]):
                if name == "write_file":
                    score += 10
                if name == "run_python":
                    score += 8
                if name == "run_python_file":
                    score += 5
            
            # 代码类任务 → 优先 python 工具
            if any(w in goal_lower for w in ["代码", "执行", "运行", "编程", "code", "run", "execute", "python"]):
                if "python" in name or name == "run_python":
                    score += 10
                if name == "shell":
                    score += 3
                if name == "write_file":
                    score += 5
            
            # 搜索类任务
            if any(w in goal_lower for w in ["搜索", "查找", "search", "find"]):
                if name == "web_search":
                    score += 10
                if name == "web_fetch":
                    score += 8
                if name == "list_directory":
                    score += 6
            
            # 如果问题已加载，降低 read_file 优先级（防止重复读）
            if facts.get("question_loaded") and name == "read_file":
                score -= 5
            
            # 如果目录已列出，降低 list_directory 优先级
            if facts.get("directory_listed") and name == "list_directory":
                score -= 5
            
            # 通用工具总是保留
            if name in ("read_file", "write_file", "list_directory", "run_python"):
                score = max(score, 2)
            
            # shell 降低优先级（安全考量）
            if name == "shell":
                score -= 2
            
            scored.append((name, score))
        
        scored.sort(key=lambda x: -x[1])
        return [name for name, score in scored if score > 0 or name in (
            "read_file", "write_file", "list_directory", "run_python"
        )]
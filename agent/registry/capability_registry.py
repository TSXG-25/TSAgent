"""CapabilityRegistry — 独立的能力注册中心。

Capability 是 Agent 的知识能力，不是 Tool 的属性。
一个 Capability 可以映射到多个 Tool。
一个 Tool 可以提供多个 Capability。

与 ToolRegistry 的关系：
- CapabilityRegistry: 管"能做什么"（语义）
- ToolRegistry: 管"怎么调用"（实现）
"""
from typing import Dict, List, Optional, Callable, Any


class CapabilityResolver:
    """能力解析结果。"""
    def __init__(self, capability: str, tool_name: str, priority: int = 0):
        self.capability = capability
        self.tool_name = tool_name
        self.priority = priority


class CapabilityRegistry:
    """能力注册中心。
    
    每种能力（如 "repository_search"）可以映射到多个工具实现。
    工具的选择策略由 CapabilityRegistry 管理，而非 ToolRegistry。
    """

    def __init__(self):
        self._capabilities: Dict[str, List[CapabilityResolver]] = {}
        self._resolver_fns: Dict[str, Callable] = {}

    def register_capability(
        self,
        capability: str,
        tool_name: str,
        priority: int = 0,
        description: str = "",
    ):
        """注册一个能力到工具映射。
        
        Args:
            capability: 能力名称，如 "file_read", "web_search", "code_execution"
            tool_name: 实现该能力的工具名
            priority: 优先级（越高越优先）
            description: 能力描述
        """
        resolver = CapabilityResolver(
            capability=capability,
            tool_name=tool_name,
            priority=priority,
        )
        self._capabilities.setdefault(capability, []).append(resolver)
        # Sort by priority descending
        self._capabilities[capability].sort(key=lambda r: -r.priority)

    def register_resolver(self, capability: str, resolver_fn: Callable[[str], Optional[str]]):
        """注册动态解析器（运行时根据输入决定哪个工具）。
        
        Args:
            capability: 能力名称
            resolver_fn: 接受 user_input/goal，返回工具名或 None
        """
        self._resolver_fns[capability] = resolver_fn

    def resolve(self, capability: str, context: str = "") -> Optional[str]:
        """解析能力到具体的工具名。
        
        Args:
            capability: 需要的能力
            context: 上下文（如 task goal），供动态解析器使用
            
        Returns:
            工具名，如果找不到则返回 None
        """
        # 1. 尝试动态解析器
        if capability in self._resolver_fns:
            result = self._resolver_fns[capability](context)
            if result:
                return result

        # 2. 使用注册的映射（按优先级）
        resolvers = self._capabilities.get(capability, [])
        if resolvers:
            return resolvers[0].tool_name

        return None

    def resolve_all(self, capability: str, context: str = "") -> List[str]:
        """解析能力到所有可用的工具名。
        
        Returns:
            按优先级排序的工具名列表
        """
        # 动态解析器优先
        if capability in self._resolver_fns:
            result = self._resolver_fns[capability](context)
            if result:
                return [result]

        return [r.tool_name for r in self._capabilities.get(capability, [])]

    def get_all_capabilities(self) -> List[str]:
        """获取所有已注册的能力名称。"""
        return list(self._capabilities.keys())

    def get_tools_for_capability(self, capability: str) -> List[str]:
        """获取某个能力对应的所有工具。"""
        return [r.tool_name for r in self._capabilities.get(capability, [])]

    def get_capabilities_for_tool(self, tool_name: str) -> List[str]:
        """获取某个工具具备的所有能力。"""
        caps = []
        for cap, resolvers in self._capabilities.items():
            for r in resolvers:
                if r.tool_name == tool_name:
                    caps.append(cap)
                    break
        return caps


# 单例
registry = CapabilityRegistry()


def register_default_capabilities():
    """注册默认的能力-工具映射。"""
    # 文件操作
    registry.register_capability("file_read", "read_file", priority=10)
    registry.register_capability("file_write", "write_file", priority=10)
    registry.register_capability("file_copy", "copy_file", priority=10)
    registry.register_capability("file_move", "move_file", priority=10)
    registry.register_capability("file_delete", "delete_file", priority=10)
    registry.register_capability("file_list", "list_directory", priority=10)
    registry.register_capability("file_patch", "patch", priority=10)
    
    # 代码执行
    registry.register_capability("code_execution", "run_python", priority=10)
    registry.register_capability("code_execution", "run_python_file", priority=5)
    registry.register_capability("code_execution", "shell", priority=1)
    
    # 网络
    registry.register_capability("web_search", "web_search", priority=10)
    registry.register_capability("web_fetch", "web_fetch", priority=10)
    
    # 办公文档
    registry.register_capability("office_doc", "read_office", priority=10)
    registry.register_capability("office_doc", "write_office", priority=10)
    
    # Shell
    registry.register_capability("shell_execution", "shell", priority=10)
    
    # 记忆
    registry.register_capability("memory_read", "read_memory", priority=10)
    registry.register_capability("memory_write", "write_memory", priority=10)
    
    # 文件搜索
    registry.register_capability("file_search", "find_file", priority=10)
    
    # 知识检索
    registry.register_capability("repository_search", "repository_search", priority=10)

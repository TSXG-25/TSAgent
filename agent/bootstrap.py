# agent/bootstrap.py
"""Bootstrap — 项目启动时的工具和技能加载。

启动顺序（frozen）:
1. load_config()
2. init_event_bus()
3. init_workspace(Stage1)      ← fast tree scan
4. load_all_tools()
5. load_all_skills()
6. load_all_workflows()
7. build_knowledge()
8. init_repository(Stage2 async) ← background
9. start_runtime()
"""
import pkgutil
import importlib
import sys
import time
import asyncio
from pathlib import Path


_timings: dict = {}


def _timed(name: str, func):
    """记录某阶段的耗时"""
    start = time.perf_counter()
    result = func()
    elapsed = time.perf_counter() - start
    _timings[name] = round(elapsed, 3)
    return result


async def _timed_async(name: str, coro):
    """记录异步阶段的耗时"""
    start = time.perf_counter()
    result = await coro
    elapsed = time.perf_counter() - start
    _timings[name] = round(elapsed, 3)
    return result


def _ensure_project_root() -> Path:
    project_root = Path(__file__).parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    return project_root


# ── Workspace initialization ──


def init_workspace() -> None:
    """Stage 1: Initialize workspace with fast file tree scan.

    Must be called after event bus is ready, before loading tools.
    """
    from agent.workspace.manager import WorkspaceManager

    project_root = _ensure_project_root()
    manager = WorkspaceManager(project_root)
    ws = manager.get(project_root)
    ws.build_index()  # Stage 1: fast scan
    manager.set_current(ws)
    WorkspaceManager.set_active_manager(manager)
    print(f"  📁 Workspace ready: {project_root.name} ({ws.file_count()} files)")


def build_knowledge() -> None:
    """Build project knowledge from registries."""
    try:
        from agent.knowledge.project_knowledge import ProjectKnowledge
        knowledge = ProjectKnowledge()
        knowledge.build()
        _timings["knowledge_build"] = round(time.perf_counter() - _timings.get("_knowledge_start", time.perf_counter()), 3)
    except ImportError:
        pass  # Knowledge layer not yet implemented


async def init_repository_async() -> None:
    """Stage 2: Initialize repository (background symbol extraction + vector index)."""
    from agent.workspace.manager import WorkspaceManager
    ws = WorkspaceManager.current_workspace()
    if ws:
        await ws.build_symbols_async()

    # Also init the existing RepositoryIndexer if available
    try:
        from agent.repository.indexer import RepositoryIndexer, set_repository_indexer, get_repository_indexer
        from agent.workspace.manager import WorkspaceManager
        ws = WorkspaceManager.current_workspace()
        if ws and not get_repository_indexer():
            indexer = RepositoryIndexer(ws.root)
            indexer.ensure_built()
            set_repository_indexer(indexer)
    except ImportError:
        pass


# ── Tool / Skill / Workflow loading ──


def load_all_tools():
    _ensure_project_root()
    import tools as tools_pkg

    for _, module_name, _ in pkgutil.iter_modules(tools_pkg.__path__):
        importlib.import_module(f"tools.{module_name}")


def load_all_skills():
    _ensure_project_root()
    import skills as skills_pkg

    for _, module_name, _ in pkgutil.iter_modules(skills_pkg.__path__):
        importlib.import_module(f"skills.{module_name}")


def load_all_workflows():
    """Fix: 注册所有 Workflow 到 WorkflowRegistry。"""
    _ensure_project_root()
    import workflows as workflows_pkg

    for _, module_name, _ in pkgutil.iter_modules(workflows_pkg.__path__):
        importlib.import_module(f"workflows.{module_name}")


def load_all():
    """Full boot sequence (synchronous part)."""
    _timed("workspace_init", init_workspace)
    _timed("tools_import", load_all_tools)
    _timed("skills_import", load_all_skills)
    _timed("workflows_import", load_all_workflows)
    _timed("knowledge_build", build_knowledge)


    # 注册默认 Capability
    from agent.registry.capability_registry import register_default_capabilities
    _timed("capabilities_register", register_default_capabilities)


async def load_all_async():
    """Async part of boot sequence: repository + symbol extraction."""
    await _timed_async("repository_init", init_repository_async())


def get_timings() -> dict:
    """获取启动耗时统计。"""
    return dict(_timings)


def print_timings():
    """打印启动耗时统计。"""
    total = sum(_timings.values())
    print("\n" + "=" * 50)
    print("⏱️  启动耗时 Profile")
    print("=" * 50)
    for name, elapsed in sorted(_timings.items(), key=lambda x: -x[1]):
        pct = (elapsed / total * 100) if total > 0 else 0
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        print(f"  {name:20s} {elapsed:>6.2f}s  {bar} {pct:.0f}%")
    print(f"  {'TOTAL':20s} {total:>6.2f}s")
    print("=" * 50 + "\n")

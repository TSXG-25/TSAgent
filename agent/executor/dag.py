"""DAG 调度器 — 任务依赖解析与并行执行支持。

为 Plan-and-Act 架构提供 DAG 拓扑排序。
Executor 用 resolve_dag() 确定可并行执行的任务批次。
"""
from typing import Dict, List, Generator, Set, Any, Optional


def resolve_dag(tasks: List[Dict]) -> Generator[List[Dict], None, None]:
    """按拓扑序分批返回可并行执行的任务。
    
    Args:
        tasks: Task dict 列表，每个 task 必须有 id, dependencies, status 字段
    
    Yields:
        每批可并行执行的任务列表（同一批内无依赖关系）
    
    Example:
        tasks = [
            {"id": "task-1", "dependencies": [], "status": "pending", "goal": "..."},
            {"id": "task-2", "dependencies": ["task-1"], "status": "pending", "goal": "..."},
            {"id": "task-3", "dependencies": ["task-1"], "status": "pending", "goal": "..."},
            {"id": "task-4", "dependencies": ["task-2", "task-3"], "status": "pending", "goal": "..."},
        ]
        for batch in resolve_dag(tasks):
            # batch 1: [task-1]
            # batch 2: [task-2, task-3]
            # batch 3: [task-4]
            ...
    """
    task_map = {t["id"]: t for t in tasks}
    completed: Set[str] = set()
    in_progress: Set[str] = set()

    while True:
        # Find all pending tasks whose dependencies are satisfied
        ready = []
        for t in tasks:
            tid = t["id"]
            if tid in completed or tid in in_progress:
                continue
            status = t.get("status", "pending")
            if status in ("succeeded", "skipped"):
                completed.add(tid)
                continue
            if status == "failed":
                _mark_downstream_failed(task_map, tid, completed, in_progress)
                completed.add(tid)
                continue
            if status != "pending":
                continue

            deps = t.get("dependencies", [])
            if all(d in completed for d in deps):
                ready.append(t)

        if not ready:
            # Check if there are still pending tasks
            remaining = [
                t for t in tasks
                if t["id"] not in completed
                and t["id"] not in in_progress
                and t.get("status", "pending") == "pending"
            ]
            if remaining:
                # Deadlock: ready 为空但仍有 pending 任务。
                # 逐个检查：依赖既未 completed、也不在 remaining 中的任务 → 死锁。
                # 若所有 remaining 任务都在相互等待（纯循环依赖）→ 全部标记失败。
                # 修复：原实现仅在 missing 非空时标记 failed，其余情况 continue 导致死循环。
                remaining_ids = {t["id"] for t in remaining}
                progressed = False
                for t in remaining:
                    deps = t.get("dependencies", [])
                    missing = [
                        d for d in deps
                        if d not in completed and d not in remaining_ids
                    ]
                    if missing:
                        t["status"] = "failed"
                        t["error"] = f"Deadlock: unsatisfied dependencies {missing}"
                        completed.add(t["id"])
                        progressed = True
                if not progressed:
                    # 纯循环依赖：所有 remaining 任务互相等待
                    for t in remaining:
                        t["status"] = "failed"
                        t["error"] = "Deadlock: cyclic dependencies"
                        completed.add(t["id"])
                continue
            break  # All done

        # Mark ready tasks as in_progress and yield
        for t in ready:
            in_progress.add(t["id"])
        yield ready

        # After execution, status will be updated externally
        # Remove from in_progress so next call picks them up
        for t in ready:
            in_progress.discard(t["id"])
            if t.get("status") in ("succeeded", "failed", "skipped"):
                completed.add(t["id"])


def _mark_downstream_failed(
    task_map: Dict[str, Dict],
    failed_id: str,
    completed: Set[str],
    in_progress: Set[str],
) -> None:
    """Mark all tasks that depend on a failed task as failed."""
    for tid, task in task_map.items():
        if tid in completed or tid in in_progress:
            continue
        deps = task.get("dependencies", [])
        if failed_id in deps and task.get("status") == "pending":
            task["status"] = "skipped"
            task["error"] = f"依赖的任务 {failed_id} 失败，本任务被跳过"
            completed.add(tid)


def flatten_tree(tasks: List[Dict]) -> List[Dict]:
    """将层级任务（有 children 的树）展平为拓扑排序后的列表。
    
    Planner 可能输出层级结构（DeepResearch 风格），
    此函数将树展平为 DAG，保留依赖关系。
    """
    flat: List[Dict] = []
    _flatten_recursive(tasks, flat, parent_id=None)
    return flat


def _flatten_recursive(
    tasks: List[Dict],
    flat: List[Dict],
    parent_id: Optional[str],
) -> None:
    for task in tasks:
        tid = task["id"]
        children = task.pop("children", [])
        # Add parent dependency if this is a child
        if parent_id:
            existing_deps = task.get("dependencies", [])
            if parent_id not in existing_deps:
                task["dependencies"] = existing_deps + [parent_id]
        flat.append(task)
        if children:
            _flatten_recursive(children, flat, parent_id=tid)

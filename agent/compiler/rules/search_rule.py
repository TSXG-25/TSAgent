"""SearchRule — verb=SEARCH → repository.search_similar()."""
from agent.task import Task, Verb, ExecutionPlan, ExecutionStep
from agent.compiler.tool_selector import Rule


class SearchRule(Rule):
    """SEARCH "What is Runtime" → repository.semantic_search(query)."""

    @property
    def verb(self) -> Verb:
        return Verb.SEARCH

    def matches(self, task: Task) -> bool:
        return task.verb == Verb.SEARCH

    def build(self, task: Task, **services) -> ExecutionPlan:
        return ExecutionPlan(
            task=task,
            steps=[
                ExecutionStep(
                    tool="repository",
                    args={"query": task.target, "k": 5},
                    outputs=["results"],
                ),
            ],
        )
"""All Compiler lowering rules. Imported to register themselves.

New capability = new rule file + import here.
"""
from agent.compiler.rules.resolve_rule import ResolveRule
from agent.compiler.rules.read_rule import ReadRule
from agent.compiler.rules.write_rule import WriteRule
from agent.compiler.rules.modify_rule import ModifyRule
from agent.compiler.rules.explain_rule import ExplainRule
from agent.compiler.rules.search_rule import SearchRule
from agent.compiler.rules.list_rule import ListRule
from agent.compiler.rules.execute_rule import ExecuteRule

DEFAULT_RULES = [
    ResolveRule(),
    ReadRule(),
    WriteRule(),
    ModifyRule(),
    ExplainRule(),
    SearchRule(),
    ListRule(),
    ExecuteRule(),
]

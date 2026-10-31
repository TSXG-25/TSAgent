"""Code Generation Workflow。"""
from agent.workflow import (
    Workflow, Stage, ExecutionSpec, ExecutorType, ToolPolicy,
    InputArtifact, OutputArtifact, ToolArgument,
)
from agent.registry.workflow_registry import workflow_registry
from agent.validators.file_exists import FileExistsValidator
from agent.validators.python_syntax import PythonSyntaxValidator


code_generation_workflow = Workflow(
    id="code_generation",
    description=(
        "读取用户提供的题目/问题文件（question_path，如 question.docx、题目 md/txt）"
        "并生成解题代码文件。仅当用户明确给出一个待解答的题目文件路径时适用；"
        "“写个计算器/实现一个工具”这类一般编程请求不属于本 Workflow，"
        "由通用 Planner 处理。"
    ),
    version="3.1",
    metadata={
        "capability": {
            "required_bindings": ["question_path", "output_path"],
            "defaults": {"output_path": "output/solution.py"},
            "required_artifacts": [],
            "required_capabilities": [
                "filesystem.read",
                "filesystem.write",
            ],
            "output_types": ["solution_file"],
        },
    },
    stages=[
        Stage(
            id="read_question",
            description="读取题目文件",
            execution=ExecutionSpec(
                executor=ExecutorType.TOOL,
                tool_policy=ToolPolicy(allow=["read_file"], readonly=True),
            ),
            arguments=[
                ToolArgument(param="path", artifact="question_path"),
            ],
            outputs=[OutputArtifact(type="question_text")],
        ),
        Stage(
            id="design_algorithm",
            description="设计算法",
            execution=ExecutionSpec(executor=ExecutorType.LLM),
            inputs=[InputArtifact(type="question_text")],
            outputs=[OutputArtifact(type="algorithm_design")],
        ),
        Stage(
            id="generate_code",
            description="生成 Python 代码",
            execution=ExecutionSpec(executor=ExecutorType.LLM),
            inputs=[
                InputArtifact(type="question_text"),
                InputArtifact(type="algorithm_design"),
            ],
            outputs=[OutputArtifact(type="python_code")],
        ),
        Stage(
            id="write_output",
            description="写入输出文件并静态检查 Python 语法",
            execution=ExecutionSpec(
                executor=ExecutorType.TOOL,
                tool_policy=ToolPolicy(allow=["write_file"]),
            ),
            arguments=[
                ToolArgument(param="path", artifact="output_path"),
                ToolArgument(param="content", artifact="python_code"),
            ],
            outputs=[OutputArtifact(type="solution_file")],
            required_outputs=["python_code"],
            validators=[FileExistsValidator(), PythonSyntaxValidator()],
        ),
    ],
)

workflow_registry.register("code_generation", code_generation_workflow)

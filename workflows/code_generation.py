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
    description="从题目描述生成解题代码",
    version="3.0",
    metadata={
        "capability": {
            "required_bindings": ["question_path", "output_path"],
            "defaults": {"output_path": "output/solution.py"},
            "required_artifacts": [],
            "required_capabilities": [
                "filesystem.read",
                "filesystem.write",
                "run_python",
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
            id="verify_code",
            description="语法检查",
            execution=ExecutionSpec(
                executor=ExecutorType.TOOL,
                tool_policy=ToolPolicy(allow=["run_python"], max_calls=2),
            ),
            arguments=[
                ToolArgument(param="code", artifact="python_code"),
            ],
            outputs=[OutputArtifact(type="verified_code")],
            required_outputs=["python_code"],
        ),
        Stage(
            id="write_output",
            description="写入输出文件",
            execution=ExecutionSpec(
                executor=ExecutorType.TOOL,
                tool_policy=ToolPolicy(allow=["write_file"]),
            ),
            arguments=[
                ToolArgument(param="path", artifact="output_path"),
                ToolArgument(param="content", artifact="verified_code"),
            ],
            outputs=[OutputArtifact(type="solution_file")],
            required_outputs=["verified_code"],
            validators=[FileExistsValidator()],
        ),
    ],
)

workflow_registry.register("code_generation", code_generation_workflow)

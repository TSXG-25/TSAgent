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
                ToolArgument(param="path", constant="output/solution.py"),
                ToolArgument(param="content", artifact="verified_code"),
            ],
            outputs=[OutputArtifact(type="solution_file")],
            required_outputs=["verified_code"],
            validators=[FileExistsValidator()],
        ),
    ],
)

workflow_registry.register("code_generation", code_generation_workflow)

# 注册到 WorkflowRouter（embedding 路由）
from agent.services.workflow_router import router
router.register_workflow(
    workflow_id="code_generation",
    examples=[
        "写一个 Python 程序解题",
        "生成代码实现排序算法",
        "阅读题目并输出解题代码",
        "用 python 实现这个功能",
        "编写一个二分查找函数的代码",
        "做这道算法题写出解法",
        "实现快速排序算法",
        "写 leetcode 第42题的解法",
    ],
    keywords=[
        "写代码", "生成代码", "编程题", "算法题", "解题",
        "solution.py", "leetcode", "algorithm",
        "python 程序", "实现算法",
    ],
    workflow_obj=code_generation_workflow,
)

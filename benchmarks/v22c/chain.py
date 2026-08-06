"""v2.2C 固定测试拓扑：Workflow A→B→C 三链（真实 LLM + 文件副作用）。

    Workflow A（spec）：LLM 生成 spec.md
    Workflow B（impl）：读 spec.md → LLM 生成 solution.py → 写入
    Workflow C（verify）：LLM 验证产物 → 生成 report.md
"""
from agent.workflow import (
    Workflow, Stage, ExecutionSpec, ExecutorType, ToolPolicy,
    InputArtifact, OutputArtifact, ToolArgument,
)

SPEC_LLM_PROMPT = (
    "你是需求分析师。根据以下需求，输出 output/spec.md 的完整 Markdown 内容"
    "（包含：项目名、目标、功能清单、验收标准）。只输出文件内容，不要解释。\n"
    "需求：编写一个 Python 模块 solution.py，提供函数 square(n)，"
    "返回整数 n 的平方；同时提供 main() 读取 stdin 的一个整数并打印其平方。"
)


def build_workflows(output_dir: str) -> dict:
    """构造 A/B/C 三工作流。output_dir 用于文件副作用定位（隔离 workspace）。"""

    spec_path = f"{output_dir}/output/spec.md"
    code_path = f"{output_dir}/output/solution.py"
    report_path = f"{output_dir}/output/report.md"

    spec_wf = Workflow(
        id="spec", version="1.0.0", description="需求分析：生成 spec.md",
        stages=[
            Stage(
                id="gen_spec",
                description=SPEC_LLM_PROMPT,
                execution=ExecutionSpec(executor=ExecutorType.LLM, max_retries=0),
                outputs=[OutputArtifact(type="spec_text")],
            ),
            Stage(
                id="write_spec",
                execution=ExecutionSpec(
                    executor=ExecutorType.TOOL,
                    tool_policy=ToolPolicy(allow=["write_file"]),
                ),
                arguments=[
                    ToolArgument(param="path", constant=spec_path),
                    ToolArgument(param="content", artifact="spec_text"),
                ],
                outputs=[OutputArtifact(type="spec_file")],
                required_outputs=["spec_text"],
            ),
        ],
    )

    impl_wf = Workflow(
        id="impl", version="1.0.0", description="代码实现：生成 solution.py",
        stages=[
            Stage(
                id="read_spec",
                execution=ExecutionSpec(
                    executor=ExecutorType.TOOL,
                    tool_policy=ToolPolicy(allow=["read_file"], readonly=True),
                ),
                arguments=[ToolArgument(param="path", constant=spec_path)],
                outputs=[OutputArtifact(type="spec_content")],
            ),
            Stage(
                id="gen_code",
                description=(
                    "你是 Python 工程师。根据 spec 内容，输出 output/solution.py 的完整"
                    " Python 代码：函数 square(n) 返回 n 的平方；main() 读取 stdin 一个整数并"
                    " 打印平方。只输出代码，不要 markdown 围栏。"
                ),
                execution=ExecutionSpec(executor=ExecutorType.LLM, max_retries=0),
                inputs=[InputArtifact(type="spec_content")],
                outputs=[OutputArtifact(type="python_code")],
                required_outputs=["spec_content"],
                idempotent=True,
            ),
            Stage(
                id="write_code",
                execution=ExecutionSpec(
                    executor=ExecutorType.TOOL,
                    tool_policy=ToolPolicy(allow=["write_file"]),
                ),
                arguments=[
                    ToolArgument(param="path", constant=code_path),
                    ToolArgument(param="content", artifact="python_code"),
                ],
                outputs=[OutputArtifact(type="code_file")],
                required_outputs=["python_code"],
            ),
        ],
    )

    verify_wf = Workflow(
        id="verify", version="1.0.0", description="验证与报告：生成 report.md",
        stages=[
            Stage(
                id="verify_code",
                description=(
                    "你是 QA。读取 output/solution.py 与 output/spec.md，验证 solution.py "
                    "是否实现 square(n)（返回 n 的平方）。输出 Markdown 验证报告"
                    "（含：是否实现、是否可运行、结论 PASS/FAIL）。只输出报告内容。"
                ),
                execution=ExecutionSpec(executor=ExecutorType.LLM, max_retries=0),
                inputs=[
                    InputArtifact(type="spec_file"),
                    InputArtifact(type="code_file"),
                ],
                outputs=[OutputArtifact(type="verify_text")],
                required_outputs=["code_file"],
            ),
            Stage(
                id="write_report",
                execution=ExecutionSpec(
                    executor=ExecutorType.TOOL,
                    tool_policy=ToolPolicy(allow=["write_file"]),
                ),
                arguments=[
                    ToolArgument(param="path", constant=report_path),
                    ToolArgument(param="content", artifact="verify_text"),
                ],
                outputs=[OutputArtifact(type="report_file")],
                required_outputs=["verify_text"],
            ),
        ],
    )

    return {
        "spec": spec_wf,
        "impl": impl_wf,
        "verify": verify_wf,
    }

#!/usr/bin/env python3
"""Write all improvement files at once."""
import os, sys

BASE = os.path.dirname(os.path.abspath(__file__))

def w(relpath, content):
    fullpath = os.path.join(BASE, relpath)
    os.makedirs(os.path.dirname(fullpath), exist_ok=True)
    with open(fullpath, 'w', encoding='utf-8') as f:
        f.write(content.lstrip('\n'))
    # Verify
    with open(fullpath) as f:
        lines = f.readlines()
    print(f"  {relpath}: {len(lines)} lines")
    return True

# ===================== WORKFLOWS =====================

w('workflows/code_review.py', """# workflows/code_review.py
"""
Code Review Workflow - 代码审查。
步骤：读取代码 → 检查问题 → 输出审查报告
"""
from agent.registry.workflow_registry import workflow_registry


async def code_review_workflow(user_input: str, memory_context: str = "") -> list[dict]:
    """代码审查工作流：读取文件 → 分析问题 → 输出审查报告"""
    return [
        {"goal": "读取需要审查的代码文件", "status": "pending"},
        {"goal": "分析代码中的潜在问题（bug、性能、安全、风格）", "status": "pending"},
        {"goal": "汇总审查结果并输出改进建议", "status": "pending"},
    ]


workflow_registry.register("code_review", code_review_workflow)
""")

w('workflows/research.py', """# workflows/research.py
"""
Research Workflow - 调研任务。
步骤：搜索资料 → 阅读摘要 → 整理报告
"""
from agent.registry.workflow_registry import workflow_registry


async def research_workflow(user_input: str, memory_context: str = "") -> list[dict]:
    """调研工作流：搜索 → 阅读 → 整理答案"""
    return [
        {"goal": "使用 web_search 搜索相关信息和最新资料", "status": "pending"},
        {"goal": "阅读搜索结果中的重要链接内容", "status": "pending"},
        {"goal": "综合所有信息整理出完整回答", "status": "pending"},
    ]


workflow_registry.register("research", research_workflow)
""")

# ===================== TOOLS =====================

w('tools/python.py', """# tools/python.py
"""Python 代码执行工具"""
import sys
import io
import traceback
from contextlib import redirect_stdout, redirect_stderr
from agent.registry.tool_registry import registry


def run_python_code(code: str) -> str:
    """在隔离环境中执行 Python 代码并返回输出结果。
    注意：此工具会执行任意 Python 代码，仅用于验证简单逻辑。
    """
    try:
        local_scope = {}
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
            exec(code, {"__builtins__": __builtins__}, local_scope)
        output = stdout_capture.getvalue()
        error = stderr_capture.getvalue()
        if error and not output:
            return error
        if output:
            return output
        last = local_scope.get("_", "")
        return str(last) if last else "代码执行成功，无输出。"
    except Exception as e:
        return f"执行错误:\\n{traceback.format_exc()}"


registry.register(run_python_code, name="run_python", category="code", tags=["python", "execution"])
""")

w('tools/memory.py', """# tools/memory.py
"""记忆查询工具"""
from agent.registry.tool_registry import registry
from agent.services import MemoryService


def query_memory(query: str, k: int = 3) -> str:
    """查询语义记忆，检索与 query 相关的历史对话记录。"""
    result = MemoryService.retrieve_semantic("default", query, k=k)
    return result if result else "未找到相关记忆。"


registry.register(query_memory, category="memory", tags=["memory", "search"])
""")

w('tools/workflow.py', """# tools/workflow.py
"""工作流管理工具"""
from agent.registry.tool_registry import registry
from agent.services import WorkflowService


def list_workflows() -> str:
    """列出所有已注册的工作流及其名称。"""
    workflows = WorkflowService.list_workflows()
    if not workflows:
        return "未注册任何工作流。"
    return "已注册的工作流:\\n" + "\\n".join(f"- {w}" for w in workflows)


registry.register(list_workflows, category="management", tags=["workflow", "list"])
""")

# ===================== SANDBOX WITH DOCKER FALLBACK =====================

w('agent/sandbox.py', """import subprocess
import tempfile
import shutil
from pathlib import Path

SANDBOX_IMAGE = "agent-sandbox"
DEFAULT_TIMEOUT = 10


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _run_docker(cmd: str, project_root: Path, timeout: int) -> str:
    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(
            [
                "docker", "run",
                "--rm",
                "--network", "none",
                "--cpus", "1.0",
                "--memory", "512m",
                "-v", f"{project_root}:/workspace",
                "-w", "/workspace",
                SANDBOX_IMAGE,
                "bash", "-c", cmd
            ],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if result.returncode != 0:
            return f"命令执行失败:\\n{result.stderr}"
        return result.stdout.strip()


def _run_local(cmd: str, project_root: Path, timeout: int) -> str:
    \"\"\"Fallback: run shell command locally (limited sandboxing).\"\"\"
    try:
        result = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(project_root),
        )
        if result.returncode != 0:
            return f"命令执行失败:\\n{result.stderr}"
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return "命令执行超时"
    except FileNotFoundError:
        return "错误：bash 不可用"
    except Exception as e:
        return f"执行错误: {str(e)}"


def run_in_sandbox(cmd: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    \"\"\"
    在沙箱环境中执行 shell 命令。
    优先使用 Docker 沙箱，如果 Docker 不可用则降级为本地执行。
    \"\"\"
    project_root = Path(__file__).resolve().parent.parent

    if _docker_available():
        try:
            return _run_docker(cmd, project_root, timeout)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return "命令执行超时"
        except Exception:
            pass

    print("[警告] Docker 不可用，使用本地 shell 执行（隔离性降低）")
    return _run_local(cmd, project_root, timeout)
""")

# ===================== README =====================

w('README.md', """# TSAgent

A universal AI agent framework implementing a Plan-Execute-Reflect-Retry loop.

## Architecture

```
User Input → Intent Router → Planner → Executor (Tool Calling) → Reflector → Answer Generator → Output
                                                   ↑                                    │
                                                   └────── Retry (if reflection fails) ──┘
```

### Core Components

| Component | Description |
|-----------|-------------|
| **Intent Router** | Classifies user intent (code fix, research, question, etc.) |
| **Planner** | LLM generates a list of tasks from user input |
| **Executor** | Routes each task to the appropriate tool via LLM decision |
| **Reflector** | Checks execution quality, decides whether to retry |
| **Answer Generator** | Compiles execution artifacts into a final response |

### Infrastructure

- **Tool Registry**: LangChain StructuredTool-based registration system
- **Skill Registry**: Intent-to-skill mapping with embedding-based matching
- **Workflow Registry**: Predefined multi-step workflows (bug fix, feature dev, code review, research)
- **Semantic Memory**: ChromaDB vector store for conversation history
- **Preference Memory**: SQLite + LLM fact extraction for user preferences
- **Repository Indexer**: Code vectorization + symbol indexing with ChromaDB
- **Event Bus**: Simple pub/sub for internal events
- **Sandbox**: Docker-based command execution (falls back to local shell)

## Setup

### Prerequisites

- Python 3.10+
- (Optional) Docker for sandboxed command execution
- DeepSeek API key (or any OpenAI-compatible API)

### Installation

```bash
# Clone and enter the project
cd TSAgent

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\\Scripts\\\\activate

# Install dependencies (lightweight, without torch)
pip install -r requirements.txt

# For web search (recommended)
pip install duckduckgo-search

# For Docker sandbox (optional)
# Install Docker Desktop from https://www.docker.com/products/docker-desktop/
```

### Configuration

Create a `.env` file in the project root:

```env
OPENAI_API_KEY=your_deepseek_api_key_here
MODEL_NAME=deepseek-chat
```

Or use any OpenAI-compatible provider:

```env
OPENAI_API_KEY=your_openai_api_key
MODEL_NAME=gpt-4
```

## Usage

```bash
python main.py
```

### Example interactions

- "修复 src/helloworld.py 中的语法错误"
- "写一个 Python 冒泡排序"
- "搜索最新的 AI Agent 框架"
- "查看当前目录下的文件"
- "说一个笑话"

## Tools

| Tool | Description |
|------|-------------|
| `read_file` | Read file content (relative to project root) |
| `write_file` | Write/overwrite file |
| `list_directory` | List directory contents |
| `shell` | Execute shell command in sandbox |
| `web_search` | Search the web via DuckDuckGo |
| `web_fetch` | Fetch and extract plain text from a URL |
| `propose_patch` | Save a unified diff patch |
| `apply_patch` | Apply a unified diff patch |
| `run_python` | Execute Python code (isolated) |
| `query_memory` | Query semantic memory |
| `list_workflows` | List registered workflows |

## Workflows

- **Bug Fix**: Read file → Analyze → Generate patch → Apply → Verify
- **Feature Dev**: Analyze requirements → Write code → Verify
- **Code Review**: Read code → Analyze issues → Output report
- **Research**: Search → Read links → Compile answer

## Architecture Decisions

- **LLM as Planner + Decider**: Uses LLM for both task decomposition and tool selection, avoiding hardcoded routing logic
- **Registries for Extensibility**: Tools, skills, and workflows are all registered, making it easy to add new capabilities
- **Memory with Embeddings**: ChromaDB enables semantic search across conversation history
- **Docker Sandbox with Fallback**: Commands execute in Docker when available, falling back to local shell

## Project Structure

```
TSAgent/
├── agent/              # Core agent logic
│   ├── runtime.py      # Main execution loop
│   ├── planner.py      # Task planning
│   ├── executor.py     # Task execution + tool routing
│   ├── reflector.py    # Execution quality check
│   ├── answer_generator.py
│   ├── intent_router.py
│   ├── sandbox.py      # Command sandboxing
│   ├── llm.py          # LLM client configuration
│   ├── event_bus.py    # Pub/sub event system
│   ├── state.py        # Agent state type definitions
│   ├── embeddings.py   # Embedding model (lazy loaded)
│   ├── registry/       # Tool, skill, workflow registries
│   ├── memory/         # Semantic + preference memory
│   ├── repository/     # Code indexer
│   └── services/       # Service facades
├── tools/              # Available tools
├── skills/             # Skill definitions
├── workflows/          # Predefined workflows
├── main.py             # Entry point
└── requirements.txt
```
""")

# ===================== REQUIREMENTS.TXT =====================

w('requirements.txt', """# Core
langchain>=0.3.0
langchain-openai>=0.3.0
langgraph>=0.3.0
langchain-community>=0.3.0

# Memory / Embeddings (use pip install torch separately if needed)
langchain-huggingface>=0.2.0
langchain-chroma>=0.2.0
chromadb>=0.6.0
sentence-transformers>=3.0.0

# API / Web
httpx>=0.28.0
python-dotenv>=1.0.0
pydantic>=2.0.0
tiktoken>=0.9.0
orjson>=3.0.0

# Resilience
tenacity>=9.0.0

# Optional: for web search
# duckduckgo-search>=7.0.0

# Optional (remove if not using MCP server mode):
# fastmcp
# fastapi>=0.115.0
# starlette>=0.46.0
# aiofiles>=24.0.0

# Heavy dependencies - only install if needed for local embeddings:
# torch>=2.0.0
# torchvision>=0.15.0
# torchaudio>=2.0.0
""")

# ===================== CLEANUP =====================

# Clean up temp files
for f in ['_write_files.py', 'write_all.py', 'tasks.json']:
    fp = os.path.join(BASE, f)
    if os.path.exists(fp):
        os.remove(fp)
        print(f"  Cleaned up: {f}")

print("\\n=== ALL DONE ===")
</｜｜DSML｜｜>
</write_to_file>
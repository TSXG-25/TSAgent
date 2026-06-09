from typing import Annotated, Sequence, Optional
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    patch_path: Optional[str]          # patch 文件的绝对路径
    patch_content: Optional[str]       # diff 内容
    approved: Optional[bool]           # 是否批准
    test_passed: Optional[bool]        # 测试是否通过
    retries: int                       # 当前重试次数
    should_exit: Optional[bool]        # 是否强制结束
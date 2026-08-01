"""
Eval 测试集 - 20 道代码知识图谱评测题 (Data_Analyst 项目)

⚠️ 本测试集基于 Data_Analyst 项目设计（函数名/类名均为该项目特有）。
   如果你要用自己的项目评测，请参考本文件格式创建自定义 JSON 测试集：
   python -m code_guard.eval.runner <你的项目> --test-set <你的测试集.json>

覆盖类型:
- callers (4题): 查询函数被谁调用
- callees (3题): 查询函数调用了谁
- search (3题): 搜索函数
- detail (3题): 函数详情
- impact (3题): 变更影响分析
- structure (2题): 文件结构
- stats (2题): 统计
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


@dataclass
class EvalQuestion:
    id: int
    category: str
    question: str
    expected_type: str  # callers/callees/search/detail/impact/structure/stats
    expected_target: str  # 期望查询的目标
    min_count: Optional[int] = None  # 最少结果数
    keywords: List[str] = field(default_factory=list)  # 答案中应包含的关键词
    must_not: List[str] = field(default_factory=list)  # 答案中不应包含的
    description: str = ""


EVAL_QUESTIONS = [
    # ── callers (4题) ──
    EvalQuestion(
        id=1, category="callers",
        question="谁调用了 get_llm_client 函数？",
        expected_type="callers", expected_target="get_llm_client",
        min_count=5,
        keywords=["AutonomousAgentLoop", "HybridAgentLoop", "ToolDrivenAgentLoop",
                   "AgentLoop", "InspectorAgent"],
        description="查询 get_llm_client 的所有调用者"
    ),
    EvalQuestion(
        id=2, category="callers",
        question="tool_read_dataset 这个函数被谁调用了？",
        expected_type="callers", expected_target="tool_read_dataset",
        min_count=5,
        keywords=["_handle_tool_call", "_explore_data"],
        description="查询 tool_read_dataset 的调用者"
    ),
    EvalQuestion(
        id=3, category="callers",
        question="哪些地方调用了 run_code 函数？",
        expected_type="callers", expected_target="run_code",
        min_count=5,
        keywords=["_handle_tool_call", "_execute_run_code"],
        description="查询 run_code 的所有调用者"
    ),
    EvalQuestion(
        id=4, category="callers",
        question="InspectorAgent 被哪些类使用了？",
        expected_type="callers", expected_target="InspectorAgent",
        min_count=1,
        keywords=["ToolDrivenAgentLoop"],
        description="查询 InspectorAgent 被哪些类初始化"
    ),

    # ── callees (3题) ──
    EvalQuestion(
        id=5, category="callees",
        question="__init__ 方法调用了哪些函数？",
        expected_type="callees", expected_target="__init__",
        min_count=5,
        keywords=["AgentState", "get_llm_client"],
        description="查看 __init__ 中的调用"
    ),
    EvalQuestion(
        id=6, category="callees",
        question="run 方法里面调用了什么？",
        expected_type="callees", expected_target="ToolDrivenAgentLoop.run",
        min_count=3,
        description="查看 run 方法内部的函数调用"
    ),
    EvalQuestion(
        id=7, category="callees",
        question="tool_read_dataset 函数内部调用了哪些库函数？",
        expected_type="callees", expected_target="tool_read_dataset",
        min_count=3,
        keywords=["pd.read", "Path", "df.head"],
        description="查看 tool_read_dataset 内部的库调用"
    ),

    # ── search (3题) ──
    EvalQuestion(
        id=8, category="search",
        question="帮我搜索跟 memory 相关的函数",
        expected_type="search", expected_target="memory",
        min_count=3,
        keywords=["memory"],
        description="搜索名字中带 memory 的函数"
    ),
    EvalQuestion(
        id=9, category="search",
        question="查找代码中所有的工具函数（名称带 tool 的）",
        expected_type="search", expected_target="tool",
        min_count=5,
        keywords=["tool"],
        description="搜索名字中带 tool 的函数"
    ),
    EvalQuestion(
        id=10, category="search",
        question="有没有跟 weather 相关的函数？",
        expected_type="search", expected_target="weather",
        min_count=2,
        keywords=["weather"],
        description="搜索 weather 相关函数"
    ),

    # ── detail (3题) ──
    EvalQuestion(
        id=11, category="detail",
        question="get_llm_client 这个函数是干什么的？能详细介绍一下吗？",
        expected_type="detail", expected_target="get_llm_client",
        keywords=["LLM", "客户端"],
        description="查询 get_llm_client 的详情和文档"
    ),
    EvalQuestion(
        id=12, category="detail",
        question="tool_run_code 方法在哪个文件里？详细说说",
        expected_type="detail", expected_target="tool_run_code",
        keywords=["run_code"],
        description="查看 tool_run_code 的详细信息"
    ),
    EvalQuestion(
        id=13, category="detail",
        question="帮我看看 AgentState 类里的 get_task 和 get_current_task 方法的详细信息",
        expected_type="detail", expected_target="get_task",
        keywords=["state.py", "get_task", "get_current_task"],
        description="查看 AgentState 相关方法详情"
    ),

    # ── impact (3题) ──
    EvalQuestion(
        id=14, category="impact",
        question="如果我要修改 get_llm_client，会影响哪些地方？",
        expected_type="impact", expected_target="get_llm_client",
        min_count=5,
        keywords=["__init__"],
        description="变更影响分析：get_llm_client"
    ),
    EvalQuestion(
        id=15, category="impact",
        question="改动 tool_read_dataset 会影响哪些模块？",
        expected_type="impact", expected_target="tool_read_dataset",
        min_count=5,
        description="变更影响分析：tool_read_dataset"
    ),
    EvalQuestion(
        id=16, category="impact",
        question="如果重构 MemoryManager，需要同步修改哪些调用方？",
        expected_type="impact", expected_target="MemoryManager",
        min_count=3,
        keywords=["__init__"],
        description="变更影响分析：MemoryManager"
    ),

    # ── structure (2题) ──
    EvalQuestion(
        id=17, category="structure",
        question="state.py 文件里有什么类和函数？",
        expected_type="structure", expected_target="state.py",
        min_count=3,
        keywords=["TaskStatus", "AgentPhase", "AgentState"],
        description="查看 state.py 的文件结构"
    ),
    EvalQuestion(
        id=18, category="structure",
        question="inspector_agent.py 里有哪些类？",
        expected_type="structure", expected_target="inspector_agent.py",
        min_count=1,
        keywords=["InspectorAgent"],
        description="查看 inspector_agent.py 的文件结构"
    ),

    # ── stats (2题) ──
    EvalQuestion(
        id=19, category="stats",
        question="这个项目总共有多少函数、类和文件？",
        expected_type="stats", expected_target="",
        keywords=["文件", "函数", "类"],
        description="查询项目统计信息"
    ),
    EvalQuestion(
        id=20, category="stats",
        question="代码知识图谱里的调用关系总共有多少条？",
        expected_type="stats", expected_target="",
        keywords=["调用"],
        description="查询调用边数量"
    ),
]

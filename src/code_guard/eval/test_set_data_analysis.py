"""
Eval 测试集 - Data_Analysis 项目泛化测试 (12题)

⚠️ 本测试集基于 Data_Analysis 项目设计（函数名/类名均为该项目特有）。
   用于验证 CodeGuard 在结构完全不同的项目上的泛化能力。
   用户可用 --test-set 参数加载自定义测试集。

覆盖类型:
- callers (3题): 查询函数被谁调用
- callees (2题): 查询函数调用了谁
- search (2题): 搜索函数
- detail (2题): 函数详情
- impact (1题): 变更影响分析
- structure (1题): 文件结构
- stats (1题): 统计
"""
from dataclasses import dataclass, field
from typing import List


@dataclass
class EvalQuestion:
    id: int
    category: str
    question: str
    expected_type: str
    expected_target: str
    min_count: int = None
    keywords: List[str] = field(default_factory=list)
    must_not: List[str] = field(default_factory=list)
    description: str = ""


EVAL_QUESTIONS = [
    # ── callers (3题) ──
    EvalQuestion(
        id=1, category="callers",
        question="谁调用了 get_llm 函数？",
        expected_type="callers", expected_target="get_llm",
        min_count=2,
        keywords=["get_analysis_llm"],
        description="查询 get_llm 的所有调用者"
    ),
    EvalQuestion(
        id=2, category="callers",
        question="DataAnalysisAgent.run 被谁调用了？",
        expected_type="callers", expected_target="DataAnalysisAgent.run",
        min_count=1,
        keywords=["interactive_mode"],
        description="查询 DataAnalysisAgent.run 的调用者"
    ),
    EvalQuestion(
        id=3, category="callers",
        question="MemoryManager.save_interaction 被谁调用了？",
        expected_type="callers", expected_target="MemoryManager.save_interaction",
        min_count=1,
        keywords=["DataAnalysisAgent"],
        description="查询 save_interaction 的调用者"
    ),

    # ── callees (2题) ──
    EvalQuestion(
        id=4, category="callees",
        question="DataAnalysisAgent.run 方法里面调用了什么？",
        expected_type="callees", expected_target="DataAnalysisAgent.run",
        min_count=3,
        keywords=["_plan", "_execute_step", "_reflect"],
        description="查看 DataAnalysisAgent.run 内部的调用"
    ),
    EvalQuestion(
        id=5, category="callees",
        question="FileParser.parse 调用了哪些内部方法？",
        expected_type="callees", expected_target="FileParser.parse",
        min_count=3,
        keywords=["_parse_csv", "_parse_excel", "_parse_pdf"],
        description="查看 FileParser.parse 的分发逻辑"
    ),

    # ── search (2题) ──
    EvalQuestion(
        id=6, category="search",
        question="搜索跟 memory 管理相关的函数",
        expected_type="search", expected_target="memory",
        min_count=3,
        keywords=["memory"],
        description="搜索名字中带 memory 的函数"
    ),
    EvalQuestion(
        id=7, category="search",
        question="有没有跟 vector 相关的类或函数？",
        expected_type="search", expected_target="vector",
        min_count=2,
        keywords=["VectorStore", "vector"],
        description="搜索 vector 相关函数和类"
    ),

    # ── detail (2题) ──
    EvalQuestion(
        id=8, category="detail",
        question="get_agent 这个函数是干什么的？能详细介绍一下吗？",
        expected_type="detail", expected_target="get_agent",
        keywords=["DataAnalysisAgent", "session_id"],
        description="查询 get_agent 的详情"
    ),
    EvalQuestion(
        id=9, category="detail",
        question="VectorStore.search 方法的详细信息和参数",
        expected_type="detail", expected_target="VectorStore.search",
        keywords=["embedding", "top_k"],
        description="查看 VectorStore.search 的详细信息"
    ),

    # ── impact (1题) ──
    EvalQuestion(
        id=10, category="impact",
        question="如果修改 DataAnalysisAgent._execute_step，会影响哪些地方？",
        expected_type="impact", expected_target="DataAnalysisAgent._execute_step",
        min_count=1,
        keywords=["_plan", "_reflect"],
        description="变更影响分析：_execute_step"
    ),

    # ── structure (1题) ──
    EvalQuestion(
        id=11, category="structure",
        question="agent.py 文件里有什么类和函数？",
        expected_type="structure", expected_target="agent.py",
        min_count=1,
        keywords=["DataAnalysisAgent"],
        description="查看 agent.py 的文件结构"
    ),

    # ── stats (1题) ──
    EvalQuestion(
        id=12, category="stats",
        question="这个项目总共有多少个类？",
        expected_type="stats", expected_target="",
        min_count=1,
        keywords=["类"],
        description="查询项目中的类数量"
    ),
]

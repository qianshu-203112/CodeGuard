"""
Eval 测试集 - CodeGuard 自身元测试 (12题)

⚠️ 本测试集基于 CodeGuard 自身项目设计，用于元测试验证。
   用户可用 --test-set 参数加载自定义测试集。

元测试：CodeGuard 分析自己的代码，验证对自身结构的理解能力。

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
        question="谁调用了 get_callers 函数？",
        expected_type="callers", expected_target="get_callers",
        min_count=3,
        keywords=["_query_graph"],
        description="查询 get_callers 的所有调用者"
    ),
    EvalQuestion(
        id=2, category="callers",
        question="parse_file 被哪些函数调用了？",
        expected_type="callers", expected_target="parse_file",
        min_count=2,
        keywords=["parse_project", "cmd_parse"],
        description="查询 parse_file 的调用者"
    ),
    EvalQuestion(
        id=3, category="callers",
        question="extract_function_name 被谁调用了？",
        expected_type="callers", expected_target="extract_function_name",
        min_count=1,
        keywords=["_query_graph"],
        description="查询 extract_function_name 的调用者"
    ),

    # ── callees (2题) ──
    EvalQuestion(
        id=4, category="callees",
        question="QualityGate.answer 方法调用了什么？",
        expected_type="callees", expected_target="QualityGate.answer",
        min_count=2,
        keywords=["_query_graph", "_llm_answer", "_verify"],
        description="查看 QualityGate.answer 的调用链"
    ),
    EvalQuestion(
        id=5, category="callees",
        question="cmd_query 这个 CLI 命令调用了 CodeGraph 的哪些方法？",
        expected_type="callees", expected_target="cmd_query",
        min_count=2,
        keywords=["get_callers", "get_callees", "get_function_detail"],
        description="查看 cmd_query 中调用的图查询方法"
    ),

    # ── search (2题) ──
    EvalQuestion(
        id=6, category="search",
        question="搜索跟 parse 相关的函数",
        expected_type="search", expected_target="parse",
        min_count=3,
        keywords=["parse"],
        description="搜索名字中带 parse 的函数"
    ),
    EvalQuestion(
        id=7, category="search",
        question="有没有跟 impact 相关的函数？",
        expected_type="search", expected_target="impact",
        min_count=1,
        keywords=["impact"],
        description="搜索 impact 相关函数"
    ),

    # ── detail (2题) ──
    EvalQuestion(
        id=8, category="detail",
        question="analyze_change_impact 这个方法是干什么的？详细介绍一下",
        expected_type="detail", expected_target="analyze_change_impact",
        keywords=["变更影响", "调用者", "affected"],
        description="查询 analyze_change_impact 的详情"
    ),
    EvalQuestion(
        id=9, category="detail",
        question="parse_project 函数的详细信息",
        expected_type="detail", expected_target="parse_project",
        keywords=["递归", "解析", "Python"],
        description="查看 parse_project 的详细信息"
    ),

    # ── impact (1题) ──
    EvalQuestion(
        id=10, category="impact",
        question="如果修改 _query_graph 方法，会影响哪些调用方？",
        expected_type="impact", expected_target="_query_graph",
        min_count=1,
        keywords=["QualityGate"],
        description="变更影响分析：_query_graph"
    ),

    # ── structure (1题) ──
    EvalQuestion(
        id=11, category="structure",
        question="ast_parser.py 文件里有什么类和函数？",
        expected_type="structure", expected_target="ast_parser.py",
        min_count=5,
        keywords=["FunctionInfo", "ClassInfo", "parse_file"],
        description="查看 ast_parser.py 的文件结构"
    ),

    # ── stats (1题) ──
    EvalQuestion(
        id=12, category="stats",
        question="CodeGuard 项目总共有多少个类和函数？",
        expected_type="stats", expected_target="",
        min_count=1,
        keywords=["类", "函数"],
        description="查询项目统计"
    ),
]

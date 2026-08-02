"""Eval评测运行器

用法:
  # 使用默认测试集（Data_Analyst 20题）
  python -m code_guard.eval.runner <项目路径>

  # 使用指定 JSON 测试集
  python -m code_guard.eval.runner <项目路径> --test-set tests/data_analysis.json

  # 使用自定义 JSON 测试集（外部文件）
  python -m code_guard.eval.runner <项目路径> --test-set /path/to/my_tests.json
"""
import sys, os, time, json, argparse
from dataclasses import dataclass, field, asdict
from typing import List, Optional

# src/code_guard/eval/runner.py → 上溯三级即 src/（旧实现再拼了个 'src' 变成 src/src，导致直接跑脚本时 import 失败）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from code_guard.parser.ast_parser import parse_project_multilang as parse_project
from code_guard.graph.code_graph import CodeGraph
from code_guard.quality_gate import QualityGate, detect_intent


# ── 测试题目数据类（与 JSON 格式兼容） ──

@dataclass
class EvalQuestion:
    id: int
    category: str
    question: str
    expected_type: str
    expected_target: str
    min_count: Optional[int] = None
    keywords: List[str] = field(default_factory=list)
    description: str = ""


@dataclass
class ScoreResult:
    question_id: int
    category: str
    question: str
    intent_correct: bool = False
    expected_type: str = ""
    detected_type: str = ""
    has_keywords: bool = False
    keywords_found: list = None
    keywords_missing: list = None
    count_check: bool = True
    result_count: int = 0
    min_count: int = 0
    confidence: float = 0
    passed_gate: bool = False
    has_speculative: bool = False
    raw_answer: str = ""
    score: float = 0

    def __post_init__(self):
        if self.keywords_found is None: self.keywords_found = []
        if self.keywords_missing is None: self.keywords_missing = []


# ── 语义关键词命中（--semantic 可选） ──

_SEMANTIC_THRESHOLD = 0.55   # 余弦相似度阈值：子串未命中时，高于此算"语义命中"
_SEMANTIC_EMBED_CACHE = {}


def _semantic_keyword_hit(keyword: str, answer_text: str) -> bool:
    """关键词在回答里没按子串出现时，用 embedding 余弦相似度兜底判断。

    解决"LLM 用词和测试集期望词不一致但语义对"的扣分问题（README 标注的
    评测局限）。嵌入结果运行内缓存（答案每问一次、关键词按唯一词一次）。
    失败时回退 False，绝不因嵌入报错影响评测。
    """
    try:
        import numpy as np
        from code_guard.vector.embedder import CodeEmbedder

        def _embed(t):
            if t not in _SEMANTIC_EMBED_CACHE:
                _SEMANTIC_EMBED_CACHE[t] = CodeEmbedder().embed(t)
            return _SEMANTIC_EMBED_CACHE[t]

        a = np.array(_embed(keyword), dtype=float)
        b = np.array(_embed(answer_text), dtype=float)
        sim = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))
        return sim >= _SEMANTIC_THRESHOLD
    except Exception as e:
        print(f"  [semantic] 嵌入失败: {e}")
        return False


# ── 测试集加载 ──

_EVAL_DIR = os.path.dirname(os.path.abspath(__file__))


def load_test_set(test_set_path: str) -> List[EvalQuestion]:
    """
    从 JSON 文件加载测试集。

    Args:
        test_set_path: JSON 文件路径（相对 eval/ 目录，或绝对路径）

    Returns:
        EvalQuestion 列表
    """
    # 解析路径：相对路径基于 eval/ 目录，也支持绝对路径
    if not os.path.isabs(test_set_path):
        full_path = os.path.join(_EVAL_DIR, test_set_path)
    else:
        full_path = test_set_path

    if not os.path.exists(full_path):
        # 尝试在 tests/ 子目录下找
        alt_path = os.path.join(_EVAL_DIR, "tests", test_set_path)
        if os.path.exists(alt_path):
            full_path = alt_path
        else:
            raise FileNotFoundError(
                f"测试集文件不存在: {test_set_path}\n"
                f"  尝试过: {full_path}\n"
                f"  尝试过: {alt_path}\n"
                f"请指定正确的 JSON 测试集路径"
            )

    with open(full_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    questions = []
    for q in data.get("questions", []):
        questions.append(EvalQuestion(
            id=q["id"],
            category=q.get("category", ""),
            question=q["question"],
            expected_type=q.get("expected_type", ""),
            expected_target=q.get("expected_target", ""),
            min_count=q.get("min_count"),
            keywords=q.get("keywords", []),
            description=q.get("description", ""),
        ))
    return questions


# ── 评测逻辑 ──

def run_evals(target_project: str, test_set: List[EvalQuestion] = None,
              test_set_name: str = "自定义", report_path: Optional[str] = None,
              semantic: bool = False):
    if test_set is None:
        from code_guard.eval.test_set import EVAL_QUESTIONS
        test_set = EVAL_QUESTIONS
        test_set_name = "默认"

    t0 = time.time()
    print(f"🔍 正在解析项目: {target_project}")
    results = parse_project(target_project)
    graph = CodeGraph()
    graph.load_project(results)
    stats = graph.get_stats()
    gate = QualityGate(graph)

    print(f"📊 项目统计: {stats['files']} 文件, {stats['functions']} 函数, "
          f"{stats['classes']} 类, {stats['calls']} 调用边")
    print(f"📋 测试集: {test_set_name} ({len(test_set)} 题)")
    print()

    scores = []
    for i, eq in enumerate(test_set):
        print(f"  [{i+1}/{len(test_set)}] Q{eq.id} {eq.category:9s} — {eq.question[:50]}...", end=" ")
        sys.stdout.flush()
        s = _eval_one(gate, eq, semantic=semantic)
        scores.append(s)
        status = "✅" if s.score >= 0.7 else "❌"
        print(f"{status} score={s.score:.2f}")

    report = _build_report(scores, stats, time.time() - t0, test_set_name,
                           report_path=report_path)
    graph.close()
    return report


def _eval_one(gate, eq, semantic: bool = False):
    s = ScoreResult(question_id=eq.id, category=eq.category, question=eq.question,
                    expected_type=eq.expected_type)
    detected = detect_intent(eq.question)
    s.detected_type = detected
    s.intent_correct = detected == eq.expected_type
    result = gate.answer(eq.question)
    s.raw_answer = result.raw_answer[:200]
    s.confidence = result.confidence
    s.passed_gate = result.passed
    s.has_speculative = len(result.speculative_claims) > 0
    r = result.graph_data.get("result", result.graph_data.get("raw", []))
    r2 = result.graph_data.get("detail")
    if isinstance(r, dict):
        s.result_count = len(r.get("all_affected", [])) if "all_affected" in r else len(r)
    elif isinstance(r, list):
        s.result_count = len(r)
    elif r2:
        s.result_count = 1
    else:
        s.result_count = 1 if r else 0
    s.min_count = eq.min_count or 0
    s.count_check = s.result_count >= (eq.min_count or 0) if eq.min_count else True
    if eq.keywords:
        text = result.raw_answer
        low = text.lower()
        s.keywords_found = [kw for kw in eq.keywords if kw.lower() in low]
        missing = [kw for kw in eq.keywords if kw.lower() not in low]
        # --semantic：子串未命中的关键词用 embedding 相似度兜底
        if semantic and missing:
            for kw in missing:
                if _semantic_keyword_hit(kw, text):
                    s.keywords_found.append(kw)
            missing = [kw for kw in eq.keywords if kw not in s.keywords_found]
        s.keywords_missing = missing
        s.has_keywords = len(s.keywords_missing) == 0
    else:
        s.has_keywords = True
    subs = [float(s.intent_correct), float(s.has_keywords), float(s.count_check),
            s.confidence, 1.0 if not s.has_speculative else 0.5]
    s.score = sum(subs) / len(subs)
    return s


def _build_report(scores, stats, total_time, test_set_name="默认",
                  report_path: Optional[str] = None):
    total = len(scores)
    passed = sum(1 for s in scores if s.score >= 0.7)
    avg = sum(s.score for s in scores) / total
    cats = {}
    for s in scores:
        cats.setdefault(s.category, []).append(s.score)
    cat_scores = {c: f"{sum(v)/len(v):.0%}" for c, v in cats.items()}

    # 默认写到当前工作目录，而不是包目录——包被安装/只读时也能跑
    if report_path is None:
        report_path = os.path.abspath("eval_report.json")
    report = {
        "summary": {
            "total": total, "passed": passed,
            "avg_score": f"{avg:.0%}", "time": f"{total_time:.1f}s",
            "test_set": test_set_name,
        },
        "category_scores": cat_scores,
        "project_stats": stats,
        "details": [asdict(s) for s in scores],
    }
    report_dir = os.path.dirname(report_path)
    if report_dir:
        os.makedirs(report_dir, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 打印摘要
    print(f"\n{'='*50}")
    print(f"📊 {test_set_name}")
    print(f"{'='*50}")
    print(f"总分: {avg:.0%}  通过: {passed}/{total}  耗时: {total_time:.1f}s")
    print(f"分类得分:")
    for k, v in cat_scores.items():
        print(f"  {k:12s}: {v}")
    print()

    for s in scores:
        flag = "❌" if s.score < 0.7 else "✅"
        issues = []
        if not s.intent_correct:
            issues.append(f"🎯意图({s.detected_type}→{s.expected_type})")
        if not s.has_keywords:
            issues.append(f"🔤缺:{s.keywords_missing}")
        if not s.count_check:
            issues.append(f"📊计数({s.result_count}<{s.min_count})")
        if s.has_speculative:
            issues.append("⚠️推测")
        iss_str = f" [{', '.join(issues)}]" if issues else ""
        print(f"  {flag} Q{s.question_id:2d} ({s.category:9s}) score={s.score:.2f}{iss_str} {s.question[:50]}")

    print(f"\n📄 详细报告已保存: {report_path}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CodeGuard 评测运行器")
    parser.add_argument("project", help="目标项目路径")
    parser.add_argument("--test-set", "-t", default="",
                        help="测试集 JSON 文件路径（相对 eval/ 目录或绝对路径）")
    parser.add_argument("--report", default=None,
                        help="报告输出路径（默认当前目录 eval_report.json）")
    parser.add_argument("--semantic", action="store_true",
                        help="关键词未命中时用 embedding 语义相似度兜底（更宽松，默认关，保 CI 确定性）")
    args = parser.parse_args()

    if args.test_set:
        questions = load_test_set(args.test_set)
        test_set_name = os.path.splitext(os.path.basename(args.test_set))[0]
    else:
        from code_guard.eval.test_set import EVAL_QUESTIONS
        questions = EVAL_QUESTIONS
        test_set_name = "默认 (Data_Analyst)"

    run_evals(args.project, questions, test_set_name, report_path=args.report,
              semantic=args.semantic)

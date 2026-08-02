"""LLM 纯问答基线评测 — 无图检索、直接读源码，用于对比验证"解析建图+检索"的贡献

与 runner.py 共用测试集与评分维度，唯一区别：
- 上下文来源：原始源代码（naive 文件检索），而非代码知识图谱数据
- 其余完全一致：同问题、同 detect_intent（规则式）、同模型、同 temperature

用法（与 runner.py 同款参数）:
  python -m code_guard.eval.eval_baseline <项目路径> --test-set tests/snake.json --report /tmp/baseline_snake.json
"""
import sys, os, time, json, re, argparse
from pathlib import Path
from typing import Optional

# 与 runner.py 相同的包路径注入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from code_guard.config.settings import settings
from code_guard.quality_gate import detect_intent, extract_raw_keyword, QualityGate
from code_guard.eval.runner import (load_test_set, EvalQuestion, ScoreResult,
                                    _build_report)


SOURCE_EXTS = (".py", ".java", ".js", ".ts", ".go", ".cpp", ".c", ".h", ".hpp", ".cs", ".rb", ".rs")
MAX_FILES = 6
MAX_CHARS = 40000  # 上下文预算（约 40k 字符），防止超 token


def _source_files(project: str) -> list:
    """收集项目内所有源码文件（跳过 venv/.git/__pycache__/node_modules）。"""
    files = []
    for root, dirs, names in os.walk(project):
        # 修剪大型非源码目录
        dirs[:] = [d for d in dirs if d not in
                   ("venv", ".git", "__pycache__", "node_modules", "dist", "build",
                    ".idea", ".claude")]
        for n in names:
            if n.endswith(SOURCE_EXTS):
                files.append(os.path.join(root, n))
    return files


def _naive_select_files(project: str, question: str) -> list:
    """Naive 检索：按问题中的目标标识符/文件名 grep 源码，选出相关文件。

    无任何结构化索引——纯文本匹配，等价于"人知道大概名字后直接读文件"。
    """
    files = _source_files(project)
    target = extract_raw_keyword(question)
    if not target:
        # 无目标标识符（如统计类问题）：按行数升序取样本，小项目全取
        ordered = sorted(files, key=lambda f: os.path.getsize(f))
        return ordered[:MAX_FILES]

    # 1) 文件名匹配优先（问题里带扩展名，如 "ast_parser.py 文件里有什么"）
    base = os.path.basename(target)
    name_hits = [f for f in files if os.path.basename(f) == base
                 or (base and base in os.path.basename(f))]
    if name_hits:
        return name_hits[:MAX_FILES]

    # 2) 内容 grep：目标标识符出现在哪些文件
    target_lower = target.lower()
    try:
        content_hits = [f for f in files
                        if target_lower in Path(f).read_text(encoding="utf-8", errors="ignore").lower()]
    except OSError:
        content_hits = []
    if content_hits:
        # 按命中次数排序（粗略用出现次数当相关度），取前 N
        def _count(f):
            try:
                return Path(f).read_text(encoding="utf-8", errors="ignore").lower().count(target_lower)
            except OSError:
                return 0
        content_hits.sort(key=_count, reverse=True)
        return content_hits[:MAX_FILES]

    # 3) 兜底：小项目全给，大项目取样
    ordered = sorted(files, key=lambda f: os.path.getsize(f))
    return ordered[:MAX_FILES]


def _build_context(files: list) -> str:
    """拼接选中文件的源码，带文件头，控制总预算。"""
    parts = []
    total = 0
    for f in files:
        try:
            text = Path(f).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not text.strip():
            continue
        block = f"===== FILE: {f} =====\n{text}"
        total += len(block)
        if total > MAX_CHARS:
            # 最后一个文件截断到预算
            over = total - MAX_CHARS
            block = block[:len(block) - over]
            parts.append(block)
            break
        parts.append(block)
    return "\n\n".join(parts)


def _parse_answer(text: str):
    """把 LLM 回答解析成 claim/推测，逻辑与 quality_gate._parse_result 对齐。"""
    claims, speculative = [], []
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            continue
        citations = re.findall(r'\[([^\[\]]*[A-Za-z][^\[\]]*)\]', s)
        is_spec = "推测" in s
        if is_spec:
            speculative.append({"statement": s, "citations": citations})
        else:
            claims.append({"statement": s, "citations": citations})
    return claims, speculative


_FUNC_CALL_RE = re.compile(r'\b([a-zA-Z_][a-zA-Z0-9_]{1,})\s*\(')


def _source_function_names(files: list) -> set:
    """从源码中提取"真实存在的函数调用/定义名"，作为计数词汇表。

    代码里凡是 `name(` 形态出现过的标识符都算（含库函数调用如 CreateEvent），
    因为它们在源码里真实出现——基线能引用它们说明回答跟代码有实质关联。
    """
    names = set()
    for f in files:
        try:
            text = Path(f).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        names.update(m.group(1) for m in _FUNC_CALL_RE.finditer(text))
    return names


def _count_mentioned_functions(answer_text: str, vocab: set) -> int:
    """统计回答中提到的、源码里真实存在过的不同函数名（防止乱数）。"""
    toks = {m.group(1) for m in _FUNC_CALL_RE.finditer(answer_text)}
    return len(toks & vocab)


def _baseline_one(project: str, eq: EvalQuestion, client, model: str) -> ScoreResult:
    s = ScoreResult(question_id=eq.id, category=eq.category, question=eq.question,
                    expected_type=eq.expected_type)
    detected = detect_intent(eq.question)
    s.detected_type = detected
    s.intent_correct = detected == eq.expected_type

    selected = _naive_select_files(project, eq.question)
    context = _build_context(selected)
    valid_paths = set(selected)

    prompt = f"""你是代码助手。请只基于下面提供的源代码回答用户问题。
要求：
1. 不要凭自己知识编造，只依据源代码中真实存在的内容
2. 回答要具体，列出相关的函数名、类名、文件名
3. 每条结论尽量标注来源 [文件名:行号]（来自下方源码）
4. 信息不足时如实说明，不要瞎猜

用户问题: {eq.question}

源代码:
{context}"""

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": "你是代码助手，回答必须基于提供的源代码。"},
                      {"role": "user", "content": prompt}],
            temperature=0.1)
        text = resp.choices[0].message.content or ""
    except Exception as e:
        text = f"LLM 调用失败: {e}"

    s.raw_answer = text[:200]
    claims, speculative = _parse_answer(text)

    # 关键词命中
    if eq.keywords:
        low = text.lower()
        s.keywords_found = [kw for kw in eq.keywords if kw.lower() in low]
        s.keywords_missing = [kw for kw in eq.keywords if kw.lower() not in low]
        s.has_keywords = len(s.keywords_missing) == 0
    else:
        s.has_keywords = True

    # 计数：回答里提到的、源码中真实存在的不同函数数量
    vocab = _source_function_names(selected)
    s.result_count = _count_mentioned_functions(text, vocab)
    s.min_count = eq.min_count or 0
    s.count_check = s.result_count >= s.min_count if eq.min_count else True

    # 置信度：引用可校验比例（valid_paths = 喂进去的文件）
    verified = 0
    bad = 0
    for cl in claims:
        if not cl["citations"]:
            continue
        ok = any(QualityGate._citation_valid(c, valid_paths) for c in cl["citations"])
        if ok:
            verified += 1
        else:
            bad += 1
    s.confidence = verified / (verified + bad) if (verified + bad) > 0 else 1.0

    # 推测：标注"推测" + 无引用的声明
    uncited = sum(1 for cl in claims if not cl["citations"])
    s.has_speculative = (len(speculative) + uncited) > 0

    subs = [float(s.intent_correct), float(s.has_keywords), float(s.count_check),
            s.confidence, 1.0 if not s.has_speculative else 0.5]
    s.score = sum(subs) / len(subs)
    return s


def run_baseline(project: str, test_set: list, test_set_name: str = "自定义",
                 report_path: Optional[str] = None):
    from openai import OpenAI
    client = OpenAI(api_key=settings.LLM_API_KEY, base_url=settings.LLM_BASE_URL)
    model = settings.LLM_MODEL

    t0 = time.time()
    files = _source_files(project)
    print(f"🔍 基线(纯问答) 项目: {project}  ({len(files)} 个源码文件)")
    print(f"📋 测试集: {test_set_name} ({len(test_set)} 题)")
    print()

    scores = []
    for i, eq in enumerate(test_set):
        print(f"  [{i+1}/{len(test_set)}] Q{eq.id} {eq.category:9s} — {eq.question[:50]}...", end=" ")
        sys.stdout.flush()
        s = _baseline_one(project, eq, client, model)
        scores.append(s)
        status = "✅" if s.score >= 0.7 else "❌"
        print(f"{status} score={s.score:.2f} 关键词={s.keywords_found} 计数={s.result_count}")

    stats = {"files": len(files), "mode": "baseline-llm-raw"}
    report = _build_report(scores, stats, time.time() - t0, f"{test_set_name} [基线]",
                           report_path=report_path)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CodeGuard LLM 纯问答基线评测")
    parser.add_argument("project", help="目标项目路径")
    parser.add_argument("--test-set", "-t", default="",
                        help="测试集 JSON 文件路径（相对 eval/ 目录或绝对路径）")
    parser.add_argument("--report", default=None, help="报告输出路径")
    args = parser.parse_args()

    questions = load_test_set(args.test_set)
    name = os.path.splitext(os.path.basename(args.test_set))[0]
    run_baseline(args.project, questions, name, report_path=args.report)

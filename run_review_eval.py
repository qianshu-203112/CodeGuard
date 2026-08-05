"""review_diff 评测台 — 已知 bug 注入测试集。

在临时 git 仓库里造两个版本：v1 干净 / v2 注入 4 个真实 bug（除零检查移除、
off-by-one、负折扣、年龄边界），跑 review_diff(v1,v2)，检查：
  ① 结构层：变更函数是否覆盖所有 bug 位置（检出率/召回）
  ② 语义层：AI 摘要是否点出注入的 bug（真实审查能力）

用法: venv/Scripts/python.exe run_review_eval.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _src not in sys.path:
    sys.path.insert(0, _src)
from code_guard.service import CodeAnalysisService  # noqa: E402

V1 = '''"""价格与数值工具函数。"""
from __future__ import annotations


def divide(a: float, b: float) -> float:
    """除法，除数为 0 时抛 ValueError。"""
    if b == 0:
        raise ValueError("除数不能为 0")
    return a / b


def clamp(value: float, lo: float, hi: float) -> float:
    """把 value 夹在 [lo, hi] 区间。"""
    return min(max(value, lo), hi)


def discount_price(price: float, rate: float) -> float:
    """打折价；折扣率应在 [0,1]，超范围修正到边界。"""
    if rate < 0:
        rate = 0
    if rate > 1:
        rate = 1
    return price * (1 - rate)


def is_eligible(age: int) -> bool:
    """满 18 岁有资格。"""
    return age >= 18
'''

V2 = '''"""价格与数值工具函数。"""
from __future__ import annotations


def divide(a: float, b: float) -> float:
    """除法。"""
    return a / b  # BUG: 移除了除零检查，b=0 抛 ZeroDivisionError


def clamp(value: float, lo: float, hi: float) -> float:
    """把 value 夹在 [lo, hi] 区间。"""
    return min(max(value, lo), hi - 1)  # BUG: off-by-one，上限少 1


def discount_price(price: float, rate: float) -> float:
    """打折价。"""
    if rate > 1:
        rate = 1
    return price * (1 - rate)  # BUG: 移除了 rate<0 修正，负折扣反加价


def is_eligible(age: int) -> bool:
    """满 18 岁有资格。"""
    return age > 18  # BUG: 改成 >，满 18 反而不合格
'''

BUG_FUNCS = ["divide", "clamp", "discount_price", "is_eligible"]
BUG_HINTS = {
    "divide": ["除零", "除以 0", "zero", "ZeroDivision", "除数", "0 除"],
    "clamp": ["off-by-one", "边界", "上限", "少 1", "越界", "hi"],
    "discount_price": ["负折扣", "rate", "折扣率", "负数", "加价", "价格"],
    "is_eligible": ["18", "边界", "年龄", "不合格", "大于"],
}


def _git(repo: str, *args: str) -> str:
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout.strip()


def _build_fixture() -> str:
    """建临时 git 仓库：v1 干净 → v2 注入 4 个 bug（分别打 tag）。返回仓库路径。

    用每次唯一的临时目录（避免复用可能被锁的旧目录），旧目录交给系统临时目录清理。
    """
    base = tempfile.mkdtemp(prefix="codeguard_review_eval_")
    os.makedirs(base, exist_ok=True)
    with open(os.path.join(base, "calculator.py"), "w", encoding="utf-8") as f:
        f.write(V1)
    _git(base, "init", "-q")
    _git(base, "config", "user.email", "eval@test")
    _git(base, "config", "user.name", "eval")
    _git(base, "add", ".")
    _git(base, "commit", "-q", "-m", "v1 clean")
    _git(base, "tag", "v1")  # review_diff 按 ref 名解析，必须打 tag
    with open(os.path.join(base, "calculator.py"), "w", encoding="utf-8") as f:
        f.write(V2)
    _git(base, "add", ".")
    _git(base, "commit", "-q", "-m", "v2 inject 4 bugs")
    _git(base, "tag", "v2")
    return base


def main() -> int:
    repo = _build_fixture()
    print("fixture 仓库:", repo)
    svc = CodeAnalysisService()
    load = svc.load_project(repo)
    print("load 结果:", load)
    report = svc.review_diff("v1", "v2", project=repo, with_summary=True)
    if "error" in report:
        print("❌ review_diff 失败:", report["error"])
        return 1

    findings = report.get("findings") or []
    changed = [str(f.get("function", "")) for f in findings]
    print("\n=== 结构层：review_diff 识别出的变更函数 ===")
    print(changed)
    missed = [f for f in BUG_FUNCS if f not in changed]
    recall = (len(BUG_FUNCS) - len(missed)) / len(BUG_FUNCS)
    print(f"检出率(变更函数覆盖 bug 位置): {recall:.0%}  未覆盖: {missed or '无'}")

    summary = report.get("summary") or ""
    print("\n=== 语义层：AI 摘要（前 600 字）===")
    print(summary[:600])
    caught = [fn for fn in BUG_FUNCS if any(h in summary for h in BUG_HINTS[fn])]
    sem_recall = len(caught) / len(BUG_FUNCS)
    print(f"语义检出(摘要点出 bug): {sem_recall:.0%}  点出: {caught or '无'}")

    print("\n=== 评估 ===")
    ok = recall >= 0.75 and sem_recall >= 0.5
    print(("✅ " if ok else "⚠️ ") +
          f"结构检出率 {recall:.0%} / 语义检出率 {sem_recall:.0%}（阈值 75% / 50%）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

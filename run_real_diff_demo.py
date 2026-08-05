"""真实项目 review_diff 演示 — 在任意 git 项目的两个真实 commit 上跑代码级审查。

用法: venv/Scripts/python.exe run_real_diff_demo.py <项目路径> <base_ref> [head_ref]
示例: venv/Scripts/python.exe run_real_diff_demo.py D:/Project/ShoppingAgent/shopping-agent HEAD~1 HEAD
"""
import os
import sys

_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _src not in sys.path:
    sys.path.insert(0, _src)
from code_guard.service import CodeAnalysisService  # noqa: E402


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    project = sys.argv[1]
    base = sys.argv[2]
    head = sys.argv[3] if len(sys.argv) > 3 else "."

    svc = CodeAnalysisService()
    load = svc.load_project(project)
    print("load:", load.get("name"), "-", load.get("files"), "文件 /",
          load.get("functions"), "函数")
    print(f"review_diff({base!r}, {head!r}) ...")
    report = svc.review_diff(base, head, project=project, with_summary=True)
    if "error" in report:
        print("❌ 失败:", report["error"])
        return 1

    s = report["stats"]
    print(f"\n=== 变更统计 ===")
    print(f"文件 +{s['added_files']}/-{s['removed_files']}/~{s['modified_files']} | "
          f"函数 +{s['added_functions']}/-{s['removed_functions']}/~{s['modified_functions']} | "
          f"波及 {report['impact']['count']} 个函数")
    findings = report.get("findings") or []
    print(f"\n=== 变更函数（{len(findings)} 个）===")
    for f in findings[:15]:
        print(f"  [{f['action']}] {f.get('function')} ({f.get('file')})")
    if len(findings) > 15:
        print(f"  ... 还有 {len(findings)-15} 个")

    summary = report.get("summary") or ""
    if summary and not summary.startswith("(未配置"):
        print(f"\n=== AI 代码级审查摘要（前 1200 字）===")
        print(summary[:1200])
    else:
        print("\n（未生成 AI 摘要）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

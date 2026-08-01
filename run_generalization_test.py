"""泛化测试运行器 - 对指定项目使用测试集跑评测（兼容入口）

用法:
  python run_generalization_test.py <项目路径> <测试集模块名>
示例:
  python run_generalization_test.py D:/Project/Data_Analysis/data-analysis-agent test_set_data_analysis

说明:
  评测逻辑统一实现在 code_guard.eval.runner（runner.py 支持 JSON 测试集 + 多语言
  解析），本文件只是兼容旧模块测试集入口的薄壳：把"测试集模块名"里的 EVAL_QUESTIONS
  归一化为 runner.EvalQuestion 列表后调用 runner.run_evals，不再各自维护一份评分逻辑。

  新测试集请优先使用 JSON 格式：
    venv/Scripts/python.exe src/code_guard/eval/runner.py <项目> -t tests/<名称>.json
"""
import sys
import os

_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src')
if _src not in sys.path:
    sys.path.insert(0, _src)

from code_guard.eval.runner import run_evals, EvalQuestion


def _load_module_test_set(module_name: str) -> list:
    """从任意可导入模块加载 EVAL_QUESTIONS（兼容旧的 Python 模块测试集）。

    模块里的题目可能是 runner.EvalQuestion 实例、字段相同的旧 dataclass 实例，
    或 dict 形态，这里统一归一化，避免后续逻辑依赖具体类。
    """
    import importlib
    try:
        mod = importlib.import_module(f"code_guard.eval.{module_name}")
    except ModuleNotFoundError:
        mod = importlib.import_module(module_name)
    questions = getattr(mod, "EVAL_QUESTIONS")
    if questions is None:
        raise ValueError(f"测试集模块 {module_name} 缺少 EVAL_QUESTIONS")

    normalized = []
    for q in questions:
        if isinstance(q, dict):
            normalized.append(EvalQuestion(
                id=q.get("id", 0), category=q.get("category", ""),
                question=q.get("question", ""),
                expected_type=q.get("expected_type", ""),
                expected_target=q.get("expected_target", ""),
                min_count=q.get("min_count"),
                keywords=q.get("keywords", []) or [],
                description=q.get("description", ""),
            ))
        else:
            normalized.append(EvalQuestion(
                id=getattr(q, "id", 0), category=getattr(q, "category", ""),
                question=getattr(q, "question", ""),
                expected_type=getattr(q, "expected_type", ""),
                expected_target=getattr(q, "expected_target", ""),
                min_count=getattr(q, "min_count", None),
                keywords=list(getattr(q, "keywords", []) or []),
                description=getattr(q, "description", ""),
            ))
    return normalized


def main():
    if len(sys.argv) < 3:
        print("用法: python run_generalization_test.py <项目路径> <测试集模块名>")
        print("示例: python run_generalization_test.py D:/Project/Data_Analysis/data-analysis-agent test_set_data_analysis")
        print("提示: 新测试集请用 JSON + runner.py --test-set tests/<名称>.json")
        sys.exit(1)

    target = sys.argv[1]
    test_module = sys.argv[2]
    questions = _load_module_test_set(test_module)
    run_evals(target, questions, test_module)


if __name__ == "__main__":
    main()

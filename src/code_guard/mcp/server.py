"""
CodeGuard MCP Server

让 Claude Code / Cursor 等 AI 编辑器能直接调 CodeGuard 的代码分析能力。

用法:
  1. 安装: pip install -e .
  2. 配置环境变量: CODE_GUARD_PROJECT=<项目路径>
  3. 运行: python -m code_guard.mcp.server

  或在 Claude Code settings 中添加:
  {
    "mcpServers": {
      "code-guard": {
        "command": "python",
        "args": ["-m", "code_guard.mcp.server"],
        "env": { "CODE_GUARD_PROJECT": "D:/Project/xxx" }
      }
    }
  }
"""
import os
import sys
import json
from pathlib import Path
from mcp.server.fastmcp import FastMCP

# 确保 src 在路径中
_src = Path(__file__).parent.parent.parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from code_guard.parser.ast_parser import parse_project_multilang, parse_file
from code_guard.graph.code_graph import CodeGraph
from code_guard.quality_gate import QualityGate
from code_guard.analyzer import ModuleDependencyAnalyzer
from code_guard.viz import generate_html as _generate_viz
from code_guard.config.settings import settings


# ── 全局状态 ──

_graph: CodeGraph = None
_gate: QualityGate = None
_project_path: str = ""
_project_stats: dict = {}


def _get_project_path() -> str:
    """获取项目路径：环境变量 -> .env 配置 -> 命令行参数 -> 当前目录"""
    # 1. 环境变量（最高优先级）
    path = os.environ.get("CODE_GUARD_PROJECT", "")
    if path and Path(path).exists():
        return path
    # 2. .env 文件配置
    path = settings.CODE_GUARD_PROJECT
    if path and Path(path).exists():
        return path
    # 3. 命令行参数
    if len(sys.argv) > 1 and Path(sys.argv[1]).exists():
        return sys.argv[1]
    return str(Path.cwd())


def _init():
    """初始化：解析项目并建图"""
    global _graph, _gate, _project_path, _project_stats

    _project_path = _get_project_path()
    print(f"🔍 CodeGuard MCP: 正在解析 {_project_path} ...", file=sys.stderr)

    if Path(_project_path).is_file():
        results = {_project_path: parse_file(_project_path)}
    else:
        results = parse_project_multilang(_project_path)

    _graph = CodeGraph()
    _graph.load_project(results)
    _project_stats = _graph.get_stats()
    _gate = QualityGate(_graph)

    print(f"✅ CodeGuard MCP 就绪: {_project_stats['files']} 文件, "
          f"{_project_stats['functions']} 函数, {_project_stats['classes']} 类",
          file=sys.stderr)


# ── FastMCP 实例 ──

mcp = FastMCP("CodeGuard", log_level="WARNING")


@mcp.tool()
def query_callers(function_name: str) -> str:
    """查询指定函数被哪些地方调用了。

    Args:
        function_name: 函数名（如 "load_data"、"DataProcessor.run"）
    """
    if not _graph:
        return "❌ CodeGuard 未初始化，请设置 CODE_GUARD_PROJECT 环境变量"
    callers = _graph.get_callers(function_name)
    if not callers:
        return f"`{function_name}` 没有被其他函数调用，或未在图数据中找到。"
    result = [f"`{function_name}` 被以下 {len(callers)} 个函数调用：\n"]
    for c in callers:
        result.append(f"- `{c['caller']}` → `{c['file']}`:行{c['line']}")
    return "\n".join(result)


@mcp.tool()
def query_callees(function_name: str) -> str:
    """查询指定函数内部调用了哪些函数。

    Args:
        function_name: 函数名（如 "run"、"FileParser.parse"）
    """
    if not _graph:
        return "❌ CodeGuard 未初始化"
    callees = _graph.get_callees(function_name)
    if not callees:
        return f"`{function_name}` 内部未发现函数调用，或未在图数据中找到。"
    seen = set()
    lines = [f"`{function_name}` 内部调用了以下函数（{len(callees)} 个）：\n"]
    for c in callees:
        if c["callee"] not in seen:
            seen.add(c["callee"])
            lines.append(f"- `{c['callee']}` @行{c['line']}")
    return "\n".join(lines)


@mcp.tool()
def search_functions(keyword: str) -> str:
    """搜索代码中的函数/方法。

    Args:
        keyword: 搜索关键词（如 "memory"、"tool"、"parse"）
    """
    if not _graph:
        return "❌ CodeGuard 未初始化"
    results = _graph.search_functions(keyword)
    if not results:
        return f"未找到名称中包含「{keyword}」的函数。"
    lines = [f"🔍 搜索「{keyword}」找到 {len(results)} 个函数：\n"]
    for r in results[:30]:
        lines.append(f"- `{r['name']}` ({Path(r['file']).name}:{r['line']})")
    if len(results) > 30:
        lines.append(f"\n... 还有 {len(results)-30} 个")
    return "\n".join(lines)


@mcp.tool()
def analyze_impact(function_name: str) -> str:
    """变更影响分析 - 如果修改指定函数，会影响哪些地方。

    Args:
        function_name: 被修改的函数名
    """
    if not _graph:
        return "❌ CodeGuard 未初始化"
    impact = _graph.analyze_change_impact(function_name)
    lines = [f"🔍 变更影响分析: 修改 `{impact['target']}`\n"]

    if impact["direct_callers"]:
        lines.append(f"📌 直接影响（{len(impact['direct_callers'])} 个）：")
        for c in impact["direct_callers"][:10]:
            lines.append(f"  - `{c['caller']}` ({Path(c['file']).name}:{c['line']})")

    if impact["all_affected"]:
        lines.append(f"\n📌 间接影响（{len(impact['all_affected'])} 个）：")
        for c in impact["all_affected"][:10]:
            lines.append(f"  - `{c['caller']}` ({Path(c['file']).name}:{c['line']}) 深度={c['depth']}")

    if impact["affected_tests"]:
        lines.append(f"\n🧪 受影响测试（{len(impact['affected_tests'])} 个）：")
        for c in impact["affected_tests"]:
            lines.append(f"  - `{c['caller']}` ({Path(c['file']).name}:{c['line']})")

    if impact["affected_files"]:
        lines.append(f"\n📁 受影响文件（{len(impact['affected_files'])} 个）：")
        for f in impact["affected_files"]:
            lines.append(f"  - {Path(f).name}")

    if not impact["direct_callers"] and not impact["all_affected"]:
        lines.append(f"`{function_name}` 未被其他函数调用，修改是安全的。")
    return "\n".join(lines)


@mcp.tool()
def get_file_structure(file_path: str) -> str:
    """查看指定文件中的类和函数结构。

    Args:
        file_path: 文件名或路径（如 "agent.py"、"backend/agent/state.py"）
    """
    if not _graph:
        return "❌ CodeGuard 未初始化"
    # 尝试搜索文件
    files = _graph.search_files(file_path)
    if not files:
        return f"未找到文件「{file_path}」。"
    fp = files[0]["path"]
    functions = _graph.get_functions_in_file(fp)
    classes = _graph.get_classes_in_file(fp)
    lines = [f"📄 {fp}\n"]
    if classes:
        lines.append(f"📦 类（{len(classes)} 个）：")
        for c in classes:
            bases = f"({c['base_classes']})" if c["base_classes"] and c["base_classes"] != "[]" else ""
            lines.append(f"  - `{c['name']}` {bases} [{c['start']}:{c['end']}]")
    if functions:
        lines.append(f"\n🔧 函数/方法（{len(functions)} 个）：")
        for f in functions:
            prefix = f"  [{f['class']}] " if f["class"] else "  "
            lines.append(f"{prefix}`{f['name']}` [{f['start']}:{f['end']}]")
    return "\n".join(lines)


@mcp.tool()
def get_project_stats() -> str:
    """查看当前项目的代码统计信息。"""
    if not _graph:
        return "❌ CodeGuard 未初始化"
    s = _project_stats
    return (
        f"📊 项目: {_project_path}\n"
        f"  - 代码文件: {s['files']}\n"
        f"  - 函数:     {s['functions']}\n"
        f"  - 类:       {s['classes']}\n"
        f"  - 调用关系: {s['calls']}\n"
        f"  - Import:   {s['imports']}"
    )


@mcp.tool()
def ask(question: str) -> str:
    """用自然语言提问代码相关问题，支持复杂查询。

    Args:
        question: 自然语言问题（如 "用户登录流程是怎样的？"、"修改 payment.py 会影响哪些模块？"）
    """
    if not _gate:
        return "❌ CodeGuard 未初始化"
    result = _gate.answer(question)
    return result.raw_answer


# ── 启动 ──

@mcp.tool()
def analyze_modules() -> str:
    """分析项目的模块依赖关系，检测核心模块和循环依赖。"""
    if not _graph:
        return "CodeGuard 未初始化"
    results = parse_project_multilang(_project_path)
    analyzer = ModuleDependencyAnalyzer(_graph, results)
    analysis = analyzer.analyze()
    return analyzer.to_text(analysis)


@mcp.tool()
def visualize(output_path: str = "") -> str:
    """生成代码图谱可视化 HTML 文件（用浏览器打开可交互查看）。

    Args:
        output_path: 输出 HTML 路径（默认: code_graph.html）
    """
    if not _graph:
        return "CodeGuard 未初始化"
    path = output_path or os.path.join(os.path.dirname(_project_path), "code_graph.html")
    result = _generate_viz(_project_path, path)
    return f"可视化已生成: {result}\n用浏览器打开即可查看模块依赖图和调用链图。"


def main():
    _init()
    mcp.run()


if __name__ == "__main__":
    main()

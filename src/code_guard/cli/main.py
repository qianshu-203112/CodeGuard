"""
CodeGuard CLI - 代码知识图谱命令行工具

用法:
  python -m code_guard.cli.main parse <项目路径>       # 解析项目并建图
  python -m code_guard.cli.main query <函数名>          # 查询函数调用者
  python -m code_guard.cli.main impact <函数名>         # 变更影响分析
  python -m code_guard.cli.main search <关键词>         # 搜索函数
  python -m code_guard.cli.main stats <项目路径>        # 显示项目统计
"""
import sys
import os
import argparse
import json

# 确保模块可导入（开发环境）
# src/code_guard/cli/main.py → 上溯三级即 src/（旧实现再拼 'src' 变 src/src，直接跑脚本时 import 失败）
_src = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _src not in sys.path:
    sys.path.insert(0, _src)

from code_guard.parser.ast_parser import parse_project, parse_project_multilang, parse_file
from code_guard.graph.code_graph import CodeGraph
from code_guard.analyzer import ModuleDependencyAnalyzer
from code_guard.viz import generate_html


def cmd_parse(args):
    """解析项目并建图"""
    project_path = args.path

    if not os.path.isdir(project_path):
        # 可能是单个文件
        result = parse_file(project_path)
        results = {project_path: result}
    else:
        print(f"🔍 正在解析 {project_path} ...")
        results = parse_project(project_path)

    print(f"✅ 解析完成: {len(results)} 个文件")

    # 建图
    graph_path = args.output or ":memory:"
    graph = CodeGraph(graph_path)
    graph.load_project(results)

    stats = graph.get_stats()
    print(f"\n📊 图统计:")
    print(f"  文件: {stats['files']}")
    print(f"  函数: {stats['functions']}")
    print(f"  类: {stats['classes']}")
    print(f"  调用边: {stats['calls']}")
    print(f"  Import: {stats['imports']}")

    if graph_path != ":memory:":
        print(f"\n💾 已保存到: {graph_path}")

    graph.close()


def cmd_query(args):
    """查询函数调用者"""
    graph_path = args.db or ":memory:"
    if graph_path == ":memory:":
        print("❌ 请指定 --db 参数（已保存的图数据库文件）")
        return

    graph = CodeGraph(graph_path)
    callers = graph.get_callers(args.function)
    callees = graph.get_callees(args.function)
    detail = graph.get_function_detail(args.function)

    if detail:
        print(f"\n📋 函数: {detail['name']}")
        print(f"  文件: {detail['file']}:{detail['start_line']}-{detail['end_line']}")
        if detail['docstring']:
            print(f"  文档: {detail['docstring'][:200]}")

    print(f"\n⬅️  被谁调用 ({len(callers)}):")
    for c in callers[:10]:
        print(f"  ← {c['caller']} 在 {os.path.basename(c['file'])}:{c['line']}")
    if len(callers) > 10:
        print(f"  ... 还有 {len(callers)-10} 个")

    print(f"\n➡️  调用了谁 ({len(callees)}):")
    for c in callees[:10]:
        print(f"  → {c['callee']} @行{c['line']}")
    if len(callees) > 10:
        print(f"  ... 还有 {len(callees)-10} 个")

    graph.close()


def cmd_impact(args):
    """变更影响分析"""
    graph_path = args.db or ":memory:"
    if graph_path == ":memory:":
        print("❌ 请指定 --db 参数")
        return

    graph = CodeGraph(graph_path)
    impact = graph.analyze_change_impact(args.function, max_depth=args.depth)

    print(f"\n🔍 变更影响分析: 修改「{impact['target']}」")
    print(f"  {'='*45}")
    print(f"\n📌 直接影响 ({len(impact['direct_callers'])}):")
    for c in impact['direct_callers'][:10]:
        print(f"  · {c['caller']} ({os.path.basename(c['file'])}:{c['line']})")

    if impact['all_affected']:
        print(f"\n📌 间接影响 ({len(impact['all_affected'])}):")
        for c in impact['all_affected'][:10]:
            print(f"  · {c['caller']} ({os.path.basename(c['file'])}:{c['line']}) 深度={c['depth']}")
        if len(impact['all_affected']) > 10:
            print(f"  ... 还有 {len(impact['all_affected'])-10} 个")

    if impact['affected_tests']:
        print(f"\n🧪 受影响测试 ({len(impact['affected_tests'])}):")
        for c in impact['affected_tests']:
            print(f"  · {c['caller']} ({os.path.basename(c['file'])}:{c['line']})")

    print(f"\n📁 受影响文件 ({len(impact['affected_files'])}):")
    for f in impact['affected_files']:
        print(f"  · {os.path.basename(f)}")

    graph.close()


def cmd_search(args):
    """搜索函数"""
    graph_path = args.db or ":memory:"
    if graph_path == ":memory:":
        print("❌ 请指定 --db 参数")
        return

    graph = CodeGraph(graph_path)
    results = graph.search_functions(args.keyword)

    print(f"\n🔍 搜索 \"{args.keyword}\" 找到 {len(results)} 个:")
    for r in results[:20]:
        print(f"  · {r['name']} ({os.path.basename(r['file'])}:{r['line']})")

    graph.close()


def cmd_stats(args):
    """显示统计"""
    project_path = args.path

    if not os.path.isdir(project_path):
        result = parse_file(project_path)
        results = {project_path: result}
    else:
        results = parse_project_multilang(project_path)

    graph = CodeGraph()
    graph.load_project(results)
    stats = graph.get_stats()

    print(f"\n📊 项目统计: {project_path}")
    print(f"  {'='*45}")
    print(f"  代码文件: {stats['files']}")
    print(f"  函数:     {stats['functions']}")
    print(f"  类:       {stats['classes']}")
    print(f"  调用关系: {stats['calls']}")
    print(f"  Import:   {stats['imports']}")

    graph.close()


def cmd_modules(args):
    """模块依赖分析"""
    project_path = args.path

    if not os.path.isdir(project_path):
        result = parse_file(project_path)
        results = {project_path: result}
    else:
        print(f"🔍 正在分析模块依赖: {project_path} ...")
        results = parse_project_multilang(project_path)

    graph = CodeGraph()
    graph.load_project(results)
    analyzer = ModuleDependencyAnalyzer(graph, results)
    analysis = analyzer.analyze()

    if args.json:
        print(json.dumps(analysis, ensure_ascii=False, indent=2))
    else:
        print(analyzer.to_text(analysis))

    graph.close()


def main():
    parser = argparse.ArgumentParser(description="CodeGuard - 代码知识图谱 Agent")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # parse
    p = subparsers.add_parser("parse", help="解析项目并建图")
    p.add_argument("path", help="项目路径或文件路径")
    p.add_argument("-o", "--output", help="图数据库保存路径（默认内存）")
    # --db 与 --output 同义（对齐其它子命令的习惯用法，如 parse --db bench.db）
    p.add_argument("--db", dest="output", help="图数据库保存路径（--output 的别名）")

    # query
    p = subparsers.add_parser("query", help="查询函数调用关系")
    p.add_argument("function", help="函数名")
    p.add_argument("--db", help="图数据库路径")

    # impact
    p = subparsers.add_parser("impact", help="变更影响分析")
    p.add_argument("function", help="被修改的函数名")
    p.add_argument("--db", help="图数据库路径")
    p.add_argument("--depth", type=int, default=3, help="追溯深度（默认3）")

    # search
    p = subparsers.add_parser("search", help="搜索函数")
    p.add_argument("keyword", help="关键词")
    p.add_argument("--db", help="图数据库路径")

    # stats
    p = subparsers.add_parser("stats", help="项目统计")
    p.add_argument("path", help="项目路径")

    # modules
    p = subparsers.add_parser("modules", help="模块依赖分析")
    p.add_argument("path", help="项目路径")
    p.add_argument("--json", action="store_true", help="以 JSON 格式输出（供可视化使用）")

    # viz
    p = subparsers.add_parser("viz", help="生成代码图谱可视化 HTML")
    p.add_argument("path", help="项目路径")
    p.add_argument("-o", "--output", default="code_graph.html", help="输出 HTML 路径（默认 code_graph.html）")
    p.add_argument("--ai", action="store_true", help="使用 AI 生成定制的可视化 HTML（需要 API Key）")

    # agent
    p = subparsers.add_parser("agent", help="多 Agent 协作回答代码问题")
    p.add_argument("path", help="项目路径")
    p.add_argument("question", help="你的问题")
    p.add_argument("--plan", action="store_true", help="显示执行计划")

    # index
    p = subparsers.add_parser("index", help="向量索引项目代码（Chroma 语义搜索）")
    p.add_argument("path", help="项目路径")
    p.add_argument("--clear", action="store_true", help="清除已有索引重建")

    # serve
    p = subparsers.add_parser("serve", help="启动 Web 服务")
    p.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    p.add_argument("--port", type=int, default=8979, help="监听端口（默认 8979）")
    p.add_argument("--project", help="要分析的项目路径（可选，可在页面输入）")

    args = parser.parse_args()

    if args.command == "parse":
        cmd_parse(args)
    elif args.command == "query":
        cmd_query(args)
    elif args.command == "impact":
        cmd_impact(args)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "stats":
        cmd_stats(args)
    elif args.command == "modules":
        cmd_modules(args)
    elif args.command == "viz":
        if getattr(args, 'ai', False):
            from code_guard.viz.ai_gen import generate_with_ai
            generate_with_ai(args.path, args.output)
        else:
            generate_html(args.path, args.output)
    elif args.command == "agent":
        from code_guard.parser.ast_parser import parse_project_multilang
        from code_guard.graph.code_graph import CodeGraph
        from code_guard.agent import AgentOrchestrator

        print(f"解析 {args.path} ...")
        results = parse_project_multilang(args.path)
        graph = CodeGraph()
        graph.load_project(results)

        orch = AgentOrchestrator(graph, results, project_path=args.path)
        answer = orch.answer(args.question)
        graph.close()

        print(f"\n回答:\n{answer['answer']}")
        if getattr(args, 'plan', False):
            print(f"\n执行计划:")
            for s in answer.get("steps", []):
                r = s.get("result", "")
                summary = str(r)[:100] if not isinstance(r, str) else r[:100]
                print(f"  步骤{s['step']}: {s['tool']}({s['args']})")
                print(f"    结果: {summary}")
    elif args.command == "index":
        from code_guard.vector.store import VectorStore
        from code_guard.vector.indexer import index_project
        if getattr(args, 'clear', False):
            vs = VectorStore()
            vs.delete_collection()
            print("已清除旧索引")
        count = index_project(args.path)
        print(f"\n总计索引 {count} 个函数")
    elif args.command == "serve":
        from code_guard.server import run_server
        run_server(host=args.host, port=args.port, project_path=args.project)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

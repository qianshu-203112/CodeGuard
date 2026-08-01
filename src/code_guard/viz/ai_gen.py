"""
AI 代码图谱可视化生成器

让 LLM 根据项目数据生成定制的 D3.js 可视化 HTML。
流程:
  1. 解析项目, 提取结构化数据(模块依赖 + 调用链)
  2. 发送项目摘要到 LLM, 请求生成 HTML 模板(含 __MODULE_JSON__ / __CALL_JSON__ 占位符)
  3. 将真实 JSON 数据注入模板
  4. 保存到缓存目录

如果 AI 生成失败, 自动回退到硬编码模板。
"""
import os
import sys
import json
from pathlib import Path

_src = Path(__file__).parent.parent.parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from code_guard.parser.ast_parser import parse_project_multilang
from code_guard.graph.code_graph import CodeGraph
from code_guard.analyzer import ModuleDependencyAnalyzer


def build_ai_prompt(project_name, stats, module_names,
                    core_modules, has_circular):
    """构建发送给 LLM 的提示词"""
    return f"""你是一个 D3.js 数据可视化专家。请为代码项目 "{project_name}" 生成一个完整的 D3.js 可视化 HTML 页面。

项目概览:
- 代码文件: {stats['files']} 个
- 函数: {stats['functions']} 个
- 类: {stats['classes']} 个
- 调用边: {stats['calls']} 条
- 模块数: {stats.get('total_modules', 0)} 个
- 模块依赖: {stats.get('total_dependencies', 0)} 条
{"- 存在循环依赖" if has_circular else "- 无循环依赖"}

模块列表: {', '.join(module_names[:15])}{'...' if len(module_names) > 15 else ''}
核心模块: {', '.join(core_modules[:8]) if core_modules else '无'}

要求:
1. 使用 D3.js v7(从 CDN 加载: https://cdnjs.cloudflare.com/ajax/libs/d3/7.8.5/d3.min.js)
2. 暗色主题(GitHub Dark 风格)
3. 双视图模式: 模块依赖图 + 调用链图(用顶部按钮切换)
4. 模块依赖图: 分层布局(核心模块金色、中间层蓝色、叶子层灰色), 悬停显示 import 信息
5. 调用链图: 搜索模式, 选中函数居中绿色, 调用者左半圆红色, 被调用者右半圆蓝色
6. 支持拖拽、缩放、节点高亮
7. 左下角图例/状态栏
8. 右侧边栏显示模块列表或搜索结果

关键约定:
- 使用 `var MD=__MODULE_JSON__;` 作为模块数据的占位符(我会在后面替换为真实 JSON)
- 使用 `var CD=__CALL_JSON__;` 作为调用链数据的占位符(我会在后面替换为真实 JSON)
- 不要将完整项目数据硬编码到 HTML 中, 只用 __MODULE_JSON__ 和 __CALL_JSON__ 两个占位符
- 生成完整的、可直接在浏览器中运行的 HTML 文件

请直接输出完整的 HTML 代码, 不要加额外解释。"""


def _get_callgraph_data(results):
    """从解析结果中提取调用链数据"""
    graph = CodeGraph()
    graph.load_project(results)

    conn = graph.conn
    funcs = conn.execute("""
        SELECT f.qualified_name, fl.path,
               (SELECT COUNT(*) FROM call_edges ce WHERE ce.caller_func_id = f.id) as out_count,
               (SELECT COUNT(*) FROM call_edges ce2 WHERE ce2.callee_name = f.name
                OR ce2.callee_name LIKE '%' || f.name) as in_count
        FROM functions f
        JOIN files fl ON f.file_id = fl.id
        ORDER BY (out_count + in_count) DESC
    """).fetchall()

    simple_map = {}
    for f in funcs:
        qname = f[0]
        simple = qname.split(".")[-1]
        if simple not in simple_map:
            simple_map[simple] = qname
        simple_map[qname] = qname

    nodes = []
    node_set = set()
    for f in funcs[:200]:
        qname = f[0]
        node_set.add(qname)
        nodes.append({"id": qname, "file": Path(f[1]).name,
                       "calls": f[2], "called_by": f[3]})

    edges = conn.execute("""
        SELECT f1.qualified_name, ce.callee_name
        FROM call_edges ce
        JOIN functions f1 ON ce.caller_func_id = f1.id
        WHERE LENGTH(ce.callee_name) < 60
        LIMIT 500
    """).fetchall()

    links = []
    seen = set()
    for caller_qname, callee_name in edges:
        if caller_qname not in node_set:
            continue
        callee_simple = callee_name.split(".")[-1]
        target = simple_map.get(callee_simple) or simple_map.get(callee_name)
        if target and target in node_set and target != caller_qname:
            key = caller_qname + "->" + target
            if key not in seen:
                seen.add(key)
                links.append({"source": caller_qname, "target": target})

    graph.close()
    return {"nodes": nodes, "links": links}


def _call_llm(prompt, api_key, model):
    """调用 LLM 生成 HTML, 返回原始响应文本"""
    from openai import OpenAI
    from code_guard.config.settings import settings

    client = OpenAI(
        api_key=api_key,
        base_url=settings.LLM_BASE_URL,
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system",
             "content": "你是一个 D3.js 数据可视化专家。生成可直接运行的 HTML 代码。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=8192,
    )

    content = response.choices[0].message.content.strip()

    # 提取代码块(如果 LLM 用 ```html 包裹)
    if "```html" in content:
        content = content.split("```html")[1]
        if "```" in content:
            content = content.split("```")[0]
    elif "```" in content:
        content = content.split("```")[1]
        if "```" in content:
            content = content.split("```")[0]

    return content.strip()


def generate_with_ai(project_path, output_path,
                     api_key=None, model=None):
    """
    使用 AI 生成可视化 HTML。
    如果 AI 调用失败, 自动回退到硬编码模板。
    """
    from code_guard.config.settings import settings

    api_key = api_key or settings.LLM_API_KEY
    model = model or settings.LLM_MODEL

    # 解析项目
    print(f"正在解析 {project_path} ...")
    results = parse_project_multilang(project_path)

    graph = CodeGraph()
    graph.load_project(results)
    stats = graph.get_stats()

    analyzer = ModuleDependencyAnalyzer(graph, results)
    analysis = analyzer.analyze()
    graph.close()

    project_name = Path(project_path).name

    # 提取模块数据
    module_data = json.dumps(analysis, ensure_ascii=False)
    call_data = json.dumps(_get_callgraph_data(results), ensure_ascii=False)

    # 尝试 AI 生成
    if api_key:
        try:
            print(f"正在调用 AI({model}) 生成 HTML 模板...")
            prompt = build_ai_prompt(
                project_name,
                {**stats, **analysis.get('stats', {})},
                list(analysis.get('modules', {}).keys()),
                [m['name'] for m in analysis.get('core_modules', [])],
                bool(analysis.get('circular_deps')),
            )

            html = _call_llm(prompt, api_key, model)
            if html and "__MODULE_JSON__" in html:
                # 注入数据
                html = html.replace("__MODULE_JSON__", module_data)
                html = html.replace("__CALL_JSON__", call_data)

                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(html)
                print(f"AI 生成完成: {output_path}")
                return output_path
            else:
                print("AI 返回的 HTML 缺少占位符, 回退到默认模板")
        except Exception as e:
            print(f"AI 生成失败({e}), 回退到默认模板")

    # 回退: 使用硬编码模板
    from code_guard.viz import generate_html as fallback
    print("使用默认模板...")
    return fallback(project_path, output_path)


# AI 生成入口(CLI)
def main():
    import argparse
    parser = argparse.ArgumentParser(description="CodeGuard AI 可视化生成")
    parser.add_argument("path", help="项目路径")
    parser.add_argument("-o", "--output", default="code_graph_ai.html",
                        help="输出路径")
    parser.add_argument("--api-key", help="LLM API Key(默认从 .env 读取)")
    parser.add_argument("--model", help="模型名(默认从 .env 读取)")
    args = parser.parse_args()

    generate_with_ai(args.path, args.output, args.api_key, args.model)


if __name__ == "__main__":
    main()

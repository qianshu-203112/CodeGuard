"""
CodeGuard MCP Server — MCP-first 版本

让 Claude Code / Cursor 等 AI 编辑器（或任何 MCP 客户端）直接调用
CodeGuard 的代码分析能力。所有工具返回**单个 JSON 字符串**（结构化数据），
并支持多项目动态加载（不必启动时固定一个项目）。

用法:
  1. 安装: pip install -e .
  2. 运行（stdio，本机 Claude Code / Cursor 用）:
       python -m code_guard.mcp.server [项目路径]
     # 或在 Claude Code settings.json 添加:
     #   "mcpServers": {"code-guard": {
     #     "command": "python", "args": ["-m", "code_guard.mcp.server"],
     #     "env": {"CODE_GUARD_PROJECT": "D:/Project/xxx"}
     #   }}
  3. 运行（HTTP，本机 localhost，不依赖任何云服务器）:
       python -m code_guard.mcp.server --transport http --port 8978
     # 开启 Bearer 鉴权:
       python -m code_guard.mcp.server --transport http --port 8978 --api-key 你的密钥
     # 其它 MCP 客户端经 mcp-remote 桥接:
       npx mcp-remote http://127.0.0.1:8978/mcp

工具一览:
  生命周期  load_project / refresh_project / unload_project / list_projects
  查询      query_callers / query_callees / search_functions /
            get_function_detail / analyze_impact / get_file_structure /
            get_project_stats / analyze_modules / diff_versions / ask
  产品级    review_diff（版本审查：差异 + 逐函数影响 + AI 摘要）/ visualize

说明: 工具的返回值从旧版的"给人看的文本"改为"单个 JSON 字符串"，属刻意变更；
工具名保持不变以减小兼容面。多项目用法: 先 load_project(path) 加载，
再在其它工具里传 project 参数（缺省用最近加载的项目）。
"""
import argparse
import json
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# 确保 src 在路径中（直接跑脚本时可用）
_src = Path(__file__).parent.parent.parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from code_guard.config.settings import settings
from code_guard.service import CodeAnalysisService


def _default_project() -> str:
    """默认项目路径：环境变量 CODE_GUARD_PROJECT -> .env 配置。"""
    path = os.environ.get("CODE_GUARD_PROJECT", "")
    if path and Path(path).exists():
        return path
    path = settings.CODE_GUARD_PROJECT
    if path and Path(path).exists():
        return path
    return ""


def _json(result) -> str:
    """把工具返回序列化成单个 JSON 字符串（保证 MCP 输出是单一可解析块）。"""
    return json.dumps(result, ensure_ascii=False, default=str)


# 全局服务实例（线程安全：解析结果缓存 + 每次操作新建图）
_service = CodeAnalysisService(default_project=_default_project())

mcp = FastMCP("CodeGuard", log_level="WARNING")


# ─────────────────────────────────────────────
#  项目生命周期
# ─────────────────────────────────────────────

@mcp.tool()
def load_project(path: str, name: str = "") -> str:
    """加载一个代码项目（解析 + 建图缓存）。返回统计信息；之后可用 project 参数指定它。

    Args:
        path: 项目根目录或单个源码文件的路径
        name: 可选的显示名（缺省用目录名）
    """
    return _json(_service.load_project(path, name))


@mcp.tool()
def refresh_project(path: str) -> str:
    """重新解析已加载的项目，更新缓存（代码改动后刷新知识）。

    Args:
        path: 已加载项目的路径
    """
    return _json(_service.refresh_project(path))


@mcp.tool()
def unload_project(path: str) -> str:
    """卸载项目，释放缓存。

    Args:
        path: 已加载项目的路径
    """
    return _json(_service.unload_project(path))


@mcp.tool()
def list_projects() -> str:
    """列出所有已加载的项目及其统计信息。"""
    return _json(_service.list_projects())


# ─────────────────────────────────────────────
#  图查询（结构化返回）
# ─────────────────────────────────────────────

@mcp.tool()
def query_callers(function_name: str, project: str = "") -> str:
    """查询指定函数被哪些地方调用了。

    Args:
        function_name: 函数名（如 "load_data"、"DataProcessor.run"）
        project: 项目路径（缺省用最近加载的项目）
    """
    return _json(_service.get_callers(function_name, project))


@mcp.tool()
def query_callees(function_name: str, project: str = "") -> str:
    """查询指定函数内部调用了哪些函数。

    Args:
        function_name: 函数名（如 "run"、"FileParser.parse"）
        project: 项目路径（缺省用最近加载的项目）
    """
    return _json(_service.get_callees(function_name, project))


@mcp.tool()
def search_functions(keyword: str, limit: int = 30, project: str = "") -> str:
    """按关键词搜索代码中的函数/方法。

    Args:
        keyword: 搜索关键词（如 "memory"、"tool"、"parse"）
        limit: 最多返回条数（默认 30，上限 500）
        project: 项目路径（缺省用最近加载的项目）
    """
    return _json(_service.search_functions(keyword, limit, project))


@mcp.tool()
def get_function_detail(function_name: str, project: str = "") -> str:
    """获取函数的详细信息（所在文件、行范围、文档、调用关系）。

    Args:
        function_name: 函数名（如 "load_data"、"DataProcessor.run"）
        project: 项目路径（缺省用最近加载的项目）
    """
    return _json(_service.get_function_detail(function_name, project))


@mcp.tool()
def analyze_impact(function_name: str, project: str = "") -> str:
    """变更影响分析：如果修改指定函数，会影响哪些地方。

    Args:
        function_name: 被修改的函数名
        project: 项目路径（缺省用最近加载的项目）
    """
    return _json(_service.analyze_impact(function_name, project))


@mcp.tool()
def get_file_structure(file_path: str, project: str = "") -> str:
    """查看指定文件中的类和函数结构。

    Args:
        file_path: 文件名或路径（如 "agent.py"、"backend/agent/state.py"）
        project: 项目路径（缺省用最近加载的项目）
    """
    return _json(_service.get_file_structure(file_path, project))


@mcp.tool()
def get_project_stats(project: str = "") -> str:
    """查看当前项目的代码统计信息（文件/函数/类/调用/Import 数量）。

    Args:
        project: 项目路径（缺省用最近加载的项目）
    """
    return _json(_service.get_stats(project))


@mcp.tool()
def analyze_modules(project: str = "") -> str:
    """分析项目的模块依赖关系，检测核心模块和循环依赖。

    Args:
        project: 项目路径（缺省用最近加载的项目）
    """
    return _json(_service.analyze_modules(project))


@mcp.tool()
def diff_versions(base_ref: str, head_ref: str = ".", project: str = "") -> str:
    """版本图谱对比：两个 git 版本之间的代码差异（文件/函数/类/调用/影响）。

    Args:
        base_ref: 基准版本 git ref（如 HEAD~1、某个 commit、tag）
        head_ref: 对比版本 git ref（默认 "." = 当前工作树）
        project: 项目路径（缺省用最近加载的项目；需是 git 仓库）
    """
    return _json(_service.diff_versions(base_ref, head_ref, project))


@mcp.tool()
def ask(question: str, project: str = "") -> str:
    """用自然语言提问代码相关问题，多 Agent 协作回答。

    Args:
        question: 自然语言问题（如 "用户登录流程是怎样的？"、"修改 payment.py 会影响哪些模块？"）
        project: 项目路径（缺省用最近加载的项目）
    """
    return _json(_service.answer(question, project))


# ─────────────────────────────────────────────
#  产品级能力
# ─────────────────────────────────────────────

@mcp.tool()
def review_diff(base_ref: str, head_ref: str = ".", project: str = "",
                with_summary: bool = True) -> str:
    """版本审查：对比两个 git 版本，输出变更清单 + 逐函数影响 + AI 审查摘要。

    Args:
        base_ref: 基准版本 git ref（如 HEAD~1、某个 commit、tag）
        head_ref: 对比版本 git ref（默认 "." = 当前工作树）
        project: 项目路径（缺省用最近加载的项目；需是 git 仓库）
        with_summary: 是否生成 AI 摘要（默认 True；需配置 LLM_API_KEY）
    """
    return _json(_service.review_diff(base_ref, head_ref, project, with_summary))


@mcp.tool()
def visualize(output_path: str = "", project: str = "") -> str:
    """生成代码图谱可视化 HTML 文件（用浏览器打开可交互查看）。

    Args:
        output_path: 输出 HTML 路径（默认: <项目上级目录>/code_graph.html）
        project: 项目路径（缺省用最近加载的项目）
    """
    return _json(_service.visualize(output_path, project))


# ─────────────────────────────────────────────
#  Resources / Prompts（agent 可发现性）
# ─────────────────────────────────────────────

@mcp.resource("codeguard://projects")
def projects_resource() -> str:
    """列出所有已加载项目（JSON）。"""
    return json.dumps(_service.list_projects(), ensure_ascii=False, default=str)


@mcp.resource("project://{name}/stats")
def project_stats_resource(name: str) -> str:
    """查询已加载项目（按 name/目录名）的统计信息（JSON）。

    Args:
        name: load_project 时的 name 或项目目录名
    """
    p = _service.find_project_by_name(name)
    if p is None:
        return json.dumps({"error": f"未找到项目: {name}"}, ensure_ascii=False)
    return json.dumps(p, ensure_ascii=False, default=str)


@mcp.prompt()
def review(base_ref: str, head_ref: str = ".", project: str = "") -> str:
    """生成一段"代码版本审查"任务的提示词。

    Args:
        base_ref: 基准版本 git ref
        head_ref: 对比版本 git ref（默认 "." = 当前工作树）
        project: 项目路径
    """
    return (
        f"请审查代码改动。项目: {project or '当前已加载项目'}；"
        f"对比范围: {base_ref} → {head_ref}。\n"
        "步骤：\n"
        "1. 调用 review_diff 获取结构化差异与逐函数影响\n"
        "2. 逐项分析：高风险改动（被多处调用却修改、波及面大的新增/删除）优先\n"
        "3. 每条意见引用 [文件:函数]\n"
        "4. 给出修改建议"
    )


@mcp.prompt()
def explain(function_name: str, project: str = "") -> str:
    """生成一段"讲解代码函数"任务的提示词。

    Args:
        function_name: 要讲解的函数名
        project: 项目路径
    """
    return (
        f"请讲解代码函数 {function_name}（项目: {project or '当前已加载项目'}）。\n"
        "步骤：\n"
        "1. 调用 get_function_detail 获取详情\n"
        "2. 调用 query_callers / query_callees 了解调用关系\n"
        "3. 说明函数作用、输入输出、被谁调用、调用了谁，及使用注意点"
    )


# ─────────────────────────────────────────────
#  启动
# ─────────────────────────────────────────────

def _run_http(host: str, port: int, api_key: str):
    """HTTP（Streamable HTTP）模式：本机 localhost，可选 Bearer 鉴权。"""
    import uvicorn
    app = mcp.streamable_http_app()

    if api_key:
        expected = api_key.encode("utf-8")
        inner = app  # 先捕获原 app，避免闭包自引用

        async def auth_app(scope, receive, send):
            if scope["type"] == "http":
                headers = dict((k.lower(), v) for k, v in scope.get("headers", []))
                auth = headers.get(b"authorization", b"")
                if auth != b"Bearer " + expected:
                    body = b'{"error":"unauthorized"}'
                    await send({
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                        ],
                    })
                    await send({"type": "http.response.body", "body": body})
                    return
            await inner(scope, receive, send)

        app = auth_app

    print(f"🌐 CodeGuard MCP (HTTP)  http://{host}:{port}/mcp  "
          f"鉴权: {'开' if api_key else '关'}", file=sys.stderr)
    uvicorn.run(app, host=host, port=port, log_level="info")


def main():
    parser = argparse.ArgumentParser(description="CodeGuard MCP Server")
    parser.add_argument("path", nargs="?", default="",
                        help="初始项目路径（可选，覆盖 CODE_GUARD_PROJECT / .env）")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio",
                        help="传输方式（默认 stdio）")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP 监听地址（默认本机）")
    parser.add_argument("--port", type=int, default=8978, help="HTTP 监听端口（默认 8978）")
    parser.add_argument("--api-key", default="",
                        help="HTTP 模式的 Bearer 鉴权密钥（不传则不鉴权）")
    args = parser.parse_args()

    # 启动时加载初始项目（失败不阻塞启动，agent 可随时 load_project）
    project = args.path or _default_project()
    if project:
        res = _service.load_project(project)
        if "error" in res:
            print(f"⚠️ 初始项目加载失败: {res['error']}", file=sys.stderr)
        else:
            print(f"✅ 已加载项目: {res['project']} "
                  f"({res['files']} 文件, {res['functions']} 函数)", file=sys.stderr)

    if args.transport == "http":
        _run_http(args.host, args.port, args.api_key)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

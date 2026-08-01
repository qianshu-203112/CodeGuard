"""
CodeGuard Web 服务器 — FastAPI 应用

用法:
  code-guard serve [--host HOST] [--port PORT] [--project PATH]

提供三个核心页面：
  1. 项目分析 — 解析项目、显示统计
  2. 可视化 — 生成模块依赖 + 调用链图
  3. 自然语言问答 — 用中文问代码问题
"""
import os
import sys
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.templating import Jinja2Templates

# ── 确保模块可导入 ──
_src = Path(__file__).parent.parent.parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_CACHE_DIR_NAME = ".codeguard"
_VIZ_CACHE_DIR = "cache"


def _ensure_cache_dir(project_path: str) -> Path:
    cache_dir = Path(project_path) / _CACHE_DIR_NAME / _VIZ_CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def create_app(project_path: Optional[str] = None) -> FastAPI:
    """创建 FastAPI 应用实例"""
    app = FastAPI(title="CodeGuard", version="0.1.0")
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    app.state.project_path = project_path or ""
    app.state.last_analysis = None
    app.state.results_cache = {}  # 规范化项目路径 → 解析结果，避免每次查询重解析

    # ── 延迟导入核心模块 ──
    def _get_parser():
        from code_guard.parser.ast_parser import parse_project_multilang
        return parse_project_multilang

    def _get_graph():
        from code_guard.graph.code_graph import CodeGraph
        return CodeGraph

    def _get_analyzer():
        from code_guard.analyzer import ModuleDependencyAnalyzer
        return ModuleDependencyAnalyzer

    def _get_viz():
        from code_guard.viz import generate_html
        return generate_html

    def _get_results(project_path: str) -> dict:
        """获取项目解析结果；命中缓存直接复用（解析是 pipeline 最慢的一步）。

        解析结果只含纯数据对象，可跨请求安全共享；图对象仍每请求新建，
        因为 sqlite 连接不能跨线程复用。
        """
        key = str(Path(project_path).resolve())
        cached = app.state.results_cache.get(key)
        if cached is not None:
            return cached
        parse_project_multilang = _get_parser()
        results = parse_project_multilang(project_path)
        # 限制缓存规模，防止长时间运行内存膨胀
        if len(app.state.results_cache) >= 4:
            app.state.results_cache.pop(next(iter(app.state.results_cache)))
        app.state.results_cache[key] = results
        return results

    def _get_graph_for(project_path: str):
        """按项目构建只读图 + 返回解析结果（图每请求新建，结果走缓存）。"""
        results = _get_results(project_path)
        CodeGraph = _get_graph()
        graph = CodeGraph()
        graph.load_project(results)
        return graph, results

    # ════════════════════════════════════════════════
    #  路由
    # ════════════════════════════════════════════════

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(request, "index.html", {
            "project_path": app.state.project_path,
            "stats": None,
        })

    @app.post("/analyze", response_class=HTMLResponse)
    async def analyze(request: Request, project: str = Form(...)):
        project_path = Path(project).resolve()
        if not project_path.is_dir():
            return templates.TemplateResponse(request, "index.html", {
                "project_path": str(project_path),
                "project_error": f"目录不存在: {project_path}",
                "stats": None,
            })

        try:
            results = _get_results(str(project_path))

            CodeGraph = _get_graph()
            graph = CodeGraph()
            graph.load_project(results)
            stats = graph.get_stats()

            ModuleDependencyAnalyzer = _get_analyzer()
            analyzer = ModuleDependencyAnalyzer(graph, results)
            analysis = analyzer.analyze()
            graph.close()

            app.state.project_path = str(project_path)
            app.state.last_analysis = {
                "results": results, "stats": stats, "analysis": analysis,
            }

            cache_dir = _ensure_cache_dir(str(project_path))
            cached_files = sorted(cache_dir.glob("*.html"),
                                  key=os.path.getmtime, reverse=True)

            return templates.TemplateResponse(request, "index.html", {
                "project_path": str(project_path),
                "stats": stats,
                "analysis": analysis,
                "cached_viz": [f.name
                               for f in cached_files[:10]],
                "project_ok": True,
            })
        except Exception as e:
            return templates.TemplateResponse(request, "index.html", {
                "project_path": str(project_path),
                "project_error": f"分析失败: {e}",
                "stats": None,
            })

    @app.get("/analyze-json")
    async def analyze_json(project: str = Query(...)):
        project_path = Path(project).resolve()
        if not project_path.is_dir():
            return JSONResponse({"error": f"目录不存在: {project_path}"}, status_code=400)
        try:
            results = _get_results(str(project_path))
            CodeGraph = _get_graph()
            graph = CodeGraph()
            graph.load_project(results)
            stats = graph.get_stats()
            ModuleDependencyAnalyzer = _get_analyzer()
            analyzer = ModuleDependencyAnalyzer(graph, results)
            analysis = analyzer.analyze()
            graph.close()
            return JSONResponse({"stats": stats, "analysis": analysis})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    # ── 可视化 ──

    @app.get("/viz", response_class=HTMLResponse)
    async def viz_page(request: Request):
        project = app.state.project_path
        cached_files = []
        if project:
            cache_dir = _ensure_cache_dir(project)
            cached_files = sorted(cache_dir.glob("*.html"),
                                  key=os.path.getmtime, reverse=True)

        return templates.TemplateResponse(request, "viz.html", {
            "project_path": project,
            "cached_files": [
                {
                    "name": f.name,
                    "path": str(f.relative_to(Path(project) if project else ".")),
                    "size": f.stat().st_size,
                    "mtime": time.strftime(
                        "%Y-%m-%d %H:%M:%S",
                        time.localtime(f.stat().st_mtime)),
                }
                for f in cached_files[:20]
            ] if cached_files else [],
        })

    @app.post("/viz/generate")
    async def viz_generate(project: str = Form(...)):
        project_path = Path(project).resolve()
        if not project_path.is_dir():
            return JSONResponse({"error": f"目录不存在: {project_path}"}, status_code=400)
        try:
            cache_dir = _ensure_cache_dir(str(project_path))
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            project_name = project_path.name
            output_name = f"{project_name}_graph_{timestamp}.html"
            output_path = cache_dir / output_name

            results = _get_results(str(project_path))
            generate_html = _get_viz()
            generate_html(str(project_path), str(output_path), results=results)

            # 保存项目路径，供 /open-viz 使用
            app.state.project_path = str(project_path)

            rel_path = str(output_path.relative_to(project_path)).replace("\\", "/")

            return JSONResponse({
                "success": True,
                "file": rel_path,
                "full_path": str(output_path.resolve()),
                "message": f"已生成: {rel_path}",
            })
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/viz/files")
    async def viz_files(project: str = Query(...)):
        project_path = Path(project).resolve()
        if not project_path.is_dir():
            return JSONResponse({"error": "目录不存在"}, status_code=400)
        cache_dir = _ensure_cache_dir(str(project_path))
        files = sorted(cache_dir.glob("*.html"),
                       key=os.path.getmtime, reverse=True)
        return JSONResponse({
            "files": [
                {"name": f.name, "path": str(f.relative_to(project_path)),
                 "size": f.stat().st_size, "mtime": f.stat().st_mtime}
                for f in files[:50]
            ]
        })

    # ── 自然语言问答 ──

    @app.get("/query", response_class=HTMLResponse)
    async def query_page(request: Request):
        return templates.TemplateResponse(request, "query.html", {
            "project_path": app.state.project_path,
        })

    @app.post("/query")
    async def query_ask(project: str = Form(...), question: str = Form(...)):
        project_path = Path(project).resolve()
        if not project_path.is_dir():
            return JSONResponse({"error": "目录不存在"}, status_code=400)
        try:
            from code_guard.agent import AgentOrchestrator

            graph, results = _get_graph_for(str(project_path))

            orch = AgentOrchestrator(graph, results,
                                     project_path=str(project_path))
            ans = orch.answer(question)
            graph.close()

            return JSONResponse({
                "success": True,
                "answer": ans["answer"],
                "steps": ans.get("steps", []),
            })
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/query-stream")
    async def query_stream(project: str = Query(...), question: str = Query(...)):
        """SSE 流式问答 — 逐步返回规划/执行/结果。

        复用 AgentOrchestrator.answer(on_event=...) 的规划/执行/合成逻辑（唯一实现），
        这里只做两件事：
          1. 把 answer 的 on_event 回调转成 SSE 事件；
          2. answer 在 worker 线程里跑，避免阻塞事件循环，且不重复实现规划/执行。
        """
        from code_guard.agent import AgentOrchestrator
        from starlette.responses import StreamingResponse

        project_path = Path(project).resolve()
        if not project_path.is_dir():
            return JSONResponse({"error": "目录不存在"}, status_code=400)

        async def event_stream():
            import json
            import queue
            import threading

            try:
                events = queue.Queue()
                step_no = 0  # 前端用连续编号做 DOM id，与计划里的 step 编号解耦

                def emit(event, data):
                    events.put((event, data))

                def worker():
                    graph = None
                    try:
                        # 在 worker 线程内建图/解析（sqlite 连接不能跨线程复用）
                        graph, results = _get_graph_for(str(project_path))
                        orch = AgentOrchestrator(graph, results,
                                                 project_path=str(project_path))
                        orch.answer(question, on_event=emit)
                        events.put(("__done__", None))
                    except Exception as e:
                        events.put(("__error__", str(e)))
                    finally:
                        if graph:
                            graph.close()

                threading.Thread(target=worker, daemon=True).start()

                while True:
                    event, data = events.get()
                    if event == "__done__":
                        yield "event: done\ndata: ok\n\n"
                        break
                    elif event == "__error__":
                        yield f"event: error\ndata: {data}\n\n"
                        break
                    elif event == "plan":
                        if isinstance(data, list):
                            payload = json.dumps([{
                                "step": s.get("step", i + 1),
                                "tool": s.get("tool", ""),
                                "args": s.get("args", {}),
                                "purpose": s.get("purpose", ""),
                            } for i, s in enumerate(data)], ensure_ascii=False)
                        else:
                            payload = json.dumps(data, ensure_ascii=False)
                        yield f"event: plan\ndata: {payload}\n\n"
                    elif event == "step_start":
                        step_no += 1
                        payload = json.dumps({
                            "step": step_no,
                            "tool": data.get("tool", ""),
                            "args": data.get("args", {}),
                            "purpose": data.get("purpose", ""),
                        }, ensure_ascii=False)
                        yield f"event: step_start\ndata: {payload}\n\n"
                    elif event == "step_done":
                        result = data.get("result")
                        # 截断结果避免事件过大
                        if isinstance(result, list):
                            preview = f"共 {len(result)} 条结果"
                            if result:
                                preview += "，前3条: " + json.dumps(result[:3], ensure_ascii=False)
                        elif isinstance(result, dict):
                            preview = json.dumps(result, ensure_ascii=False)[:200]
                        else:
                            preview = str(result)[:200]
                        payload = json.dumps({
                            "step": step_no,
                            "tool": data.get("tool", ""),
                            "result": preview,
                        }, ensure_ascii=False)
                        yield f"event: step_done\ndata: {payload}\n\n"
                    elif event == "synthesize":
                        yield "event: synthesize\ndata: 正在合成最终回答...\n\n"
                    elif event == "answer":
                        yield f"event: answer\ndata: {json.dumps({'text': data}, ensure_ascii=False)}\n\n"
            except Exception as e:
                yield f"event: error\ndata: {str(e)}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    # ── 系统文件夹选择器 ──

    @app.get("/pick-folder")
    async def pick_folder():
        """调用 Windows 原生文件夹浏览对话框（独立窗口 + TopMost）"""
        import subprocess
        import asyncio
        try:
            ps_code = (
                'Add-Type -AssemblyName System.Windows.Forms;'
                '[System.Windows.Forms.Application]::EnableVisualStyles();'
                '$f=New-Object System.Windows.Forms.FolderBrowserDialog;'
                '$f.Description="选择要分析的项目目录";'
                '$f.ShowNewFolderButton=$false;'
                '$form=New-Object System.Windows.Forms.Form;'
                '$form.TopMost=$true;'
                '$form.WindowState="Minimized";'
                '$form.ShowInTaskbar=$false;'
                'if($f.ShowDialog($form)-eq"OK"){$f.SelectedPath}'
            )
            # 用 CREATE_NEW_CONSOLE 创建独立窗口，对话框才能正常弹出
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: subprocess.run(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                     "-STA", "-Command", ps_code],
                    capture_output=True, text=True, timeout=120,
                    creationflags=subprocess.CREATE_NEW_CONSOLE
                )
            )
            folder = result.stdout.strip()
            if folder and os.path.isdir(folder):
                return JSONResponse({"path": folder.replace("/", "\\")})
            return JSONResponse({"path": None, "message": "未选择文件夹"})
        except subprocess.TimeoutExpired:
            return JSONResponse({"path": None, "message": "操作超时"})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    # ── 缓存可视化 HTML 文件 ──

    @app.get("/cache/{project_name:path}/{file_name:path}")
    async def serve_cached(project_name: str, file_name: str):
        base = Path(app.state.project_path) if app.state.project_path else Path.cwd()
        cache_dir = (base / ".codeguard" / "cache").resolve()
        target = (cache_dir / file_name).resolve()
        # 路径穿越防护：target 必须落在 cache 目录内（不能用字符串前缀判断，
        # 否则 D:\projectX 会被 D:\project 误放过）
        if not target.is_relative_to(cache_dir) or not target.is_file():
            return HTMLResponse("文件不存在", status_code=404)
        with open(target, "r", encoding="utf-8") as f:
            content = f.read()
        return HTMLResponse(content)

    @app.get("/open-viz/{file_name:path}")
    async def open_viz(file_name: str):
        """打开缓存的可视化文件（项目路径从服务器状态取）"""
        project = app.state.project_path
        if not project:
            return HTMLResponse("未指定项目路径", status_code=400)
        cache_dir = (Path(project) / ".codeguard" / "cache").resolve()
        target = (cache_dir / file_name).resolve()
        # 路径穿越防护：target 必须落在 cache 目录内，禁止 ../ 逃逸读取任意文件
        if not target.is_relative_to(cache_dir) or not target.is_file():
            return HTMLResponse(f"文件不存在: {file_name}", status_code=404)
        with open(target, "r", encoding="utf-8") as f:
            content = f.read()
        return HTMLResponse(content)

    # ── 删除缓存文件 ──

    @app.post("/viz/delete")
    async def viz_delete(project: str = Form(...), file: str = Form(...)):
        """删除缓存的可视化文件"""
        base = Path(project).resolve()
        target = (base / ".codeguard" / "cache" / file).resolve()
        if not str(target).startswith(str(base.resolve())) or not target.exists():
            return JSONResponse({"error": "文件不存在"}, status_code=404)
        try:
            target.unlink()
            return JSONResponse({"success": True, "message": f"已删除: {file}"})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    return app


def run_server(host: str = "127.0.0.1", port: int = 8979,
               project_path: Optional[str] = None):
    """启动 CodeGuard Web 服务器"""
    import uvicorn
    app = create_app(project_path)
    print(f"""
╔══════════════════════════════════════════╗
║          CodeGuard Web 服务              ║
║──────────────────────────────────────────║
║  地址: http://{host}:{port}               ║
║  项目: {project_path or "未指定（可在页面输入）"}
║  缓存: .codeguard/cache/                 ║
║                                          ║
║  Ctrl+C 停止服务                          ║
╚══════════════════════════════════════════╝
    """)
    uvicorn.run(app, host=host, port=port, log_level="info")

"""
Agent 工具集 — 每个工具封装一个现有模块的能力，供 Agent 调用
"""
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Any

from code_guard.graph.code_graph import CodeGraph
from code_guard.analyzer import ModuleDependencyAnalyzer


class GraphQueryTool:
    """图查询工具 — 查询函数调用者/被调用者/详情/影响分析"""

    def __init__(self, graph: CodeGraph):
        self.graph = graph

    def get_callers(self, function_name: str = "", name: str = "") -> List[Dict]:
        target = function_name or name
        return self.graph.get_callers(target) if target else []

    def get_callees(self, function_name: str = "", name: str = "") -> List[Dict]:
        target = function_name or name
        return self.graph.get_callees(target) if target else []

    def search_functions(self, keyword: str = "", query: str = "") -> List[Dict]:
        kw = keyword or query
        return self.graph.search_functions(kw) if kw else []

    def get_detail(self, function_name: str = "", name: str = "") -> Optional[Dict]:
        target = function_name or name
        return self.graph.get_function_detail(target) if target else None

    def analyze_impact(self, function_name: str = "", name: str = "") -> Dict:
        target = function_name or name
        return self.graph.analyze_change_impact(target) if target else {}

    def get_stats(self) -> Dict:
        return self.graph.get_stats()

    def get_file_structure(self, file_hint: str = "") -> Dict:
        """获取文件结构（函数/类列表）"""
        conn = self.graph.conn
        if file_hint:
            files = conn.execute(
                "SELECT DISTINCT f.id, f.path FROM files f WHERE f.path LIKE ?",
                (f"%{file_hint}%",)
            ).fetchall()
        else:
            files = conn.execute(
                "SELECT id, path FROM files ORDER BY path"
            ).fetchall()
        result = {}
        for fid, fpath in files:
            funcs = conn.execute(
                "SELECT name, start_line, end_line FROM functions WHERE file_id=?",
                (fid,)
            ).fetchall()
            classes = conn.execute(
                "SELECT name, start_line FROM classes WHERE file_id=?",
                (fid,)
            ).fetchall()
            key = Path(fpath).name
            if key in result:
                # 重名文件（如多个目录的 __init__.py）：改用 父目录/文件名 区分，
                # 避免后者覆盖前者
                key = f"{Path(fpath).parent.name}/{key}"
                if key in result:
                    key = fpath  # 仍冲突则退化用完整路径
            result[key] = {
                "path": fpath,
                "functions": [{"name": r[0], "line": r[1]} for r in funcs],
                "classes": [{"name": r[0], "line": r[1]} for r in classes],
            }
        return result

    def get_modules(self) -> Dict:
        return {}

    def list_tools(self) -> List[Dict]:
        return [
            {"name": "get_callers", "desc": "查询谁调用了指定函数", "args": ["function_name"]},
            {"name": "get_callees", "desc": "查询指定函数调用了谁", "args": ["function_name"]},
            {"name": "search_functions", "desc": "按关键词搜索函数", "args": ["keyword"]},
            {"name": "get_detail", "desc": "获取函数详细信息", "args": ["function_name"]},
            {"name": "analyze_impact", "desc": "变更影响分析", "args": ["function_name"]},
            {"name": "get_stats", "desc": "项目统计", "args": []},
            {"name": "get_file_structure", "desc": "文件结构", "args": ["file_hint"]},
        ]


class VectorSearchTool:
    """向量搜索工具 — 语义搜索代码"""

    def __init__(self, project_path: str = ""):
        self.project_path = project_path

    def search(self, query: str, n: int = 5) -> List[Dict]:
        try:
            from code_guard.vector.indexer import search_code
            return search_code(query, project_path=self.project_path,
                               n_results=n)
        except Exception as e:
            return [{"error": str(e)}]

    def is_available(self) -> bool:
        try:
            from code_guard.vector.store import VectorStore
            vs = VectorStore()
            return vs.count() > 0
        except Exception:
            return False

    def list_tools(self) -> List[Dict]:
        return [
            {"name": "search", "desc": "语义搜索代码（按含义搜，非关键词）", "args": ["query"]},
        ]


class CodeReaderTool:
    """源码阅读工具 — 读取指定文件的源码"""

    def __init__(self, project_path: str = ""):
        self.project_path = project_path

    def read_file(self, file_path: str, max_lines: int = 50) -> str:
        full_path = Path(self.project_path) / file_path if self.project_path else Path(file_path)
        if not full_path.exists():
            return f"文件不存在: {file_path}"
        try:
            lines = full_path.read_text(encoding="utf-8", errors="replace").split("\n")
            selected = lines[:max_lines]
            return "\n".join(f"{i+1}: {l}" for i, l in enumerate(selected))
        except Exception as e:
            return f"读取失败: {e}"

    def list_tools(self) -> List[Dict]:
        return [
            {"name": "read_file", "desc": "读取源代码文件内容", "args": ["file_path", "max_lines"]},
        ]


class ProjectOverviewTool:
    """项目概览工具 — 了解项目结构和模块划分"""

    def __init__(self, project_path: str = "", graph=None):
        self.project_path = project_path
        self.graph = graph

    def overview(self) -> Dict:
        """获取项目概览：目录结构、各模块功能说明"""
        result = {"modules": [], "tool_files": [], "agent_files": []}
        base = Path(self.project_path) if self.project_path else Path.cwd()

        # 扫描顶层模块目录
        for item in sorted(base.iterdir()):
            if item.is_dir() and not item.name.startswith((".", "_", "venv", "node_modules")):
                init_file = item / "__init__.py"
                desc = ""
                if init_file.exists():
                    content = init_file.read_text(encoding="utf-8", errors="replace")[:500]
                    m = re.search(r'"""(.*?)"""', content, re.DOTALL)
                    if m:
                        desc = m.group(1).strip()[:100]
                py_files = list(item.rglob("*.py"))
                result["modules"].append({
                    "name": item.name,
                    "path": str(item),
                    "files": len(py_files),
                    "description": desc,
                })

                # 识别工具模块（顶层或嵌套的 tools/ 目录）
                if "tool" in item.name.lower():
                    result["tool_files"].append(str(item))
                # 也找嵌套的 tools/ 目录
                nested_tools = list(item.rglob("tools"))
                for nt in nested_tools:
                    if nt.is_dir() and nt.parent == item:
                        result["tool_files"].append(str(nt))

                # 识别 agent 模块
                if "agent" in item.name.lower():
                    result["agent_files"].append(str(item))
                nested_agents = list(item.rglob("agent"))
                for na in nested_agents:
                    if na.is_dir() and na.parent == item:
                        result["agent_files"].append(str(na))

        # 如果图数据可用，获取模块依赖
        if self.graph:
            try:
                conn = self.graph.conn
                mods = conn.execute(
                    "SELECT DISTINCT fl.path FROM files fl"
                ).fetchall()
                result["total_files"] = len(mods)
                func_count = conn.execute("SELECT COUNT(*) FROM functions").fetchone()
                result["total_functions"] = func_count[0] if func_count else 0
            except Exception:
                pass

        return result

    def list_tools(self) -> List[Dict]:
        return [
            {"name": "overview", "desc": "项目总览（模块划分、工具目录、Agent目录、文件数、函数数）", "args": []},
        ]


class ModuleAnalysisTool:
    """模块分析工具 — 模块依赖/核心模块/循环依赖"""

    def __init__(self, graph: CodeGraph, results: dict):
        self.graph = graph
        self.results = results

    def analyze(self) -> Dict:
        analyzer = ModuleDependencyAnalyzer(self.graph, self.results)
        return analyzer.analyze()

    def list_tools(self) -> List[Dict]:
        return [
            {"name": "analyze", "desc": "模块依赖分析（核心模块、循环依赖、孤立模块）", "args": []},
        ]

"""
模块依赖分析器

分析项目各模块之间的依赖关系，检测：
1. 模块依赖图谱 — 谁依赖了谁
2. 循环依赖 — A→B→C→A 的环
3. 核心模块 — 被最多模块依赖的模块
4. 孤立模块 — 既不被依赖也不依赖别人的模块
5. 依赖层级 — 模块的依赖深度
"""
import os
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Set, Tuple, Optional, Any

from code_guard.graph.code_graph import CodeGraph
from code_guard.parser.ast_parser import ParseResult, ImportInfo


class ModuleDependencyAnalyzer:
    """模块依赖分析器"""

    def __init__(self, graph: CodeGraph, results: Dict[str, ParseResult],
                 skip_dirs: Optional[Set[str]] = None):
        self.graph = graph
        self.results = results
        self.skip_dirs = skip_dirs or {
            "venv", "node_modules", "__pycache__", ".git", ".idea",
            "dist", "build", "egg-info", ".mypy_cache", ".pytest_cache",
            "session_", "tmp", "temp", ".tox", "env", "envs", ".env",
        }

    # ── 核心分析 ──

    def analyze(self) -> Dict[str, Any]:
        """
        运行完整的模块依赖分析。

        Returns:
            {
                "modules": {"agent": {"files": [...], "depends_on": [...], ...}, ...},
                "dependencies": [{"from": "agent", "to": "memory"}, ...],
                "circular_deps": [["agent", "memory", "utils"], ...],
                "core_modules": ["agent", ...],
                "orphan_modules": ["test", ...],
                "stats": {...}
            }
        """
        # 1. 提取模块结构
        modules = self._extract_modules()

        # 2. 分析依赖关系
        dependencies = self._analyze_dependencies(modules)

        # 3. 检测循环依赖
        circular_deps = self._find_circular_dependencies(dependencies)

        # 4. 识别核心模块和孤立模块
        core_modules, orphan_modules = self._rank_modules(dependencies)

        # 5. 计算统计
        stats = self._compute_stats(modules, dependencies, circular_deps)

        return {
            "modules": dict(modules),
            "dependencies": dependencies,
            "circular_deps": circular_deps,
            "core_modules": core_modules,
            "orphan_modules": orphan_modules,
            "stats": stats,
        }

    def _extract_modules(self) -> Dict[str, dict]:
        """提取项目中的模块信息"""
        modules = {}

        for fp, result in self.results.items():
            module_name = self._file_to_module(fp)
            if module_name == "__skip__":
                continue
            if module_name not in modules:
                modules[module_name] = {
                    "files": [],
                    "imports": [],
                    "exports": [],
                }
            modules[module_name]["files"].append(fp)

            for imp in result.imports:
                modules[module_name]["imports"].append({
                    "source": imp.source,
                    "names": imp.names,
                    "is_from": imp.is_from,
                    "file": fp,  # ← 记录来源文件
                })

        return modules

    def _file_to_module(self, file_path: str) -> str:
        """将文件路径映射为模块名"""
        path = Path(file_path)
        parts = path.parts

        # 跳过临时目录
        if any(s in str(path).lower() for s in ("session_", "tmp", "temp", "venv")):
            return "__skip__"

        # 尝试找到项目根目录下的顶层模块
        for i, part in enumerate(parts):
            if part == "__init__.py":
                return parts[i - 1] if i > 0 else "root"

        # 查找 .py 文件所在目录
        for i, part in enumerate(parts):
            if part.endswith(".py"):
                if i > 0 and parts[i - 1] != Path(file_path).anchor:
                    return parts[i - 1]
                return part[:-3]

        return "root"

    def _analyze_dependencies(self, modules: Dict) -> List[Dict]:
        """分析模块间依赖关系，含具体 import 来源"""
        dependencies = []
        module_names = set(modules.keys())
        dep_map = {}  # (from, to) → {from, to, imports: [...]}

        for module_name, info in modules.items():
            for imp in info["imports"]:
                target = self._resolve_import_module(imp["source"], module_names)
                if target and target != module_name:
                    pair = (module_name, target)
                    if pair not in dep_map:
                        dep_map[pair] = {"from": module_name, "to": target, "imports": []}
                    # 添加具体 import 信息
                    imp_detail = {
                        "file": imp.get("file", ""),
                        "source": imp["source"],
                        "names": imp["names"],
                    }
                    # 去重
                    if imp_detail not in dep_map[pair]["imports"]:
                        dep_map[pair]["imports"].append(imp_detail)

        return list(dep_map.values())

    def _resolve_import_module(self, source: str, module_names: Set[str]) -> Optional[str]:
        """将 import source 解析为模块名"""
        # 直接匹配模块名
        if source in module_names:
            return source

        # 匹配带点号路径的第一段（如 "agent.loop" → "agent"）
        first_segment = source.split(".")[0]
        if first_segment in module_names:
            return first_segment

        # 检查是否是文件路径中的模块
        for m in module_names:
            if m in source or source in m:
                return m

        return None

    def _find_circular_dependencies(self, dependencies: List[Dict]) -> List[List[str]]:
        """使用 DFS 检测循环依赖"""
        graph = defaultdict(set)
        for dep in dependencies:
            graph[dep["from"]].add(dep["to"])

        cycles = []
        visited = set()
        path = []

        def dfs(node: str):
            if node in path:
                cycle_start = path.index(node)
                cycle = path[cycle_start:] + [node]
                # 归一化：确保环以最小节点开头
                min_idx = cycle.index(min(cycle))
                cycle = cycle[min_idx:] + cycle[1:min_idx + 1]
                # 去重
                cycle_key = "->".join(cycle)
                if not any(cycle_key in "->".join(c) for c in cycles):
                    cycles.append(cycle)
                return

            if node in visited:
                return

            visited.add(node)
            path.append(node)

            for neighbor in graph.get(node, set()):
                dfs(neighbor)

            path.pop()

        for node in list(graph.keys()):
            dfs(node)

        return cycles

    def _rank_modules(self, dependencies: List[Dict]) -> Tuple[List[Dict], List[str]]:
        """识别核心模块和孤立模块"""
        # 依赖计数
        depends_on = defaultdict(set)  # 模块A 依赖了谁
        depended_by = defaultdict(set)  # 模块A 被谁依赖

        for dep in dependencies:
            depends_on[dep["from"]].add(dep["to"])
            depended_by[dep["to"]].add(dep["from"])

        all_modules = set(depends_on.keys()) | set(depended_by.keys())

        # 核心模块：被依赖最多的
        core = []
        for module in all_modules:
            core.append({
                "name": module,
                "depended_by_count": len(depended_by.get(module, set())),
                "depends_on_count": len(depends_on.get(module, set())),
            })
        core.sort(key=lambda m: -m["depended_by_count"])

        # 孤立模块：没有依赖关系
        orphan = []
        for module in all_modules:
            if not depends_on.get(module) and not depended_by.get(module):
                orphan.append(module)

        return core, orphan

    def _compute_stats(self, modules: Dict, dependencies: List[Dict],
                       circular_deps: List[List[str]]) -> Dict[str, Any]:
        """计算统计信息"""
        total_modules = len(modules)
        total_deps = len(dependencies)
        dep_modules = set()
        for d in dependencies:
            dep_modules.add(d["from"])
            dep_modules.add(d["to"])

        return {
            "total_modules": total_modules,
            "total_dependencies": total_deps,
            "modules_with_deps": len(dep_modules),
            "circular_dep_count": len(circular_deps),
            "orphan_count": sum(1 for m in modules if m not in dep_modules),
        }

    # ── 输出 ──

    def to_text(self, analysis: Dict[str, Any]) -> str:
        """将分析结果格式化为文本"""
        lines = []
        stats = analysis["stats"]

        lines.append("📦 模块依赖分析报告\n")
        lines.append(f"  总模块数: {stats['total_modules']}")
        lines.append(f"  总依赖数: {stats['total_dependencies']}")
        lines.append(f"  有依赖关系的模块: {stats['modules_with_deps']}")
        lines.append(f"  循环依赖数: {stats['circular_dep_count']}")
        lines.append(f"  孤立模块数: {stats['orphan_count']}")

        # 核心模块
        if analysis["core_modules"]:
            lines.append(f"\n📌 核心模块（被依赖最多的 Top-5）：")
            for m in analysis["core_modules"][:5]:
                if m["depended_by_count"] > 0:
                    lines.append(f"  · {m['name']}/ ← 被 {m['depended_by_count']} 个模块依赖")

        # 依赖图
        if analysis["dependencies"]:
            lines.append(f"\n🔗 依赖关系：")
            # 按依赖方分组
            dep_tree = defaultdict(list)
            for d in analysis["dependencies"]:
                dep_tree[d["from"]].append(d["to"])
            for from_mod in sorted(dep_tree.keys()):
                to_mods = sorted(dep_tree[from_mod])
                lines.append(f"  {from_mod}/")
                for to_mod in to_mods:
                    lines.append(f"    └─→ {to_mod}/")

        # 循环依赖
        if analysis["circular_deps"]:
            lines.append(f"\n⚠️  循环依赖（{len(analysis['circular_deps'])} 个）：")
            for cycle in analysis["circular_deps"]:
                arrow = " → ".join(cycle)
                lines.append(f"  {arrow}")

        # 孤立模块
        if analysis["orphan_modules"]:
            lines.append(f"\�  孤立模块（没有被依赖也不依赖别人）：")
            for m in sorted(analysis["orphan_modules"]):
                lines.append(f"  · {m}/")

        return "\n".join(lines)

    def to_json(self, analysis: Dict[str, Any]) -> Dict:
        """输出为 JSON（供可视化使用）"""
        return analysis

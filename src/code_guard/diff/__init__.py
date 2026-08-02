"""版本图谱对比 — 两个 git 版本代码图谱差分

用法:
  python -m code_guard.cli.main diff <项目> --base <ref> --head <ref>

原理:
  用 git worktree 把 base/head 两个版本分别检出到临时目录，走现有解析管线
  建内存图，然后逐层对比：
    - 文件：新增 / 删除 / 修改（内容 SHA1 不同即修改）
    - 函数（限定名）：新增 / 删除 / 修改（同名但行范围变化）
    - 类：新增 / 删除 / 修改
    - 调用关系：changed 函数的 callees 增删（head 图为准，top N）
    - 影响：changed 函数在 head 图上的变更影响分析（谁会被波及）

要求：项目是 git 仓库。head 支持 "."（当前工作树，直接解析不拉 worktree）。
"""
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path

from code_guard.parser.ast_parser import parse_project_multilang
from code_guard.graph.code_graph import CodeGraph

_CAP = 200          # 每个列表最多保留条数
_IMPACT_CAP = 50    # 做调用关系/影响分析时最多处理的 changed 函数数


def _sha1_of(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel(path: str, root: str) -> str:
    """绝对路径 → 相对项目根的 posix 路径。"""
    return os.path.relpath(path, root).replace("\\", "/")


class VersionDiffer:
    def __init__(self, project_path: str):
        self.project_path = str(Path(project_path).resolve())
        if not self._is_git_repo():
            raise ValueError(
                f"不是 git 仓库: {self.project_path}（版本对比需要 git 版本历史）")

    def _is_git_repo(self) -> bool:
        r = subprocess.run(
            ["git", "-C", self.project_path, "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        return r.returncode == 0 and r.stdout.strip() == "true"

    def _parse_ref(self, ref: str):
        """解析某个版本的项目，返回 (results, root, sha_map)。

        ref=='.' 表示当前工作树。git worktree 拉 ref 到临时目录解析；哈希在
        worktree 存活期内算好（_sha1_of 要读磁盘），finally 里移除不留残留。
        """
        tmp = None
        if ref in (".", "WORKING"):
            root = self.project_path
        else:
            tmp = tempfile.mkdtemp(prefix="codeguard_diff_")
            subprocess.run(
                ["git", "-C", self.project_path, "worktree", "add", "--detach",
                 str(tmp), ref],
                check=True, capture_output=True, text=True,
                encoding="utf-8", errors="replace")
            root = str(tmp)
        try:
            results = parse_project_multilang(root)
            sha_map = {_rel(fp, root): _sha1_of(fp) for fp in results}
        finally:
            if tmp is not None:
                subprocess.run(
                    ["git", "-C", self.project_path, "worktree", "remove",
                     "--force", str(tmp)],
                    capture_output=True, text=True,
                    encoding="utf-8", errors="replace")
        return results, root, sha_map

    @staticmethod
    def _build_index(results, root: str, sha_map: dict) -> dict:
        """results → {相对路径: {"sha1", "functions": {qname:(start,end)},
        "classes": {name:(start,end)}}}。类方法限定名为 Class.method。
        sha_map 由 _parse_ref 在 worktree 存活期内算好（相对路径 → sha1）。"""
        idx = {}
        for fp, result in results.items():
            rel = _rel(fp, root)
            funcs = {f.name: (f.start_line, f.end_line) for f in result.functions}
            for cls in result.classes:
                for m in cls.methods:
                    funcs[f"{cls.name}.{m.name}"] = (m.start_line, m.end_line)
            classes = {c.name: (c.start_line, c.end_line) for c in result.classes}
            idx[rel] = {"sha1": sha_map.get(rel, ""),
                        "functions": funcs, "classes": classes}
        return idx

    def compare(self, base_ref: str, head_ref: str) -> dict:
        """对比 base_ref 与 head_ref，返回结构化差分结果。"""
        base_results, base_root, base_sha = self._parse_ref(base_ref)
        head_results, head_root, head_sha = self._parse_ref(head_ref)

        base_idx = self._build_index(base_results, base_root, base_sha)
        head_idx = self._build_index(head_results, head_root, head_sha)

        base_files, head_files = set(base_idx), set(head_idx)
        common = base_files & head_files

        # ── 文件层：SHA1 不同即修改 ──
        added_files = sorted(head_files - base_files)
        removed_files = sorted(base_files - head_files)
        modified_files = sorted(
            r for r in common if base_idx[r]["sha1"] != head_idx[r]["sha1"])

        # ── 函数 / 类层：限定名 + 行范围对比（仅 common 文件） ──
        func_added, func_removed, func_modified = [], [], []
        class_added, class_removed, class_modified = [], [], []
        for rel in sorted(common):
            bf, hf = base_idx[rel]["functions"], head_idx[rel]["functions"]
            bk, hk = set(bf), set(hf)
            for k in sorted(hk - bk):
                func_added.append({"func": k, "file": rel, "line": hf[k][0]})
            for k in sorted(bk - hk):
                func_removed.append({"func": k, "file": rel, "line": bf[k][0]})
            for k in sorted(hk & bk):
                if bf[k] != hf[k]:
                    func_modified.append({"func": k, "file": rel,
                                          "old": bf[k], "new": hf[k]})

            bc, hc = base_idx[rel]["classes"], head_idx[rel]["classes"]
            bck, hck = set(bc), set(hc)
            for k in sorted(hck - bck):
                class_added.append({"class": k, "file": rel})
            for k in sorted(bck - hck):
                class_removed.append({"class": k, "file": rel})
            for k in sorted(hck & bck):
                if bc[k] != hc[k]:
                    class_modified.append({"class": k, "file": rel})

        # 整文件新增/删除：其内部所有函数/类也一并计入
        for rel in added_files:
            for k, (sl, _el) in head_idx[rel]["functions"].items():
                func_added.append({"func": k, "file": rel, "line": sl})
            for k in head_idx[rel]["classes"]:
                class_added.append({"class": k, "file": rel})
        for rel in removed_files:
            for k, (sl, _el) in base_idx[rel]["functions"].items():
                func_removed.append({"func": k, "file": rel, "line": sl})
            for k in base_idx[rel]["classes"]:
                class_removed.append({"class": k, "file": rel})

        # ── 调用关系变化 + 影响分析（head 图为准） ──
        base_graph = CodeGraph()
        base_graph.load_project(base_results)
        head_graph = CodeGraph()
        head_graph.load_project(head_results)

        changed = [{"func": i["func"], "file": i["file"]}
                   for i in (func_added + func_modified)][:_IMPACT_CAP]
        # 与图一致：顶层函数用简单名，类方法用 Class.method 限定名
        base_funcs = _collect_qnames(base_results)

        callee_added, callee_removed = [], []
        for item in changed:
            qname = item["func"]
            hc = sorted({c["callee"] for c in head_graph.get_callees(qname)})
            if qname in base_funcs:
                bc = sorted({c["callee"] for c in base_graph.get_callees(qname)})
                added, removed = _diff_sorted(bc, hc)
            else:  # 新增函数：只记其调用的新边
                added, removed = hc, []
            if added:
                callee_added.append({"func": qname, "callees": added})
            if removed:
                callee_removed.append({"func": qname, "callees": removed})

        affected_funcs, affected_files = set(), set()
        for item in changed:
            imp = head_graph.analyze_change_impact(item["func"], max_depth=3)
            for a in imp.get("direct_callers", []) + imp.get("all_affected", []):
                affected_funcs.add(a["caller"])
                affected_files.add(_rel(a["file"], head_root))
        base_graph.close()
        head_graph.close()

        def cap(xs):
            return xs[:_CAP]

        return {
            "base": base_ref,
            "head": head_ref,
            "stats": {
                "base_files": len(base_files),
                "head_files": len(head_files),
                "added_files": len(added_files),
                "removed_files": len(removed_files),
                "modified_files": len(modified_files),
                "added_functions": len(func_added),
                "removed_functions": len(func_removed),
                "modified_functions": len(func_modified),
                "added_classes": len(class_added),
                "removed_classes": len(class_removed),
                "modified_classes": len(class_modified),
                "changed_functions": len(changed),
            },
            "files": {"added": cap(added_files), "removed": cap(removed_files),
                      "modified": cap(modified_files)},
            "functions": {"added": cap(func_added), "removed": cap(func_removed),
                          "modified": cap(func_modified)},
            "classes": {"added": cap(class_added), "removed": cap(class_removed),
                        "modified": cap(class_modified)},
            "callees": {"added": cap(callee_added), "removed": cap(callee_removed)},
            "impact": {"affected_functions": sorted(affected_funcs)[:_CAP],
                       "affected_files": sorted(affected_files)[:_CAP],
                       "count": len(affected_funcs)},
        }

    def render_text(self, diff: dict) -> str:
        """把差分结果渲染成人类可读文本。"""
        s = diff["stats"]
        lines = [
            f"版本对比: {diff['base']} → {diff['head']}",
            f"文件: +{s['added_files']} 新增 / -{s['removed_files']} 删除 / "
            f"~{s['modified_files']} 修改   (基准 {s['base_files']} → 当前 {s['head_files']})",
            f"函数: +{s['added_functions']} 新增 / -{s['removed_functions']} 删除 / "
            f"~{s['modified_functions']} 修改   "
            f"类: +{s['added_classes']} / -{s['removed_classes']} / ~{s['modified_classes']}",
        ]

        def section(title, items, fmt):
            if not items:
                return []
            out = [f"\n▶ {title} ({len(items)})"]
            for it in items[:_CAP]:
                out.append("  · " + fmt(it))
            return out

        lines += section("新增文件", diff["files"]["added"], lambda i: i)
        lines += section("删除文件", diff["files"]["removed"], lambda i: i)
        lines += section("修改文件", diff["files"]["modified"], lambda i: i)
        lines += section("新增函数", diff["functions"]["added"],
                         lambda i: f"{i['func']} ({i['file']}:{i['line']})")
        lines += section("删除函数", diff["functions"]["removed"],
                         lambda i: f"{i['func']} ({i['file']}:{i['line']})")
        lines += section("修改函数", diff["functions"]["modified"],
                         lambda i: f"{i['func']} ({i['file']}) "
                                   f"[{i['old'][0]}-{i['old'][1]} → {i['new'][0]}-{i['new'][1]}]")
        lines += section("新增类", diff["classes"]["added"],
                         lambda i: f"{i['class']} ({i['file']})")
        lines += section("删除类", diff["classes"]["removed"],
                         lambda i: f"{i['class']} ({i['file']})")
        lines += section("修改类", diff["classes"]["modified"],
                         lambda i: f"{i['class']} ({i['file']})")
        lines += section("新增调用关系", diff["callees"]["added"],
                         lambda i: f"{i['func']} 新增调用: {', '.join(i['callees'])}")
        lines += section("移除调用关系", diff["callees"]["removed"],
                         lambda i: f"{i['func']} 移除调用: {', '.join(i['callees'])}")

        imp = diff["impact"]
        if imp["count"]:
            lines.append(f"\n▶ 变更影响 (head 图波及, {imp['count']} 个函数)")
            lines.append(f"  波及函数: {', '.join(imp['affected_functions'][:_CAP])}")
            lines.append(f"  波及文件: {', '.join(imp['affected_files'][:_CAP])}")
        return "\n".join(lines)


def _collect_qnames(results) -> set:
    """收集一个版本全部函数的限定名（与图一致：顶层简单名、方法 Class.method）。"""
    qnames = set()
    for result in results.values():
        for f in result.functions:
            qnames.add(f.name)
        for cls in result.classes:
            for m in cls.methods:
                qnames.add(f"{cls.name}.{m.name}")
    return qnames


def _diff_sorted(a, b):
    """两个有序列表差集：返回 (b-a 新增, a-b 移除)，各自有序。"""
    return (sorted(set(b) - set(a)), sorted(set(a) - set(b)))

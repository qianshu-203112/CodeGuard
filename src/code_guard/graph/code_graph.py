"""
代码知识图谱 - SQLite 图存储与查询

负责：
1. 将 ParseResult 数据存入 SQLite 图结构
2. 提供图遍历查询（调用链、反向可达性、变更影响分析）
3. 导出为结构化数据供 LLM 回答使用
"""
import sqlite3
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any
from dataclasses import dataclass, field

from code_guard.parser.ast_parser import ParseResult, FunctionInfo, ClassInfo, CallInfo, ImportInfo


# ── SQL ──

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS functions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    file_id INTEGER NOT NULL,
    qualified_name TEXT NOT NULL,   -- 完整限定名: Class.method 或 module.function
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    docstring TEXT,
    is_method INTEGER DEFAULT 0,
    class_name TEXT,
    FOREIGN KEY (file_id) REFERENCES files(id)
);

CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    file_id INTEGER NOT NULL,
    qualified_name TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    docstring TEXT,
    base_classes TEXT,  -- JSON 数组
    FOREIGN KEY (file_id) REFERENCES files(id)
);

CREATE TABLE IF NOT EXISTS call_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    caller_func_id INTEGER NOT NULL,
    callee_name TEXT NOT NULL,       -- 被调用函数名（可能跨文件，不确定时用名字关联）
    caller_line INTEGER NOT NULL,
    FOREIGN KEY (caller_func_id) REFERENCES functions(id)
);

CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    names TEXT,          -- JSON 数组，from X import Y 时的 Y
    is_from INTEGER DEFAULT 0,
    file_id INTEGER NOT NULL,
    FOREIGN KEY (file_id) REFERENCES files(id)
);

-- 增量同步用：记录每个文件内容哈希，判断文件是否变更（不用 mtime，
-- checkout/换分支时 mtime 不可靠）。CREATE IF NOT EXISTS 保证旧库打开时自动补表。
CREATE TABLE IF NOT EXISTS file_hashes (
    path TEXT PRIMARY KEY,
    sha1 TEXT NOT NULL
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_functions_file ON functions(file_id);
CREATE INDEX IF NOT EXISTS idx_functions_name ON functions(name);
CREATE INDEX IF NOT EXISTS idx_functions_qualified ON functions(qualified_name);
CREATE INDEX IF NOT EXISTS idx_call_edges_caller ON call_edges(caller_func_id);
CREATE INDEX IF NOT EXISTS idx_call_edges_callee ON call_edges(callee_name);
CREATE INDEX IF NOT EXISTS idx_classes_file ON classes(file_id);
"""


def _is_callee_reference(callee: str, function_name: str, simple_name: str) -> bool:
    """判断 call_edges.callee_name 是否引用了 function_name。

    全部按标识符边界匹配，避免 runner/rerun 这类"包含子串"的名字被
    误当成对 run 的调用：
      1. 完全相等（`::` 与 `.` 先归一化，game::addScore == game.addScore）
      2. 限定名后缀: callee 以 `.function_name` 结尾（如 Game.run）
      3. 限定名前缀: callee 以 `function_name.` 开头（如 Game 下所有方法）
      4. 词边界包含: callee 中出现 function_name 且前后不是标识符字符
         （兼容 C++ 的 `game::addScore` 这类由解析器产出的限定名）
      5. 简单名兜底: 目标是限定名时，用简单名做同样的词边界匹配
         （兼容 callee 存简单名，如 agent.run 匹配 DataAnalysisAgent.run）

    硬限定区分：callee 含 `::` 表示调用点显式点名了 namespace——此时只接受
    完整限定名层面（1~4）的命中，禁止简单名兜底，否则查询 `game.addScore`
    会把 `audio::addScore` 的调用边也捞进来（两个 namespace 同名函数互混）。
    """
    # `::` → `.` 归一化：两种书写在限定名语义上等价
    callee_norm = callee.replace("::", ".")
    function_norm = function_name.replace("::", ".")

    if callee_norm == function_norm:
        return True
    if callee_norm.endswith("." + function_norm) or callee_norm.startswith(function_norm + "."):
        return True
    if re.search(r"(?<![\w])" + re.escape(function_norm) + r"(?![\w])", callee_norm):
        return True
    # 含 `::` 的硬限定调用不接受简单名兜底，避免不同 namespace 同名函数互混
    if simple_name != function_norm and "::" not in callee:
        return bool(re.search(r"(?<![\w])" + re.escape(simple_name) + r"(?![\w])", callee_norm))
    return False


class CodeGraph:
    """代码知识图谱"""

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_db()

    def _init_db(self):
        """初始化数据库表"""
        self.conn.executescript(CREATE_TABLES_SQL)
        self.conn.commit()

    # ── 数据加载 ──

    def _get_or_create_file(self, file_path: str) -> int:
        """获取或创建文件记录，返回 file_id"""
        norm_path = Path(file_path).as_posix()
        name = Path(file_path).name
        self.conn.execute(
            "INSERT OR IGNORE INTO files (path, name) VALUES (?, ?)",
            (norm_path, name)
        )
        row = self.conn.execute("SELECT id FROM files WHERE path = ?", (norm_path,)).fetchone()
        return row[0]

    def load_parse_result(self, result: ParseResult) -> Dict[str, int]:
        """
        将一个文件的解析结果载入图。

        Returns:
            {"file_id": ..., "func_ids": {"func_name": id, ...}, "class_ids": ...}
        """
        file_id = self._get_or_create_file(result.file_path)
        func_ids = {}
        class_ids = {}

        # 导入
        for imp in result.imports:
            self.conn.execute(
                "INSERT INTO imports (source, names, is_from, file_id) VALUES (?, ?, ?, ?)",
                (imp.source, str(imp.names) if imp.names else None, int(imp.is_from), file_id)
            )

        # 类
        for cls in result.classes:
            cur = self.conn.execute(
                """INSERT INTO classes (name, file_id, qualified_name, start_line, end_line,
                                        docstring, base_classes)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (cls.name, file_id, cls.name, cls.start_line, cls.end_line,
                 cls.docstring, str(cls.base_classes) if cls.base_classes else None)
            )
            class_ids[cls.name] = cur.lastrowid

            # 类方法
            for method in cls.methods:
                qualified = f"{cls.name}.{method.name}"
                cur = self.conn.execute(
                    """INSERT INTO functions (name, file_id, qualified_name, start_line, end_line,
                                              docstring, is_method, class_name)
                       VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
                    (method.name, file_id, qualified, method.start_line, method.end_line,
                     method.docstring, cls.name)
                )
                func_ids[qualified] = cur.lastrowid

        # 顶层函数
        for func in result.functions:
            cur = self.conn.execute(
                """INSERT INTO functions (name, file_id, qualified_name, start_line, end_line,
                                          docstring, is_method, class_name)
                   VALUES (?, ?, ?, ?, ?, ?, 0, NULL)""",
                (func.name, file_id, func.name, func.start_line, func.end_line,
                 func.docstring)
            )
            func_ids[func.name] = cur.lastrowid

        # 调用边
        for call in result.calls:
            # 找到 caller 的 func_id（caller_name 可能是简单名或 Class.method 限定名）
            caller_id = func_ids.get(call.caller_name)
            if caller_id is None:
                # 兜底：caller_name 存的是简单名时，匹配限定名后缀
                for qname, fid in func_ids.items():
                    if qname.endswith("." + call.caller_name) or qname == call.caller_name:
                        caller_id = fid
                        break

            if caller_id:
                self.conn.execute(
                    "INSERT INTO call_edges (caller_func_id, callee_name, caller_line) VALUES (?, ?, ?)",
                    (caller_id, call.callee_name, call.caller_line)
                )

        self.conn.commit()
        return {
            "file_id": file_id,
            "func_ids": func_ids,
            "class_ids": class_ids,
        }

    def load_project(self, results: Dict[str, ParseResult]) -> None:
        """批量载入项目解析结果"""
        for file_path, result in results.items():
            self.load_parse_result(result)
        self.conn.commit()

    # ── 增量同步（删除 / 替换 / 内容哈希） ──

    def remove_file(self, file_path: str) -> None:
        """从图中移除一个文件的全部数据（增量同步用）。

        顺序敏感：call_edges 有 FK 指向 functions，必须先删调用边再删函数，
        否则 PRAGMA foreign_keys=ON 会因残留引用报错。
        """
        norm = Path(file_path).as_posix()
        row = self.conn.execute(
            "SELECT id FROM files WHERE path = ?", (norm,)).fetchone()
        if row is None:
            return
        file_id = row[0]

        self.conn.execute(
            "DELETE FROM call_edges WHERE caller_func_id IN "
            "(SELECT id FROM functions WHERE file_id = ?)", (file_id,))
        self.conn.execute(
            "DELETE FROM functions WHERE file_id = ?", (file_id,))
        self.conn.execute(
            "DELETE FROM classes WHERE file_id = ?", (file_id,))
        self.conn.execute(
            "DELETE FROM imports WHERE file_id = ?", (file_id,))
        self.conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        self.conn.execute("DELETE FROM file_hashes WHERE path = ?", (norm,))
        self.conn.commit()

    def replace_file(self, file_path: str, result: ParseResult) -> None:
        """原子替换一个文件：移除旧数据后载入新解析结果。"""
        self.remove_file(file_path)
        self.load_parse_result(result)
        self.conn.commit()

    def set_file_hash(self, file_path: str, sha1: str) -> None:
        """记录文件内容哈希。"""
        self.conn.execute(
            "INSERT OR REPLACE INTO file_hashes (path, sha1) VALUES (?, ?)",
            (Path(file_path).as_posix(), sha1))
        self.conn.commit()

    def get_file_hash(self, file_path: str) -> Optional[str]:
        """读取文件内容哈希；从未记录过返回 None。"""
        row = self.conn.execute(
            "SELECT sha1 FROM file_hashes WHERE path = ?",
            (Path(file_path).as_posix(),)).fetchone()
        return row[0] if row else None

    def delete_file_hash(self, file_path: str) -> None:
        """删除一个文件哈希（文件从磁盘消失时）。"""
        self.conn.execute(
            "DELETE FROM file_hashes WHERE path = ?",
            (Path(file_path).as_posix(),))
        self.conn.commit()

    def list_indexed_files(self) -> Set[str]:
        """返回图中已索引的全部文件路径（绝对路径的 posix 形式）。"""
        rows = self.conn.execute("SELECT path FROM files").fetchall()
        return {r[0] for r in rows}

    # ── 查询 ──

    def get_callers(self, function_name: str) -> List[Dict[str, Any]]:
        """
        查询谁调用了指定函数。

        Returns:
            [{"caller": "ToolDrivenAgentLoop.run", "file": "backend/agent/tool_driven_loop.py", "line": 350}, ...]
        """
        # 如果是限定名（如 "ClassName.method_name" 或 "game::addScore"），
        # 提取最后的简单名作为备用
        simple_name = function_name.replace("::", ".").split(".")[-1]

        # SQL 先宽松捞取（精确 + 前/后缀），再在 Python 里做标识符边界过滤。
        # 直接 LIKE '%name%' 会把 runner/rerun 这类"包含子串"的无关联误判成
        # 对 run 的调用；_is_callee_reference 只保留 name 前后是分隔符的命中。
        rows = self.conn.execute("""
            SELECT f.qualified_name, fl.path, ce.caller_line, ce.callee_name
            FROM call_edges ce
            JOIN functions f ON ce.caller_func_id = f.id
            JOIN files fl ON f.file_id = fl.id
            WHERE ce.callee_name = ?
               OR ce.callee_name LIKE ? OR ce.callee_name LIKE ?
               OR ce.callee_name LIKE ? OR ce.callee_name LIKE ?
            ORDER BY fl.path, ce.caller_line
        """, (function_name,
              f"%{function_name}", f"{function_name}%",
              f"%{simple_name}", f"{simple_name}%")).fetchall()

        results = []
        for caller_name, file_path, line, callee_name in rows:
            if _is_callee_reference(callee_name, function_name, simple_name):
                results.append({"caller": caller_name, "file": file_path, "line": line})
        return results

    def get_callees(self, function_name: str) -> List[Dict[str, Any]]:
        """
        查询指定函数调用了哪些函数。

        Returns:
            [{"callee": "load_data", "line": 311}, ...]
        """
        simple_name = function_name.replace("::", ".").split(".")[-1]

        rows = self.conn.execute("""
            SELECT DISTINCT ce.callee_name, ce.caller_line, f.name, f.qualified_name
            FROM call_edges ce
            JOIN functions f ON ce.caller_func_id = f.id
            WHERE f.name = ? OR f.qualified_name = ?
               OR f.qualified_name LIKE ? OR f.qualified_name LIKE ?
            ORDER BY ce.caller_line
        """, (function_name, function_name,
              f"%{function_name}", f"{function_name}%")).fetchall()

        # Python 边界过滤：qualified_name 需以 .name 结尾或以 name. 开头，
        # 避免把 rerun/moonrun 这类包含子串的名字算成对 run 的调用。
        results = []
        seen = set()
        for callee_name, line, fname, fqname in rows:
            ok = (fname == function_name or fqname == function_name
                  or fqname.endswith("." + function_name)
                  or (simple_name != function_name and fqname.endswith("." + simple_name)))
            if ok:
                key = (callee_name, line)
                if key not in seen:
                    seen.add(key)
                    results.append({"callee": callee_name, "line": line})
        return results

    def get_functions_in_file(self, file_path: str) -> List[Dict[str, Any]]:
        """查询文件中的函数"""
        rows = self.conn.execute("""
            SELECT f.qualified_name, f.start_line, f.end_line, f.is_method, f.class_name
            FROM functions f
            JOIN files fl ON f.file_id = fl.id
            WHERE fl.path = ?
            ORDER BY f.start_line
        """, (Path(file_path).as_posix(),)).fetchall()

        return [
            {"name": row[0], "start": row[1], "end": row[2],
             "is_method": bool(row[3]), "class": row[4]}
            for row in rows
        ]

    def get_classes_in_file(self, file_path: str) -> List[Dict[str, Any]]:
        """查询文件中的类"""
        rows = self.conn.execute("""
            SELECT c.name, c.start_line, c.end_line, c.base_classes
            FROM classes c
            JOIN files fl ON c.file_id = fl.id
            WHERE fl.path = ?
            ORDER BY c.start_line
        """, (Path(file_path).as_posix(),)).fetchall()

        return [
            {"name": row[0], "start": row[1], "end": row[2],
             "base_classes": row[3]}
            for row in rows
        ]

    def search_functions(self, keyword: str) -> List[Dict[str, Any]]:
        """搜索函数名"""
        rows = self.conn.execute("""
            SELECT f.qualified_name, fl.path, f.start_line
            FROM functions f
            JOIN files fl ON f.file_id = fl.id
            WHERE f.name LIKE ? OR f.qualified_name LIKE ?
            ORDER BY fl.path, f.start_line
            LIMIT 30
        """, (f"%{keyword}%", f"%{keyword}%")).fetchall()

        return [
            {"name": row[0], "file": row[1], "line": row[2]}
            for row in rows
        ]

    def search_files(self, filename: str) -> List[Dict[str, Any]]:
        """按文件名搜索文件"""
        rows = self.conn.execute("""
            SELECT path, name FROM files
            WHERE name LIKE ? OR path LIKE ?
            ORDER BY path
            LIMIT 10
        """, (f"%{filename}%", f"%{filename}%")).fetchall()

        return [
            {"path": row[0], "name": row[1]}
            for row in rows
        ]

    # ── 核心算法：变更影响分析 ──

    def analyze_change_impact(self, function_name: str, max_depth: int = 3) -> Dict[str, Any]:
        """
        变更影响分析 - 反向可达性分析。

        如果修改了 function_name，哪些函数/测试会受影响？

        Args:
            function_name: 被修改的函数名
            max_depth: 最大追溯深度

        Returns:
            {
                "target": "calculate_fee",
                "direct_callers": [...],
                "all_affected": [...],
                "affected_files": [...],
                "affected_tests": [...]
            }
        """
        # 第一层直接调用者
        direct_callers = self.get_callers(function_name)

        # 多级调用者（反向可达性）
        # 用 enqueued 集合去重：旧实现只在节点弹出时才标记 visited，菱形依赖里
        # 尚未弹出就被另一条路径再次扫描的节点会重复计入 all_affected，导致
        # 受影响函数计数虚高。
        enqueued = {function_name}
        all_affected = []
        queue = [(function_name, 0)]

        while queue:
            current, depth = queue.pop(0)
            if depth > max_depth:
                continue

            callers = self.get_callers(current)
            for caller in callers:
                caller_name = caller["caller"]
                if caller_name not in enqueued:
                    enqueued.add(caller_name)
                    all_affected.append({**caller, "depth": depth + 1})
                    queue.append((caller_name, depth + 1))

        # 找出测试文件
        affected_tests = []
        affected_files_set = set()
        for affected in all_affected:
            file_path = affected["file"]
            if "test" in Path(file_path).name.lower() or "test" in Path(file_path).parent.name.lower():
                affected_tests.append(affected)
            affected_files_set.add(file_path)

        return {
            "target": function_name,
            "direct_callers": direct_callers,
            "all_affected": all_affected,
            "affected_files": sorted(affected_files_set),
            "affected_tests": affected_tests,
        }

    # ── 信息获取 ──

    def get_function_detail(self, function_name: str) -> Optional[Dict[str, Any]]:
        """获取函数详细信息"""
        row = self.conn.execute("""
            SELECT f.qualified_name, fl.path, f.start_line, f.end_line,
                   f.docstring, f.is_method, f.class_name
            FROM functions f
            JOIN files fl ON f.file_id = fl.id
            WHERE f.qualified_name = ? OR f.name = ?
            LIMIT 1
        """, (function_name, function_name)).fetchone()

        if not row:
            return None

        return {
            "name": row[0],
            "file": row[1],
            "start_line": row[2],
            "end_line": row[3],
            "docstring": row[4],
            "is_method": bool(row[5]),
            "class": row[6],
        }

    def get_stats(self) -> Dict[str, int]:
        """获取图统计信息"""
        files = self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        functions = self.conn.execute("SELECT COUNT(*) FROM functions").fetchone()[0]
        classes = self.conn.execute("SELECT COUNT(*) FROM classes").fetchone()[0]
        calls = self.conn.execute("SELECT COUNT(*) FROM call_edges").fetchone()[0]
        imports = self.conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
        return {
            "files": files,
            "functions": functions,
            "classes": classes,
            "calls": calls,
            "imports": imports,
        }

    def close(self):
        self.conn.close()

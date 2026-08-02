"""
Rust 代码解析器 - 基于 Tree-sitter 解析 Rust 源码

与 Python 解析器输出相同的 ParseResult 结构，共享图模块。

规则：
  - `fn` → FunctionInfo；impl 内方法限定为 `Type.method`
  - struct / enum / union / trait → ClassInfo
  - call_expression → CallInfo
  - use → ImportInfo
  - DFS 整树遍历（含 ERROR 抢救），嵌套 mod 自然深入
"""
import os
from pathlib import Path
from typing import Dict, List, Optional

import tree_sitter_rust as tsrust
from tree_sitter import Language, Parser, Node

from code_guard.parser.ast_parser import (
    ParseResult, FunctionInfo, ClassInfo, CallInfo, ImportInfo,
    _get_node_text,
)


# ── 全局初始化 ──
_LANGUAGE = Language(tsrust.language())
_PARSER = Parser(_LANGUAGE)


def parse_rs_file(file_path: str) -> ParseResult:
    """
    解析一个 Rust 文件，提取所有代码结构信息。
    """
    result = ParseResult(file_path)

    # 自动检测编码（Windows 下中文系统常用 GBK）
    encodings = ["utf-8", "gbk", "gb2312", "gb18030", "latin-1"]
    source_code = None
    for enc in encodings:
        try:
            with open(file_path, "r", encoding=enc) as f:
                source_code = f.read()
            break
        except UnicodeDecodeError:
            continue
    if source_code is None:
        raise UnicodeDecodeError(f"无法解码文件: {file_path}")

    tree = _PARSER.parse(bytes(source_code, "utf8"))
    root = tree.root_node

    result.imports = _extract_rs_uses(root)
    _walk_rs(root, file_path, result)
    return result


def _walk_rs(node: Node, file_path: str, result: ParseResult,
             inside_impl: Optional[str] = None):
    """DFS 收集函数/类型。impl 内的 fn 方法限定为 Type.method。"""
    if node.type == "function_item":
        name_node = node.child_by_field_name("name")
        if name_node:
            name = _get_node_text(name_node)
            qname = f"{inside_impl}.{name}" if inside_impl else name
            body = node.child_by_field_name("body")
            end_line = body.end_point[0] + 1 if body else node.end_point[0] + 1
            result.functions.append(FunctionInfo(
                name=qname, file_path=file_path,
                start_line=node.start_point[0] + 1, end_line=end_line))
            result.calls.extend(_extract_rs_calls(node, qname, file_path))
        return

    if node.type in ("struct_item", "enum_item", "union_item", "trait_item"):
        name_node = node.child_by_field_name("name")
        cname = _get_node_text(name_node) if name_node else "anonymous"
        body = node.child_by_field_name("body")
        end_line = body.end_point[0] + 1 if body else node.end_point[0] + 1
        result.classes.append(ClassInfo(
            name=cname, file_path=file_path,
            start_line=node.start_point[0] + 1, end_line=end_line))
        for c in node.children:
            _walk_rs(c, file_path, result, inside_impl=None)
        return

    if node.type == "impl_item":
        type_node = node.child_by_field_name("type")
        type_name = _get_node_text(type_node) if type_node else ""
        for c in node.children:
            _walk_rs(c, file_path, result, inside_impl=type_name or None)
        return

    for c in node.children:
        _walk_rs(c, file_path, result, inside_impl=inside_impl)


# ── import（use） ──


def _extract_rs_uses(root: Node) -> List[ImportInfo]:
    """提取所有 use 声明（可能嵌套在 mod 里，用栈遍历）。"""
    imports = []
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type == "use_declaration":
            arg = n.child_by_field_name("argument")
            if arg:
                imports.append(ImportInfo(
                    source=_get_node_text(arg), names=[], is_from=False))
        for i in range(n.child_count):
            stack.append(n.child(i))
    return imports


# ── 调用提取 ──


def _extract_rs_calls(node: Node, enclosing_function: str,
                      file_path: str) -> List[CallInfo]:
    calls = []
    _rs_calls_recursive(node, enclosing_function, file_path, calls, is_root=True)
    return calls


def _rs_calls_recursive(node: Node, enclosing_function: str,
                        file_path: str, calls: List[CallInfo],
                        is_root: bool = True):
    """递归提取函数调用（不进入嵌套的 fn 声明，除非是根节点）。"""
    if node.type == "call_expression":
        func_node = node.child_by_field_name("function")
        if func_node:
            calls.append(CallInfo(
                caller_name=enclosing_function,
                caller_file=file_path,
                caller_line=node.start_point[0] + 1,
                callee_name=_get_node_text(func_node),
                callee_line=None,
            ))
        for i in range(node.child_count):
            _rs_calls_recursive(node.child(i), enclosing_function,
                                file_path, calls, is_root=False)
        return

    if is_root or node.type != "function_item":
        for i in range(node.child_count):
            _rs_calls_recursive(node.child(i), enclosing_function,
                                file_path, calls, is_root=False)


# ── 项目级入口 ──


def parse_rs_project(project_path: str) -> Dict[str, ParseResult]:
    """递归解析整个 Rust 项目。"""
    results = {}
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in (
            "venv", "node_modules", "__pycache__", ".git", ".idea",
            "dist", "build", ".mypy_cache", ".pytest_cache", ".tox",
            "target", "vendor",
        )]
        for f in sorted(files):
            if f.endswith(".rs"):
                file_path = os.path.join(root, f)
                try:
                    result = parse_rs_file(file_path)
                    results[file_path] = result
                except Exception as e:
                    print(f"  ⚠️  跳过 {file_path}: {e}")
    return results


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "."
    results = parse_rs_project(path)
    print(f"解析了 {len(results)} 个 Rust 文件")
    for fp, r in results.items():
        from code_guard.parser.ast_parser import print_parse_result
        print_parse_result(r)

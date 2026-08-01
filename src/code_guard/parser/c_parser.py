"""
C 代码解析器 - 基于 Tree-sitter 解析 C 源码

与 Python 解析器输出相同的 ParseResult 结构，共享图模块。
"""
import os
from pathlib import Path
from typing import Dict, List, Optional

import tree_sitter_c as tsc
from tree_sitter import Language, Parser, Node

from code_guard.parser.ast_parser import (
    ParseResult, FunctionInfo, ClassInfo, CallInfo, ImportInfo,
    _get_node_text,
)

# ── 全局初始化 ──
_LANGUAGE = Language(tsc.language())
_PARSER = Parser(_LANGUAGE)


def parse_c_file(file_path: str) -> ParseResult:
    """
    解析一个 C 文件，提取所有代码结构信息。

    C 没有类的概念，但有函数定义、结构体/联合体、函数调用。

    Args:
        file_path: C 文件的绝对路径

    Returns:
        ParseResult 包含函数、类（结构体）、调用、导入信息
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

    # 提取 include 信息
    result.imports = _extract_c_includes(root)

    # DFS 遍历整棵树（含 ERROR 恢复节点），避免解析错误吞掉后续函数。
    # 之前只遍历顶层 root.children：当 C++ 语法（如 node{4,7} 花括号初始化）导致
    # tree-sitter 从某个函数开始整段变 ERROR 节点时，该函数及其后的所有
    # 顶层函数都会被吞掉（如贪吃蛇.cpp 的 main/reset/displayMesseage）。
    _walk_c_nodes(root, file_path, result)

    return result


def _walk_c_nodes(node: Node, file_path: str, result: ParseResult,
                  inside_function: bool = False):
    """递归收集函数定义和结构体，能救回 ERROR 节点中被吞掉的函数。"""
    if node.type == "function_definition":
        func = _extract_c_function(node, file_path)
        if func:
            result.functions.append(func)
            result.calls.extend(_extract_c_calls_in_node(node, func.name, file_path))
        # 函数体内部的结构体不入类集合（保持原有"只收顶层类型"的语义）
        for c in node.children:
            _walk_c_nodes(c, file_path, result, inside_function=True)
        return

    if not inside_function and node.type in ("struct_specifier", "union_specifier"):
        cls = _extract_c_struct(node, file_path)
        if cls:
            result.classes.append(cls)
        return

    # ERROR 节点可能是被 C++ 语法打断的函数定义，尝试抢救
    if node.type == "ERROR" and not inside_function:
        func = _try_extract_error_function(node, file_path)
        if func:
            result.functions.append(func)
            # 只提取 ERROR 节点自身层级内的调用；不会下钻到嵌套的
            # function_definition（_extract_c_calls_recursive 会跳过它们）
            result.calls.extend(_extract_c_calls_in_node(node, func.name, file_path))

    for c in node.children:
        _walk_c_nodes(c, file_path, result, inside_function=inside_function)


def _try_extract_error_function(node: Node, file_path: str) -> Optional[FunctionInfo]:
    """从 ERROR 节点抢救函数：含 function_declarator + compound_statement 即视为函数。"""
    declarator = None
    body = None
    for c in node.children:
        if c.type == "function_declarator":
            declarator = c
        elif c.type == "compound_statement":
            body = c
    if declarator is None:
        return None
    name = _find_c_function_name(declarator)
    if not name:
        return None
    end_line = body.end_point[0] + 1 if body else node.end_point[0] + 1
    return FunctionInfo(
        name=name, file_path=file_path,
        start_line=node.start_point[0] + 1,
        end_line=end_line,
    )


def _extract_c_includes(root: Node) -> List[ImportInfo]:
    """提取 C 的 #include 语句"""
    imports = []
    for child in root.children:
        if child.type == "preproc_include":
            path_node = child.child_by_field_name("path")
            if path_node:
                source = _get_node_text(path_node)
                imports.append(ImportInfo(source=source, names=[], is_from=False))
    return imports


def _extract_c_function(node: Node, file_path: str) -> Optional[FunctionInfo]:
    """提取 C 函数信息"""
    if node.type != "function_definition":
        return None
    declarator = node.child_by_field_name("declarator")
    if not declarator:
        return None
    # 处理函数声明器（可能嵌套 function_declarator > identifier）
    name = _find_c_function_name(declarator)
    if not name:
        return None
    body = node.child_by_field_name("body")
    end_line = body.end_point[0] + 1 if body else node.end_point[0] + 1
    return FunctionInfo(
        name=name, file_path=file_path,
        start_line=node.start_point[0] + 1,
        end_line=end_line,
    )


def _find_c_function_name(node: Node) -> Optional[str]:
    """递归查找 C 函数名"""
    if node.type == "identifier":
        return _get_node_text(node)
    for i in range(node.child_count):
        result = _find_c_function_name(node.child(i))
        if result:
            return result
    return None


def _extract_c_struct(node: Node, file_path: str) -> Optional[ClassInfo]:
    """提取 C 结构体/联合体为类信息"""
    name_node = node.child_by_field_name("name")
    name = _get_node_text(name_node) if name_node else "anonymous"

    body = node.child_by_field_name("body")
    fields = []
    if body:
        for child in body.children:
            if child.type == "field_declaration":
                for grandchild in child.children:
                    if grandchild.type == "identifier":
                        fields.append(_get_node_text(grandchild))

    return ClassInfo(
        name=name, file_path=file_path,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        methods=[],
    )


def _extract_c_calls_in_node(node: Node, enclosing_function: str,
                             file_path: str) -> List[CallInfo]:
    """递归提取 C 中的函数调用"""
    calls = []
    _extract_c_calls_recursive(node, enclosing_function, file_path, calls, is_root=True)
    return calls


def _extract_c_calls_recursive(node: Node, enclosing_function: str,
                               file_path: str, calls: List[CallInfo],
                               is_root: bool = True):
    """递归提取函数调用"""
    if node.type == "call_expression":
        func_node = node.child_by_field_name("function")
        if func_node:
            callee = _get_node_text(func_node)
            calls.append(CallInfo(
                caller_name=enclosing_function,
                caller_file=file_path,
                caller_line=node.start_point[0] + 1,
                callee_name=callee,
                callee_line=None,
            ))
        for i in range(node.child_count):
            _extract_c_calls_recursive(node.child(i), enclosing_function,
                                       file_path, calls, is_root=False)
        return

    if is_root or node.type != "function_definition":
        for i in range(node.child_count):
            _extract_c_calls_recursive(node.child(i), enclosing_function,
                                       file_path, calls, is_root=False)


def parse_c_project(project_path: str) -> Dict[str, ParseResult]:
    """递归解析整个 C 项目。"""
    results = {}
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in (
            "venv", "node_modules", "__pycache__", ".git", ".idea",
            "dist", "build", ".mypy_cache", ".pytest_cache", ".tox",
        )]
        for f in sorted(files):
            if f.endswith((".c", ".h")):
                file_path = os.path.join(root, f)
                try:
                    result = parse_c_file(file_path)
                    results[file_path] = result
                except Exception as e:
                    print(f"  ⚠️  跳过 {file_path}: {e}")
    return results


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "."
    results = parse_c_project(path)
    print(f"解析了 {len(results)} 个 C 文件")
    for fp, r in results.items():
        from code_guard.parser.ast_parser import print_parse_result
        print_parse_result(r)

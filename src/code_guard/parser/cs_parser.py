"""
C# 代码解析器 - 基于 Tree-sitter 解析 C# 源码

与 Python 解析器输出相同的 ParseResult 结构，共享图模块。

规则：
  - class / struct / interface / record / enum → ClassInfo
  - 类内方法限定为 `Type.method`（与 java_parser 简化程度一致，
    namespace 前缀暂不做——真实企业项目 namespace 很常见，属已知简化）
  - invocation_expression → CallInfo
  - using → ImportInfo
  - DFS 整树遍历（含 ERROR 抢救）
"""
import os
from pathlib import Path
from typing import Dict, List, Optional

import tree_sitter_c_sharp as tscs
from tree_sitter import Language, Parser, Node

from code_guard.parser.ast_parser import (
    ParseResult, FunctionInfo, ClassInfo, CallInfo, ImportInfo,
    _get_node_text,
)


# ── 全局初始化 ──
_LANGUAGE = Language(tscs.language())
_PARSER = Parser(_LANGUAGE)

# C# 类型声明节点：统一当 ClassInfo
_TYPE_NODES = ("class_declaration", "struct_declaration",
               "interface_declaration", "record_declaration",
               "enum_declaration")
# 方法类声明节点：统一当 FunctionInfo（限定 Type.method）
_METHOD_NODES = ("method_declaration", "constructor_declaration",
                 "destructor_declaration")


def parse_cs_file(file_path: str) -> ParseResult:
    """
    解析一个 C# 文件，提取所有代码结构信息。
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

    result.imports = _extract_cs_usings(root)
    _walk_cs(root, file_path, result)
    return result


def _walk_cs(node: Node, file_path: str, result: ParseResult,
             inside_class: Optional[str] = None):
    """DFS 收集类型/方法。类内方法限定为 Type.method。"""
    if node.type in _METHOD_NODES:
        name_node = node.child_by_field_name("name")
        if name_node:
            mname = _get_node_text(name_node)
            qname = f"{inside_class}.{mname}" if inside_class else mname
            body = node.child_by_field_name("body")
            end_line = body.end_point[0] + 1 if body else node.end_point[0] + 1
            result.functions.append(FunctionInfo(
                name=qname, file_path=file_path,
                start_line=node.start_point[0] + 1, end_line=end_line))
            result.calls.extend(_extract_cs_calls(node, qname, file_path))
        return

    if node.type in _TYPE_NODES:
        name_node = node.child_by_field_name("name")
        cname = _get_node_text(name_node) if name_node else "anonymous"
        body = node.child_by_field_name("body")
        end_line = body.end_point[0] + 1 if body else node.end_point[0] + 1
        result.classes.append(ClassInfo(
            name=cname, file_path=file_path,
            start_line=node.start_point[0] + 1, end_line=end_line))
        for c in node.children:
            _walk_cs(c, file_path, result, inside_class=cname)
        return

    for c in node.children:
        _walk_cs(c, file_path, result, inside_class=inside_class)


# ── import（using） ──


def _extract_cs_usings(root: Node) -> List[ImportInfo]:
    """提取所有 using 指令（可能嵌套在 namespace 里，用栈遍历）。

    using_directive 无 name 字段，结构为 [using, identifier/qualified_name, ;]，
    直接找 identifier / qualified_name 子节点。
    """
    imports = []
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type == "using_directive":
            for c in n.children:
                if c.type in ("identifier", "qualified_name", "alias_qualified_name"):
                    imports.append(ImportInfo(
                        source=_get_node_text(c), names=[], is_from=False))
                    break
        for i in range(n.child_count):
            stack.append(n.child(i))
    return imports


# ── 调用提取 ──


def _extract_cs_calls(node: Node, enclosing_function: str,
                      file_path: str) -> List[CallInfo]:
    calls = []
    _cs_calls_recursive(node, enclosing_function, file_path, calls, is_root=True)
    return calls


def _cs_calls_recursive(node: Node, enclosing_function: str,
                        file_path: str, calls: List[CallInfo],
                        is_root: bool = True):
    """递归提取函数调用（不进入嵌套的方法声明，除非是根节点）。"""
    if node.type == "invocation_expression":
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
            _cs_calls_recursive(node.child(i), enclosing_function,
                                file_path, calls, is_root=False)
        return

    if is_root or node.type not in _METHOD_NODES:
        for i in range(node.child_count):
            _cs_calls_recursive(node.child(i), enclosing_function,
                                file_path, calls, is_root=False)


# ── 项目级入口 ──


def parse_cs_project(project_path: str) -> Dict[str, ParseResult]:
    """递归解析整个 C# 项目。"""
    results = {}
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in (
            "venv", "node_modules", "__pycache__", ".git", ".idea",
            "dist", "build", ".mypy_cache", ".pytest_cache", ".tox",
            "obj", "bin", "packages",
        )]
        for f in sorted(files):
            if f.endswith(".cs"):
                file_path = os.path.join(root, f)
                try:
                    result = parse_cs_file(file_path)
                    results[file_path] = result
                except Exception as e:
                    print(f"  ⚠️  跳过 {file_path}: {e}")
    return results


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "."
    results = parse_cs_project(path)
    print(f"解析了 {len(results)} 个 C# 文件")
    for fp, r in results.items():
        from code_guard.parser.ast_parser import print_parse_result
        print_parse_result(r)

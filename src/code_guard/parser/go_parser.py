"""
Go 代码解析器 - 基于 Tree-sitter 解析 Go 源码

与 Python 解析器输出相同的 ParseResult 结构，共享图模块。
"""
import os
from pathlib import Path
from typing import Dict, List, Optional

import tree_sitter_go as tsgo
from tree_sitter import Language, Parser, Node

from code_guard.parser.ast_parser import (
    ParseResult, FunctionInfo, ClassInfo, CallInfo, ImportInfo,
    _get_node_text,
)


# ── 全局初始化 ──
_LANGUAGE = Language(tsgo.language())
_PARSER = Parser(_LANGUAGE)


def parse_go_file(file_path: str) -> ParseResult:
    """
    解析一个 Go 文件，提取所有代码结构信息。
    """
    result = ParseResult(file_path)

    with open(file_path, "r", encoding="utf-8") as f:
        source_code = f.read()

    tree = _PARSER.parse(bytes(source_code, "utf8"))
    root = tree.root_node

    # 提取 import
    result.imports = _extract_go_imports(root)

    # 第一遍：收集所有 type 定义（struct/interface）
    type_map = {}  # type_name -> ClassInfo
    for node in root.children:
        if node.type == "type_declaration":
            for child in node.children:
                if child.type == "type_spec":
                    cls = _extract_go_type_spec(child, file_path)
                    if cls:
                        result.classes.append(cls)
                        type_map[cls.name] = cls

    # 第二遍：提取函数、方法、调用
    for node in root.children:
        if node.type == "function_declaration":
            func = _extract_go_function(node, file_path)
            if func:
                result.functions.append(func)
                calls = _extract_go_calls(node, func.name, file_path)
                result.calls.extend(calls)

        elif node.type == "method_declaration":
            func, owner = _extract_go_method(node, file_path)
            if func:
                result.functions.append(func)
                # 如果接收者类型已识别为 struct/interface，把方法挂上去
                if owner and owner in type_map:
                    type_map[owner].methods.append(func)
                calls = _extract_go_calls(node, func.name, file_path)
                result.calls.extend(calls)

    return result


# ── import ──


def _extract_go_imports(root: Node) -> List[ImportInfo]:
    """提取 Go import 语句"""
    imports = []
    for child in root.children:
        if child.type == "import_declaration":
            # 递归查找所有 import_spec 节点（可能在 import_spec_list 内或直接作为子节点）
            _collect_import_specs(child, imports)
    return imports


def _collect_import_specs(node: Node, imports: List[ImportInfo]):
    """递归收集 import_spec 中的导入路径"""
    if node.type == "import_spec":
        for leaf in node.children:
            if leaf.type in ("interpreted_string_literal", "raw_string_literal"):
                source = _get_node_text(leaf).strip('"`')
                imports.append(ImportInfo(source=source, names=[], is_from=False))
                return
    for i in range(node.child_count):
        _collect_import_specs(node.child(i), imports)


# ── 函数 ──


def _get_method_receiver_type(node: Node) -> Optional[str]:
    """提取方法声明中的接收者类型名（去掉指针标记）"""
    receiver = node.child_by_field_name("receiver")
    if not receiver:
        return None
    for child in receiver.children:
        if child.type == "parameter_declaration":
            type_node = child.child_by_field_name("type")
            if type_node:
                text = _get_node_text(type_node)
                return text.lstrip("*")  # 去掉指针标记
    return None


def _extract_go_function(node: Node, file_path: str) -> Optional[FunctionInfo]:
    """提取 Go 函数信息"""
    if node.type != "function_declaration":
        return None
    name = _get_node_text(node.child_by_field_name("name"))
    if not name:
        return None
    body = node.child_by_field_name("body")
    end_line = body.end_point[0] + 1 if body else node.end_point[0] + 1
    return FunctionInfo(
        name=name,
        file_path=file_path,
        start_line=node.start_point[0] + 1,
        end_line=end_line,
    )


def _extract_go_method(node: Node, file_path: str):
    """
    提取 Go 方法信息（带接收者的函数）。

    Returns:
        (FunctionInfo, owner_type_name)
    """
    if node.type != "method_declaration":
        return None, None
    name = _get_node_text(node.child_by_field_name("name"))
    if not name:
        return None, None
    receiver_type = _get_method_receiver_type(node)
    qualified_name = f"{receiver_type}.{name}" if receiver_type else name
    body = node.child_by_field_name("body")
    end_line = body.end_point[0] + 1 if body else node.end_point[0] + 1
    return FunctionInfo(
        name=qualified_name,
        file_path=file_path,
        start_line=node.start_point[0] + 1,
        end_line=end_line,
    ), receiver_type


# ── 类型（struct/interface → ClassInfo）──


def _extract_go_type_spec(node: Node, file_path: str) -> Optional[ClassInfo]:
    """
    提取 Go 类型定义。
    struct → ClassInfo（含字段列表）
    interface → ClassInfo（含方法签名列表）
    """
    if node.type != "type_spec":
        return None
    name = _get_node_text(node.child_by_field_name("name"))
    if not name:
        return None
    type_node = node.child_by_field_name("type")
    if not type_node:
        return None

    methods = []

    if type_node.type == "struct_type":
        # struct 字段名暂不记录（ClassInfo 无 fields 属性），后续可扩展
        pass

    elif type_node.type == "interface_type":
        for child in type_node.children:
            if child.type == "method_elem":
                mname = None
                for name_node in child.children:
                    if name_node.type == "field_identifier":
                        mname = _get_node_text(name_node)
                        break
                if mname:
                    methods.append(FunctionInfo(
                        name=mname, file_path=file_path,
                        start_line=child.start_point[0] + 1,
                        end_line=child.end_point[0] + 1,
                    ))

    return ClassInfo(
        name=name,
        file_path=file_path,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        methods=methods,
    )


# ── 调用提取 ──


def _extract_go_calls(node: Node, enclosing_function: str,
                      file_path: str) -> List[CallInfo]:
    """提取 Go 函数/方法内部的调用"""
    calls = []
    _extract_go_calls_recursive(node, enclosing_function, file_path, calls, is_root=True)
    return calls


def _extract_go_calls_recursive(node: Node, enclosing_function: str,
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
        # 继续处理参数中的嵌套调用
        for i in range(node.child_count):
            _extract_go_calls_recursive(node.child(i), enclosing_function,
                                        file_path, calls, is_root=False)
        return

    # 继续递归（不进入新的函数/方法声明，除非是根节点）
    if is_root or node.type not in ("function_declaration", "method_declaration"):
        for i in range(node.child_count):
            _extract_go_calls_recursive(node.child(i), enclosing_function,
                                        file_path, calls, is_root=False)


# ── 项目级入口 ──


def parse_go_project(project_path: str) -> Dict[str, ParseResult]:
    """递归解析整个 Go 项目。"""
    results = {}
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in (
            "venv", "node_modules", "__pycache__", ".git", ".idea",
            "dist", "build", ".mypy_cache", ".pytest_cache", ".tox",
            "vendor", ".glide", "Godeps",
        )]
        for f in sorted(files):
            if f.endswith(".go"):
                file_path = os.path.join(root, f)
                try:
                    result = parse_go_file(file_path)
                    results[file_path] = result
                except Exception as e:
                    print(f"  ⚠️  跳过 {file_path}: {e}")
    return results


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "."
    results = parse_go_project(path)
    print(f"解析了 {len(results)} 个 Go 文件")
    for fp, r in results.items():
        from code_guard.parser.ast_parser import print_parse_result
        print_parse_result(r)

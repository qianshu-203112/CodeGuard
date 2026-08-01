"""
Java 代码解析器 - 基于 Tree-sitter 解析 Java 源码

与 Python 解析器输出相同的 ParseResult 结构，共享图模块。
"""
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import tree_sitter_java as tsjava
from tree_sitter import Language, Parser, Node

from code_guard.parser.ast_parser import (
    ParseResult, FunctionInfo, ClassInfo, CallInfo, ImportInfo,
    _get_node_text, _extract_docstring,
)

# ── 全局初始化 ──
_LANGUAGE = Language(tsjava.language())
_PARSER = Parser(_LANGUAGE)


def parse_java_file(file_path: str) -> ParseResult:
    """
    解析一个 Java 文件，提取所有代码结构信息。

    Args:
        file_path: Java 文件的绝对路径

    Returns:
        ParseResult 包含函数、类、调用、导入信息
    """
    result = ParseResult(file_path)

    # 自动检测编码
    encodings = ["utf-8", "gbk", "gb2312", "gb18030"]
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

    # 提取 import 信息
    result.imports = _extract_java_imports(root)

    # 遍历顶层节点，提取类和函数定义
    for node in root.children:
        if node.type == "class_declaration":
            cls = _extract_java_class(node, file_path)
            if cls:
                result.classes.append(cls)
                # 提取类方法内部的调用
                _extract_method_calls(node, cls.name, file_path, result.calls)
        elif node.type == "interface_declaration":
            cls = _extract_java_interface(node, file_path)
            if cls:
                result.classes.append(cls)
        elif node.type == "method_declaration":
            func = _extract_java_method(node, file_path)
            if func:
                result.functions.append(func)
                calls = _extract_java_calls_in_node(node, func.name, file_path)
                result.calls.extend(calls)

    return result


def _extract_java_imports(root: Node) -> List[ImportInfo]:
    """提取 Java import 语句"""
    imports = []
    for child in root.children:
        if child.type == "import_declaration":
            path_node = child.child_by_field_name("name")
            if path_node:
                source = _get_node_text(path_node)
                # import java.util.List → source="java.util.List"
                # import static java.util.Collections.sort → 略过 static
                imports.append(ImportInfo(source=source, names=[], is_from=False))
        elif child.type == "package_declaration":
            name_node = child.child_by_field_name("name")
            if name_node:
                source = _get_node_text(name_node)
                imports.append(ImportInfo(source=source, names=[], is_from=False))
    return imports


def _extract_java_class(node: Node, file_path: str) -> Optional[ClassInfo]:
    """提取 Java 类信息"""
    if node.type != "class_declaration":
        return None
    name = _get_node_text(node.child_by_field_name("name"))
    if not name:
        return None

    # 基类
    base_classes = []
    superclass = node.child_by_field_name("superclass")
    if superclass:
        base_classes.append(_get_node_text(superclass))
    interfaces = node.child_by_field_name("interfaces")
    if interfaces:
        for child in interfaces.children:
            if child.type in ("type_identifier", "scoped_type_identifier"):
                base_classes.append(_get_node_text(child))

    # 方法
    methods = []
    body = node.child_by_field_name("body")
    if body:
        for child in body.children:
            if child.type == "method_declaration":
                method = _extract_java_method(child, file_path)
                if method:
                    methods.append(method)
            elif child.type == "constructor_declaration":
                # 构造器当成方法处理
                cname = _get_node_text(child.child_by_field_name("name"))
                if cname:
                    doc = _extract_java_docstring(child)
                    start = child.start_point[0] + 1
                    end = child.end_point[0] + 1
                    methods.append(FunctionInfo(
                        name=cname, file_path=file_path,
                        start_line=start, end_line=end, docstring=doc
                    ))

    return ClassInfo(
        name=name, file_path=file_path,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        base_classes=base_classes, methods=methods,
        docstring=_extract_java_docstring(node) or _extract_docstring(node),
    )


def _extract_java_interface(node: Node, file_path: str) -> Optional[ClassInfo]:
    """提取 Java 接口信息"""
    if node.type != "interface_declaration":
        return None
    name = _get_node_text(node.child_by_field_name("name"))
    if not name:
        return None

    methods = []
    body = node.child_by_field_name("body")
    if body:
        for child in body.children:
            if child.type == "method_declaration":
                method = _extract_java_method(child, file_path)
                if method:
                    methods.append(method)

    return ClassInfo(
        name=name, file_path=file_path,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        base_classes=[], methods=methods,
        docstring=_extract_docstring(node),
    )


def _extract_java_docstring(node: Node) -> Optional[str]:
    """提取 Java 元素的 Javadoc 注释（当前节点之前的 /** ... */ 块注释）"""
    prev = node.prev_sibling
    # 跳过修饰符节点
    while prev and prev.type in ("modifiers", "annotation"):
        prev = prev.prev_sibling
    if prev and prev.type == "comment" and prev.text:
        text = prev.text.decode("utf-8").strip()
        if text.startswith("/**") or text.startswith("//"):
            return text
    return None


def _extract_java_method(node: Node, file_path: str) -> Optional[FunctionInfo]:
    """提取 Java 方法信息"""
    if node.type not in ("method_declaration", "constructor_declaration"):
        return None
    name = _get_node_text(node.child_by_field_name("name"))
    if not name:
        return None
    body = node.child_by_field_name("body")
    end_line = body.end_point[0] + 1 if body else node.end_point[0] + 1
    return FunctionInfo(
        name=name, file_path=file_path,
        start_line=node.start_point[0] + 1,
        end_line=end_line,
        docstring=_extract_java_docstring(node) or _extract_docstring(node),
    )


def _extract_method_calls(class_node: Node, class_name: str,
                          file_path: str, calls: List[CallInfo]):
    """提取类中所有方法内部的函数调用"""
    body = class_node.child_by_field_name("body")
    if not body:
        return
    for child in body.children:
        if child.type == "method_declaration":
            method_name = _get_node_text(child.child_by_field_name("name"))
            qualified = f"{class_name}.{method_name}"
            java_calls = _extract_java_calls_in_node(child, qualified, file_path)
            calls.extend(java_calls)
        elif child.type == "constructor_declaration":
            cname = _get_node_text(child.child_by_field_name("name"))
            qualified = f"{class_name}.{cname}"
            java_calls = _extract_java_calls_in_node(child, qualified, file_path)
            calls.extend(java_calls)


def _extract_java_calls_in_node(node: Node, enclosing_function: str,
                                file_path: str) -> List[CallInfo]:
    """递归提取 Java 节点中的方法调用"""
    calls = []
    _extract_java_calls_recursive(node, enclosing_function, file_path, calls, is_root=True)
    return calls


def _extract_java_calls_recursive(node: Node, enclosing_function: str,
                                  file_path: str, calls: List[CallInfo],
                                  is_root: bool = True):
    """递归遍历提取方法调用"""
    if node.type == "method_invocation":
        name_node = node.child_by_field_name("name")
        if name_node:
            callee = _get_node_text(name_node)
            calls.append(CallInfo(
                caller_name=enclosing_function,
                caller_file=file_path,
                caller_line=node.start_point[0] + 1,
                callee_name=callee,
                callee_line=None,
            ))
        # 继续处理参数中的调用
        for i in range(node.child_count):
            _extract_java_calls_recursive(node.child(i), enclosing_function,
                                          file_path, calls, is_root=False)
        return

    # 继续递归所有子节点（包括匿名类中的方法声明和 lambda 表达式）
    # 不再阻塞 method_declaration/constructor_declaration，
    # 从而捕获匿名类内部方法中的调用
    for i in range(node.child_count):
        _extract_java_calls_recursive(node.child(i), enclosing_function,
                                      file_path, calls, is_root=False)


def parse_java_project(project_path: str) -> Dict[str, ParseResult]:
    """递归解析整个 Java 项目。"""
    results = {}
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in (
            "venv", "node_modules", "__pycache__", ".git", ".idea",
            "dist", "build", ".mypy_cache", ".pytest_cache", ".tox", "target",
        )]
        for f in sorted(files):
            if f.endswith(".java"):
                file_path = os.path.join(root, f)
                try:
                    result = parse_java_file(file_path)
                    results[file_path] = result
                except Exception as e:
                    print(f"  ⚠️  跳过 {file_path}: {e}")
    return results


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "."
    results = parse_java_project(path)
    print(f"解析了 {len(results)} 个 Java 文件")
    for fp, r in results.items():
        from code_guard.parser.ast_parser import print_parse_result
        print_parse_result(r)

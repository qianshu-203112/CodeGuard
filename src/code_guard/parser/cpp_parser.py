"""
C++ 代码解析器 - 基于 Tree-sitter C++ 语法解析 C++ 源码

与 C 解析器共享提取辅助函数，但使用 tree-sitter-cpp 语法，正确处理
C++ 专属语法（class、花括号初始化、限定名 Foo::bar 等），
避免 C 语法解析 C++ 时触发 ERROR 吞函数的问题。

需要安装: pip install tree-sitter-cpp
"""
import os
import re
from typing import Dict, List, Optional

import tree_sitter_cpp as tscpp
from tree_sitter import Language, Parser, Node

from code_guard.parser.ast_parser import (
    ParseResult, FunctionInfo, ClassInfo, CallInfo, ImportInfo,
    _get_node_text, _extract_docstring,
)
from code_guard.parser.c_parser import (
    _extract_c_calls_in_node, _extract_c_includes, _extract_c_struct,
    _try_extract_error_function, _find_c_function_name,
)

# ── 全局初始化 ──
_CPP_LANGUAGE = Language(tscpp.language())
_CPP_PARSER = Parser(_CPP_LANGUAGE)


def parse_cpp_file(file_path: str) -> ParseResult:
    """
    解析一个 C++ 文件，提取所有代码结构信息。

    Args:
        file_path: C++ 文件的绝对路径

    Returns:
        ParseResult 包含函数、类（class/struct/union）、调用、导入信息
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

    tree = _CPP_PARSER.parse(bytes(source_code, "utf8"))
    root = tree.root_node

    # 提取 include 信息
    result.imports = _extract_c_includes(root)

    # DFS 遍历整棵树（含 ERROR 恢复节点），避免解析错误吞掉后续函数
    _walk_cpp_nodes(root, file_path, result)

    return result


def _walk_cpp_nodes(node: Node, file_path: str, result: ParseResult,
                    inside_function: bool = False, ns_scopes: Optional[List] = None):
    """递归收集 C++ 函数、类、结构体；能救回 ERROR 节点中被吞掉的函数。

    ns_scopes: 命名空间作用域栈，从外到内为 [(prefix, 简单函数名集合), ...]。
      让 namespace 内的函数/类以限定名入图（game.addScore、game.Foo.bar），
      否则多个 namespace 的同名函数会撞名、且限定名查询（game.addScore）
      精确匹配不到。同时用它对函数体内的裸调用做非限定名解析：
      game::update 里写 addScore()，应归属 game::addScore 而非 audio::addScore。
    """
    ns_scopes = ns_scopes or []

    # namespace 定义：进入其作用域时把名字拼进前缀，递归处理内部声明
    if node.type == "namespace_definition":
        name_node = node.child_by_field_name("name")
        if name_node is not None:
            ns = _get_node_text(name_node)
            prefix = f"{ns_scopes[-1][0]}.{ns}" if ns_scopes else ns
            scopes = ns_scopes + [(prefix, _collect_ns_function_names(node))]
        else:
            # 匿名 namespace（namespace {}）无名字，等价于文件内 static 作用域，
            # 不引入新的限定前缀（保持函数/调用为简单名）
            scopes = ns_scopes
        for c in node.children:
            _walk_cpp_nodes(c, file_path, result,
                            inside_function=inside_function, ns_scopes=scopes)
        return

    if node.type == "function_definition":
        func = _extract_cpp_function(node, file_path, ns_scopes)
        if func:
            result.functions.append(func)
            calls = _extract_c_calls_in_node(node, func.name, file_path)
            result.calls.extend(_resolve_ns_calls(calls, ns_scopes))
        # 函数体内部的结构体/类不入类集合（保持"只收顶层类型"的语义）
        for c in node.children:
            _walk_cpp_nodes(c, file_path, result, inside_function=True)
        return

    if not inside_function and node.type == "class_specifier":
        cls = _extract_cpp_class(node, file_path, result, ns_scopes)
        if cls:
            result.classes.append(cls)
        return

    if not inside_function and node.type in ("struct_specifier", "union_specifier"):
        cls = _extract_c_struct(node, file_path)
        ns_prefix = ns_scopes[-1][0] if ns_scopes else ""
        if cls and ns_prefix and cls.name and cls.name != "anonymous":
            cls.name = f"{ns_prefix}.{cls.name}"
        if cls:
            result.classes.append(cls)
        return

    # ERROR 节点可能是被宏/异常语法打断的函数定义，尝试抢救
    if node.type == "ERROR" and not inside_function:
        func = _try_extract_error_function(node, file_path)
        if func:
            ns_prefix = ns_scopes[-1][0] if ns_scopes else ""
            if ns_prefix and not func.name.startswith(ns_prefix + "."):
                func.name = f"{ns_prefix}.{func.name}"
            result.functions.append(func)
            # 只提取 ERROR 节点自身层级内的调用，不包含嵌套函数
            calls = _extract_c_calls_in_node(node, func.name, file_path)
            result.calls.extend(_resolve_ns_calls(calls, ns_scopes))

    for c in node.children:
        _walk_cpp_nodes(c, file_path, result,
                        inside_function=inside_function, ns_scopes=ns_scopes)


def _collect_ns_function_names(ns_node: Node) -> set:
    """收集某 namespace 直接声明的自由函数简单名（不含类内方法、不含嵌套 namespace）。

    用于把 namespace 内函数体中的裸调用解析成当前 namespace 的限定调用
    （如 game::update 里写 addScore() → 归属 game::addScore）。
    """
    names = set()
    body = ns_node.child_by_field_name("body")
    if not body:
        return names
    for c in body.children:
        if c.type == "function_definition":
            declarator = c.child_by_field_name("declarator")
            if declarator:
                nm = _extract_cpp_function_name(declarator)
                if nm and "." not in nm:  # 只收简单名（限定名由调用点显式写全）
                    names.add(nm)
    return names


def _resolve_ns_calls(calls: List[CallInfo],
                      ns_scopes: Optional[List]) -> List[CallInfo]:
    """把 namespace 内的裸调用解析为限定调用。

    C++ 非限定名查找：先查最内层 namespace，逐层向外。若裸名在该 namespace
    的函数集合里命中，则改写为 `ns::name`（硬限定，入库后 get_callers 查询
    限定名时不会与其它 namespace 的同名函数互混）。
    """
    if not ns_scopes:
        return calls
    for call in calls:
        callee = call.callee_name
        if re.match(r"^[A-Za-z_]\w*$", callee):  # 纯简单标识符才解析
            for prefix, names in reversed(ns_scopes):  # 内层优先
                if callee in names:
                    call.callee_name = f"{prefix}::{callee}"
                    break
    return calls


def _extract_cpp_function(node: Node, file_path: str,
                          ns_scopes: Optional[List] = None) -> Optional[FunctionInfo]:
    """提取 C++ 函数/方法定义（支持 Foo::bar 限定名 → "Foo.bar"）。

    当函数位于 namespace 内（ns_scopes 非空），把最内层 namespace 前缀拼在
    函数名之前：
      - namespace game 内的 addScore()      → "game.addScore"
      - namespace game 内的 Foo::bar()       → "game.Foo.bar"
    若声明里已显式写全限定名（void game::addScore()），不再重复前缀。
    """
    if node.type != "function_definition":
        return None
    declarator = node.child_by_field_name("declarator")
    if not declarator:
        return None
    name = _extract_cpp_function_name(declarator)
    if not name:
        return None
    if ns_scopes:
        ns_prefix = ns_scopes[-1][0]
        if ns_prefix and not name.startswith(ns_prefix + "."):
            name = f"{ns_prefix}.{name}"
    body = node.child_by_field_name("body")
    end_line = body.end_point[0] + 1 if body else node.end_point[0] + 1
    return FunctionInfo(
        name=name, file_path=file_path,
        start_line=node.start_point[0] + 1,
        end_line=end_line,
        docstring=_extract_docstring(node),
    )


def _extract_cpp_function_name(declarator: Node) -> Optional[str]:
    """提取函数名，处理 C++ 限定名 Foo::bar → "Foo.bar"、bar() → "bar"。

    tree-sitter-cpp 的 function_declarator 有 declarator 字段：
      - 简单函数：identifier "bar"
      - 类外方法：qualified_identifier（namespace_identifier "Foo" + identifier "bar"）
    """
    if declarator.type != "function_declarator":
        # 可能被 reference_declarator / pointer_declarator 等包装，递归找
        for c in declarator.children:
            if c.type == "function_declarator":
                return _extract_cpp_function_name(c)
        return None
    name_node = declarator.child_by_field_name("declarator")
    if name_node is None:
        return None

    parts = []

    def _collect(n: Node):
        if n.type in ("identifier", "field_identifier", "namespace_identifier",
                      "type_identifier", "destructor_name"):
            parts.append(_get_node_text(n))
        for c in n.children:
            _collect(c)

    _collect(name_node)
    if not parts:
        # 兜底：深度找名字
        return _find_c_function_name(declarator)
    return ".".join(parts)


def _extract_cpp_class(node: Node, file_path: str, result: ParseResult,
                       ns_scopes: Optional[List] = None) -> Optional[ClassInfo]:
    """提取 C++ class（含内联方法及其调用边）

    ns_scopes 非空时（class 位于 namespace 内），类名也带上限定名前缀：
      namespace game 内的 class Foo → "game.Foo"，其方法 → "game.Foo.bar"
    方法体内的裸调用同样做 namespace 解析（方法归属当前 namespace 作用域）。
    """
    if node.type != "class_specifier":
        return None
    name_node = node.child_by_field_name("name")
    name = _get_node_text(name_node) if name_node else ""
    if not name:
        return None  # 匿名类跳过
    if ns_scopes:
        ns_prefix = ns_scopes[-1][0]
        if ns_prefix and not name.startswith(ns_prefix + "."):
            name = f"{ns_prefix}.{name}"

    # 基类
    base_classes = []
    base_clause = node.child_by_field_name("base_class_clause")
    if base_clause:
        for child in base_clause.children:
            if child.type in ("type_identifier", "template_type"):
                base_classes.append(_get_node_text(child))

    # 内联方法
    methods = []
    body = node.child_by_field_name("body")
    if body:
        for child in body.children:
            if child.type == "function_definition":
                method = _extract_cpp_function(child, file_path)
                if method:
                    methods.append(method)
                    qualified = f"{name}.{method.name}"
                    calls = _extract_c_calls_in_node(child, qualified, file_path)
                    result.calls.extend(_resolve_ns_calls(calls, ns_scopes))

    return ClassInfo(
        name=name, file_path=file_path,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        base_classes=base_classes, methods=methods,
        docstring=_extract_docstring(node),
    )


def parse_cpp_project(project_path: str) -> Dict[str, ParseResult]:
    """递归解析整个 C++ 项目。"""
    results = {}
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in (
            "venv", "node_modules", "__pycache__", ".git", ".idea",
            "dist", "build", ".mypy_cache", ".pytest_cache", ".tox", "target",
        )]
        for f in sorted(files):
            if f.endswith((".cpp", ".hpp", ".cc", ".cxx")):
                file_path = os.path.join(root, f)
                try:
                    result = parse_cpp_file(file_path)
                    results[file_path] = result
                except Exception as e:
                    print(f"  ⚠️  跳过 {file_path}: {e}")
    return results


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "."
    results = parse_cpp_project(path)
    print(f"解析了 {len(results)} 个 C++ 文件")
    for fp, r in results.items():
        from code_guard.parser.ast_parser import print_parse_result
        print_parse_result(r)

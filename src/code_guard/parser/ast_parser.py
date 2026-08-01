"""
AST 解析器 - 基于 Tree-sitter 解析 Python 代码

职责：
1. 解析 Python 源文件为 AST 树
2. 提取函数定义、类定义、函数调用、import 语句
3. 输出结构化数据供图模块使用
"""
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 真实项目的源码可能深嵌套（如 u-boot 上千层括号的头文件），tree-sitter AST
# 深度 = 源码嵌套深度，递归遍历会撞 Python 默认 ~1000 递归上限。
# 提高限制避免 RecursionError 跳过整个文件；本模块是所有语言解析器的共享
# 依赖，import 时生效，各解析器的递归遍历（_walk_* / *_recursive）一并受益。
if sys.getrecursionlimit() < 10000:
    sys.setrecursionlimit(10000)

import tree_sitter_python as tspython
from tree_sitter import Language, Parser, Tree, Node


# ── 全局初始化 ──
_LANGUAGE = Language(tspython.language())
_PARSER = Parser(_LANGUAGE)


# ── 数据结构 ──

class FunctionInfo:
    """提取到的函数信息"""
    def __init__(self, name: str, file_path: str, start_line: int, end_line: int,
                 docstring: Optional[str] = None, decorators: Optional[List[str]] = None):
        self.name = name
        self.file_path = file_path
        self.start_line = start_line
        self.end_line = end_line
        self.docstring = docstring
        self.decorators = decorators or []

    def __repr__(self) -> str:
        return f"Function({self.name}@{self.file_path}:{self.start_line})"


class ClassInfo:
    """提取到的类信息"""
    def __init__(self, name: str, file_path: str, start_line: int, end_line: int,
                 base_classes: Optional[List[str]] = None,
                 methods: Optional[List[FunctionInfo]] = None,
                 docstring: Optional[str] = None):
        self.name = name
        self.file_path = file_path
        self.start_line = start_line
        self.end_line = end_line
        self.base_classes = base_classes or []
        self.methods = methods or []
        self.docstring = docstring

    def __repr__(self) -> str:
        return f"Class({self.name}@{self.file_path}:{self.start_line})"


class CallInfo:
    """函数调用信息"""
    def __init__(self, caller_name: str, caller_file: str, caller_line: int,
                 callee_name: str, callee_line: Optional[int] = None):
        self.caller_name = caller_name
        self.caller_file = caller_file
        self.caller_line = caller_line
        self.callee_name = callee_name
        self.callee_line = callee_line

    def __repr__(self) -> str:
        return f"Call({self.caller_name} → {self.callee_name}@{self.caller_file}:{self.caller_line})"


class ImportInfo:
    """导入信息"""
    def __init__(self, source: str, names: List[str], is_from: bool = False):
        self.source = source      # import X → "X", from X import Y → "X"
        self.names = names        # import X → [], from X import Y → ["Y"]
        self.is_from = is_from

    def __repr__(self) -> str:
        return f"Import({self.source} → {self.names})"


class ParseResult:
    """一次解析的完整结果"""
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.functions: List[FunctionInfo] = []
        self.classes: List[ClassInfo] = []
        self.calls: List[CallInfo] = []
        self.imports: List[ImportInfo] = []

    def __repr__(self) -> str:
        return (f"ParseResult({Path(self.file_path).name}: "
                f"{len(self.functions)} functions, {len(self.classes)} classes, "
                f"{len(self.calls)} calls)")


# ── 核心解析逻辑 ──

def _get_node_text(node: Node) -> str:
    """获取 AST 节点的文本内容"""
    return node.text.decode("utf-8") if node.text else ""


def _extract_decorators(node: Node) -> List[str]:
    """提取函数/类的装饰器"""
    decorators = []
    if node.type in ("function_definition", "async_function_definition", "class_definition"):
        # 对于 decorated_definition 内的函数/类，prev_sibling 是 decorator 节点
        prev = node.prev_sibling
        while prev and prev.type == "decorator":
            decorators.append(_get_node_text(prev))
            prev = prev.prev_sibling
    return list(reversed(decorators))


def _extract_docstring(node: Node) -> Optional[str]:
    """提取函数/类的文档字符串"""
    body = node.child_by_field_name("body")
    if not body:
        return None
    first_stmt = body.child(0)
    if first_stmt and first_stmt.type == "expression_statement":
        expr = first_stmt.child(0)
        if expr and expr.type == "string":
            return _get_node_text(expr)
    return None


def _extract_base_classes(node: Node) -> List[str]:
    """提取类的基类列表"""
    bases = []
    superclass = node.child_by_field_name("superclass")
    if superclass:
        # 处理单个基类
        bases.append(_get_node_text(superclass))
    # 处理逗号分隔的基类
    arg_list = node.child_by_field_name("arguments")
    if arg_list:
        for i in range(arg_list.child_count):
            child = arg_list.child(i)
            if child.type in ("identifier", "attribute", "subscript"):
                bases.append(_get_node_text(child))
    return bases


def _extract_function_calls(node: Node, enclosing_function: str,
                            file_path: str) -> List[CallInfo]:
    """从 AST 节点中提取所有函数调用（传入函数定义节点或其 body）"""
    calls = []
    # 如果是函数定义节点，取其 body 而非节点本身
    if node.type == "function_definition":
        body = node.child_by_field_name("body")
        if body:
            _extract_calls_recursive(body, enclosing_function, file_path, calls)
    else:
        _extract_calls_recursive(node, enclosing_function, file_path, calls)
    return calls


def _extract_calls_recursive(node: Node, enclosing_function: str,
                              file_path: str, calls: List[CallInfo]):
    """递归遍历提取函数调用"""
    if node.type == "call":
        func_node = node.child_by_field_name("function")
        if func_node:
            callee = _get_node_text(func_node)
            calls.append(CallInfo(
                caller_name=enclosing_function,
                caller_file=file_path,
                caller_line=node.start_point[0] + 1,
                callee_name=callee,
                callee_line=None
            ))
            # 仍然递归处理调用参数中的调用
            for i in range(node.child_count):
                _extract_calls_recursive(node.child(i), enclosing_function, file_path, calls)
            return  # 已处理此节点的子节点，避免重复

    # 继续递归子节点（但对于 function_definition 不进去，因为那是另一层）
    if node.type != "function_definition":
        for i in range(node.child_count):
            _extract_calls_recursive(node.child(i), enclosing_function, file_path, calls)


def _extract_imports(node: Node) -> List[ImportInfo]:
    """提取 import 语句"""
    imports = []
    for child in node.children:
        if child.type == "import_statement":
            # import os → source="os", names=[]
            # import os, sys → 两个独立的 import_statement，各处理各的
            name_node = child.child_by_field_name("name")
            if name_node:
                imports.append(ImportInfo(
                    source=_get_node_text(name_node),
                    names=[],
                    is_from=False
                ))
        elif child.type == "import_from_statement":
            # from typing import List, Optional
            module = child.child_by_field_name("module_name")
            if not module:
                continue
            source = _get_node_text(module)
            imported = []
            for i in range(child.child_count):
                n = child.child(i)
                if n.type == "dotted_name" and n != module:
                    imported.append(_get_node_text(n))
                elif n.type == "aliased_import":
                    name_field = n.child_by_field_name("name")
                    if name_field:
                        imported.append(_get_node_text(name_field))
            imports.append(ImportInfo(source=source, names=imported, is_from=True))
    return imports


def _extract_function(node: Node, file_path: str) -> Optional[FunctionInfo]:
    """从函数定义节点提取信息"""
    if node.type not in ("function_definition", "async_function_definition"):
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
        docstring=_extract_docstring(node),
        decorators=_extract_decorators(node)
    )


def _extract_class(node: Node, file_path: str) -> Optional[ClassInfo]:
    """从类定义节点提取信息"""
    if node.type != "class_definition":
        return None
    name = _get_node_text(node.child_by_field_name("name"))
    if not name:
        return None

    # 提取类内的方法
    methods = []
    body = node.child_by_field_name("body")
    if body:
        for child in body.children:
            func = _extract_function(child, file_path)
            if func:
                methods.append(func)

    return ClassInfo(
        name=name,
        file_path=file_path,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        base_classes=_extract_base_classes(node),
        methods=methods,
        docstring=_extract_docstring(node)
    )


def _unwrap_node(node: Node) -> Node:
    """如果节点是 decorated_definition，返回其内部的 definition 子节点"""
    if node.type == "decorated_definition":
        for child in node.children:
            if child.type in ("class_definition", "function_definition", "async_function_definition"):
                return child
    return node


def parse_file(file_path: str) -> ParseResult:
    """
    解析一个 Python 文件，提取所有代码结构信息。

    Args:
        file_path: Python 文件的绝对路径

    Returns:
        ParseResult 包含函数、类、调用、导入信息
    """
    result = ParseResult(file_path)

    with open(file_path, "r", encoding="utf-8") as f:
        source_code = f.read()

    tree = _PARSER.parse(bytes(source_code, "utf8"))
    root = tree.root_node

    # 提取 import 信息
    result.imports = _extract_imports(root)

    # 遍历顶层节点，提取函数和类定义
    current_function = None
    current_class = None

    for node in root.children:
        # 处理 decorated_definition（@dataclass、@staticmethod 等）
        inner = _unwrap_node(node)

        # 顶层函数定义
        func = _extract_function(inner, file_path)
        if func:
            result.functions.append(func)
            current_function = func.name
            # 提取该函数内部的调用（从原始节点提取，包含装饰器中的信息）
            calls = _extract_function_calls(node, func.name, file_path)
            result.calls.extend(calls)
            continue

        # 顶层类定义
        cls = _extract_class(inner, file_path)
        if cls:
            result.classes.append(cls)
            current_class = cls.name
            # 提取类方法内部的调用
            body = inner.child_by_field_name("body")
            if body:
                for child in body.children:
                    if child.type == "function_definition":
                        method_name = _get_node_text(child.child_by_field_name("name"))
                        calls = _extract_function_calls(child, f"{cls.name}.{method_name}", file_path)
                        result.calls.extend(calls)

    return result


def parse_project(project_path: str) -> Dict[str, ParseResult]:
    """
    递归解析整个 Python 项目。

    Args:
        project_path: 项目根目录

    Returns:
        file_path → ParseResult 的映射
    """
    results = {}
    for root, dirs, files in os.walk(project_path):
        # 跳过常见非代码目录
        dirs[:] = [d for d in dirs if d not in (
            "venv", "node_modules", "__pycache__", ".git",
            ".idea", "dist", "build", "egg-info", ".mypy_cache",
            ".pytest_cache", ".tox", "env", "envs"
        )]
        for f in sorted(files):
            if f.endswith(".py"):
                file_path = os.path.join(root, f)
                try:
                    result = parse_file(file_path)
                    results[file_path] = result
                except Exception as e:
                    print(f"  ⚠️  跳过 {file_path}: {e}")
    return results


def parse_project_multilang(project_path: str) -> Dict[str, ParseResult]:
    """
    多语言项目解析 — 自动识别 .py / .java / .c / .h / .cpp / .go 等文件。

    各语言的解析器在各自的模块中，入口统一在这里调度。
    """
    results = {}
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in (
            "venv", "node_modules", "__pycache__", ".git",
            ".idea", "dist", "build", "egg-info", ".mypy_cache",
            ".pytest_cache", ".tox", "env", "envs", "target",
            "Dev-C++", "Dev-C++-Easyx", "MinGW64", "MinGW",
            "coverage", ".nyc_output", ".next",
        )]
        for f in sorted(files):
            file_path = os.path.join(root, f)
            try:
                if f.endswith(".py"):
                    result = parse_file(file_path)
                elif f.endswith(".java"):
                    from code_guard.parser.java_parser import parse_java_file
                    result = parse_java_file(file_path)
                elif f.endswith((".js", ".jsx", ".ts", ".tsx", ".vue")):
                    from code_guard.parser.js_parser import parse_js_file
                    result = parse_js_file(file_path)
                elif f.endswith(".go"):
                    from code_guard.parser.go_parser import parse_go_file
                    result = parse_go_file(file_path)
                elif f.endswith((".c", ".h")):
                    from code_guard.parser.c_parser import parse_c_file
                    result = parse_c_file(file_path)
                elif f.endswith((".cpp", ".hpp", ".cc", ".cxx")):
                    try:
                        from code_guard.parser.cpp_parser import parse_cpp_file
                        result = parse_cpp_file(file_path)
                    except ImportError:
                        # 没装 C++ 解析器时用 C 解析器兜底
                        from code_guard.parser.c_parser import parse_c_file
                        result = parse_c_file(file_path)
                else:
                    continue
                results[file_path] = result
            except Exception as e:
                print(f"  ⚠️  跳过 {file_path}: {e}")
    return results


def print_parse_result(result: ParseResult):
    """打印解析结果（调试用）"""
    rel = Path(result.file_path).relative_to(Path.cwd()) if Path(result.file_path).is_relative_to(Path.cwd()) else result.file_path
    print(f"\n📄 {rel}")
    print(f"   Import: {[str(i) for i in result.imports]}")
    print(f"   Functions: {[f'{f.name}({f.start_line}:{f.end_line})' for f in result.functions]}")
    print(f"   Classes: {[c.name for c in result.classes]}")

    for cls in result.classes:
        print(f"     └─ {cls.name} 方法: {[m.name for m in cls.methods]}")

    print(f"   Calls:")
    for call in result.calls[:10]:  # 最多显示 10 个
        print(f"     {call.caller_name} → {call.callee_name} @{call.caller_line}")
    if len(result.calls) > 10:
        print(f"     ... 还有 {len(result.calls) - 10} 个")


if __name__ == "__main__":
    # 测试：解析当前项目的 parser 模块自身
    result = parse_file(__file__)
    print_parse_result(result)

"""
JavaScript / TypeScript / JSX / TSX 解析器

基于 Tree-sitter，解析内容：
  - 函数声明（包括箭头函数、方法定义）
  - 类声明
  - 函数调用
  - import/export 语句
  - JSX/TSX 组件（作为函数处理）
"""
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Set

import tree_sitter_javascript as tsjs
import tree_sitter_typescript as tsts
from tree_sitter import Language, Parser, Node

from code_guard.parser.ast_parser import (
    ParseResult, FunctionInfo, ClassInfo, CallInfo, ImportInfo,
    _get_node_text,
)

# ── 全局初始化 ──
_JS_LANGUAGE = Language(tsjs.language())
_TS_LANGUAGE = Language(tsts.language_typescript())
_TSX_LANGUAGE = Language(tsts.language_tsx())
_JS_PARSER = Parser(_JS_LANGUAGE)
_TS_PARSER = Parser(_TS_LANGUAGE)
_TSX_PARSER = Parser(_TSX_LANGUAGE)

# ── JS 节点类型常量 ──
_FUNCTION_TYPES = {
    "function_declaration",
    "function_expression",
    "arrow_function",
    "method_definition",
    "generator_function_declaration",
    "generator_function_expression",
}
_CLASS_TYPES = {"class_declaration", "class_expression"}
_CALL_TYPES = {"call_expression", "new_expression"}
_IMPORT_TYPES = {"import_statement", "import_specifier"}
_EXPORT_TYPES = {"export_statement"}


def _js_query(text: bytes) -> Node:
    """用 JS 解析器解析"""
    tree = _JS_PARSER.parse(text)
    return tree.root_node


def _ts_query(text: bytes) -> Node:
    """用 TS 解析器解析"""
    tree = _TS_PARSER.parse(text)
    return tree.root_node


def _tsx_query(text: bytes) -> Node:
    """用 TSX 解析器解析"""
    tree = _TSX_PARSER.parse(text)
    return tree.root_node


def _get_parser(file_path: str) -> Parser:
    """根据文件扩展名选择解析器"""
    if file_path.endswith(".tsx"):
        return _TSX_PARSER
    if file_path.endswith(".ts"):
        return _TS_PARSER
    return _JS_PARSER


def _extract_functions(node: Node, file_path: str) -> List[FunctionInfo]:
    funcs = []
    def walk(n: Node):
        if n.type in _FUNCTION_TYPES:
            name = _resolve_function_name(n)
            # 取不到名字的匿名函数（回调、useCallback 参数、onClick={() => {}} 等）
            # 不作为"项目函数"入库——它们不是独立可查的代码单元。
            # 唯一例外：export default () => {} 是模块入口，给兜底名保留。
            if not name:
                if n.type in ("arrow_function", "function_expression") and n.parent:
                    if n.parent.type == "export_statement":
                        name = "export_default"
                    else:
                        for child in n.children:
                            walk(child)
                        return
            funcs.append(FunctionInfo(
                name=name, file_path=file_path,
                start_line=n.start_point[0] + 1,
                end_line=n.end_point[0] + 1,
            ))
        for child in n.children:
            walk(child)
    walk(node)
    return funcs


def _resolve_function_name(n: Node) -> str:
    """提取函数名。函数声明/方法自带 name；箭头函数/函数表达式从父节点取名。

    命名来源：
      - function_declaration / generator_function_declaration：自带 name
      - method_definition：name 或 property 字段
      - 箭头函数 / 函数表达式：
        * const fn = () => {}           → variable_declarator 的 name
        * { foo: () => {} }             → pair 的 key
        * export default () => {}        → 父节点 export_statement（返回空，由调用方兜底）
        * useCallback((e) => {}) 等参数 → 父节点 arguments/jsx_expression，返回空（不入库）
    """
    name_node = n.child_by_field_name("name")
    if name_node is None and n.type == "method_definition":
        name_node = n.child_by_field_name("property")
    name = _get_node_text(name_node) if name_node else ""
    if name:
        return name
    # 箭头函数/函数表达式：从父节点取名字
    if n.type in ("arrow_function", "function_expression", "generator_function_expression") \
            and n.parent:
        if n.parent.type == "variable_declarator":
            vname = n.parent.child_by_field_name("name")
            if vname:
                return _get_node_text(vname)
        elif n.parent.type == "pair":
            # 对象属性: { foo: function() {}, bar: () => {} }
            kname = n.parent.child_by_field_name("key")
            if kname:
                return _get_node_text(kname)
    return name


def _extract_classes(node: Node, file_path: str) -> List[ClassInfo]:
    classes = []
    def walk(n: Node):
        if n.type in _CLASS_TYPES:
            name_node = n.child_by_field_name("name")
            name = _get_node_text(name_node) if name_node else ""
            body_node = n.child_by_field_name("body")
            methods = []
            if body_node:
                for child in body_node.children:
                    if child.type in _FUNCTION_TYPES:
                        mn = child.child_by_field_name("name") or \
                             child.child_by_field_name("property")
                        m_name = _get_node_text(mn) if mn else ""
                        methods.append(FunctionInfo(
                            name=m_name, file_path=file_path,
                            start_line=child.start_point[0] + 1,
                            end_line=child.end_point[0] + 1,
                        ))
            classes.append(ClassInfo(
                name=name, file_path=file_path,
                start_line=n.start_point[0] + 1,
                end_line=n.end_point[0] + 1,
                methods=methods,
            ))
        for child in n.children:
            walk(child)
    walk(node)
    return classes


def _extract_calls(node: Node, file_path: str) -> List[CallInfo]:
    calls = []
    def walk(n: Node):
        if n.type in _CALL_TYPES:
            func_node = n.child_by_field_name("function")
            if func_node:
                callee = _get_node_text(func_node)
                # 找包裹该调用的函数作为 caller
                parent = n.parent
                caller = ""
                while parent:
                    if parent.type in _FUNCTION_TYPES:
                        caller = _resolve_function_name(parent)
                        break
                    parent = parent.parent
                # caller 取不到名的调用边丢弃——它属于匿名回调（useCallback 参数、
                # onClick={() => {}} 等），不是可归属到项目函数的调用。记录空 caller
                # 会污染"谁调用了 X"的统计。
                if caller:
                    calls.append(CallInfo(
                        caller_name=caller,
                        caller_file=file_path,
                        caller_line=n.start_point[0] + 1,
                        callee_name=callee,
                    ))
        for child in n.children:
            walk(child)
    walk(node)
    return calls


def _extract_imports(node: Node) -> List[ImportInfo]:
    imports = []
    def walk(n: Node):
        if n.type == "import_statement":
            src_node = n.child_by_field_name("source")
            if src_node:
                source = _get_node_text(src_node).strip("'\"`")
                names = []
                clause = n.child_by_field_name("imports")
                if clause and clause.type == "import_clause":
                    for child in clause.children:
                        if child.type == "identifier":
                            names.append(_get_node_text(child))
                        elif child.type == "named_imports":
                            for spec in child.children:
                                if spec.type == "import_specifier":
                                    name = (spec.child_by_field_name("local") or
                                            spec.child_by_field_name("imported"))
                                    if name:
                                        names.append(_get_node_text(name))
                imports.append(ImportInfo(source=source, names=names, is_from=True))
        elif n.type == "call_expression":
            func = n.child_by_field_name("function")
            if func and _get_node_text(func) == "require":
                args = n.child_by_field_name("arguments")
                if args and len(args.children) > 2:
                    src = _get_node_text(args.children[1]).strip("'\"`")
                    imports.append(ImportInfo(source=src, names=[], is_from=False))
        for child in n.children:
            walk(child)
    walk(node)
    return imports


def _extract_exports(node: Node) -> List[str]:
    exports = []
    def walk(n: Node):
        if n.type == "export_statement":
            decl = n.child_by_field_name("declaration")
            if decl:
                name_node = decl.child_by_field_name("name") or \
                            decl.child_by_field_name("property")
                if name_node:
                    exports.append(_get_node_text(name_node))
            if n.child_by_field_name("default"):
                exports.append("default")
        for child in n.children:
            walk(child)
    walk(node)
    return exports


def _extract_script_from_vue(source: str) -> str:
    """从 .vue 文件中提取 <script> 标签内容"""
    # 匹配 <script>...</script> 和 <script setup>...</script>
    m = re.search(r'<script\b[^>]*>(.*?)</script>', source, re.DOTALL)
    if m:
        return m.group(1).strip()
    return ""


def parse_js_file(file_path: str) -> ParseResult:
    """解析 JS/TS/JSX/TSX/VUE 文件，自动根据后缀选择解析器"""
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()

    # .vue 文件：提取 <script> 内容再解析
    if file_path.endswith(".vue"):
        script = _extract_script_from_vue(source)
        if not script:
            return ParseResult(file_path=file_path)
        source_bytes = script.encode("utf-8")
        root = _JS_PARSER.parse(source_bytes).root_node
    else:
        parser = _get_parser(file_path)
        source_bytes = source.encode("utf-8")
        root = parser.parse(source_bytes).root_node

    result = ParseResult(file_path=file_path)
    result.functions = _extract_functions(root, file_path)
    result.classes = _extract_classes(root, file_path)
    result.calls = _extract_calls(root, file_path)
    result.imports = _extract_imports(root)

    return result

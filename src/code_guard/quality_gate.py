"""
质量门控 - 确保 LLM 回答基于图数据而非幻觉
"""
import json
import os
import re
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from openai import OpenAI
from code_guard.graph.code_graph import CodeGraph
from code_guard.config.settings import settings


@dataclass
class Claim:
    statement: str
    citations: List[str] = field(default_factory=list)
    is_speculative: bool = False
    confidence: float = 1.0


@dataclass
class AnswerResult:
    question: str
    intent_type: str
    claims: List[Claim] = field(default_factory=list)
    speculative_claims: List[Claim] = field(default_factory=list)
    graph_data: dict = field(default_factory=dict)
    confidence: float = 1.0
    passed: bool = True
    raw_answer: str = ""


INTENT_PATTERNS = [
    # 影响分析优先（包含"重构"+"调用"的句子）
    (r"重构.*需要.*修改|需要同步修改", "impact"),
    (r"如果.*(改|修|重构|变更).*影响|变更影响|影响范围", "impact"),
    (r"(影响|重构|改动|修改|更改|改).*(影响|波及|涉及|同步|需要)", "impact"),
    (r"(谁|哪些|什么|哪里).*(调用|使用|引用|用到)", "callers"),
    (r"被.*(调用|使用|引用|用到)", "callers"),
    (r"调用.*了.*哪些|调用了什么|调用链|调用关系(?!.*多少条)", "callees"),
    (r"里面.*(调用|使用).*什么|方法.*调用|函数.*调用", "callees"),
    (r"搜索|查找|找到|找出|有没有.*(函数|方法|相关)", "search"),
    (r"(函数|方法|接口).*(详情|介绍|干什么|作用|干嘛的|做什么)", "detail"),
    (r"详细介绍|详细说说|详细信息", "detail"),
    (r"文件.*(结构|函数|类|内容|什么)", "structure"),
    (r"\\.\w+.*(有|里).*(什么|哪些)|(有|里).*(什么|哪些).*\.\w+", "structure"),
    (r"\.\w+.*(里|中|里面).*(干嘛|干什么|作用|做|什么|哪些)", "structure"),
    (r"\.\w+.*(干嘛|干什么|作用|做啥)", "detail"),
    (r"里(面|头|边).*(什么|哪些)", "structure"),
    (r"总共有|有多少|总共有多少|几个|多少条|多少.*函数|多少.*类", "stats"),
    (r"统计|概览|总览|规模|多大", "stats"),
    (r"调用关系.*多少条|多少条.*调用", "stats"),
]


def detect_intent(question: str) -> str:
    for pattern, intent in INTENT_PATTERNS:
        if re.search(pattern, question):
            return intent
    return "unknown"


def extract_keywords(question: str) -> list:
    """提取问题中所有候选关键词（保留原始词）"""
    words = []
    # 引号中的内容优先
    quoted = re.findall(r'[""「」]([^""「」]+)[""「」]', question)
    words.extend(quoted)
    # "函数:xxx" 或 "方法:xxx" 模式
    m = re.search(r"(?:函数|方法|接口)\s*[:：]?\s*([a-zA-Z_][a-zA-Z0-9_.]*)", question)
    if m:
        words.append(m.group(1))
    # 所有可能的标识符（含短名如 run、get、go、db）
    for word in re.findall(r"[a-zA-Z_][a-zA-Z0-9_.]{1,}", question):
        words.append(word)
    # 大写开头的类名
    for word in re.findall(r"[A-Z][a-zA-Z0-9_]{2,}", question):
        words.append(word)
    # 去重，优先保留长词（更精确）
    seen = set()
    unique = []
    for w in words:
        if w not in seen:
            seen.add(w)
            unique.append(w)
    return sorted(unique, key=len, reverse=True)


def extract_function_name(question: str, graph: CodeGraph, intent: str = "") -> Optional[str]:
    """
    从问题中提取函数名。

    Returns:
        匹配到的函数完整名（如 "ToolDrivenAgentLoop.run"）
    """
    words = extract_keywords(question)
    for word in words:
        results = graph.search_functions(word)
        if results:
            return results[0]["name"]
    return None


def extract_raw_keyword(question: str) -> Optional[str]:
    """
    从问题中提取原始关键词（用于 callers/callees 的精确匹配）。
    返回用户问题中出现的原始词，而非图里的函数完整名。
    优先返回带点号的完整限定名（如 MemoryManager.get_context 而非 MemoryManager）。
    """
    quoted = re.findall(r'[""「」]([^""「」]+)[""「」]', question)
    if quoted:
        return quoted[0]
    m = re.search(r"(?:函数|方法|接口)\s*[:：]?\s*([a-zA-Z_][a-zA-Z0-9_.]*)", question)
    if m:
        return m.group(1)
    # 找所有标识符，优先返回最长/最具体的
    words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_.]{1,}", question)
    # 优先选带点号的（限定名），其次选最长的
    dotted = [w for w in words if "." in w]
    if dotted:
        return max(dotted, key=len)
    if words:
        return max(words, key=len)
    return None


class QualityGate:
    def __init__(self, graph: CodeGraph, project_path: str = ""):
        self.graph = graph
        self.project_path = project_path
        self.client = OpenAI(api_key=settings.LLM_API_KEY, base_url=settings.LLM_BASE_URL)
        self.model = settings.LLM_MODEL

    def answer(self, question: str) -> AnswerResult:
        intent = detect_intent(question)
        data = self._query_graph(intent, question)

        # 向量检索——当图查询结果弱时（未知意图/搜索意图），用语义结果替代
        if self.project_path and intent in ("unknown", "search"):
            vec_results = self._vector_search(question)
            if vec_results:
                data["vector_search"] = vec_results
                data["result"] = vec_results
                data["hit_source"] = "vector"

        if self._is_empty(data):
            return AnswerResult(question=question, intent_type=intent,
                                graph_data=data, confidence=1.0, passed=True,
                                raw_answer="图数据中未找到相关信息。")
        result = self._llm_answer(question, intent, data)
        return self._verify(result, data)

    def _vector_search(self, question: str) -> list:
        """向量检索兜底"""
        try:
            from code_guard.vector.indexer import search_code
            from code_guard.vector.store import VectorStore
            from code_guard.vector.embedder import CodeEmbedder

            vs = VectorStore()
            if vs.count() == 0:
                return []
            items = search_code(question, project_path=self.project_path,
                                n_results=3)
            return [{
                "name": it["name"],
                "file": it["file"],
                "line": it["line"],
                "doc": it["doc"],
                "score": it["score"],
                "qualified_name": it["qualified_name"],
            } for it in items]
        except Exception as e:
            print(f"  [vector] {e}")
            return []

    def _query_graph(self, intent: str, question: str) -> dict:
        data = {"intent": intent}
        fn = extract_function_name(question, self.graph, intent)
        raw_kw = extract_raw_keyword(question)

        if intent == "callers":
            if raw_kw:
                # 优先用原始关键词查调用者（比如 "run_code" 直接查 call_edges）
                data["target"] = raw_kw
                data["result"] = self.graph.get_callers(raw_kw)
                data["detail"] = self.graph.get_function_detail(raw_kw)
                if not data["result"] and fn and fn != raw_kw:
                    # 原始词没查到，再用图匹配名
                    data["target"] = fn
                    data["result"] = self.graph.get_callers(fn)
                    data["detail"] = self.graph.get_function_detail(fn)
            elif fn:
                data["target"] = fn
                data["result"] = self.graph.get_callers(fn)
                data["detail"] = self.graph.get_function_detail(fn)

        elif intent == "callees" and fn:
            data["target"] = fn
            result = self.graph.get_callees(fn)
            # 如果精确匹配没结果，尝试模糊匹配
            if not result and raw_kw:
                result = self.graph.get_callees(raw_kw)
            data["result"] = result
            data["detail"] = self.graph.get_function_detail(fn)

        elif intent == "search":
            for kw in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{1,}", question):
                r = self.graph.search_functions(kw)
                if r:
                    data["keyword"] = kw
                    data["result"] = r
                    break
            if not data.get("result"):
                data["result"] = []

        elif intent == "detail" and fn:
            d = self.graph.get_function_detail(fn)
            if d:
                data["target"] = fn
                data["detail"] = d
                data["callers"] = self.graph.get_callers(fn)
                data["callees"] = self.graph.get_callees(fn)
            else:
                data["result"] = []

        elif intent == "impact":
            target = fn or raw_kw
            if target:
                impact_result = self.graph.analyze_change_impact(target)
                # 如果精确查找没结果，尝试用原始关键词再查
                if not impact_result.get("all_affected") and raw_kw and raw_kw != target:
                    impact_result2 = self.graph.analyze_change_impact(raw_kw)
                    if impact_result2.get("all_affected"):
                        impact_result = impact_result2
                        target = raw_kw
                # 如果还是没有，尝试把类名当作模块名搜索所有相关函数
                if not impact_result.get("all_affected") and target:
                    # 搜所有名字包含该类名的函数
                    related = self.graph.search_functions(target.split(".")[0])
                    all_affected = []
                    direct_callers = []
                    seen = set()
                    for rf in related:
                        ri = self.graph.analyze_change_impact(rf["name"])
                        for a in ri.get("all_affected", []):
                            k = a.get("caller", "") + str(a.get("depth", ""))
                            if k not in seen:
                                seen.add(k)
                                all_affected.append(a)
                        for d in ri.get("direct_callers", []):
                            k = d.get("caller", "")
                            if k not in [c.get("caller", "") for c in direct_callers]:
                                direct_callers.append(d)
                    if all_affected:
                        impact_result = {
                            "target": target,
                            "direct_callers": direct_callers,
                            "all_affected": all_affected,
                            "affected_tests": [],
                            "affected_files": list(set(
                                a.get("file", "") for a in all_affected
                            )),
                        }
                data["target"] = target
                data["impact"] = impact_result
                data["result"] = impact_result.get("all_affected", [])
                data["direct_callers"] = impact_result.get("direct_callers", [])
                data["detail"] = self.graph.get_function_detail(target)
            else:
                data["result"] = []
                data["impact"] = {"target": "", "direct_callers": [], "all_affected": []}

        elif intent == "stats":
            data["result"] = self.graph.get_stats()

        elif intent == "unknown" and fn:
            data["type"] = "callers"
            data["target"] = fn
            data["result"] = self.graph.get_callers(fn)
            data["detail"] = self.graph.get_function_detail(fn)

        elif intent == "structure":
            files_found = None
            # 用 \.\w+ 替换 .py，支持多语言（.java / .go / .js / .ts 等）；
            # 文件名首字符用 \w 而非 [a-zA-Z0-9_]，支持中文文件名（如 贪吃蛇.cpp）
            m = re.search(r'([\w][\w/\\]*\.\w+)', question)
            if m:
                files_found = self.graph.search_files(m.group(1))
            if not files_found:
                for word in re.findall(r'\w+\.\w+', question):
                    files_found = self.graph.search_files(word)
                    if files_found:
                        break
            if files_found:
                fp = files_found[0]["path"]
                data["file"] = fp
                data["functions"] = self.graph.get_functions_in_file(fp)
                data["classes"] = self.graph.get_classes_in_file(fp)
                data["result"] = data["functions"] + data["classes"]
            else:
                data["result"] = self.graph.get_stats()
        else:
            data["result"] = []
        return data

    def _is_empty(self, data: dict) -> bool:
        r = data.get("result")
        if r and (isinstance(r, list) and len(r) > 0 or isinstance(r, dict) and r):
            return False
        if data.get("detail"):
            return False
        return True

    def _llm_answer(self, question: str, intent: str, data: dict) -> AnswerResult:
        data_json = json.dumps(data, ensure_ascii=False, indent=2, default=str)
        prompt = f"""你是一个代码知识图谱助手。你的回答必须严格遵守：
1. 只基于以下图数据回答，不要用自己的知识补充
2. 每条结论引用来源，格式为 [文件名:行号] 或 [函数名]
3. 没有来源的信息必须标注为"推测"
4. 数据不足时如实告知

用户问题: {question}

图数据:
```json
{data_json}
```"""
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": "你是代码知识图谱助手，回答必须基于提供的图数据。"},
                          {"role": "user", "content": prompt}],
                temperature=0.1)
            text = resp.choices[0].message.content or ""
        except Exception as e:
            text = f"LLM 调用失败: {e}"
        return self._parse_result(question, intent, data, text)

    def _parse_result(self, question: str, intent: str, data: dict, text: str) -> AnswerResult:
        claims, speculative = [], []
        for line in text.split("\n"):
            s = line.strip()
            if not s:
                continue
            # 引用形如 [GameJFrame.java:83] / [LoginJFrame] / [贪吃蛇.cpp:12]；
            # 只要括号里含 ASCII 字母就算引用（纯类名/模块名也能被提取校验）
            citations = re.findall(r'\[([^\[\]]*[A-Za-z][^\[\]]*)\]', s)
            is_spec = "推测" in s
            c = Claim(statement=s, citations=citations, is_speculative=is_spec,
                      confidence=0.3 if is_spec else 0.9)
            (speculative if is_spec else claims).append(c)
        return AnswerResult(question=question, intent_type=intent, claims=claims,
                            speculative_claims=speculative, graph_data=data, raw_answer=text)

    @staticmethod
    def _collect_valid_paths(data) -> set:
        """从图数据里递归收集所有可被引用的文件路径。

        语义：LLM 是基于整份 data 回答的，data 里出现的每个文件路径都是合法
        引用目标——不依赖具体是哪个键（result/detail/callers/...）。这样任何
        intent 只要把数据放进 data，质量门就能自动校验，不用每个分支记得塞
        进 result。跳过大段文本字段（docstring/base_classes），避免把文档内容
        误当成路径。
        """
        valid = set()

        def walk(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k in ("docstring", "base_classes"):
                        continue
                    walk(v)
            elif isinstance(obj, list):
                for item in obj:
                    walk(item)
            elif isinstance(obj, str) and (".py" in obj or "/" in obj or "\\" in obj):
                valid.add(obj)

        walk(data)
        return valid

    def _verify(self, result: AnswerResult, data: dict) -> AnswerResult:
        valid = self._collect_valid_paths(data)

        # 图数据里没有任何文件路径可校验 → 直接放行（如纯 callees 只有函数名）
        if not valid:
            result.passed = True
            result.confidence = result.confidence or 0.8
            return result

        # LLM 自己标注的"推测"行是诚实的（评测已用 has_speculative 单独记录），
        # 不再扣门禁置信度——门禁置信度只统计"门禁抓到的错误引用"，否则描述性
        # 回答（detail 等）会被结构性扣分。
        pre_speculative = len(result.speculative_claims)

        for cl in result.claims[:]:
            # 有引用但引用对不上数据 → 推测（门禁抓到幻觉引用）
            bad = [c for c in cl.citations if not self._citation_valid(c, valid)]
            if bad:
                cl.is_speculative = True
                cl.confidence = 0.3
                result.claims.remove(cl)
                result.speculative_claims.append(cl)
            elif not cl.citations:
                # 无引用：无法验证——既不当作已验证声明抬高置信度，也不武断归为推测
                # （LLM 回答开头的概括句常见无引用）。从 verified 集合中剔除即可。
                result.claims.remove(cl)

        verified = len(result.claims)
        gate_caught = len(result.speculative_claims) - pre_speculative
        total = verified + gate_caught
        result.confidence = verified / total if total > 0 else 1.0
        result.passed = result.confidence >= 0.5
        return result

    @staticmethod
    def _citation_valid(citation: str, valid_paths) -> bool:
        """判断 LLM 引用 [文件:行号] 是否能在图数据中找到来源。

        图数据里存的是完整绝对路径，而 LLM 引用常见写法是短名+行号
        （如 [GameJFrame.java:83]）或类名（如 [LoginJFrame:42]）。
        只做完整子串匹配会把正确引用误判为无来源 → 降级成推测，白扣分。
        这里按 basename 匹配：去掉行号后，短名等于路径 basename 即算命中。
        """
        file_part = citation.split(":", 1)[0].strip()
        if not file_part:
            return False
        for v in valid_paths:
            if file_part in v or file_part == os.path.basename(v):
                return True
        return False


def answer_question(question: str, graph: CodeGraph,
                    project_path: str = "") -> AnswerResult:
    return QualityGate(graph, project_path=project_path).answer(question)

"""
Multi-Agent Orchestrator — 多 Agent 任务编排

实际流程（线性三步，与代码一致）:
  1. 接收用户问题
  2. Planner (LLM) 分析问题 → 输出任务计划（一系列工具调用）；
     LLM 规划解析失败时用规则式 _auto_plan 兜底
  3. Executor 依次执行工具调用（最多 5 步），收集结果，支持 $编号 引用上一步结果
  4. Synthesizer 基于工具返回数据合成最终回答（只依据数据，不自行编造）

注:早期文档曾描述 Planner→Executor→Verifier→Synthesizer 的迭代回炉流程，
   但实现为线性三步。若后续要支持"数据不足时补充计划再回答"，可在 Executor
   后加一轮有硬上限的校验循环（代价是多 1~2 次 LLM 调用）。answer() 接受
   on_event 回调，方便在 Web 端以 SSE 流式推送各阶段事件。
"""
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Any

from openai import OpenAI

from code_guard.config.settings import settings
from code_guard.agent.tools import (
    GraphQueryTool, VectorSearchTool, CodeReaderTool, ModuleAnalysisTool,
    ProjectOverviewTool,
)
from code_guard.graph.code_graph import CodeGraph
from code_guard.quality_gate import detect_intent, extract_function_name, extract_raw_keyword


# ── 规划 prompt ──

PLANNER_PROMPT = """你是一个代码分析任务的规划专家。用户会提出一个关于代码项目的问题，你需要：
1. 理解用户想要什么
2. 拆解成一系列工具调用步骤
3. 每个步骤指定工具和参数

可用工具：
{tools_desc}

重要：区分"工具"的含义
- 如果用户问"调用了哪些工具/函数/方法"→ 用 search_functions 搜索名称，或用 get_callees 查函数内部调用
- 如果用户问"项目结构/模块/依赖"→ 用 analyze 做模块分析
- 如果用户问"统计/有多少"→ 用 get_stats
- 如果用户问"文件/类/结构"→ 用 get_file_structure

规则：
- 步骤要具体、可执行
- 每个步骤依赖上一步的结果用 \"$上一步编号\" 引用
- 不需要的步骤就不加
- 最多 5 步
- 输出格式：JSON 数组，每个元素 {{"step": 编号, "tool": "工具名", "args": {{参数}}, "purpose": "目的"}}

示例：
用户: "谁调用了 load_data 这个函数？这个函数在哪个文件？"
输出: [
  {{"step": 1, "tool": "get_callers", "args": {{"function_name": "load_data"}}, "purpose": "查询 load_data 的调用者"}},
  {{"step": 2, "tool": "get_detail", "args": {{"function_name": "load_data"}}, "purpose": "查询 load_data 的详细信息"}}
]
"""

SYNTHESIZER_PROMPT = """你是一个代码分析助手。基于以下工具调用结果，回答用户的问题。

严格规则：
1. 只基于工具返回的数据回答，不要自己编造或推测
2. 每条结论必须引用来源（文件名、函数名、行号）
3. 如果有多个来源，综合对比回答
4. 如果数据不足，只说"根据现有信息无法确定"，不要强行解释
5. 不要添加"可能"、"也许"、"推测"等推测性语言
6. 区分"没找到数据"和"确实不存在"——前者说没找到，后者才说没有
7. 工具结果里出现"截断"、"省略"、"共 N 项（已省略后 M 项）"、"仅显示前 N 项"等
   字样，代表数据被裁剪过、不完整。此时回答必须：
   - 如实说明"数据被截断，只列出了部分项"，并把看到的项列出来
   - 绝不能把截断误判成"项目里不存在"或"只有这些"
   - 如果用户的问题需要被截断掉的那部分（如"所有文件""全部调用者"），
     明确告知数据不完整，建议换个更具体的问法
"""


def _summarize_result(result) -> str:
    """把工具结果转成给 Synthesizer 看的摘要。

    原则：递减式截断，而不是 JSON 硬切。结构结果（dict/list）按语义裁剪，
    保留骨架、标注省略数量——Synthesizer 拿到的永远是完整结构的缩小版，
    而不是从中间被腰斩的残缺块：
      - dict：每个 key 递归处理，key 名不丢（保留结构骨架）
      - list：前 _MAX_LIST_ITEMS 项保留，其余用"已省略 M 项"标注总数
      - 字符串：原样保留（read_file 的源码有行号和引用，不能切）
      - 顶层还有 _MAX_CHARS 全局兜底，防止极端嵌套结构把摘要撑爆
    """
    if isinstance(result, str):
        return result

    # 结构感知截断，输出含省略标注（Synthesizer prompt 规则7 依赖此标记）
    def _compact(obj):
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                out[k] = _compact(v)
            return out
        if isinstance(obj, list):
            if len(obj) <= _MAX_LIST_ITEMS:
                return [_compact(x) for x in obj]
            kept = [_compact(x) for x in obj[:_MAX_LIST_ITEMS]]
            return kept + [{"__truncated__": True, "total": len(obj),
                            "omitted": len(obj) - _MAX_LIST_ITEMS}]
        return obj

    try:
        compact = _compact(result)
        text = json.dumps(compact, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(result)

    if len(text) > _MAX_CHARS:
        return text[:_MAX_CHARS] + f"\n...(截断，完整结果共 {len(text)} 字符)"
    return text


# 结构感知摘要的裁剪参数
_MAX_LIST_ITEMS = 20      # list 最多保留的项数
_MAX_CHARS = 8000         # 全局兜底：单个工具结果摘要的最大字符数


class AgentOrchestrator:
    """多 Agent 编排器"""

    def __init__(self, graph: Optional[CodeGraph] = None,
                 results: Optional[dict] = None,
                 project_path: str = ""):
        self.graph = graph
        self.results = results or {}
        self.project_path = project_path
        self.client = OpenAI(api_key=settings.LLM_API_KEY,
                             base_url=settings.LLM_BASE_URL)
        self.model = settings.LLM_MODEL

        # 初始化工具
        self.tools = {}
        if graph:
            self.tools["graph"] = GraphQueryTool(graph)
        if project_path:
            self.tools["vector"] = VectorSearchTool(project_path)
            self.tools["reader"] = CodeReaderTool(project_path)
            self.tools["overview"] = ProjectOverviewTool(project_path, graph)
        if graph and results:
            self.tools["module"] = ModuleAnalysisTool(graph, results)

    def _get_tools_desc(self) -> str:
        """生成工具描述列表"""
        lines = []
        for name, tool in self.tools.items():
            for t in tool.list_tools():
                args_str = ", ".join(t["args"]) if t["args"] else "无"
                lines.append(f"- {t['name']}({args_str}): {t['desc']} [来源: {name}]")
        return "\n".join(lines)

    def _call_llm(self, system: str, prompt: str, temp: float = 0.1,
                  max_tokens: int = 2048) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            temperature=temp,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""

    def _execute_tool(self, tool_name: str, args: dict) -> Any:
        """执行一个工具调用"""
        for source_name, tool in self.tools.items():
            for t in tool.list_tools():
                if t["name"] == tool_name:
                    method = getattr(tool, tool_name, None)
                    if method:
                        try:
                            result = method(**args)
                            return result
                        except Exception as e:
                            return {"error": str(e)}
        return {"error": f"未知工具: {tool_name}"}

    def _parse_plan(self, text: str) -> List[Dict]:
        """解析 LLM 输出的计划 JSON"""
        # 提取 JSON
        json_match = re.search(r'\[[\s\S]*\]', text)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass
        # 尝试找 ```json 块
        code_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if code_match:
            try:
                return json.loads(code_match.group(1))
            except json.JSONDecodeError:
                pass
        return []

    def answer(self, question: str, on_event=None) -> Dict:
        """
        主入口：多 Agent 协作回答问题

        Args:
            question: 用户问题
            on_event: 可选回调 on_event(event_type, data)，用于流式推送阶段事件。
                      事件类型:
                        - plan:        规划字符串或计划列表
                        - step_start:  {"step", "tool", "args", "purpose"}
                        - step_done:   {"step", "tool", "result"}
                        - synthesize:  开始合成（data=None）
                        - answer:      最终回答文本

        Returns:
            {"answer": str, "steps": [...], "raw_data": {...}}
        """
        steps_taken = []
        all_data = {}

        def emit(event: str, data=None):
            if on_event:
                on_event(event, data)

        # ── 阶段 1: 规划 ──
        tools_desc = self._get_tools_desc()
        if not tools_desc:
            # 没有工具可用，走简单回答
            return {"answer": "没有可用的分析工具", "steps": [], "raw_data": {}}

        emit("plan", "正在分析问题并规划执行步骤...")

        # "工具清单"类问题（有哪些/列出/手写/包含 + 工具）不走 LLM 规划：
        # LLM 会把它拆成 search_functions("工具")——拿中文搜英文函数名必然 0 结果。
        # 规则式 _auto_plan 才是正确方案：overview 识别工具/Agent模块 → 读源码。
        # 只在"要工具清单"时短路；"xxx工具调用了谁"这类定位问题仍走 LLM 规划。
        _TOOL_LIST_RE = re.compile(
            r"(有哪些|列出|列一下|手写|手写的|自定义|包含|用了哪些|用了什么).{0,4}(工具|tool)|"
            r"(工具|tool).{0,4}(有哪些|有哪些列表|是什么)"
        )
        if _TOOL_LIST_RE.search(question):
            plan = self._auto_plan(question)
            emit("plan", plan)
        else:
            plan_prompt = PLANNER_PROMPT.replace("{tools_desc}", tools_desc)
            plan_text = self._call_llm(
                "你是代码分析规划专家。",
                f"{plan_prompt}\n\n用户问题: {question}",
                temp=0.3, max_tokens=2048
            )
            plan = self._parse_plan(plan_text)

            # 如果计划解析失败，生成一个简单的单步计划
            if not plan:
                # 自动检测意图，生成简单计划
                plan = self._auto_plan(question)
            emit("plan", plan)

        # ── 阶段 2: 执行 ──
        for step in plan[:5]:
            tool_name = step.get("tool", "")
            args = step.get("args", {})
            purpose = step.get("purpose", "")

            # 支持 $编号 引用上一步结果（按 step 编号查找，兼容非连续编号的计划）
            for k, v in args.items():
                if isinstance(v, str) and v.startswith("$"):
                    ref = v[1:]
                    if ref.isdigit():
                        ref_num = int(ref)
                        ref_result = None
                        for prev in reversed(steps_taken):
                            if prev.get("step") == ref_num:
                                ref_result = prev.get("result")
                                break
                        if isinstance(ref_result, list) and len(ref_result) > 0:
                            first = ref_result[0]
                            if isinstance(first, dict) and "name" in first:
                                args[k] = first["name"]
                            elif isinstance(first, str):
                                args[k] = first
                        elif isinstance(ref_result, dict):
                            # 字典结果（如 get_stats / get_file_structure）：取路径/名称字段
                            for field in ("name", "path", "file"):
                                if ref_result.get(field):
                                    args[k] = ref_result[field]
                                    break

            emit("step_start", {
                "step": step.get("step", 0),
                "tool": tool_name,
                "args": args,
                "purpose": purpose,
            })
            result = self._execute_tool(tool_name, args)
            emit("step_done", {
                "step": step.get("step", 0),
                "tool": tool_name,
                "result": result,
            })

            step_record = {
                "step": step.get("step", 0),
                "tool": tool_name,
                "args": args,
                "purpose": purpose,
                "result": result,
            }
            steps_taken.append(step_record)
            all_data[f"step_{step.get('step', 0)}_{tool_name}"] = result

        # ── 阶段 3: 合成 ──
        emit("synthesize", None)
        steps_summary = "\n".join(
            f"步骤{s['step']}: {s['tool']}({json.dumps(s['args'], ensure_ascii=False)}) "
            f"→ {_summarize_result(s.get('result'))}"
            for s in steps_taken
        )
        synthesis = self._call_llm(
            SYNTHESIZER_PROMPT,
            f"问题: {question}\n\n工具调用结果:\n{steps_summary}",
            temp=0.1, max_tokens=2048
        )
        emit("answer", synthesis)

        return {
            "answer": synthesis,
            "steps": steps_taken,
            "raw_data": all_data,
        }

    def _auto_plan(self, question: str) -> List[Dict]:
        """自动生成简单计划（不依赖 LLM 规划）"""
        # detect_intent 等在文件顶已导入，这里直接用
        view_match = re.search(r'(?:看|查看|列出|列一下|展示|显示|有什么|包含).{0,10}?([A-Z][a-zA-Z0-9_]{1,}|[a-z][a-zA-Z0-9_]{2,}).{0,10}?(?:函数|方法|功能|内容|有什么|目录|模块|文件)', question)
        if view_match:
            module_hint = view_match.group(1)
            return [
                {"step": 1, "tool": "get_file_structure",
                 "args": {"file_hint": module_hint},
                 "purpose": f"查看 {module_hint} 相关文件的所有函数"},
                {"step": 2, "tool": "search_functions",
                 "args": {"keyword": module_hint},
                 "purpose": f"搜索名称含 {module_hint} 的函数"},
            ]

        # 特殊处理"工具"类问题：先看项目概览，再找工具模块源码
        if "工具" in question or "tool" in question.lower():
            base_plan = [
                {"step": 1, "tool": "overview",
                 "args": {},
                 "purpose": "项目概览，识别工具模块和Agent模块的位置"},
                {"step": 2, "tool": "read_file",
                 "args": {"file_path": "", "max_lines": 100},
                 "purpose": "根据概览结果读取工具模块的代码"},
            ]
            # 动态确定工具模块路径
            overview_tool = self.tools.get("overview")
            if overview_tool:
                ov = overview_tool.overview()
                tool_dirs = ov.get("tool_files", [])
                agent_dirs = ov.get("agent_files", [])

                def _non_empty(p: Path) -> bool:
                    """跳过空文件：空 __init__.py 读了白读，还占 5 步上限。"""
                    try:
                        return p.stat().st_size > 0
                    except OSError:
                        return False

                # 用工具目录里的 __init__.py（空文件则改读第一个非空 .py）
                if tool_dirs:
                    td = Path(tool_dirs[0])
                    rel = td.relative_to(Path(self.project_path)) if self.project_path else td
                    base_plan[1]["args"]["file_path"] = f"{rel}/__init__.py"
                    # 读具体工具 Python 文件
                    py_files = sorted(td.glob("*.py"))
                    non_empty_py = [pf for pf in py_files if _non_empty(pf)]
                    if not _non_empty(td / "__init__.py") and non_empty_py:
                        # __init__.py 为空 → 第2步改读第一个真正的工具文件
                        base_plan[1]["args"]["file_path"] = f"{rel}/{non_empty_py[0].name}"
                        base_plan[1]["purpose"] = f"读取工具文件 {non_empty_py[0].name}"
                        for pf in non_empty_py[1:]:
                            base_plan.append({
                                "step": len(base_plan) + 1,
                                "tool": "read_file",
                                "args": {"file_path": f"{rel}/{pf.name}",
                                         "max_lines": 200},
                                "purpose": f"读取工具文件 {pf.name}",
                            })
                    else:
                        for pf in non_empty_py:
                            base_plan.append({
                                "step": len(base_plan) + 1,
                                "tool": "read_file",
                                "args": {"file_path": f"{rel}/{pf.name}",
                                         "max_lines": 200},
                                "purpose": f"读取工具文件 {pf.name}",
                            })

                # 读 Agent 主文件
                if agent_dirs:
                    ad = Path(agent_dirs[0])
                    rel_a = ad.relative_to(Path(self.project_path)) if self.project_path else ad
                    agent_py_files = sorted(ad.glob("*.py"))
                    for apf in agent_py_files:
                        if not _non_empty(apf):
                            continue  # 空文件（如空 __init__.py）跳过
                        if apf.name == "__init__.py":
                            # 先读 __init__ 看导出了什么
                            base_plan.append({
                                "step": len(base_plan) + 1,
                                "tool": "read_file",
                                "args": {"file_path": f"{rel_a}/{apf.name}",
                                         "max_lines": 40},
                                "purpose": "读取Agent模块的__init__.py",
                            })
                        else:
                            base_plan.append({
                                "step": len(base_plan) + 1,
                                "tool": "read_file",
                                "args": {"file_path": f"{rel_a}/{apf.name}",
                                         "max_lines": 200},
                                "purpose": f"读取Agent文件 {apf.name}",
                            })
            vs = self.tools.get("vector")
            if vs and vs.is_available():
                base_plan.append({
                    "step": len(base_plan) + 1, "tool": "search",
                    "args": {"query": question, "n": 5},
                    "purpose": "语义搜索工具相关代码"
                })
            return base_plan

        intent = detect_intent(question)
        fn = extract_function_name(question, self.graph) if self.graph else None
        kw = extract_raw_keyword(question)

        plans = {
            "callers": [{"step": 1, "tool": "get_callers",
                         "args": {"function_name": fn or kw or ""},
                         "purpose": f"查询 {fn or kw} 的调用者"}],
            "callees": [{"step": 1, "tool": "get_callees",
                         "args": {"function_name": fn or kw or ""},
                         "purpose": f"查询 {fn or kw} 调用了谁"}],
            "search": [{"step": 1, "tool": "search_functions",
                        "args": {"keyword": kw or fn or question},
                        "purpose": f"搜索相关函数"}],
            "detail": [{"step": 1, "tool": "get_detail",
                        "args": {"function_name": fn or ""},
                        "purpose": f"查询 {fn} 的详情"}],
            "impact": [{"step": 1, "tool": "analyze_impact",
                        "args": {"name": fn or kw or ""},
                        "purpose": f"分析 {fn or kw} 的影响范围"}],
            "stats": [{"step": 1, "tool": "get_stats",
                       "args": {}, "purpose": "项目统计"}],
            "structure": [{"step": 1, "tool": "get_file_structure",
                           "args": {"file_hint": kw or question},
                           "purpose": "文件结构"}],
        }

        # 向量检索可用时，加一步语义搜索兜底
        base_plan = plans.get(intent, [{
            "step": 1, "tool": "search_functions",
            "args": {"keyword": kw or question},
            "purpose": "搜索相关函数"
        }])

        # 如果向量检索可用，加一步
        vs = self.tools.get("vector")
        if vs and vs.is_available():
            base_plan.append({
                "step": len(base_plan) + 1,
                "tool": "search",
                "args": {"query": question, "n": 3},
                "purpose": "语义搜索"
            })

        return base_plan

# CodeGuard — 代码知识图谱分析 Agent

> 代码知识图谱 + 多 Agent 协作 + 自然语言问答 + 语义搜索

CodeGuard 是一个基于 **Tree-sitter AST 解析 + 图数据库 + LLM 推理**的代码分析工具。它能解析 Python/Java/JS/TS/C/C++ 项目，构建函数调用图、类继承图、模块依赖图，然后通过自然语言问答来分析代码。

---

## ✨ 功能

### 🔍 图查询（核心）
- `get_callers(func)` — 谁调用了这个函数？
- `get_callees(func)` — 这个函数调用了谁？
- `search_functions(keyword)` — 按名称搜索函数
- `get_function_detail(func)` — 函数详情（docstring、参数、位置）
- `analyze_change_impact(func)` — 变更影响分析（谁会被波及）
- `get_file_structure(file)` — 文件结构（类/函数列表）
- `get_stats()` — 项目统计（文件数、函数数、调用边数）

### 🧠 多 Agent 问答（实验性）
- **Planner**：LLM 分析问题 → 拆解工具调用步骤
- **Executor**：依次执行工具，收集结果
- **Synthesizer**：基于工具结果合成最终回答
- 支持 SSE 流式 Web 问答

### 🔎 语义搜索
- Chroma + 通义千问 text-embedding-v3
- 关键词搜不到时自动向量检索兜底

### 🖼️ 可视化
- D3.js 力导向图 / 分层图 / 半圆布局
- 调用链图（搜索模式，选中函数高亮）
- 模块依赖图（核心模块→叶子模块层次化）

### 📦 多语言支持
| 语言 | 解析器 | 状态 |
|------|--------|------|
| Python | Tree-sitter Python | ✅ |
| Java | Tree-sitter Java | ✅ |
| JavaScript/TS/Vue | Tree-sitter JS | ✅ |
| C | Tree-sitter C | ✅ |
| C++ | Tree-sitter C++（无解析器时降级 C） | ✅ |

---

## 🚀 快速开始

```bash
# 安装
pip install -r requirements.txt

# 解析项目并建图
python -m code_guard.cli.main parse D:/Project/MyProject

# 查询函数调用者
python -m code_guard.cli.main query run_code --db code_graph.db

# 变更影响分析
python -m code_guard.cli.main impact get_llm_client --db code_graph.db

# 生成可视化 HTML
python -m code_guard.cli.main viz D:/Project/MyProject -o graph.html

# 多 Agent 问答
python -m code_guard.cli.main agent D:/Project/MyProject "这个项目用到了哪些外部工具？"

# 启动 Web 服务
python -m code_guard.cli.main serve --project D:/Project/MyProject

# 向量索引（语义搜索需要）
python -m code_guard.cli.main index D:/Project/MyProject
```

---

## 📊 评测

CodeGuard 内置了一个评测系统，用预设问题测试代码理解准确率。

### 运行评测

```bash
# 使用默认测试集（Data_Analyst 20题）
python -m code_guard.eval.runner D:/Project/MyProject

# 使用自定义测试集
python -m code_guard.eval.runner D:/Project/MyProject --test-set tests/data_analysis.json
```

### 自定义测试集

评测系统支持加载外部 JSON 测试集，方便你在自己的项目上验证：

```json
{
  "name": "我的项目测试集",
  "questions": [
    {
      "id": 1,
      "category": "callers",
      "question": "谁调用了 load_data 函数？",
      "expected_type": "callers",
      "expected_target": "load_data",
      "min_count": 2,
      "keywords": ["DataProcessor", "main"],
      "description": "查询 load_data 的调用者"
    }
  ]
}
```

JSON 字段说明：

| 字段 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `id` | int | ✅ | 题目编号 |
| `category` | string | ✅ | 分类：callers/callees/search/detail/impact/structure/stats |
| `question` | string | ✅ | 自然语言问题 |
| `expected_type` | string | ✅ | 期望识别到的意图类型 |
| `expected_target` | string | ✅ | 期望查询的目标函数/文件 |
| `min_count` | int | | 最少结果数（不填则不检查计数） |
| `keywords` | string[] | | 答案中应包含的关键词（空数组则不检查） |
| `description` | string | | 题目说明 |

> ⚠️ **评测系统的局限**：
> - 当前基于**中文自然语言问题**测试，关键词匹配 + 意图正则对中文友好
> - keywords 检查比较严格——LLM 回答用词和测试数据期望的词不一致就会被扣分（但可能语义正确）
> - 测试数据中的函数名/类名必须在你目标项目中存在，否则会报"图数据中未找到"
> - 建议：对每个项目单独写测试集，不要直接复用 Data_Analyst 的 20 题

### 评测参考成绩

| 测试集 | 目标项目 | 总分 | 说明 |
|--------|----------|:----:|------|
| 主测试集 (20题) | Data_Analyst | **100%** | 经过多轮调试后达成的上限 |
| 泛化测试 (12题) | Data_Analysis | **89%** | 未调参直接跑的结果 |
| 元测试 (12题) | CodeGuard 自身 | **98%** | 自身代码结构理解 |

---

## ⚙️ 配置

创建 `.env` 文件（不上传 Git）：

```env
LLM_API_KEY=your_api_key_here
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-max
CODE_GUARD_PROJECT=D:/Project/MyProject
```

参考 `.env.example`。

---

## 🧠 评测从 59% → 100% 的经验

### 问题 ①：Tree-sitter 的 `decorated_definition` 坑

`@dataclass`、`@staticmethod` 等装饰器会让 AST 节点类型变成 `decorated_definition`，而不是 `class_definition`/`function_definition`。解析器必须主动解包装。

**修复**：`_unwrap_node()` 递归取内部的 definition 子节点。

### 问题 ②：函数名解析的语义混淆

`extract_function_name` 提取函数名时，会匹配到**图里的完整限定名**（如 `_execute_run_code`），但 callers 查询需要的是**用户问题中的原始词**（如 `run_code`）。两者语义不同，用错就查不到结果。

**修复**：分离 `extract_raw_keyword()`（保留原始词）和 `extract_function_name()`（图匹配名），callers 查询优先用原始词。

### 问题 ③：SQL 精确匹配 vs 模糊匹配

不同项目的 callee 命名风格不同：
- Data_Analyst：`run_code`（简单名）
- Data_Analysis：`agent.run`（带前缀）
- CodeGuard：`conn.execute`（对象方法）

精确匹配 + LIKE 前缀/后缀无法覆盖所有风格。

**修复**：`get_callers` 加入简单名后缀模糊匹配 `LIKE "%run"`。

### 问题 ④：意图检测顺序

"重新构 MemoryManager，需要同步修改哪些调用方？" 同时匹配 impact 和 callers 模式。callers 模式排在前面，导致意图识别错误。

**修复**：调整 INTENT_PATTERNS 顺序，impact 相关模式（含"影响/重构/改动"）优先。

---

## 🏗️ 项目结构

```
code-guard/src/code_guard/
├── agent/              # 多 Agent 模块（编排器 + 工具集）
│   ├── orchestrator.py # Planner → Executor → Synthesizer
│   └── tools.py        # 图查询/向量搜索/源码读取等工具
├── parser/             # AST 解析器（Python/Java/JS/C/C++）
├── graph/              # 图数据库（SQLite + 图查询）
├── analyzer/           # 模块依赖分析
├── vector/             # 向量检索（Chroma）
├── viz/                # D3.js 可视化 HTML 生成
├── eval/               # 评测系统
│   ├── runner.py       # 评测运行器
│   ├── test_set.py     # 默认测试集（Data_Analyst 20题）
│   └── tests/          # 预置 JSON 测试集
├── server/             # Web 服务器（FastAPI）
├── mcp/                # MCP Server 对接
├── cli/                # CLI 入口
└── config/             # 配置
```

---

## 📝 说明

- 本项目中预置的测试集（`eval/tests/` 目录下）基于特定项目设计，**函数名/类名均为该项目特有**，直接在其他项目上跑会不通过。请参考 JSON 格式创建你自己的测试集。
- 多 Agent 问答功能仍在实验阶段，复杂问题可能出现规划不准确的情况。
- 向量检索需要先运行 `code-guard index <项目>` 建立索引。
- 可视化生成的 HTML 文件会缓存在目标项目的 `.codeguard/cache/` 目录下。

---

## 📄 License

MIT

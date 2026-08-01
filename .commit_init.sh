#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

git config user.name "qianshu-203112"
git config user.email "qianshu-203112@users.noreply.github.com"
git branch -M main

if [ -z "$(git log --oneline -1 2>/dev/null)" ]; then
  git add -A
  git commit \
    -m "feat: CodeGuard 代码审查 Agent 初始提交" \
    -m "多语言代码解析建图 + 图搜索/查询(CLI 与 MCP Server)" \
    -m "LLM 质量门禁评测: 5 组测试集" \
    -m "GitHub Actions CI: 安装依赖 + 冒烟测试 + 评测断言全通过" \
    -m "敏感信息不入库(.env/.idea/venv 均已忽略)"
fi

echo "===== 完成 ====="
git log --oneline -1
git branch --show-current

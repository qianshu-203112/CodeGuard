"""
配置 - 从 .env 加载配置

环境变量：
  OPENAI_API_KEY       LLM API 密钥（必填，quality_gate 需要）
  OPENAI_BASE_URL      LLM API 地址（默认：阿里云通义千问）
  LLM_MODEL            模型名（默认：qwen-max）
  CODE_GUARD_PROJECT   要分析的项目路径（MCP Server 使用）
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 尝试加载 .env
_env_path = Path(__file__).parent.parent.parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
else:
    # 回退到系统环境变量
    load_dotenv()


class Settings:
    @property
    def LLM_API_KEY(self) -> str:
        return os.getenv("OPENAI_API_KEY", "")

    @property
    def LLM_BASE_URL(self) -> str:
        return os.getenv("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

    @property
    def LLM_MODEL(self) -> str:
        return os.getenv("LLM_MODEL", "qwen-max")

    @property
    def CODE_GUARD_PROJECT(self) -> str:
        return os.getenv("CODE_GUARD_PROJECT", "")


settings = Settings()

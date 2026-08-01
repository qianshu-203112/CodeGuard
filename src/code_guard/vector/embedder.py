"""
嵌入模型封装 — 用通义千问 text-embedding-v3 将代码文本转向量

使用 OpenAI 兼容接口（阿里云 DashScope）：
  https://help.aliyun.com/zh/model-studio/developer-reference/use-text-embedding-v3
"""
from typing import List, Optional

from code_guard.config.settings import settings


class CodeEmbedder:
    """代码文本嵌入器"""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None,
                 model: str = "text-embedding-v3"):
        self.api_key = api_key or settings.LLM_API_KEY
        self.base_url = base_url or settings.LLM_BASE_URL
        self.model = model
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    def embed(self, text: str) -> List[float]:
        """将单段文本转为向量"""
        resp = self.client.embeddings.create(
            model=self.model,
            input=text,
            encoding_format="float",
        )
        return resp.data[0].embedding

    def embed_batch(self, texts: List[str], batch_size: int = 10) -> List[List[float]]:
        """批量转向量（分批次，每批不超过 batch_size）"""
        if not texts:
            return []
        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            resp = self.client.embeddings.create(
                model=self.model,
                input=batch,
                encoding_format="float",
            )
            indexed = {d.index: d.embedding for d in resp.data}
            all_embeddings.extend(indexed[j] for j in range(len(batch)))
        return all_embeddings

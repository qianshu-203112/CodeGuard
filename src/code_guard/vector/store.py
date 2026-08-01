"""
Chroma 存储层 — 管理向量数据库的增删查
"""
import os
from pathlib import Path
from typing import List, Dict, Optional, Any

import chromadb
from chromadb.config import Settings as ChromaSettings


class VectorStore:
    """向量存储封装"""

    def __init__(self, persist_dir: str = None):
        self.persist_dir = persist_dir or os.path.join(
            str(Path.home()), ".codeguard", "chroma"
        )
        self._client = None
        self._collection = None

    @property
    def client(self):
        if self._client is None:
            os.makedirs(self.persist_dir, exist_ok=True)
            self._client = chromadb.PersistentClient(
                path=self.persist_dir,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        return self._client

    @property
    def collection(self):
        if self._collection is None:
            self._collection = self.client.get_or_create_collection(
                name="codeguard",
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    def add(self, ids: List[str], embeddings: List[List[float]],
            documents: List[str], metadatas: List[Dict]):
        """添加向量"""
        self.collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

    def search(self, query_embedding: List[float],
               n_results: int = 5,
               where: Optional[Dict] = None) -> Dict[str, Any]:
        """搜索最相似的文本"""
        return self.collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            where=where,
        )

    def delete_collection(self):
        """删除整个集合（重建用）"""
        try:
            self.client.delete_collection("codeguard")
        except (ValueError, chromadb.errors.NotFoundError):
            pass
        self._collection = None

    def delete_by_project(self, project_name: str):
        """删除指定项目的所有向量（用于重索引时清理旧数据）"""
        try:
            self.collection.delete(where={"project": project_name})
        except Exception:
            pass  # 首次索引，无数据可删

    def count(self) -> int:
        return self.collection.count()

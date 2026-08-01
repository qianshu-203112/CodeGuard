"""
代码索引器 — 把项目的函数 + 文档注释切块 → 转向量 → 存 Chroma
"""
import hashlib
from pathlib import Path
from typing import List, Dict, Optional

from code_guard.vector.embedder import CodeEmbedder
from code_guard.vector.store import VectorStore


def _make_id(file_path: str, func_name: str, line: int) -> str:
    raw = f"{file_path}:{func_name}:{line}"
    return hashlib.md5(raw.encode()).hexdigest()


def _build_doc(func_info: dict, file_path: str) -> str:
    """构建函数文档文本（用于转向量）"""
    parts = [
        f"函数: {func_info['name']}",
        f"文件: {Path(file_path).name}",
    ]
    if func_info.get("docstring"):
        parts.append(f"说明: {func_info['docstring']}")
    return "\n".join(parts)


def index_project(project_path: str,
                  embedder: Optional[CodeEmbedder] = None,
                  vector_store: Optional[VectorStore] = None) -> int:
    """
    解析项目并将所有函数索引到 Chroma。
    Returns: 索引的函数数量
    """
    from code_guard.parser.ast_parser import parse_project_multilang

    embedder = embedder or CodeEmbedder()
    vector_store = vector_store or VectorStore()

    print(f"解析 {project_path} ...")
    results = parse_project_multilang(project_path)

    funcs = []
    for file_path, result in results.items():
        for f in result.functions:
            funcs.append({
                "file_path": file_path, "name": f.name,
                "qualified_name": f"{Path(file_path).stem}.{f.name}",
                "docstring": f.docstring or "",
                "start_line": f.start_line, "end_line": f.end_line,
            })
        for cls in result.classes:
            for m in cls.methods:
                funcs.append({
                    "file_path": file_path,
                    "name": f"{cls.name}.{m.name}",
                    "qualified_name": f"{Path(file_path).stem}.{cls.name}.{m.name}",
                    "docstring": m.docstring or "",
                    "start_line": m.start_line, "end_line": m.end_line,
                })

    if not funcs:
        print("项目中未发现函数")
        return 0
    print(f"提取 {len(funcs)} 个函数/方法")

    docs = [_build_doc(f, f["file_path"]) for f in funcs]
    ids = [_make_id(f["file_path"], f["name"], f["start_line"]) for f in funcs]
    metadatas = [{
        "file": f["file_path"], "name": f["name"],
        "qualified_name": f["qualified_name"],
        "line": f["start_line"],
        "project": str(Path(project_path).name),
    } for f in funcs]

    # 清除该项目旧索引（保留其他项目的索引）
    project_name = str(Path(project_path).name)
    vector_store.delete_by_project(project_name)
    print(f"已清除 {project_name} 的旧索引，准备重建...")

    print(f"生成向量（共 {len(docs)} 段）...")
    embeddings = embedder.embed_batch(docs)
    batch_size = 50
    for i in range(0, len(ids), batch_size):
        end = min(i + batch_size, len(ids))
        vector_store.add(
            ids=ids[i:end], embeddings=embeddings[i:end],
            documents=docs[i:end], metadatas=metadatas[i:end],
        )
        print(f"  已索引 {end}/{len(ids)}")
    print(f"索引完成: {len(ids)} 个函数 -> Chroma")
    return len(ids)


def search_code(query: str, project_path: str = None,
                n_results: int = 5,
                embedder: Optional[CodeEmbedder] = None,
                vector_store: Optional[VectorStore] = None) -> List[Dict]:
    """向量搜索代码"""
    embedder = embedder or CodeEmbedder()
    vector_store = vector_store or VectorStore()

    query_vec = embedder.embed(query)
    where = None
    if project_path:
        where = {"project": str(Path(project_path).name)}

    result = vector_store.search(query_vec, n_results=n_results, where=where)
    items = []
    if result["ids"] and result["ids"][0]:
        n = len(result["ids"][0])
        for i in range(n):
            items.append({
                "id": result["ids"][0][i],
                "name": result["metadatas"][0][i].get("name", ""),
                "qualified_name": result["metadatas"][0][i].get("qualified_name", ""),
                "file": result["metadatas"][0][i].get("file", ""),
                "line": result["metadatas"][0][i].get("line", ""),
                "score": result["distances"][0][i] if result.get("distances") else 0,
                "doc": (result["documents"][0][i][:300]
                        if result.get("documents") else ""),
            })
    return items

"""增量同步 — 代码变更后只重解析变更文件，合并进已有图，不用全量重扫

与 parse 全量解析的区别：
  parse  全量重建：忽略已有数据，全部重新解析
  sync   增量更新：打开已有图数据库，对比文件内容哈希（SHA1），只处理
        新增 / 变更 / 删除的文件；未变的文件跳过

用法：
  python -m code_guard.cli.main sync <项目路径> --db <图数据库路径>

原理：
  - 图数据库里存 file_hashes(path, sha1) 表，记录每个文件上次解析时的内容哈希
  - 同步时对每个源码文件算当前 sha1，与记录的比对：
      * 相同 → 跳过（未变）
      * 无记录 → 新增文件，解析后载入
      * 不同 → 变更文件，先移除旧数据再载入新解析结果（replace_file）
  - 磁盘上已消失但图里还有的文件 → 整文件移除（remove_file）

为什么用内容哈希而不用 mtime：切换分支 / git checkout 时文件 mtime 可能
不变但内容变了，mtime 不可靠；内容哈希一定正确。代价是大文件要全读，
但对增量场景（每次只读几个文件）开销可忽略。
"""
import hashlib
import os
from pathlib import Path

from code_guard.parser.ast_parser import list_source_files, parse_one_file
from code_guard.graph.code_graph import CodeGraph


def _sha1_of(path: str) -> str:
    """文件内容 SHA1（读二进制，编码无关）。"""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def record_all_hashes(graph: CodeGraph, project_path: str,
                      only_files=None) -> None:
    """给源码文件记录内容哈希，使后续 sync 能识别"未变文件"直接跳过。

    全量 parse 建图后调用。only_files 传入实际成功解析的文件集合
    （parse 的 results.keys()）——解析失败的文件不记哈希，下次 sync 会重试。
    """
    targets = list_source_files(project_path) if only_files is None else sorted(only_files)
    for fp in targets:
        graph.set_file_hash(fp, _sha1_of(fp))


def sync_project(project_path: str, db_path: str, verbose: bool = True) -> dict:
    """增量同步项目到图数据库。

    Args:
        project_path: 项目根目录
        db_path: 图数据库路径（不存在则新建，行为等价首次全量 parse + 记录哈希）
        verbose: 打印每个变更文件的处理

    Returns:
        {"parsed": 新增文件数, "updated": 变更重解析数, "removed": 删除数,
         "unchanged": 未变数, "total_files": 当前磁盘源码文件数}
    """
    graph = CodeGraph(db_path)

    current_files = list_source_files(project_path)
    current_paths = {Path(fp).as_posix() for fp in current_files}
    stats = {"parsed": 0, "updated": 0, "removed": 0,
             "unchanged": 0, "total_files": len(current_files)}

    # 1) 变更 / 新增文件
    for fp in current_files:
        sha = _sha1_of(fp)
        old = graph.get_file_hash(fp)
        if old == sha:
            stats["unchanged"] += 1
            continue
        try:
            result = parse_one_file(fp)
        except Exception as e:
            if verbose:
                print(f"  ⚠️  跳过 {fp}: {e}")
            continue
        if result is None:
            continue
        if old is None:
            stats["parsed"] += 1
            kind = "新增"
        else:
            stats["updated"] += 1
            kind = "变更"
        graph.replace_file(fp, result)
        graph.set_file_hash(fp, sha)
        if verbose:
            print(f"  {kind}: {os.path.basename(fp)}")

    # 2) 磁盘上已删除的文件（图里有记录但当前目录已不存在）
    for indexed in graph.list_indexed_files():
        if indexed not in current_paths:
            graph.remove_file(indexed)
            stats["removed"] += 1
            if verbose:
                print(f"  删除: {os.path.basename(indexed)}")

    graph.close()
    return stats

"""目录文本检索的最小实现。

用法：
    python -m local_search <目录> <关键词>

仅依赖 Python 3 标准库，离线运行，只读扫描源文件，不生成索引。
"""

import json
import os
import sys
from collections.abc import Sequence

SUPPORTED_SUFFIXES = (".txt", ".md")
CONTEXT_CHARS = 30


class TraversalError(Exception):
    """目录树无法完成遍历时抛出。"""


def _emit_stderr(message: str) -> None:
    """向标准错误写入一行，遇到无法编码的字符以替换符代替，避免二次失败。"""
    data = (message + "\n").encode("utf-8", errors="replace")
    sys.stderr.buffer.write(data)
    sys.stderr.buffer.flush()


def _fail(message: str) -> int:
    _emit_stderr(message)
    return 2


def _relative_path(path: str, root: str) -> str:
    """相对 root 的路径，统一使用正斜杠。"""
    rel = os.path.relpath(path, root)
    if os.sep != "/":
        rel = rel.replace(os.sep, "/")
    return rel


def _collect_files(root: str) -> list[str]:
    """收集 root 下所有受支持的普通文件；不跟随任何符号链接。"""
    collected: list[str] = []

    def scan(dirpath: str) -> None:
        try:
            with os.scandir(dirpath) as iterator:
                entries = list(iterator)
        except OSError as exc:
            raise TraversalError(str(exc)) from exc

        for entry in entries:
            try:
                # 符号链接（无论指向文件还是目录）一律跳过，不跟随。
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    scan(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    name = entry.name.lower()
                    if name.endswith(SUPPORTED_SUFFIXES):
                        collected.append(entry.path)
            except OSError as exc:
                raise TraversalError(str(exc)) from exc

    scan(root)
    return collected


def _search_file(path: str, keyword: str) -> dict | None:
    """在单个文件中查找首个单行命中，返回结果项；无命中返回 None。"""
    with open(path, "r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.rstrip("\r\n")
            position = line.find(keyword)
            if position == -1:
                continue
            start = max(0, position - CONTEXT_CHARS)
            end = min(len(line), position + len(keyword) + CONTEXT_CHARS)
            return {
                "line": line_number,
                "snippet": line[start:end],
            }
    return None


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    if len(args) != 2:
        return _fail("用法: python -m local_search <目录> <关键词>")

    target_dir, keyword = args

    if not keyword or not keyword.strip():
        return _fail("错误: 关键词为空或全为空白")
    if not os.path.exists(target_dir):
        return _fail(f"错误: 目录不存在: {target_dir}")
    if not os.path.isdir(target_dir):
        return _fail(f"错误: 路径不是目录: {target_dir}")

    try:
        file_paths = _collect_files(target_dir)
    except TraversalError as exc:
        return _fail(f"错误: 无法完成目录遍历 {target_dir}: {exc}")

    results = []
    for path in file_paths:
        rel = _relative_path(path, target_dir)
        try:
            hit = _search_file(path, keyword)
        except UnicodeDecodeError as exc:
            _emit_stderr(f"警告: 跳过文件 {rel}: 无法按 UTF-8 解码 ({exc})")
            continue
        except OSError as exc:
            _emit_stderr(f"警告: 跳过文件 {rel}: 无法读取 ({exc})")
            continue
        if hit is not None:
            results.append({"path": rel, "line": hit["line"], "snippet": hit["snippet"]})

    results.sort(key=lambda item: item["path"])

    payload = json.dumps(results, ensure_ascii=False)
    sys.stdout.buffer.write(payload.encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()
    return 0

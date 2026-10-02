"""目录文本检索的最小实现。

用法：
    python -m local_search <目录> <关键词> [--path-contains <路径片段>]

仅依赖 Python 3 标准库，离线运行，只读扫描源文件，不生成索引。
"""

import json
import os
import sys
from collections.abc import Sequence

SUPPORTED_SUFFIXES = (".txt", ".md")
CONTEXT_CHARS = 30
PATH_OPTION = "--path-contains"
USAGE = "用法: python -m local_search <目录> <关键词> [--path-contains <路径片段>]"


class TraversalError(Exception):
    """目录树无法完成遍历时抛出。"""


class ArgumentError(Exception):
    """命令行参数不合法时抛出，消息写入标准错误。"""


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
    """在单个文件中查找首个单行命中，返回结果项；无命中返回 None。

    先完整读取并按 UTF-8 解码整个文件：非法字节无论出现在命中之前、之后
    还是文件末尾（包括末尾截断的多字节字符），都会抛出 UnicodeDecodeError，
    由调用方跳过该文件并告警，不会返回该文件的任何命中。
    """
    with open(path, "r", encoding="utf-8") as handle:
        content = handle.read()
    for line_number, raw_line in enumerate(content.split("\n"), start=1):
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


def _parse_args(args: list[str]) -> tuple[str, str, str | None]:
    """解析参数：恰好两个位置参数，其后可出现一次 --path-contains <片段>。

    不使用通用选项解析：两个位置参数按字面取值，因此以连字符开头的
    关键词仍是字面文本。选项只允许出现在两个位置参数之后。
    """
    if len(args) < 2:
        raise ArgumentError(USAGE)

    target_dir, keyword = args[0], args[1]
    rest = args[2:]

    path_contains: str | None = None
    index = 0
    while index < len(rest):
        token = rest[index]
        if token == PATH_OPTION:
            if path_contains is not None:
                raise ArgumentError(f"错误: 选项 {PATH_OPTION} 只能指定一次")
            if index + 1 >= len(rest):
                raise ArgumentError(f"错误: 选项 {PATH_OPTION} 缺少值")
            path_contains = rest[index + 1]
            index += 2
        else:
            raise ArgumentError(f"错误: 无法识别的参数: {token}")

    return target_dir, keyword, path_contains


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    try:
        target_dir, keyword, path_contains = _parse_args(args)
    except ArgumentError as exc:
        return _fail(str(exc))

    if not keyword or not keyword.strip():
        return _fail("错误: 关键词为空或全为空白")
    if path_contains is not None and not path_contains.strip():
        return _fail(f"错误: {PATH_OPTION} 的路径片段为空或全为空白")
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
        # 先按路径决定文件是否参与内容检索：被排除的文件既不读取也不校验，
        # 因此即使无法读取或含非法 UTF-8 字节也不会产生该文件的告警。
        if path_contains is not None and path_contains not in rel:
            continue
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

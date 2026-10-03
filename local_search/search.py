"""目录文本检索的最小实现。

用法：
    python -m local_search <目录> <关键词> [--path-contains <路径片段>]
                           [--ignore-case] [--all-lines]

仅依赖 Python 3 标准库，离线运行，只读扫描源文件，不生成索引。
"""

import json
import os
import sys
from collections.abc import Sequence

SUPPORTED_SUFFIXES = (".txt", ".md")
CONTEXT_CHARS = 30
PATH_OPTION = "--path-contains"
IGNORE_CASE_OPTION = "--ignore-case"
ALL_LINES_OPTION = "--all-lines"
USAGE = (
    "用法: python -m local_search <目录> <关键词> "
    "[--path-contains <路径片段>] [--ignore-case] [--all-lines]"
)

# 仅折叠 ASCII 的 A-Z 到 a-z；translate 按码点一一映射，不产生多字符展开
# （因此 ß 不会变成 ss），也不影响 É 等非 ASCII 字符。
_ASCII_UPPER_TO_LOWER = str.maketrans(
    {code: code + 32 for code in range(ord("A"), ord("Z") + 1)}
)


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


def _ascii_lower(text: str) -> str:
    """仅将 ASCII A-Z 转为小写，其他码点（含 É、é、ß 等）原样保留。"""
    return text.translate(_ASCII_UPPER_TO_LOWER)


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


def _search_file(
    path: str,
    keyword: str,
    ignore_case: bool = False,
    all_lines: bool = False,
) -> list[dict]:
    """在单个文件中查找单行命中，返回结果项列表（无命中返回空列表）。

    先完整读取并按 UTF-8 解码整个文件：非法字节无论出现在命中之前、之后
    还是文件末尾（包括末尾截断的多字节字符），都会抛出 UnicodeDecodeError，
    由调用方跳过该文件并告警，不会返回该文件的任何命中。

    ignore_case 为真时，仅把 ASCII 的 A-Z/a-z 视为同一字符；其他字符仍精确
    比较（É 不匹配 é、ß 不匹配 ss）。匹配只用于定位，返回的片段始终取自
    源文本，保留原始大小写。

    all_lines 为假（默认）时只返回按行号、行内位置确定的首个命中；
    all_lines 为真时每个命中行各返回一项，按行号递增排列。同一行多次出现
    关键词仍只产生一项，片段以该行最左侧命中为中心。
    """
    with open(path, "r", encoding="utf-8") as handle:
        content = handle.read()
    folded_keyword = _ascii_lower(keyword) if ignore_case else keyword
    hits: list[dict] = []
    for line_number, raw_line in enumerate(content.split("\n"), start=1):
        line = raw_line.rstrip("\r\n")
        if ignore_case:
            position = _ascii_lower(line).find(folded_keyword)
        else:
            position = line.find(keyword)
        if position == -1:
            continue
        start = max(0, position - CONTEXT_CHARS)
        end = min(len(line), position + len(keyword) + CONTEXT_CHARS)
        hits.append(
            {
                "line": line_number,
                "snippet": line[start:end],
            }
        )
        if not all_lines:
            break
    return hits


def _parse_args(args: list[str]) -> tuple[str, str, str | None, bool, bool]:
    """解析参数：恰好两个位置参数，其后可出现选项。

    支持的选项：
    - ``--path-contains <片段>``：路径片段取紧随选项的一个参数，该值即使
      看起来像选项（包括 ``--ignore-case``、``--all-lines``）也仍是字面值；
    - ``--ignore-case``：无取值的开关，可重复指定即报错；
    - ``--all-lines``：无取值的开关，每个命中行各返回一项，可重复指定即报错。

    选项只允许出现在两个位置参数之后，相互先后顺序不限。不使用通用
    选项解析：两个位置参数按字面取值，因此以连字符开头的关键词（即使恰好
    为 ``--ignore-case`` 或 ``--all-lines``）仍是字面文本。
    """
    if len(args) < 2:
        raise ArgumentError(USAGE)

    target_dir, keyword = args[0], args[1]
    rest = args[2:]

    path_contains: str | None = None
    ignore_case = False
    all_lines = False
    index = 0
    while index < len(rest):
        token = rest[index]
        if token == PATH_OPTION:
            if path_contains is not None:
                raise ArgumentError(f"错误: 选项 {PATH_OPTION} 只能指定一次")
            if index + 1 >= len(rest):
                raise ArgumentError(f"错误: 选项 {PATH_OPTION} 缺少值")
            # 取值始终按字面消费，不把它解释成任何开关。
            path_contains = rest[index + 1]
            index += 2
        elif token == IGNORE_CASE_OPTION:
            if ignore_case:
                raise ArgumentError(f"错误: 选项 {IGNORE_CASE_OPTION} 只能指定一次")
            ignore_case = True
            index += 1
        elif token == ALL_LINES_OPTION:
            if all_lines:
                raise ArgumentError(f"错误: 选项 {ALL_LINES_OPTION} 只能指定一次")
            all_lines = True
            index += 1
        else:
            raise ArgumentError(f"错误: 无法识别的参数: {token}")

    return target_dir, keyword, path_contains, ignore_case, all_lines


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    try:
        target_dir, keyword, path_contains, ignore_case, all_lines = _parse_args(args)
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
        # 路径筛选始终区分大小写，不受 --ignore-case 影响。
        if path_contains is not None and path_contains not in rel:
            continue
        try:
            hits = _search_file(path, keyword, ignore_case, all_lines)
        except UnicodeDecodeError as exc:
            _emit_stderr(f"警告: 跳过文件 {rel}: 无法按 UTF-8 解码 ({exc})")
            continue
        except OSError as exc:
            _emit_stderr(f"警告: 跳过文件 {rel}: 无法读取 ({exc})")
            continue
        for hit in hits:
            results.append({"path": rel, "line": hit["line"], "snippet": hit["snippet"]})

    # 先按路径的区分大小写 Unicode 码点序，同一路径再按行号递增。
    results.sort(key=lambda item: (item["path"], item["line"]))

    payload = json.dumps(results, ensure_ascii=False)
    sys.stdout.buffer.write(payload.encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()
    return 0

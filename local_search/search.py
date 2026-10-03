"""目录文本检索的最小实现。

用法：
    python -m local_search <目录> <关键词> [--path-contains <路径片段>]
                           [--path-excludes <路径片段>]
                           [--ignore-case] [--all-lines]
                           [--context-chars <0-200>]

仅依赖 Python 3 标准库，离线运行，只读扫描源文件，不生成索引。
"""

import json
import os
import sys
from collections.abc import Sequence

SUPPORTED_SUFFIXES = (".txt", ".md")
DEFAULT_CONTEXT_CHARS = 30
MAX_CONTEXT_CHARS = 200
PATH_OPTION = "--path-contains"
PATH_EXCLUDES_OPTION = "--path-excludes"
IGNORE_CASE_OPTION = "--ignore-case"
ALL_LINES_OPTION = "--all-lines"
CONTEXT_CHARS_OPTION = "--context-chars"
USAGE = (
    "用法: python -m local_search <目录> <关键词> "
    "[--path-contains <路径片段>] [--path-excludes <路径片段>] "
    "[--ignore-case] [--all-lines] "
    "[--context-chars <0-200>]"
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


def _parse_context_chars(raw: str) -> int:
    """解析 --context-chars 的取值：非空 ASCII 十进制数字串，0 到 200。

    允许前导零（如 ``007`` 即 7），前导零的数量不影响数值，也不设字符串
    长度上限；首尾空白、正负号、小数点、非 ASCII 数字（如全角数字）一律
    拒绝。

    Python 3.11 及以上默认拒绝把超过 4300 位的数字串直接传给 ``int()``，
    因此不能对超长取值直接转换。合法数值至多三位，先剔除不影响数值的
    前导零：剩余位数超过三位必然大于 200，按超范围处理；其余至多三位，
    转换不受整数长度限制影响，始终按数值而非字符串长度判断合法性。
    """
    if not raw or not raw.isascii() or not raw.isdecimal():
        raise ArgumentError(
            f"错误: 选项 {CONTEXT_CHARS_OPTION} 的值必须是非空的 ASCII 十进制数字串: {raw!r}"
        )
    digits = raw.lstrip("0")
    if len(digits) > 3:
        raise ArgumentError(
            f"错误: 选项 {CONTEXT_CHARS_OPTION} 的值超出范围 0-{MAX_CONTEXT_CHARS}: {raw!r}"
        )
    value = int(digits) if digits else 0
    if value > MAX_CONTEXT_CHARS:
        raise ArgumentError(
            f"错误: 选项 {CONTEXT_CHARS_OPTION} 的值超出范围 0-{MAX_CONTEXT_CHARS}: {raw!r}"
        )
    return value


def _search_file(
    path: str,
    keyword: str,
    ignore_case: bool = False,
    all_lines: bool = False,
    context_chars: int = DEFAULT_CONTEXT_CHARS,
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

    context_chars 为命中关键词前后各保留的码点数（按 Unicode 码点计，
    中文与补充平面字符各算一个；关键词自身长度不计入额度），到行首或行尾
    停止，不借用相邻行也不添加省略号。为 0 时片段只保留完整命中关键词。
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
        start = max(0, position - context_chars)
        end = min(len(line), position + len(keyword) + context_chars)
        hits.append(
            {
                "line": line_number,
                "snippet": line[start:end],
            }
        )
        if not all_lines:
            break
    return hits


def _parse_args(
    args: list[str],
) -> tuple[str, str, str | None, str | None, bool, bool, int]:
    """解析参数：恰好两个位置参数，其后可出现选项。

    支持的选项：
    - ``--path-contains <片段>``：路径片段取紧随选项的一个参数，该值即使
      看起来像选项（包括 ``--ignore-case``、``--all-lines``、
      ``--context-chars``、``--path-excludes``）也仍是字面值；
    - ``--path-excludes <片段>``：路径排除片段，同样取紧随选项的一个
      参数并按字面消费，不解释成任何开关；与 ``--path-contains`` 同用时，
      文件须符合包含条件且不符合排除条件才参与内容检索；
    - ``--ignore-case``：无取值的开关，可重复指定即报错；
    - ``--all-lines``：无取值的开关，每个命中行各返回一项，可重复指定即报错；
    - ``--context-chars <0-200>``：片段上下文的码点数，取紧随选项的一个
      参数，该值同样按字面消费（即使看起来像选项），随后按格式校验；
      缺省为 30，可重复指定即报错。

    选项只允许出现在两个位置参数之后，相互先后顺序不限。不使用通用
    选项解析：两个位置参数按字面取值，因此以连字符开头的关键词（即使恰好
    为 ``--ignore-case``、``--all-lines`` 或 ``--context-chars``）仍是
    字面文本。
    """
    if len(args) < 2:
        raise ArgumentError(USAGE)

    target_dir, keyword = args[0], args[1]
    rest = args[2:]

    path_contains: str | None = None
    path_excludes: str | None = None
    ignore_case = False
    all_lines = False
    context_chars = DEFAULT_CONTEXT_CHARS
    context_chars_seen = False
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
        elif token == PATH_EXCLUDES_OPTION:
            if path_excludes is not None:
                raise ArgumentError(f"错误: 选项 {PATH_EXCLUDES_OPTION} 只能指定一次")
            if index + 1 >= len(rest):
                raise ArgumentError(f"错误: 选项 {PATH_EXCLUDES_OPTION} 缺少值")
            # 取值始终按字面消费，不把它解释成任何开关。
            path_excludes = rest[index + 1]
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
        elif token == CONTEXT_CHARS_OPTION:
            if context_chars_seen:
                raise ArgumentError(f"错误: 选项 {CONTEXT_CHARS_OPTION} 只能指定一次")
            context_chars_seen = True
            if index + 1 >= len(rest):
                raise ArgumentError(f"错误: 选项 {CONTEXT_CHARS_OPTION} 缺少值")
            # 取值始终按字面消费，不把它解释成任何开关。
            context_chars = _parse_context_chars(rest[index + 1])
            index += 2
        else:
            raise ArgumentError(f"错误: 无法识别的参数: {token}")

    return (
        target_dir,
        keyword,
        path_contains,
        path_excludes,
        ignore_case,
        all_lines,
        context_chars,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    try:
        (
            target_dir,
            keyword,
            path_contains,
            path_excludes,
            ignore_case,
            all_lines,
            context_chars,
        ) = _parse_args(args)
    except ArgumentError as exc:
        return _fail(str(exc))

    if not keyword or not keyword.strip():
        return _fail("错误: 关键词为空或全为空白")
    if path_contains is not None and not path_contains.strip():
        return _fail(f"错误: {PATH_OPTION} 的路径片段为空或全为空白")
    if path_excludes is not None and not path_excludes.strip():
        return _fail(f"错误: {PATH_EXCLUDES_OPTION} 的路径片段为空或全为空白")
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
        # 路径筛选始终区分大小写，不受 --ignore-case 影响；包含与排除同用时，
        # 文件须符合包含条件且不符合排除条件。
        if path_contains is not None and path_contains not in rel:
            continue
        if path_excludes is not None and path_excludes in rel:
            continue
        try:
            hits = _search_file(path, keyword, ignore_case, all_lines, context_chars)
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

"""目录文本检索的最小实现。

用法：
    python -m local_search <目录> <关键词> [--path-contains <路径片段>]
                           [--path-excludes <路径片段>]
                           [--ignore-case] [--all-lines]
                           [--context-chars <0-200>]
                           [--file-type <txt|md>]
                           [--and-keyword <附加关键词>]

仅依赖 Python 3 标准库，离线运行，只读扫描源文件，不生成索引。
"""

import json
import os
import sys
from collections.abc import Callable, Sequence
from typing import Any

SUPPORTED_SUFFIXES = (".txt", ".md")
DEFAULT_CONTEXT_CHARS = 30
MAX_CONTEXT_CHARS = 200
PATH_OPTION = "--path-contains"
PATH_EXCLUDES_OPTION = "--path-excludes"
IGNORE_CASE_OPTION = "--ignore-case"
ALL_LINES_OPTION = "--all-lines"
CONTEXT_CHARS_OPTION = "--context-chars"
FILE_TYPE_OPTION = "--file-type"
AND_KEYWORD_OPTION = "--and-keyword"
# --file-type 的合法小写字面量到受支持扩展名（扩展名比较仍忽略大小写）。
FILE_TYPE_SUFFIXES = {"txt": (".txt",), "md": (".md",)}
USAGE = (
    "用法: python -m local_search <目录> <关键词> "
    "[--path-contains <路径片段>] [--path-excludes <路径片段>] "
    "[--ignore-case] [--all-lines] "
    "[--context-chars <0-200>] [--file-type <txt|md>] "
    "[--and-keyword <附加关键词>]"
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


def _collect_files(root: str, suffixes: Sequence[str] = SUPPORTED_SUFFIXES) -> list[str]:
    """收集 root 下扩展名为 suffixes 之一的普通文件；不跟随任何符号链接。

    扩展名比较忽略大小写；suffixes 为 :data:`SUPPORTED_SUFFIXES` 的子集，
    由 --file-type 限定为单种格式时只收集该格式的文件。
    """
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
                    if name.endswith(suffixes):
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


def _parse_file_type(raw: str) -> str:
    """解析 --file-type 的取值：仅接受小写字面值 ``txt`` 或 ``md``。

    空值、纯空白、首尾带空白、大写（如 ``TXT``、``Md``）以及其他格式
    （如 ``markdown``、``log``、``.txt``）一律拒绝；取值按字面消费，
    不做大小写折叠或空白修剪。
    """
    if raw not in FILE_TYPE_SUFFIXES:
        raise ArgumentError(
            f"错误: 选项 {FILE_TYPE_OPTION} 的值必须是小写的 txt 或 md: {raw!r}"
        )
    return raw


def _match_line(search_line: str, keyword: str, and_keyword: str | None) -> int:
    """在单行检索视图中做命中判定，返回主关键词最左侧命中的位置，未命中返回 -1。

    ``search_line`` 是已经按需折叠大小写的行文本，``keyword`` 与
    ``and_keyword`` 是与之对应的关键词形式；区分大小写与忽略大小写两个分支
    只在进入本函数前如何构造这些文本上不同，判定逻辑完全一致。

    and_keyword 不为 None 时，同一行还须包含该附加关键词（位置不限，可与主
    关键词的命中重叠或共用文字），否则同样按未命中处理。
    """
    position = search_line.find(keyword)
    if position == -1:
        return -1
    if and_keyword is not None and and_keyword not in search_line:
        return -1
    return position


def _search_file(
    path: str,
    keyword: str,
    ignore_case: bool = False,
    all_lines: bool = False,
    context_chars: int = DEFAULT_CONTEXT_CHARS,
    and_keyword: str | None = None,
) -> list[dict]:
    """在单个文件中查找单行命中，返回结果项列表（无命中返回空列表）。

    先完整读取并按 UTF-8 解码整个文件：非法字节无论出现在命中之前、之后
    还是文件末尾（包括末尾截断的多字节字符），都会抛出 UnicodeDecodeError，
    由调用方跳过该文件并告警，不会返回该文件的任何命中。

    ignore_case 为真时，仅把 ASCII 的 A-Z/a-z 视为同一字符；其他字符仍精确
    比较（É 不匹配 é、ß 不匹配 ss）。匹配只用于定位，返回的片段始终取自
    源文本，保留原始大小写。

    and_keyword 不为 None 时，同一行还须包含该附加关键词才算命中：两词均按
    连续字面子串匹配（不拆词、不解释正则或通配符，首尾空格保留），必须分别
    出现在同一行内，分处不同行不能合并；两者出现顺序不限，相同关键词或重叠
    匹配可共用文字。ignore_case 同时作用于两个关键词，仍只折叠 ASCII 字母。
    片段仍只围绕主关键词在该行最左侧的命中，不为展示附加关键词扩大片段。

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
    folded_and_keyword = (
        _ascii_lower(and_keyword) if ignore_case and and_keyword is not None else and_keyword
    )
    hits: list[dict] = []
    for line_number, raw_line in enumerate(content.split("\n"), start=1):
        line = raw_line.rstrip("\r\n")
        # 命中判定统一在“检索视图”上进行：忽略大小写时为 ASCII 折叠后的行，
        # 否则为原行；两种模式共用 _match_line 的同一套判定。
        search_line = _ascii_lower(line) if ignore_case else line
        position = _match_line(search_line, folded_keyword, folded_and_keyword)
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


class _OptionSpec:
    """一个命令行选项的解析规格，``name`` 即它在命令行中的完整记号。

    ``takes_value`` 为真时消费紧随其后的一个参数，该参数始终按字面值获取，
    即使看起来像开关也不再二次解释；``validate`` 若给出，则对取到的字面值
    做格式与范围校验，返回最终存入解析结果的值。``default`` 是选项缺省时
    解析结果中使用的值。
    """

    def __init__(
        self,
        name: str,
        takes_value: bool,
        default: Any = None,
        validate: Callable[[str], Any] | None = None,
    ) -> None:
        self.name = name
        self.takes_value = takes_value
        self.default = default
        self.validate = validate


# 七个选项共用同一套规则（至多出现一次；带值选项缺值即报错；取值按字面
# 消费），差异只在“是否带值”“缺省值”和“取值校验”三处，集中在本表声明。
# 新增选项时只需在此追加一行，无需改动扫描循环。
_OPTION_SPECS = (
    _OptionSpec(PATH_OPTION, takes_value=True),
    _OptionSpec(PATH_EXCLUDES_OPTION, takes_value=True),
    _OptionSpec(IGNORE_CASE_OPTION, takes_value=False, default=False),
    _OptionSpec(ALL_LINES_OPTION, takes_value=False, default=False),
    _OptionSpec(
        CONTEXT_CHARS_OPTION,
        takes_value=True,
        default=DEFAULT_CONTEXT_CHARS,
        validate=_parse_context_chars,
    ),
    _OptionSpec(
        FILE_TYPE_OPTION,
        takes_value=True,
        default=None,
        validate=_parse_file_type,
    ),
    _OptionSpec(AND_KEYWORD_OPTION, takes_value=True, default=None),
)
_OPTION_BY_TOKEN = {spec.name: spec for spec in _OPTION_SPECS}


def _parse_args(
    args: list[str],
) -> tuple[str, str, str | None, str | None, bool, bool, int, str | None, str | None]:
    """解析参数：恰好两个位置参数，其后可出现选项。

    支持的选项：
    - ``--path-contains <片段>``：路径包含片段，取紧随选项的一个参数，该值
      即使看起来像选项（包括 ``--path-excludes``、``--ignore-case``、
      ``--all-lines``、``--context-chars``、``--file-type``、
      ``--and-keyword``）也仍是字面值；
    - ``--path-excludes <片段>``：路径排除片段，语义与取值规则同
      ``--path-contains``；文件相对路径包含该片段即被排除，不读取其内容；
    - ``--ignore-case``：无取值的开关，可重复指定即报错；
    - ``--all-lines``：无取值的开关，每个命中行各返回一项，可重复指定即报错；
    - ``--context-chars <0-200>``：片段上下文的码点数，取紧随选项的一个
      参数，该值同样按字面消费（即使看起来像选项），随后按格式校验；
      缺省为 30，可重复指定即报错。
    - ``--file-type <txt|md>``：只检索一种格式，取紧随选项的一个参数，
      该值同样按字面消费（即使看起来像开关也不开启该开关），仅接受小写
      字面值 ``txt`` 或 ``md``；缺省为 ``None``，表示同时检索两种格式，
      缺值、重复指定或非法取值均报错。
    - ``--and-keyword <附加关键词>``：要求同一行同时包含主关键词与该附加
      关键词才算命中，取紧随选项的一个参数，该值同样按字面消费（即使
      看起来像开关，如 ``--all-lines``，也按文本处理而不开启该开关）；
      缺省为 ``None``，表示不做双关键词筛选，缺值或重复指定均报错。

    选项只允许出现在两个位置参数之后，相互先后顺序不限。不使用通用
    选项解析：两个位置参数按字面取值，因此以连字符开头的关键词（即使恰好
    为任一选项记号）仍是字面文本。

    各选项的去重、缺值与取值校验由统一的扫描循环按 :data:`_OPTION_SPECS`
    完成：选项记号按出现顺序查表，重复指定对所有选项都优先报“只能指定
    一次”，带值选项缺紧随参数时报“缺少值”，无法查表的记号按出现位置报
    “无法识别的参数”，因此多个错误并存时报告的仍是扫描到的第一个。
    """
    if len(args) < 2:
        raise ArgumentError(USAGE)

    target_dir, keyword = args[0], args[1]

    values: dict[str, Any] = {spec.name: spec.default for spec in _OPTION_SPECS}
    seen: set[str] = set()
    rest = args[2:]
    index = 0
    while index < len(rest):
        token = rest[index]
        spec = _OPTION_BY_TOKEN.get(token)
        if spec is None:
            raise ArgumentError(f"错误: 无法识别的参数: {token}")
        if spec.name in seen:
            raise ArgumentError(f"错误: 选项 {spec.name} 只能指定一次")
        seen.add(spec.name)
        if not spec.takes_value:
            index += 1
            continue
        if index + 1 >= len(rest):
            raise ArgumentError(f"错误: 选项 {spec.name} 缺少值")
        # 取值始终按字面消费，不把它解释成任何开关。
        raw_value = rest[index + 1]
        values[spec.name] = spec.validate(raw_value) if spec.validate else raw_value
        index += 2

    return (
        target_dir,
        keyword,
        values[PATH_OPTION],
        values[PATH_EXCLUDES_OPTION],
        IGNORE_CASE_OPTION in seen,
        ALL_LINES_OPTION in seen,
        values[CONTEXT_CHARS_OPTION],
        values[FILE_TYPE_OPTION],
        values[AND_KEYWORD_OPTION],
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
            file_type,
            and_keyword,
        ) = _parse_args(args)
    except ArgumentError as exc:
        return _fail(str(exc))

    if not keyword or not keyword.strip():
        return _fail("错误: 关键词为空或全为空白")
    if path_contains is not None and not path_contains.strip():
        return _fail(f"错误: {PATH_OPTION} 的路径片段为空或全为空白")
    if path_excludes is not None and not path_excludes.strip():
        return _fail(f"错误: {PATH_EXCLUDES_OPTION} 的路径片段为空或全为空白")
    if and_keyword is not None and not and_keyword.strip():
        return _fail(f"错误: {AND_KEYWORD_OPTION} 的附加关键词为空或全为空白")
    if not os.path.exists(target_dir):
        return _fail(f"错误: 目录不存在: {target_dir}")
    if not os.path.isdir(target_dir):
        return _fail(f"错误: 路径不是目录: {target_dir}")

    # 未指定 --file-type 时同时收集两种格式；指定后只收集对应扩展名，
    # 扩展名比较仍忽略大小写。被格式条件排除的文件不会进入遍历结果，
    # 因而既不读取也不告警。
    suffixes = FILE_TYPE_SUFFIXES[file_type] if file_type is not None else SUPPORTED_SUFFIXES
    try:
        file_paths = _collect_files(target_dir, suffixes)
    except TraversalError as exc:
        return _fail(f"错误: 无法完成目录遍历 {target_dir}: {exc}")

    results = []
    for path in file_paths:
        rel = _relative_path(path, target_dir)
        # 先按路径决定文件是否参与内容检索：未通过路径筛选的文件既不读取也
        # 不校验，因此即使无法读取或含非法 UTF-8 字节也不会产生该文件的告警。
        # 路径筛选始终区分大小写、按连续字面子串比较，不受 --ignore-case 影响。
        # 同时给出包含与排除片段时，文件须符合包含条件且不符合排除条件。
        if path_contains is not None and path_contains not in rel:
            continue
        if path_excludes is not None and path_excludes in rel:
            continue
        try:
            hits = _search_file(
                path, keyword, ignore_case, all_lines, context_chars, and_keyword
            )
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

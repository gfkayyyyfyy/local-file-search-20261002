"""目录文本检索的最小实现。

用法：
    python -m local_search <目录> <关键词> [--path-contains <路径片段>]
                           [--path-excludes <路径片段>]
                           [--ignore-case] [--all-lines]
                           [--context-chars <0-200>]
                           [--file-type <txt|md>]

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
# --file-type 的合法小写字面取值到扩展名的映射；扩展名匹配仍忽略大小写。
FILE_TYPE_SUFFIXES = {"txt": ".txt", "md": ".md"}
USAGE = (
    "用法: python -m local_search <目录> <关键词> "
    "[--path-contains <路径片段>] [--path-excludes <路径片段>] "
    "[--ignore-case] [--all-lines] "
    "[--context-chars <0-200>] [--file-type <txt|md>]"
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
    """收集 root 下扩展名属于 ``suffixes`` 的普通文件；不跟随任何符号链接。

    扩展名比较忽略大小写：``suffixes`` 中的扩展名统一为小写，文件名先转
    小写再判断后缀。``suffixes`` 缺省为全部受支持格式；``--file-type``
    指定单一格式时，另一种格式在此处即不收集，不读取也不告警。
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
                    if name.endswith(tuple(suffixes)):
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

    按完全相等判断，不做任何修剪或大小写折叠：空值、纯空白、首尾带空白、
    大写取值（如 ``TXT``、``Md``）以及其他格式（如 ``log``、``markdown``）
    一律拒绝。
    """
    if raw not in FILE_TYPE_SUFFIXES:
        raise ArgumentError(
            f"错误: 选项 {FILE_TYPE_OPTION} 的值必须是 txt 或 md: {raw!r}"
        )
    return raw


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


# 各选项共用同一套规则（至多出现一次；带值选项缺值即报错；取值按字面
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
)
_OPTION_BY_TOKEN = {spec.name: spec for spec in _OPTION_SPECS}


def _parse_args(
    args: list[str],
) -> tuple[str, str, str | None, str | None, bool, bool, int, str | None]:
    """解析参数：恰好两个位置参数，其后可出现选项。

    支持的选项：
    - ``--path-contains <片段>``：路径包含片段，取紧随选项的一个参数，该值
      即使看起来像选项（包括 ``--path-excludes``、``--ignore-case``、
      ``--all-lines``、``--context-chars``、``--file-type``）也仍是字面值；
    - ``--path-excludes <片段>``：路径排除片段，语义与取值规则同
      ``--path-contains``；文件相对路径包含该片段即被排除，不读取其内容；
    - ``--ignore-case``：无取值的开关，可重复指定即报错；
    - ``--all-lines``：无取值的开关，每个命中行各返回一项，可重复指定即报错；
    - ``--context-chars <0-200>``：片段上下文的码点数，取紧随选项的一个
      参数，该值同样按字面消费（即使看起来像选项），随后按格式校验；
      缺省为 30，可重复指定即报错；
    - ``--file-type <txt|md>``：只检索一种格式，取紧随选项的一个参数，该值
      同样按字面消费（即使看起来像开关也不开启该开关），只接受小写字面值
      ``txt`` 或 ``md``；缺省为 ``None``，即同时检索两种格式，可重复指定即
      报错。

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

    # file_type 为 None 时收集两种格式；否则只收集指定扩展名的文件，
    # 另一种格式不收集、不读取也不告警；扩展名比较仍忽略大小写。
    suffixes = (
        SUPPORTED_SUFFIXES
        if file_type is None
        else (FILE_TYPE_SUFFIXES[file_type],)
    )
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

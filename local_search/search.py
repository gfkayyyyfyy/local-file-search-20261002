"""目录文本检索的最小实现。

用法：
    python -m local_search <目录> <关键词> [--path-contains <路径片段>]
                           [--path-excludes <路径片段>]
                           [--ignore-case] [--all-lines]
                           [--context-chars <0-200>]
                           [--file-type <txt|md>]
                           [--and-keyword <附加关键词>]
                           [--not-keyword <排除关键词>]
                           [--or-keyword <替代关键词>]
                           [--limit <1-1000>]
                           [--offset <0-1000>]
                           [--format <json|csv>]

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
NOT_KEYWORD_OPTION = "--not-keyword"
OR_KEYWORD_OPTION = "--or-keyword"
LIMIT_OPTION = "--limit"
OFFSET_OPTION = "--offset"
FORMAT_OPTION = "--format"
MIN_LIMIT = 1
MAX_LIMIT = 1000
MIN_OFFSET = 0
MAX_OFFSET = 1000
# --format 的合法小写字面量；缺省与 json 都输出原有 JSON，csv 输出表格文本。
FORMAT_VALUES = ("json", "csv")
# --file-type 的合法小写字面量到受支持扩展名（扩展名比较仍忽略大小写）。
FILE_TYPE_SUFFIXES = {"txt": (".txt",), "md": (".md",)}
USAGE = (
    "用法: python -m local_search <目录> <关键词> "
    "[--path-contains <路径片段>] [--path-excludes <路径片段>] "
    "[--ignore-case] [--all-lines] "
    "[--context-chars <0-200>] [--file-type <txt|md>] "
    "[--and-keyword <附加关键词>] [--not-keyword <排除关键词>] "
    "[--or-keyword <替代关键词>] "
    "[--limit <1-1000>] [--offset <0-1000>] [--format <json|csv>]"
)

# 仅折叠 ASCII 的 A-Z 到 a-z；translate 按码点一一映射，不产生多字符展开
# （因此 ß 不会变成 ss），也不影响 É 等非 ASCII 字符。
_ASCII_UPPER_TO_LOWER = str.maketrans(
    {code: code + 32 for code in range(ord("A"), ord("Z") + 1)}
)

# 平台认可的目录分隔符集合，用于剥离所选路径末尾的分隔符。
_DIRECTORY_SEPARATORS = os.sep + (os.altsep or "")


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


def _parse_decimal_in_range(raw: str, option: str, minimum: int, maximum: int) -> int:
    """解析数字选项的取值：非空 ASCII 十进制数字串，数值在 minimum 到 maximum。

    ``--context-chars`` 与 ``--limit`` 共用同一套规则，集中在本函数维护：

    - 格式：取值必须是非空的 ASCII 十进制数字串，允许任意数量前导零
      （如 ``007`` 即 7），前导零不影响数值，也不设字符串长度上限；
      取值按字面消费、不修剪空白，首尾空白、正负号、小数点、非 ASCII
      数字（如全角数字）一律按格式错误拒绝；
    - 范围：格式合法但数值不在 ``[minimum, maximum]`` 时按超范围处理。

    Python 3.11 及以上默认拒绝把超过 4300 位的数字串直接传给 ``int()``，
    因此不能对超长取值直接转换。maximum 至多四位（1000），先剔除不影响
    数值的前导零：剩余位数超过 maximum 的位数必然大于 maximum，按超范围
    处理；其余至多四位，转换不受整数长度限制影响，始终按数值而非字符串
    长度判断合法性（五千个零仍按 0 接受，五千个 9 按超范围拒绝且不产生
    异常堆栈）。
    """
    if not raw or not raw.isascii() or not raw.isdecimal():
        raise ArgumentError(
            f"错误: 选项 {option} 的值必须是非空的 ASCII 十进制数字串: {raw!r}"
        )
    digits = raw.lstrip("0")
    if len(digits) > len(str(maximum)):
        raise ArgumentError(
            f"错误: 选项 {option} 的值超出范围 {minimum}-{maximum}: {raw!r}"
        )
    value = int(digits) if digits else 0
    if value < minimum or value > maximum:
        raise ArgumentError(
            f"错误: 选项 {option} 的值超出范围 {minimum}-{maximum}: {raw!r}"
        )
    return value


def _parse_context_chars(raw: str) -> int:
    """解析 --context-chars 的取值：非空 ASCII 十进制数字串，0 到 200。

    格式与范围规则与 :func:`_parse_limit` 完全一致，统一由
    :func:`_parse_decimal_in_range` 维护；本选项区间为 0 至
    :data:`MAX_CONTEXT_CHARS`，故全零取值（含任意数量前导零）按 0 接受。
    """
    return _parse_decimal_in_range(raw, CONTEXT_CHARS_OPTION, 0, MAX_CONTEXT_CHARS)


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


def _parse_limit(raw: str) -> int:
    """解析 --limit 的取值：非空 ASCII 十进制数字串，1 到 1000。

    格式规则与 :func:`_parse_context_chars` 完全一致，统一由
    :func:`_parse_decimal_in_range` 维护；本选项区间为
    :data:`MIN_LIMIT` 至 :data:`MAX_LIMIT`，``0``（含 ``000`` 等前导零
    形式）不在范围内，按超范围处理。
    """
    return _parse_decimal_in_range(raw, LIMIT_OPTION, MIN_LIMIT, MAX_LIMIT)


def _parse_offset(raw: str) -> int:
    """解析 --offset 的取值：非空 ASCII 十进制数字串，0 到 1000。

    格式规则与 :func:`_parse_limit` 完全一致，统一由
    :func:`_parse_decimal_in_range` 维护；本选项区间为
    :data:`MIN_OFFSET` 至 :data:`MAX_OFFSET`，``0``（含 ``000`` 等前导零
    形式）在范围内，按 0 接受。
    """
    return _parse_decimal_in_range(raw, OFFSET_OPTION, MIN_OFFSET, MAX_OFFSET)


def _parse_format(raw: str) -> str:
    """解析 --format 的取值：仅接受小写字面值 ``json`` 或 ``csv``。

    空值、纯空白、首尾带空白、大写（如 ``JSON``、``Csv``）以及其他取值
    （如 ``xml``、``tsv``）一律拒绝；取值按字面消费，不做大小写折叠或
    空白修剪，即使看起来像开关（如 ``--all-lines``）也仍作为格式值校验。
    """
    if raw not in FORMAT_VALUES:
        raise ArgumentError(
            f"错误: 选项 {FORMAT_OPTION} 的值必须是小写的 json 或 csv: {raw!r}"
        )
    return raw


def _match_line(
    search_line: str,
    keyword: str,
    or_keyword: str | None,
    and_keyword: str | None,
    not_keyword: str | None,
) -> tuple[int, int] | None:
    """在单行检索视图中做命中判定，返回 ``(片段锚定位置, 命中词长度)``，未命中返回 None。

    ``search_line`` 是已经按需折叠大小写的行文本，``keyword``、``or_keyword``、
    ``and_keyword`` 与 ``not_keyword`` 是与之对应的关键词形式；区分大小写与忽略
    大小写两个分支只在进入本函数前如何构造这些文本上不同，判定逻辑完全一致。

    or_keyword 不为 None 时，行内包含主关键词或替代词之一即满足关键词条件
    （两词均出现或重复出现仍只算一次命中）；片段锚定在两个词各自最左侧命中中
    起始位置更小的一处，起始位置相同（含两词相同）时锚定主关键词，长度按所选
    词计算。or_keyword 为 None 时仍只按主关键词判定，行为与之前完全一致。

    and_keyword 不为 None 时，同一行还须包含该附加关键词（位置不限，可与主
    关键词或替代词的命中重叠或共用文字），否则按未命中处理。not_keyword 不为
    None 时，完整当前行内任何位置出现该排除关键词（包括片段之外）即排除该行，
    按未命中处理；排除词在其他行出现不影响本行。
    """
    main_position = search_line.find(keyword)
    if or_keyword is None:
        if main_position == -1:
            return None
        position, hit_length = main_position, len(keyword)
    else:
        or_position = search_line.find(or_keyword)
        if main_position == -1 and or_position == -1:
            return None
        if or_position == -1 or (main_position != -1 and main_position <= or_position):
            # 主词命中且起始位置不晚于替代词（含同位置）时锚定主词。
            position, hit_length = main_position, len(keyword)
        else:
            position, hit_length = or_position, len(or_keyword)
    if and_keyword is not None and and_keyword not in search_line:
        return None
    if not_keyword is not None and not_keyword in search_line:
        return None
    return position, hit_length


def _search_file(
    path: str,
    keyword: str,
    ignore_case: bool = False,
    all_lines: bool = False,
    context_chars: int = DEFAULT_CONTEXT_CHARS,
    and_keyword: str | None = None,
    not_keyword: str | None = None,
    or_keyword: str | None = None,
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

    not_keyword 不为 None 时，完整当前行内任何位置出现该排除关键词即排除
    该行：排除词同样按连续字面子串匹配（不拆词、不解释正则或通配符，首尾
    空格保留），判断覆盖整行而非仅片段范围，排除词在其他行出现不影响本行。
    ignore_case 同时作用于排除关键词，仍只折叠 ASCII 字母。排除只影响行的
    合格性，不改变片段的取法。

    or_keyword 不为 None 时，行内包含主关键词或替代词之一即满足关键词条件：
    替代词同样按连续字面子串匹配（不拆词、不解释正则或通配符，首尾空格保留），
    每行独立判断，两词均出现或重复出现仍只产生一项。ignore_case 同时作用于
    替代词，仍只折叠 ASCII 字母。片段锚定在主词与替代词各自最左侧命中中起始
    位置更小的一处，起始位置相同（含两词相同）时锚定主关键词，片段长度按所选
    词计算。or_keyword 为 None 时检索与片段行为与之前完全一致。

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
    folded_not_keyword = (
        _ascii_lower(not_keyword) if ignore_case and not_keyword is not None else not_keyword
    )
    folded_or_keyword = (
        _ascii_lower(or_keyword) if ignore_case and or_keyword is not None else or_keyword
    )
    hits: list[dict] = []
    for line_number, raw_line in enumerate(content.split("\n"), start=1):
        line = raw_line.rstrip("\r\n")
        # 命中判定统一在“检索视图”上进行：忽略大小写时为 ASCII 折叠后的行，
        # 否则为原行；两种模式共用 _match_line 的同一套判定。
        search_line = _ascii_lower(line) if ignore_case else line
        match = _match_line(
            search_line, folded_keyword, folded_or_keyword,
            folded_and_keyword, folded_not_keyword,
        )
        if match is None:
            continue
        position, hit_length = match
        start = max(0, position - context_chars)
        end = min(len(line), position + hit_length + context_chars)
        hits.append(
            {
                "line": line_number,
                "snippet": line[start:end],
            }
        )
        if not all_lines:
            break
    return hits


def _csv_field(value: str) -> str:
    """按 RFC 4180 转义单个 CSV 字段。

    字段含逗号、双引号或换行（``\\r``/``\\n``）时整体用双引号包围，内部
    双引号写成两个双引号；其余字符（含中文、制表符等）保持原样。
    """
    if any(char in value for char in ',"\r\n'):
        return '"' + value.replace('"', '""') + '"'
    return value


def _render_csv(results: list[dict]) -> bytes:
    """把结果项序列化为不带 BOM 的 UTF-8 CSV 字节串。

    表头固定为 ``path,line,snippet``，每个命中项一条记录，每条记录（含
    表头）以 CRLF 结束；无命中时只输出表头及其 CRLF。列值沿用结果项中的
    相对路径、行号与片段，不重新加工。
    """
    lines = ["path,line,snippet"]
    for item in results:
        lines.append(
            ",".join(
                (
                    _csv_field(item["path"]),
                    str(item["line"]),
                    _csv_field(item["snippet"]),
                )
            )
        )
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


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


# 十二个选项共用同一套规则（至多出现一次；带值选项缺值即报错；取值按字面
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
    _OptionSpec(NOT_KEYWORD_OPTION, takes_value=True, default=None),
    _OptionSpec(OR_KEYWORD_OPTION, takes_value=True, default=None),
    _OptionSpec(
        LIMIT_OPTION,
        takes_value=True,
        default=None,
        validate=_parse_limit,
    ),
    _OptionSpec(
        OFFSET_OPTION,
        takes_value=True,
        default=0,
        validate=_parse_offset,
    ),
    _OptionSpec(
        FORMAT_OPTION,
        takes_value=True,
        default="json",
        validate=_parse_format,
    ),
)
_OPTION_BY_TOKEN = {spec.name: spec for spec in _OPTION_SPECS}


def _parse_args(
    args: list[str],
) -> tuple[
    str, str, str | None, str | None, bool, bool, int, str | None, str | None,
    str | None, str | None, int | None, int, str,
]:
    """解析参数：恰好两个位置参数，其后可出现选项。

    支持的选项：
    - ``--path-contains <片段>``：路径包含片段，取紧随选项的一个参数，该值
      即使看起来像选项（包括 ``--path-excludes``、``--ignore-case``、
      ``--all-lines``、``--context-chars``、``--file-type``、
      ``--and-keyword``、``--not-keyword``、``--limit``、``--offset``、
      ``--format``）也仍是字面值；
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
    - ``--not-keyword <排除关键词>``：要求完整当前行不包含该排除关键词
      才算命中，取紧随选项的一个参数，该值同样按字面消费（即使看起来像
      开关，如 ``--all-lines``，也按文本处理而不开启该开关）；缺省为
      ``None``，表示不做排除词筛选，缺值或重复指定均报错。
    - ``--or-keyword <替代关键词>``：行内包含主关键词或该替代词之一即
      满足关键词条件，取紧随选项的一个参数，该值同样按字面消费（即使
      看起来像开关，如 ``--all-lines``，也按文本处理而不开启该开关）；
      缺省为 ``None``，表示不做替代词检索，缺值或重复指定均报错。
    - ``--limit <1-1000>``：结果数量上限，取紧随选项的一个参数，该值同样
      按字面消费（即使看起来像开关，如 ``--all-lines``，也作为取值校验而
      不开启该开关）；取值只接受非空的 ASCII 十进制数字串，允许前导零，
      范围 1 至 1000；缺省为 ``None``，表示不限制结果数量，缺值、重复
      指定或非法取值均报错。
    - ``--offset <0-1000>``：结果起始偏移，取紧随选项的一个参数，该值同样
      按字面消费（即使看起来像开关，如 ``--all-lines``，也作为取值校验而
      不开启该开关）；取值只接受非空的 ASCII 十进制数字串，允许前导零，
      范围 0 至 1000；缺省为 ``0``，表示不跳过任何结果项，缺值、重复
      指定或非法取值均报错。
    - ``--format <json|csv>``：输出格式，取紧随选项的一个参数，该值同样
      按字面消费（即使看起来像开关，如 ``--all-lines``，也作为格式值校验
      而不开启该开关）；仅接受小写字面值 ``json`` 或 ``csv``；缺省为
      ``json``，输出原有 JSON 数组，``csv`` 时输出表头为
      ``path,line,snippet`` 的 CSV 文本；格式选择只影响序列化，不改变
      检索、排序与 ``--limit`` 截断行为；缺值、重复指定或非法取值均报错。

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
        values[NOT_KEYWORD_OPTION],
        values[OR_KEYWORD_OPTION],
        values[LIMIT_OPTION],
        values[OFFSET_OPTION],
        values[FORMAT_OPTION],
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
            not_keyword,
            or_keyword,
            limit,
            offset,
            output_format,
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
    if not_keyword is not None and not not_keyword.strip():
        return _fail(f"错误: {NOT_KEYWORD_OPTION} 的排除关键词为空或全为空白")
    if or_keyword is not None and not or_keyword.strip():
        return _fail(f"错误: {OR_KEYWORD_OPTION} 的替代关键词为空或全为空白")
    # 不跟随符号链接的规则同样覆盖所选目录本身：末级是符号链接时一律拒绝，
    # 无论链接指向目录、普通文件还是已失效。判定在存在性与目录检查之前，
    # 使失效链接与指向文件的链接也按本规则报错，而非报“目录不存在”或
    # “路径不是目录”；普通缺失路径与普通文件路径不是链接，仍走原有检查。
    # 末尾的目录分隔符会让 lstat 跟随链接，先剥离再判定；错误说明中保留
    # 用户传入的原始写法。拒绝发生在目录枚举与文件读取之前。
    link_check_path = target_dir.rstrip(_DIRECTORY_SEPARATORS) or target_dir
    if os.path.islink(link_check_path):
        return _fail(f"错误: 所选目录不能是符号链接: {target_dir}")
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
                path, keyword, ignore_case, all_lines, context_chars, and_keyword,
                not_keyword, or_keyword,
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

    # --offset 与 --limit 在完整排序之后依次截取：先跳过排序后结果序列的前
    # N 项，再对剩余项应用数量上限。截断依据是排序后的结果序列而非目录扫描
    # 顺序。扫描与告警不受偏移或上限影响——即使结果被全部跳过或已足够，候选
    # 坏文件的既有告警仍照常产生，因此截取只在全部文件处理完、排序后进行。
    # 偏移等于或大于合格项总数时剩余为空，与无命中一样输出空结果。
    if offset:
        results = results[offset:]
    if limit is not None:
        results = results[:limit]

    # 格式选择只影响序列化：检索、排序与截断在两种格式下完全一致。
    if output_format == "csv":
        payload = _render_csv(results)
    else:
        payload = json.dumps(results, ensure_ascii=False).encode("utf-8") + b"\n"
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()
    return 0

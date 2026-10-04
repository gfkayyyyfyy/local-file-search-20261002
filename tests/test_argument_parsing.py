"""参数解析流程的端到端回归测试（重构后集中验证共用规则）。

``local_search.search`` 中六个选项的“去重 / 缺值 / 取值校验”改由统一的
规格表扫描循环完成后，本文件固定其对外可观察行为，防止重构改变：

- 前两个参数按字面解释：即使关键词恰为 ``--all-lines`` 等选项记号，也仍按
  字面文本检索；选项只允许出现在两个位置参数之后，相互顺序不限；
- 带值选项的值始终按字面消费：``--path-contains --ignore-case`` 中的
  ``--ignore-case`` 是路径片段而非开关，``--context-chars``、
  ``--file-type`` 的值看似开关时也先被取值、再按各自格式报错；
- 六个选项各自重复指定都报“只能指定一次”，四个带值选项缺紧随参数都报
  “缺少值”，无法识别的记号报“无法识别的参数”，且一律报告扫描到的第一个
  错误（多个错误并存时保持既有优先级）；
- ``--context-chars`` 只接受非空 ASCII 十进制数字串、允许任意数量前导零、
  范围 0-200；空白、正负号、小数、非 ASCII 数字、超范围均拒绝；
- ``--file-type`` 只接受小写字面值 ``txt`` 或 ``md``；空值、纯空白、首尾
  空白、大写值及其他格式均拒绝；不指定时为 ``None``（两种格式都检索）；
- 缺位置参数报用法，关键词或路径片段为空 / 全空白报对应文案；以上错误均在
  目录扫描前以退出码 2、空标准输出、单行中文标准错误返回，不出现异常堆栈；
- 同一组选项任意调换顺序，标准输出 / 标准错误 / 退出码完全一致。

资料由测试在 TemporaryDirectory 中独立准备并自动清理，仅使用 Python 标准库、
离线运行。期望值全部以字面量直接写出，不调用任何被测函数来生成期望结果。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from local_search.search import ArgumentError, _parse_args  # noqa: E402

PATH_OPTION = "--path-contains"
EXCLUDES_OPTION = "--path-excludes"
IGNORE_CASE_OPTION = "--ignore-case"
ALL_LINES_OPTION = "--all-lines"
CONTEXT_CHARS_OPTION = "--context-chars"
FILE_TYPE_OPTION = "--file-type"
USAGE_LINE = (
    "用法: python -m local_search <目录> <关键词> "
    "[--path-contains <路径片段>] [--path-excludes <路径片段>] "
    "[--ignore-case] [--all-lines] "
    "[--context-chars <0-200>] [--file-type <txt|md>]"
)


def run_cli(*argv: str, cwd: Path = PROJECT_ROOT) -> subprocess.CompletedProcess:
    """以任意命令行参数调用 python -m local_search，返回完成的子进程。"""
    return subprocess.run(
        [sys.executable, "-m", "local_search", *argv],
        cwd=cwd,
        capture_output=True,
    )


def decode(proc: subprocess.CompletedProcess) -> tuple[str, str]:
    return proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


class ParseArgsUnitTest(unittest.TestCase):
    """直接固定解析结果元组与缺省值（main 仍按原顺序解包使用）。"""

    def test_defaults_when_no_options(self) -> None:
        self.assertEqual(
            _parse_args(["some-dir", "target"]),
            ("some-dir", "target", None, None, False, False, 30, None),
        )

    def test_all_options_parsed_in_declared_tuple_order(self) -> None:
        parsed = _parse_args(
            [
                "some-dir",
                "TARGET",
                PATH_OPTION,
                "notes/",
                EXCLUDES_OPTION,
                "private/",
                IGNORE_CASE_OPTION,
                ALL_LINES_OPTION,
                CONTEXT_CHARS_OPTION,
                "0",
                FILE_TYPE_OPTION,
                "md",
            ]
        )
        self.assertEqual(
            parsed,
            ("some-dir", "TARGET", "notes/", "private/", True, True, 0, "md"),
        )

    def test_option_token_as_keyword_stays_literal(self) -> None:
        # 关键词恰为开关记号时，后面的位置才进入选项扫描。
        self.assertEqual(
            _parse_args(["some-dir", ALL_LINES_OPTION, IGNORE_CASE_OPTION]),
            ("some-dir", ALL_LINES_OPTION, None, None, True, False, 30, None),
        )

    def test_option_token_as_value_stays_literal(self) -> None:
        parsed = _parse_args(
            ["some-dir", "target", PATH_OPTION, IGNORE_CASE_OPTION]
        )
        # 第三个元素是 path-contains 的值：恰为 --ignore-case，开关不启用。
        self.assertEqual(parsed[2], IGNORE_CASE_OPTION)
        self.assertFalse(parsed[4])

    def test_leading_zeros_accepted_including_all_zeros(self) -> None:
        self.assertEqual(
            _parse_args(["d", "k", CONTEXT_CHARS_OPTION, "007"])[6], 7
        )
        self.assertEqual(
            _parse_args(["d", "k", CONTEXT_CHARS_OPTION, "000"])[6], 0
        )
        self.assertEqual(
            _parse_args(["d", "k", CONTEXT_CHARS_OPTION, "0200"])[6], 200
        )
        # 任意数量前导零的 "0"（远超 int() 默认 4300 位限制）仍合法为 0。
        self.assertEqual(
            _parse_args(["d", "k", CONTEXT_CHARS_OPTION, "0" * 5000])[6], 0
        )

    def test_duplicate_option_raises_for_every_option(self) -> None:
        cases = (
            [IGNORE_CASE_OPTION, IGNORE_CASE_OPTION],
            [ALL_LINES_OPTION, ALL_LINES_OPTION],
            [CONTEXT_CHARS_OPTION, "1", CONTEXT_CHARS_OPTION, "2"],
            [PATH_OPTION, "a", PATH_OPTION, "b"],
            [EXCLUDES_OPTION, "a", EXCLUDES_OPTION, "b"],
            [FILE_TYPE_OPTION, "txt", FILE_TYPE_OPTION, "md"],
        )
        for tail in cases:
            name = tail[0]
            with self.subTest(name=name):
                with self.assertRaises(ArgumentError) as caught:
                    _parse_args(["d", "k", *tail])
                self.assertEqual(
                    str(caught.exception), f"错误: 选项 {name} 只能指定一次"
                )

    def test_missing_value_raises_for_value_options(self) -> None:
        for name in (PATH_OPTION, EXCLUDES_OPTION, CONTEXT_CHARS_OPTION, FILE_TYPE_OPTION):
            with self.subTest(name=name):
                with self.assertRaises(ArgumentError) as caught:
                    _parse_args(["d", "k", name])
                self.assertEqual(
                    str(caught.exception), f"错误: 选项 {name} 缺少值"
                )

    def test_file_type_only_accepts_lowercase_literals(self) -> None:
        for raw in ("txt", "md"):
            with self.subTest(raw=raw):
                self.assertEqual(
                    _parse_args(["d", "k", FILE_TYPE_OPTION, raw])[7], raw
                )
        for raw in ("", " ", "  ", " txt", "txt ", "TXT", "Md", "log", "markdown"):
            with self.subTest(raw=raw):
                with self.assertRaises(ArgumentError) as caught:
                    _parse_args(["d", "k", FILE_TYPE_OPTION, raw])
                self.assertEqual(
                    str(caught.exception),
                    f"错误: 选项 {FILE_TYPE_OPTION} 的值必须是 txt 或 md: {raw!r}",
                )

    def test_unknown_token_raises(self) -> None:
        with self.assertRaises(ArgumentError) as caught:
            _parse_args(["d", "k", "--bogus"])
        self.assertEqual(str(caught.exception), "错误: 无法识别的参数: --bogus")

    def test_context_chars_format_and_range_messages(self) -> None:
        bad_format = ("", " 3", "3 ", "+1", "-1", "2.5", "１２", "3a")
        for raw in bad_format:
            with self.subTest(raw=raw):
                with self.assertRaises(ArgumentError) as caught:
                    _parse_args(["d", "k", CONTEXT_CHARS_OPTION, raw])
                self.assertEqual(
                    str(caught.exception),
                    f"错误: 选项 {CONTEXT_CHARS_OPTION} 的值必须是非空的 "
                    f"ASCII 十进制数字串: {raw!r}",
                )
        for raw in ("201", "00201", "9" * 5000):
            with self.subTest(raw=raw):
                with self.assertRaises(ArgumentError) as caught:
                    _parse_args(["d", "k", CONTEXT_CHARS_OPTION, raw])
                self.assertEqual(
                    str(caught.exception),
                    f"错误: 选项 {CONTEXT_CHARS_OPTION} 的值超出范围 0-200: "
                    f"{raw!r}",
                )

    def test_missing_positionals_raise_usage(self) -> None:
        for args in ([], ["d"]):
            with self.subTest(args=args):
                with self.assertRaises(ArgumentError) as caught:
                    _parse_args(args)
                self.assertEqual(str(caught.exception), USAGE_LINE)


class ArgumentParsingCliTest(unittest.TestCase):
    """通过命令行入口固定退出码、标准输出、标准错误与错误优先级。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "root"
        (self.root / "notes" / "private").mkdir(parents=True)
        (self.root / "a.txt").write_text("target root\n", encoding="utf-8")
        (self.root / "notes" / "b.md").write_text(
            "Target note\nother\ntarget later\n", encoding="utf-8"
        )
        (self.root / "notes" / "private" / "c.txt").write_text(
            "no match here\n", encoding="utf-8"
        )

    def assertParseFailure(
        self, expected_stderr: str, *argv: str
    ) -> subprocess.CompletedProcess:
        proc = run_cli(*argv)
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 2, stderr)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, expected_stderr + "\n")
        self.assertFalse(stderr.startswith("Traceback"))
        return proc

    def test_acceptance_query_and_option_order_invariance(self) -> None:
        expected = [
            {"path": "notes/b.md", "line": 1, "snippet": "Target"},
            {"path": "notes/b.md", "line": 3, "snippet": "target"},
        ]
        orders = (
            (
                IGNORE_CASE_OPTION,
                ALL_LINES_OPTION,
                PATH_OPTION,
                "notes/",
                EXCLUDES_OPTION,
                "private/",
                CONTEXT_CHARS_OPTION,
                "0",
            ),
            (
                CONTEXT_CHARS_OPTION,
                "0",
                EXCLUDES_OPTION,
                "private/",
                ALL_LINES_OPTION,
                PATH_OPTION,
                "notes/",
                IGNORE_CASE_OPTION,
            ),
            (
                PATH_OPTION,
                "notes/",
                CONTEXT_CHARS_OPTION,
                "000",
                IGNORE_CASE_OPTION,
                EXCLUDES_OPTION,
                "private/",
                ALL_LINES_OPTION,
            ),
        )
        observed = []
        for tail in orders:
            proc = run_cli(str(self.root), "TARGET", *tail)
            stdout, stderr = decode(proc)
            self.assertEqual(proc.returncode, 0, stderr)
            self.assertEqual(stderr, "")
            observed.append(stdout)
            self.assertEqual(json.loads(stdout), expected)
        self.assertEqual(len(set(observed)), 1)

    def test_keyword_equal_to_option_token_is_searched_literally(self) -> None:
        hit_dir = Path(self._tmp.name) / "literal"
        hit_dir.mkdir()
        (hit_dir / "f.txt").write_text(
            f"x {ALL_LINES_OPTION} y\n", encoding="utf-8"
        )
        proc = run_cli(str(hit_dir), ALL_LINES_OPTION)
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            [{"path": "f.txt", "line": 1, "snippet": f"x {ALL_LINES_OPTION} y"}],
        )

    def test_value_equal_to_switch_is_consumed_as_value(self) -> None:
        # TARGET 默认区分大小写；--ignore-case 在此只是路径片段，开关不启用。
        proc = run_cli(
            str(self.root), "TARGET", PATH_OPTION, IGNORE_CASE_OPTION
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(json.loads(stdout), [])

        # --context-chars 的值看似开关时也先按字面取值，再报数字格式错误。
        self.assertParseFailure(
            f"错误: 选项 {CONTEXT_CHARS_OPTION} 的值必须是非空的 ASCII "
            f"十进制数字串: {ALL_LINES_OPTION!r}",
            str(self.root),
            "target",
            CONTEXT_CHARS_OPTION,
            ALL_LINES_OPTION,
        )

    def test_duplicate_options_fail_before_scan(self) -> None:
        cases = {
            IGNORE_CASE_OPTION: (IGNORE_CASE_OPTION, IGNORE_CASE_OPTION),
            ALL_LINES_OPTION: (ALL_LINES_OPTION, ALL_LINES_OPTION),
            CONTEXT_CHARS_OPTION: (
                CONTEXT_CHARS_OPTION,
                "1",
                CONTEXT_CHARS_OPTION,
                "2",
            ),
            PATH_OPTION: (PATH_OPTION, "a", PATH_OPTION, "b"),
            EXCLUDES_OPTION: (EXCLUDES_OPTION, "a", EXCLUDES_OPTION, "b"),
            FILE_TYPE_OPTION: (
                FILE_TYPE_OPTION,
                "txt",
                FILE_TYPE_OPTION,
                "md",
            ),
        }
        for name, tail in cases.items():
            self.assertParseFailure(
                f"错误: 选项 {name} 只能指定一次",
                str(self.root),
                "target",
                *tail,
            )

    def test_missing_values_fail_before_scan(self) -> None:
        for name in (
            PATH_OPTION,
            EXCLUDES_OPTION,
            CONTEXT_CHARS_OPTION,
            FILE_TYPE_OPTION,
        ):
            self.assertParseFailure(
                f"错误: 选项 {name} 缺少值",
                str(self.root),
                "target",
                name,
            )

    def test_unknown_token_and_option_before_keyword_position(self) -> None:
        self.assertParseFailure(
            "错误: 无法识别的参数: --bogus",
            str(self.root),
            "target",
            "--bogus",
        )
        # 第三个参数才是选项扫描的起点：位置上的 target 变成无法识别的参数。
        self.assertParseFailure(
            "错误: 无法识别的参数: target",
            str(self.root),
            IGNORE_CASE_OPTION,
            "target",
        )

    def test_context_chars_format_and_range_failures(self) -> None:
        for raw in ("2.5", "-1", "+3", " 3", "３"):
            self.assertParseFailure(
                f"错误: 选项 {CONTEXT_CHARS_OPTION} 的值必须是非空的 ASCII "
                f"十进制数字串: {raw!r}",
                str(self.root),
                "target",
                CONTEXT_CHARS_OPTION,
                raw,
            )
        for raw in ("201", "00201"):
            self.assertParseFailure(
                f"错误: 选项 {CONTEXT_CHARS_OPTION} 的值超出范围 0-200: "
                f"{raw!r}",
                str(self.root),
                "target",
                CONTEXT_CHARS_OPTION,
                raw,
            )

    def test_error_precedence_first_scanned_wins(self) -> None:
        # 目录错误在解析之后才检查：长度超范围先报，即使目录不存在。
        self.assertParseFailure(
            f"错误: 选项 {CONTEXT_CHARS_OPTION} 的值超出范围 0-200: '201'",
            str(self.root) + "_不存在",
            "target",
            CONTEXT_CHARS_OPTION,
            "201",
        )
        # 同一扫描序内：先出现的重复开关先报，后面的 201 不再报告。
        self.assertParseFailure(
            f"错误: 选项 {IGNORE_CASE_OPTION} 只能指定一次",
            str(self.root),
            "target",
            IGNORE_CASE_OPTION,
            IGNORE_CASE_OPTION,
            CONTEXT_CHARS_OPTION,
            "201",
        )
        # 重复先于该选项自身的缺值被检查。
        self.assertParseFailure(
            f"错误: 选项 {PATH_OPTION} 只能指定一次",
            str(self.root),
            "target",
            PATH_OPTION,
            "a",
            PATH_OPTION,
        )
        # 先出现的未知记号先报。
        self.assertParseFailure(
            "错误: 无法识别的参数: --bogus",
            str(self.root),
            "target",
            "--bogus",
            IGNORE_CASE_OPTION,
            IGNORE_CASE_OPTION,
        )

    def test_usage_when_positionals_missing(self) -> None:
        for argv in ((), (str(self.root),)):
            proc = run_cli(*argv)
            stdout, stderr = decode(proc)
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(stdout, "")
            self.assertEqual(stderr, USAGE_LINE + "\n")

    def test_blank_keyword_and_blank_fragments_fail(self) -> None:
        self.assertParseFailure(
            "错误: 关键词为空或全为空白", str(self.root), "   "
        )
        self.assertParseFailure(
            f"错误: {PATH_OPTION} 的路径片段为空或全为空白",
            str(self.root),
            "target",
            PATH_OPTION,
            "   ",
        )
        self.assertParseFailure(
            f"错误: {EXCLUDES_OPTION} 的路径片段为空或全为空白",
            str(self.root),
            "target",
            EXCLUDES_OPTION,
            "  ",
        )


class FileTypeCliTest(unittest.TestCase):
    """固定 --file-type 的筛选语义与验收场景。

    目录仅含 a.txt（``target root``）、notes/b.MD（``Target note``）和
    other.log（``target log``）：扩展名选择忽略大小写，但取值只接受小写
    字面 txt/md；格式条件与路径条件共同生效。
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "sample"
        (self.root / "notes").mkdir(parents=True)
        (self.root / "a.txt").write_text("target root\n", encoding="utf-8")
        (self.root / "notes" / "b.MD").write_text("Target note\n", encoding="utf-8")
        (self.root / "other.log").write_text("target log\n", encoding="utf-8")

    def test_acceptance_txt_and_md(self) -> None:
        proc = run_cli(
            str(self.root),
            "TARGET",
            IGNORE_CASE_OPTION,
            FILE_TYPE_OPTION,
            "txt",
            CONTEXT_CHARS_OPTION,
            "0",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            [{"path": "a.txt", "line": 1, "snippet": "target"}],
        )

        proc = run_cli(
            str(self.root),
            "TARGET",
            IGNORE_CASE_OPTION,
            FILE_TYPE_OPTION,
            "md",
            CONTEXT_CHARS_OPTION,
            "0",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            [{"path": "notes/b.MD", "line": 1, "snippet": "Target"}],
        )

    def test_without_option_both_formats_still_searched(self) -> None:
        proc = run_cli(
            str(self.root), "TARGET", IGNORE_CASE_OPTION, CONTEXT_CHARS_OPTION, "0"
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        # other.log 从不收集；两种文本格式各返回首个命中。
        self.assertEqual(
            json.loads(stdout),
            [
                {"path": "a.txt", "line": 1, "snippet": "target"},
                {"path": "notes/b.MD", "line": 1, "snippet": "Target"},
            ],
        )

    def test_option_order_does_not_change_result(self) -> None:
        tails = (
            (IGNORE_CASE_OPTION, FILE_TYPE_OPTION, "txt", CONTEXT_CHARS_OPTION, "0"),
            (CONTEXT_CHARS_OPTION, "0", IGNORE_CASE_OPTION, FILE_TYPE_OPTION, "txt"),
            (FILE_TYPE_OPTION, "txt", CONTEXT_CHARS_OPTION, "0", IGNORE_CASE_OPTION),
        )
        observed = []
        for tail in tails:
            proc = run_cli(str(self.root), "TARGET", *tail)
            stdout, stderr = decode(proc)
            self.assertEqual(proc.returncode, 0, stderr)
            self.assertEqual(stderr, "")
            observed.append(stdout)
        self.assertEqual(len(set(observed)), 1)
        self.assertEqual(
            json.loads(observed[0]),
            [{"path": "a.txt", "line": 1, "snippet": "target"}],
        )

    def test_no_matching_file_of_type_outputs_empty_array(self) -> None:
        # 区分大小写：a.txt 只有小写 target，大写 TARGET 在 txt 中无命中。
        proc = run_cli(str(self.root), "TARGET", FILE_TYPE_OPTION, "txt")
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(stdout, "[]\n")

    def test_format_condition_and_path_filters_apply_together(self) -> None:
        # md 但路径不含 notes/ → 全部排除，输出 []。
        proc = run_cli(
            str(self.root),
            "TARGET",
            IGNORE_CASE_OPTION,
            FILE_TYPE_OPTION,
            "md",
            PATH_OPTION,
            "other/",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(stdout, "[]\n")

        # 两个条件同时满足时正常命中。
        proc = run_cli(
            str(self.root),
            "TARGET",
            IGNORE_CASE_OPTION,
            FILE_TYPE_OPTION,
            "md",
            PATH_OPTION,
            "notes/",
            CONTEXT_CHARS_OPTION,
            "0",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            [{"path": "notes/b.MD", "line": 1, "snippet": "Target"}],
        )

    def test_illegal_values_fail_before_scan(self) -> None:
        for raw in ("", " ", "  ", " txt", "txt ", "TXT", "Md", "log", "markdown"):
            proc = run_cli(str(self.root), "TARGET", FILE_TYPE_OPTION, raw)
            stdout, stderr = decode(proc)
            self.assertEqual(proc.returncode, 2, repr(raw))
            self.assertEqual(stdout, "")
            self.assertEqual(
                stderr,
                f"错误: 选项 {FILE_TYPE_OPTION} 的值必须是 txt 或 md: {raw!r}\n",
            )

    def test_value_looking_like_switch_is_consumed_as_value(self) -> None:
        # --ignore-case 在此只是 --file-type 的取值：不开启开关，并按非法值报错。
        proc = run_cli(
            str(self.root), "TARGET", FILE_TYPE_OPTION, IGNORE_CASE_OPTION
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(
            stderr,
            f"错误: 选项 {FILE_TYPE_OPTION} 的值必须是 txt 或 md: "
            f"{IGNORE_CASE_OPTION!r}\n",
        )

    def test_keyword_equal_to_file_type_token_is_searched_literally(self) -> None:
        hit_dir = Path(self._tmp.name) / "literal"
        hit_dir.mkdir()
        (hit_dir / "f.txt").write_text(
            f"x {FILE_TYPE_OPTION} y\n", encoding="utf-8"
        )
        proc = run_cli(str(hit_dir), FILE_TYPE_OPTION, FILE_TYPE_OPTION, "txt")
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            [
                {
                    "path": "f.txt",
                    "line": 1,
                    "snippet": f"x {FILE_TYPE_OPTION} y",
                }
            ],
        )

    def test_excluded_format_bad_file_is_not_read_but_selected_one_warns(self) -> None:
        # broken.txt 含非法 UTF-8；选 md 时它不被收集，故不告警。
        (self.root / "broken.txt").write_bytes(b"bad \xff bytes\n")
        proc = run_cli(str(self.root), "TARGET", IGNORE_CASE_OPTION,
                       FILE_TYPE_OPTION, "md", CONTEXT_CHARS_OPTION, "0")
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            [{"path": "notes/b.MD", "line": 1, "snippet": "Target"}],
        )

        # 选 txt 时 broken.txt 属于候选：告警后跳过，a.txt 仍正常返回。
        proc = run_cli(str(self.root), "TARGET", IGNORE_CASE_OPTION,
                       FILE_TYPE_OPTION, "txt", CONTEXT_CHARS_OPTION, "0")
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("警告: 跳过文件 broken.txt", stderr)
        self.assertEqual(
            json.loads(stdout),
            [{"path": "a.txt", "line": 1, "snippet": "target"}],
        )


if __name__ == "__main__":
    unittest.main()

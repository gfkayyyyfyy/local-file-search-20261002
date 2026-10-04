"""参数解析流程的回归测试。

针对 ``local_search/search.py`` 中由声明式选项表驱动的统一解析循环，固定
从命令行输入到进程行为的外在契约，确保“重复指定”“缺少值”等同类规则对
所有选项一致生效，且重构不改变既有行为：

- 两个位置参数按字面取值，选项只能出现在其后、相互先后顺序不限；五个选项
  以任意排列同用时输出完全相同；
- 每个选项（``--path-contains``、``--path-excludes``、``--ignore-case``、
  ``--all-lines``、``--context-chars``）重复指定时都在扫描前退出码 2、
  标准输出为空、标准错误为“错误: 选项 <名称> 只能指定一次”；
- 每个带值选项在末尾缺值时统一报“错误: 选项 <名称> 缺少值”；同一选项
  重复出现且第二次缺值时，优先报重复而非缺值；
- 带值选项的取值始终按字面消费：即使恰好写作其他选项名也不被当作开关；
- 无法识别的参数（含出现在关键词之前的开关、多余的第三个位置参数）统一
  报“错误: 无法识别的参数: <参数>”；
- 多个错误并存时保留既有优先级：按命令行从左到右先出现的错误先报；解析
  阶段的错误（如长度超范围）优先于目录存在性检查；
- 缺少位置参数时输出用法说明；所有参数错误均无异常堆栈；
- ``python -m local_search`` 与直接调用 ``local_search.main`` 两种入口
  行为一致。

所有资料由测试在 TemporaryDirectory 中独立准备并自动清理，仅使用 Python
标准库、离线运行。期望值全部以字面量直接写出，不调用任何被测函数来生成
期望结果。
"""

import io
import json
import subprocess
import sys
import tempfile
import unittest
from itertools import permutations
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import local_search

PATH_OPTION = "--path-contains"
EXCLUDES_OPTION = "--path-excludes"
IGNORE_CASE_OPTION = "--ignore-case"
ALL_LINES_OPTION = "--all-lines"
CONTEXT_CHARS_OPTION = "--context-chars"

FLAG_OPTIONS = (IGNORE_CASE_OPTION, ALL_LINES_OPTION)
VALUED_OPTIONS = (PATH_OPTION, EXCLUDES_OPTION, CONTEXT_CHARS_OPTION)
ALL_OPTIONS = VALUED_OPTIONS + FLAG_OPTIONS

# 验收场景：五个选项同用时的完整参数与唯一期望输出。
ACCEPTANCE_ARGS = (
    "TARGET",
    IGNORE_CASE_OPTION,
    ALL_LINES_OPTION,
    PATH_OPTION,
    "notes/",
    EXCLUDES_OPTION,
    "private/",
    CONTEXT_CHARS_OPTION,
    "0",
)
ACCEPTANCE_OUTPUT = (
    b'[{"path": "notes/b.md", "line": 1, "snippet": "Target"}, '
    b'{"path": "notes/b.md", "line": 3, "snippet": "target"}]\n'
)


def run_args(*argv: str) -> subprocess.CompletedProcess:
    """以任意命令行参数调用 python -m local_search，返回完成的子进程。"""
    return subprocess.run(
        [sys.executable, "-m", "local_search", *argv],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )


class _CapturedStream:
    """sys.stdout/sys.stderr 的最小替身：被测代码只使用其 .buffer 写字节。"""

    def __init__(self) -> None:
        self.buffer = io.BytesIO()


def run_main(*argv: str) -> tuple:
    """直接调用公开入口 local_search.main，返回 (返回值, 标准输出字节, 标准错误字节)。"""
    stdout = _CapturedStream()
    stderr = _CapturedStream()
    with mock.patch.object(sys, "stdout", stdout), mock.patch.object(sys, "stderr", stderr):
        code = local_search.main(list(argv))
    return code, stdout.buffer.getvalue(), stderr.buffer.getvalue()


class ParseArgsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "资料 目录"
        self.root.mkdir(parents=True)
        (self.root / "a.txt").write_bytes(b"target root\n")
        (self.root / "notes").mkdir()
        (self.root / "notes" / "b.md").write_bytes(
            b"Target note\nother\ntarget later\n"
        )
        (self.root / "notes" / "private").mkdir()
        # 仅含非法 UTF-8 字节 0xff；被 --path-excludes 排除时不产生告警。
        (self.root / "notes" / "private" / "c.txt").write_bytes(b"\xff")

    # -- 辅助 ------------------------------------------------------------

    def assert_acceptance(self, proc: subprocess.CompletedProcess, label: str) -> None:
        """验收场景：退出码 0、标准错误为空、标准输出为唯一期望结果。"""
        self.assertEqual(
            proc.returncode,
            0,
            f"{label}: 退出码应为 0，实际 {proc.returncode}，stderr={proc.stderr!r}",
        )
        self.assertEqual(
            proc.stderr, b"", f"{label}: 标准错误应为空，实际 {proc.stderr!r}"
        )
        self.assertEqual(
            proc.stdout,
            ACCEPTANCE_OUTPUT,
            f"{label}: 标准输出应为验收结果，实际 {proc.stdout!r}",
        )

    def assert_error(
        self, proc: subprocess.CompletedProcess, message: str, label: str
    ) -> None:
        """参数错误：退出码 2、标准输出为空、标准错误恰为指定的一行。"""
        self.assertEqual(
            proc.returncode,
            2,
            f"{label}: 退出码应为 2，实际 {proc.returncode}，stderr={proc.stderr!r}",
        )
        self.assertEqual(
            proc.stdout, b"", f"{label}: 出错时标准输出必须为空，实际 {proc.stdout!r}"
        )
        self.assertEqual(
            proc.stderr.decode("utf-8"),
            message + "\n",
            f"{label}: 标准错误应为 {message!r}",
        )

    # -- 1. 选项顺序不影响结果 --------------------------------------------

    def test_option_order_permutations_give_identical_output(self) -> None:
        # 三个可自由排列的选项组（开关与“选项+值”整体）的全部排列。
        groups = (
            (IGNORE_CASE_OPTION,),
            (ALL_LINES_OPTION,),
            (PATH_OPTION, "notes/"),
            (EXCLUDES_OPTION, "private/"),
            (CONTEXT_CHARS_OPTION, "0"),
        )
        for perm in permutations(groups):
            option_args = [token for group in perm for token in group]
            label = " ".join(option_args)
            with self.subTest(order=label):
                proc = run_args(str(self.root), "TARGET", *option_args)
                self.assert_acceptance(proc, label)

    def test_main_entry_matches_command_line(self) -> None:
        code, stdout, stderr = run_main(str(self.root), *ACCEPTANCE_ARGS)
        self.assertEqual(code, 0)
        self.assertEqual(stderr, b"")
        self.assertEqual(stdout, ACCEPTANCE_OUTPUT)

    # -- 2. 重复指定：所有选项统一规则 ------------------------------------

    def test_duplicate_flag_options_rejected(self) -> None:
        for option in FLAG_OPTIONS:
            with self.subTest(option=option):
                proc = run_args(str(self.root), "target", option, option)
                self.assert_error(proc, f"错误: 选项 {option} 只能指定一次", option)

    def test_duplicate_valued_options_rejected(self) -> None:
        for option, value in (
            (PATH_OPTION, "notes/"),
            (EXCLUDES_OPTION, "private/"),
            (CONTEXT_CHARS_OPTION, "7"),
        ):
            with self.subTest(option=option):
                proc = run_args(str(self.root), "target", option, value, option, value)
                self.assert_error(proc, f"错误: 选项 {option} 只能指定一次", option)

    def test_duplicate_reported_before_missing_value(self) -> None:
        # 第二次出现时即使缺值，也优先报重复指定。
        for option, value in (
            (PATH_OPTION, "notes/"),
            (EXCLUDES_OPTION, "private/"),
            (CONTEXT_CHARS_OPTION, "7"),
        ):
            with self.subTest(option=option):
                proc = run_args(str(self.root), "target", option, value, option)
                self.assert_error(proc, f"错误: 选项 {option} 只能指定一次", option)

    def test_duplicate_reported_before_value_validation(self) -> None:
        # 第一次取值合法、第二次取值非法时，重复错误优先于取值校验。
        proc = run_args(
            str(self.root), "target", CONTEXT_CHARS_OPTION, "7", CONTEXT_CHARS_OPTION, "abc"
        )
        self.assert_error(
            proc, f"错误: 选项 {CONTEXT_CHARS_OPTION} 只能指定一次", "重复优先于校验"
        )

    # -- 3. 带值选项缺值：统一规则 ----------------------------------------

    def test_missing_value_rejected(self) -> None:
        for option in VALUED_OPTIONS:
            with self.subTest(option=option):
                proc = run_args(str(self.root), "target", option)
                self.assert_error(proc, f"错误: 选项 {option} 缺少值", option)

    # -- 4. 取值按字面消费，不解释成开关 ----------------------------------

    def test_option_like_value_consumed_literally(self) -> None:
        # 本组场景不使用 --path-excludes，先移除坏字节文件以免产生解码告警。
        (self.root / "notes" / "private" / "c.txt").unlink()

        # --path-contains 的值恰为 --ignore-case：不开启开关、不区分大小写。
        proc = run_args(str(self.root), "target", PATH_OPTION, IGNORE_CASE_OPTION)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, b"")
        self.assertEqual(json.loads(proc.stdout.decode("utf-8")), [])

        # --path-excludes 的值恰为 --all-lines：不排除任何文件、不开启逐行。
        proc = run_args(str(self.root), "target", EXCLUDES_OPTION, ALL_LINES_OPTION)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, b"")
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8")),
            [
                {"path": "a.txt", "line": 1, "snippet": "target root"},
                {"path": "notes/b.md", "line": 3, "snippet": "target later"},
            ],
        )

        # --context-chars 的值恰为 --all-lines：按字面取值后报格式错误。
        proc = run_args(str(self.root), "target", CONTEXT_CHARS_OPTION, ALL_LINES_OPTION)
        self.assert_error(
            proc,
            f"错误: 选项 {CONTEXT_CHARS_OPTION} 的值必须是非空的 ASCII 十进制数字串: "
            f"'--all-lines'",
            "选项名作长度值",
        )

    def test_keyword_matching_option_name_is_literal(self) -> None:
        # 关键词恰为 --all-lines：仍按字面文本检索，不开启开关。
        (self.root / "notes" / "private" / "c.txt").unlink()
        (self.root / "opt.txt").write_bytes(b"use --all-lines here\n")
        proc = run_args(str(self.root), ALL_LINES_OPTION)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, b"")
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8")),
            [{"path": "opt.txt", "line": 1, "snippet": "use --all-lines here"}],
        )

    # -- 5. 无法识别的参数 -------------------------------------------------

    def test_unknown_argument_rejected(self) -> None:
        proc = run_args(str(self.root), "target", "--verbose")
        self.assert_error(proc, "错误: 无法识别的参数: --verbose", "未知开关")

    def test_extra_positional_argument_rejected(self) -> None:
        proc = run_args(str(self.root), "target", "extra")
        self.assert_error(proc, "错误: 无法识别的参数: extra", "第三个位置参数")

    def test_option_before_keyword_rejected(self) -> None:
        # 开关出现在两个位置参数之前时不被当作选项。
        proc = run_args(IGNORE_CASE_OPTION, str(self.root), "target")
        self.assert_error(proc, "错误: 无法识别的参数: target", "开关在关键词之前")

    # -- 6. 缺少位置参数 ---------------------------------------------------

    def test_missing_positional_arguments_shows_usage(self) -> None:
        for argv in ((), (str(self.root),)):
            with self.subTest(argv=argv):
                proc = run_args(*argv)
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(proc.stdout, b"")
                self.assertIn("用法: python -m local_search", proc.stderr.decode("utf-8"))

    # -- 7. 多错误并存的优先级 ---------------------------------------------

    def test_leftmost_error_wins(self) -> None:
        # 左侧的长度格式错误先于右侧的重复开关报出。
        proc = run_args(
            str(self.root),
            "target",
            CONTEXT_CHARS_OPTION,
            "abc",
            IGNORE_CASE_OPTION,
            IGNORE_CASE_OPTION,
        )
        self.assert_error(
            proc,
            f"错误: 选项 {CONTEXT_CHARS_OPTION} 的值必须是非空的 ASCII 十进制数字串: 'abc'",
            "左侧错误优先",
        )

    def test_parse_error_precedes_directory_check(self) -> None:
        # 目录不存在且长度超范围时，先报长度超范围（解析错误优先于目录检查）。
        missing = str(self.root / "不存在的目录")
        proc = run_args(missing, "target", CONTEXT_CHARS_OPTION, "201")
        self.assert_error(
            proc,
            f"错误: 选项 {CONTEXT_CHARS_OPTION} 的值超出范围 0-200: '201'",
            "解析错误优先于目录检查",
        )

    # -- 8. 错误路径不产生异常堆栈 -----------------------------------------

    def test_errors_have_no_traceback(self) -> None:
        cases = (
            (str(self.root), "target", IGNORE_CASE_OPTION, IGNORE_CASE_OPTION),
            (str(self.root), "target", PATH_OPTION),
            (str(self.root), "target", "--verbose"),
            (str(self.root), "target", CONTEXT_CHARS_OPTION, "2.5"),
            (str(self.root), "   "),
        )
        for argv in cases:
            with self.subTest(argv=argv):
                proc = run_args(*argv)
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(proc.stdout, b"")
                stderr = proc.stderr.decode("utf-8")
                self.assertNotIn("Traceback", stderr)
                self.assertEqual(len(stderr.strip().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()

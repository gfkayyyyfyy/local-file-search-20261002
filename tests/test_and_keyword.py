"""--and-keyword 同行双关键词筛选的端到端回归测试。

固定 ``--and-keyword`` 的对外可观察行为：

- 只有同一行分别包含主关键词与附加关键词才算命中，分处不同行不能合并，
  两词出现顺序不限，相同关键词或重叠匹配可共用同一段文字；
- 两词均按连续字面子串匹配，保留首尾空格，不拆词、不解释正则或通配符；
  默认区分大小写，--ignore-case 同时作用于二者且仍只折叠 ASCII 字母；
- 默认每个文件返回行号最小的合格行，--all-lines 每个合格行各返回一项，
  行内重复出现不增加结果；snippet 围绕主关键词在该行最左侧的命中，
  保留源文本大小写，--context-chars 沿用既有规则，不为附加关键词扩大；
- 选项位于两个位置参数之后，可与其他选项任意排序，至多指定一次；紧随
  其后的参数即使写作 --all-lines 也按字面文本处理，不开启开关；位置
  参数或路径选项取值中的 --and-keyword 仍按字面处理；
- 缺值、重复指定、值为空或全为空白时在扫描前退出码 2、标准输出为空、
  标准错误包含选项名及原因；未提供该选项时既有行为完全不变；
- 路径与格式筛选继续生效，被过滤文件不读取、不告警；选中文件无法读取
  或含非法 UTF-8 时整文件告警跳过，其余文件继续，退出码仍为 0。

资料由测试在 TemporaryDirectory 中独立准备并自动清理，仅使用 Python
标准库、离线运行。
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

AND_KEYWORD_OPTION = "--and-keyword"


def run_cli(*argv: str, cwd: Path = PROJECT_ROOT) -> subprocess.CompletedProcess:
    """以任意命令行参数调用 python -m local_search，返回完成的子进程。"""
    return subprocess.run(
        [sys.executable, "-m", "local_search", *argv],
        cwd=cwd,
        capture_output=True,
    )


def decode(proc: subprocess.CompletedProcess) -> tuple[str, str]:
    return proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


class AndKeywordAcceptanceTest(unittest.TestCase):
    """复现验收目录：sample 中仅有 a.txt，四行依次为
    target、budget、Target budget、budget target target。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "sample"
        self.root.mkdir()
        (self.root / "a.txt").write_text(
            "target\nbudget\nTarget budget\nbudget target target\n",
            encoding="utf-8",
        )

    def test_acceptance_default_returns_only_third_line(self) -> None:
        proc = run_cli(
            str(self.root),
            "target",
            AND_KEYWORD_OPTION,
            "budget",
            "--ignore-case",
            "--context-chars",
            "0",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            [{"path": "a.txt", "line": 3, "snippet": "Target"}],
        )

    def test_acceptance_all_lines_adds_fourth_line(self) -> None:
        proc = run_cli(
            str(self.root),
            "target",
            AND_KEYWORD_OPTION,
            "budget",
            "--ignore-case",
            "--context-chars",
            "0",
            "--all-lines",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            [
                {"path": "a.txt", "line": 3, "snippet": "Target"},
                {"path": "a.txt", "line": 4, "snippet": "target"},
            ],
        )

    def test_snippet_not_expanded_for_and_keyword(self) -> None:
        # 片段只围绕主关键词最左侧命中：第四行 budget 在主关键词之前，
        # 上下文额度不用于把附加关键词纳入片段。
        proc = run_cli(
            str(self.root),
            "target",
            AND_KEYWORD_OPTION,
            "budget",
            "--context-chars",
            "2",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            [{"path": "a.txt", "line": 4, "snippet": "t target t"}],
        )

    def test_case_sensitive_by_default(self) -> None:
        # 第三行 Target 大写不命中主关键词；只有第四行两词均小写命中。
        proc = run_cli(
            str(self.root), "target", AND_KEYWORD_OPTION, "budget"
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            [{"path": "a.txt", "line": 4, "snippet": "budget target target"}],
        )

    def test_option_order_interchangeable(self) -> None:
        tails = (
            ("--ignore-case", "--context-chars", "0", AND_KEYWORD_OPTION, "budget"),
            (AND_KEYWORD_OPTION, "budget", "--ignore-case", "--context-chars", "0"),
            ("--context-chars", "0", AND_KEYWORD_OPTION, "budget", "--ignore-case"),
        )
        observed = []
        for tail in tails:
            proc = run_cli(str(self.root), "target", *tail)
            stdout, stderr = decode(proc)
            self.assertEqual(proc.returncode, 0, stderr)
            self.assertEqual(stderr, "")
            observed.append(stdout)
        self.assertEqual(len(set(observed)), 1)
        self.assertEqual(
            json.loads(observed[0]),
            [{"path": "a.txt", "line": 3, "snippet": "Target"}],
        )

    def test_without_and_keyword_behavior_unchanged(self) -> None:
        proc = run_cli(
            str(self.root), "target", "--ignore-case", "--context-chars", "0"
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            [{"path": "a.txt", "line": 1, "snippet": "target"}],
        )


class AndKeywordMatchingTest(unittest.TestCase):
    """同行匹配语义：顺序不限、跨行不合并、相同或重叠可共用文字。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "root"
        self.root.mkdir()

    def write(self, name: str, text: str) -> None:
        (self.root / name).write_text(text, encoding="utf-8")

    def test_keywords_on_different_lines_do_not_combine(self) -> None:
        self.write("a.txt", "alpha only\nonly beta\nneither here\n")
        proc = run_cli(
            str(self.root), "alpha", AND_KEYWORD_OPTION, "beta",
            "--context-chars", "0",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(stdout, "[]\n")

    def test_either_order_in_same_line_matches(self) -> None:
        self.write("a.txt", "beta then alpha\nalpha then beta\n")
        proc = run_cli(
            str(self.root), "alpha", AND_KEYWORD_OPTION, "beta",
            "--all-lines", "--context-chars", "0",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            [
                {"path": "a.txt", "line": 1, "snippet": "alpha"},
                {"path": "a.txt", "line": 2, "snippet": "alpha"},
            ],
        )

    def test_identical_keywords_share_same_text(self) -> None:
        # 主关键词与附加关键词相同：单次出现即可同时满足两者。
        self.write("a.txt", "one target\nno hit\n")
        proc = run_cli(
            str(self.root), "target", AND_KEYWORD_OPTION, "target",
            "--context-chars", "0",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            [{"path": "a.txt", "line": 1, "snippet": "target"}],
        )

    def test_overlapping_keywords_share_text(self) -> None:
        # ab 与 bc 重叠共用 b：一行 abc 同时包含两者。
        self.write("a.txt", "x abc y\nab alone\n")
        proc = run_cli(
            str(self.root), "ab", AND_KEYWORD_OPTION, "bc",
            "--context-chars", "0",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            [{"path": "a.txt", "line": 1, "snippet": "ab"}],
        )

    def test_ignore_case_applies_to_both_keywords_ascii_only(self) -> None:
        self.write("a.txt", "ALPHA Beta\nalpha BETA\nALPHA only\n")
        proc = run_cli(
            str(self.root), "alpha", AND_KEYWORD_OPTION, "BETA",
            "--ignore-case", "--all-lines", "--context-chars", "0",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            [
                {"path": "a.txt", "line": 1, "snippet": "ALPHA"},
                {"path": "a.txt", "line": 2, "snippet": "alpha"},
            ],
        )

    def test_ignore_case_still_ascii_only_for_and_keyword(self) -> None:
        # É 与 é 不视为同一字符，附加关键词同样如此。
        self.write("a.txt", "hit Éclair\nhit éclair\n")
        proc = run_cli(
            str(self.root), "hit", AND_KEYWORD_OPTION, "Éclair",
            "--ignore-case", "--all-lines", "--context-chars", "0",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            [{"path": "a.txt", "line": 1, "snippet": "hit"}],
        )

    def test_and_keyword_with_spaces_is_literal(self) -> None:
        # 附加关键词的首尾空格原样保留，不拆词。
        self.write("a.txt", "key  spaced \nkey spaced\n")
        proc = run_cli(
            str(self.root), "key", AND_KEYWORD_OPTION, " spaced ",
            "--all-lines", "--context-chars", "0",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            [{"path": "a.txt", "line": 1, "snippet": "key"}],
        )

    def test_and_keyword_regex_metachars_are_literal(self) -> None:
        self.write("a.txt", "hit a.b*c\nhit aXbYc\n")
        proc = run_cli(
            str(self.root), "hit", AND_KEYWORD_OPTION, "a.b*c",
            "--all-lines", "--context-chars", "0",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            [{"path": "a.txt", "line": 1, "snippet": "hit"}],
        )

    def test_repeated_main_keyword_in_line_yields_single_item(self) -> None:
        self.write("a.txt", "hit and hit again\n")
        proc = run_cli(
            str(self.root), "hit", AND_KEYWORD_OPTION, "and",
            "--all-lines", "--context-chars", "0",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            [{"path": "a.txt", "line": 1, "snippet": "hit"}],
        )

    def test_snippet_uses_leftmost_main_keyword_hit(self) -> None:
        self.write("a.txt", "and hit and hit\n")
        proc = run_cli(
            str(self.root), "hit", AND_KEYWORD_OPTION, "and",
            "--context-chars", "1",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            [{"path": "a.txt", "line": 1, "snippet": " hit "}],
        )

    def test_multiple_files_sorted_and_first_qualifying_line(self) -> None:
        self.write("b.txt", "miss\nhit plus extra\nhit again extra\n")
        self.write("a.md", "extra hit\n")
        proc = run_cli(
            str(self.root), "hit", AND_KEYWORD_OPTION, "extra",
            "--context-chars", "0",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            [
                {"path": "a.md", "line": 1, "snippet": "hit"},
                {"path": "b.txt", "line": 2, "snippet": "hit"},
            ],
        )


class AndKeywordLiteralTokenTest(unittest.TestCase):
    """--and-keyword 记号在位置参数或其他选项取值中仍按字面处理。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "root"
        self.root.mkdir()

    def test_value_equal_to_switch_is_literal_text(self) -> None:
        # 附加关键词写作 --all-lines：按文本匹配，不开启 --all-lines 开关。
        (self.root / "a.txt").write_text(
            "hit --all-lines\nhit x\n", encoding="utf-8"
        )
        proc = run_cli(
            str(self.root), "hit", AND_KEYWORD_OPTION, "--all-lines",
            "--context-chars", "0",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        # 只有第一行含字面 --all-lines；该记号未被当作开关消费。
        self.assertEqual(
            json.loads(stdout),
            [{"path": "a.txt", "line": 1, "snippet": "hit"}],
        )

    def test_keyword_equal_to_and_keyword_token_is_literal(self) -> None:
        (self.root / "f.txt").write_text(
            f"x {AND_KEYWORD_OPTION} y\n", encoding="utf-8"
        )
        proc = run_cli(str(self.root), AND_KEYWORD_OPTION)
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            [{"path": "f.txt", "line": 1, "snippet": f"x {AND_KEYWORD_OPTION} y"}],
        )

    def test_path_option_value_equal_to_and_keyword_token_is_literal(self) -> None:
        (self.root / "a.txt").write_text("hit\n", encoding="utf-8")
        proc = run_cli(
            str(self.root), "hit", "--path-contains", AND_KEYWORD_OPTION
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        # --and-keyword 在此只是路径片段，没有路径包含它，结果为空。
        self.assertEqual(stdout, "[]\n")


class AndKeywordErrorTest(unittest.TestCase):
    """缺值、重复、空值或全空白值在扫描前报错：退出码 2、标准输出为空。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "root"
        self.root.mkdir()
        (self.root / "a.txt").write_text("hit\n", encoding="utf-8")

    def assert_failure(self, expected_stderr: str, *argv: str) -> None:
        proc = run_cli(*argv)
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 2, stderr)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, expected_stderr + "\n")
        self.assertNotIn("Traceback", stderr)

    def test_missing_value(self) -> None:
        self.assert_failure(
            f"错误: 选项 {AND_KEYWORD_OPTION} 缺少值",
            str(self.root),
            "hit",
            AND_KEYWORD_OPTION,
        )

    def test_duplicate_option(self) -> None:
        self.assert_failure(
            f"错误: 选项 {AND_KEYWORD_OPTION} 只能指定一次",
            str(self.root),
            "hit",
            AND_KEYWORD_OPTION,
            "a",
            AND_KEYWORD_OPTION,
            "b",
        )

    def test_empty_value(self) -> None:
        self.assert_failure(
            f"错误: {AND_KEYWORD_OPTION} 的附加关键词为空或全为空白",
            str(self.root),
            "hit",
            AND_KEYWORD_OPTION,
            "",
        )

    def test_whitespace_only_value(self) -> None:
        self.assert_failure(
            f"错误: {AND_KEYWORD_OPTION} 的附加关键词为空或全为空白",
            str(self.root),
            "hit",
            AND_KEYWORD_OPTION,
            "   ",
        )

    def test_blank_value_fails_before_scan_even_with_bad_directory(self) -> None:
        # 值校验先于目录检查：不存在的目录不会掩盖空白附加关键词错误。
        self.assert_failure(
            f"错误: {AND_KEYWORD_OPTION} 的附加关键词为空或全为空白",
            str(self.root) + "_不存在",
            "hit",
            AND_KEYWORD_OPTION,
            "  ",
        )


class AndKeywordFilterInteractionTest(unittest.TestCase):
    """路径与格式筛选继续生效；选中的坏文件告警跳过，退出码仍为 0。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "root"
        (self.root / "notes").mkdir(parents=True)
        (self.root / "a.txt").write_text("hit extra\n", encoding="utf-8")
        (self.root / "notes" / "b.md").write_text("hit extra\n", encoding="utf-8")

    def test_combines_with_path_and_type_filters(self) -> None:
        proc = run_cli(
            str(self.root), "hit", AND_KEYWORD_OPTION, "extra",
            "--path-contains", "notes/", "--file-type", "md",
            "--context-chars", "0",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            [{"path": "notes/b.md", "line": 1, "snippet": "hit"}],
        )

    def test_excluded_bad_file_not_read_or_warned(self) -> None:
        (self.root / "notes" / "broken.md").write_bytes(b"hit extra \xff\xff\n")
        proc = run_cli(
            str(self.root), "hit", AND_KEYWORD_OPTION, "extra",
            "--path-excludes", "broken", "--context-chars", "0",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            [
                {"path": "a.txt", "line": 1, "snippet": "hit"},
                {"path": "notes/b.md", "line": 1, "snippet": "hit"},
            ],
        )

    def test_selected_bad_utf8_file_warned_and_skipped_exit_zero(self) -> None:
        (self.root / "broken.txt").write_bytes(b"hit extra \xff\xff\n")
        proc = run_cli(
            str(self.root), "hit", AND_KEYWORD_OPTION, "extra",
            "--context-chars", "0",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("警告: 跳过文件 broken.txt", stderr)
        self.assertEqual(
            json.loads(stdout),
            [
                {"path": "a.txt", "line": 1, "snippet": "hit"},
                {"path": "notes/b.md", "line": 1, "snippet": "hit"},
            ],
        )

    def test_no_qualifying_line_returns_empty_array(self) -> None:
        proc = run_cli(
            str(self.root), "hit", AND_KEYWORD_OPTION, "absent"
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(stdout, "[]\n")


if __name__ == "__main__":
    unittest.main()

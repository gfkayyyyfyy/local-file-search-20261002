"""``--context-chars`` 选项的端到端回归测试。

通过命令入口 ``python -m local_search <目录> <关键词> --context-chars <0-200>``
固定从命令行输入到 JSON 结果输出的行为：

- 取值只接受非空的 ASCII 十进制数字串，允许前导零，范围 0 至 200；首尾空白、
  正负号、小数、非 ASCII 数字（如全角数字）一律拒绝；
- 不指定该选项时片段仍为命中关键词前后各 30 个 Unicode 码点；传入 0 时片段
  只保留完整命中关键词，原文大小写不变；
- 每项片段仍以该行最左侧命中为中心，前后各截取至多指定数量的码点，到行首或
  行尾停止，不借用相邻行、不添加省略号；中文与补充平面字符各按一个码点计算，
  关键词自身长度不计入上下文额度；
- 同一行多次命中仍只返回一项；默认仍取每个文件的首个命中，``--all-lines``
  仍返回每个命中行；
- 选项可与 ``--path-contains``、``--ignore-case``、``--all-lines`` 在位置参数
  之后以任意顺序同用；第二个位置参数或 ``--path-contains`` 的取值即使恰好写作
  ``--context-chars`` 也仍按字面文本处理；
- 缺少取值、重复指定、取值格式不合要求或超出范围时退出码 2、标准输出为空、
  标准错误说明该选项及对应原因，且不开始目录扫描；
- 合法查询继续输出仅含 ``path``、``line``、``snippet`` 的 JSON 数组，沿用既有
  排序与无命中时的空数组；源文件只读，查询后不留下索引或其他文件。

所有资料由测试在 TemporaryDirectory 中独立准备并自动清理，仅使用 Python 标准库、
离线运行。期望值全部以字面量直接写出，不调用任何被测函数来生成期望结果。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

CONTEXT_CHARS = "--context-chars"
ALL_LINES = "--all-lines"
IGNORE_CASE = "--ignore-case"
PATH_OPTION = "--path-contains"


def run_args(directory: Path, *argv: str) -> subprocess.CompletedProcess:
    """以任意命令行参数调用 python -m local_search，返回完成的子进程。"""
    return subprocess.run(
        [sys.executable, "-m", "local_search", str(directory), *argv],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )


class ContextCharsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _write(self, rel: str, data: bytes) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def _write_text(self, rel: str, text: str) -> None:
        self._write(rel, text.encode("utf-8"))

    def assert_success_clean(self, proc: subprocess.CompletedProcess, label: str) -> list:
        """场景 label：退出码必须为 0、标准错误必须为空，返回解析后的 JSON。"""
        self.assertEqual(
            proc.returncode,
            0,
            f"{label}: 退出码应为 0，实际 {proc.returncode}，stderr={proc.stderr!r}",
        )
        self.assertEqual(
            proc.stderr,
            b"",
            f"{label}: 标准错误应为空，实际 {proc.stderr!r}",
        )
        return json.loads(proc.stdout.decode("utf-8"))

    def assert_item_shape(self, item: dict, label: str) -> None:
        self.assertEqual(
            set(item.keys()),
            {"path", "line", "snippet"},
            f"{label}: 每项结果只能含 path、line、snippet 三个键",
        )
        self.assertIsInstance(item["path"], str)
        self.assertIsInstance(item["line"], int)
        self.assertNotIn("\n", item["snippet"], f"{label}: 片段不得包含换行符")
        self.assertNotIn("\r", item["snippet"], f"{label}: 片段不得包含回车符")
        self.assertNotIn("...", item["snippet"], f"{label}: 片段不得添加省略号")

    def assert_argument_error(self, proc: subprocess.CompletedProcess, reason: str, label: str) -> None:
        """输入边界错误：退出码 2、标准输出为空、标准错误说明对应原因。"""
        self.assertEqual(proc.returncode, 2, f"{label}: 退出码应为 2，实际 {proc.returncode}")
        self.assertEqual(proc.stdout, b"", f"{label}: 出错时标准输出必须为空")
        stderr = proc.stderr.decode("utf-8")
        self.assertIn(CONTEXT_CHARS, stderr, f"{label}: 标准错误应指名该选项，实际 {stderr!r}")
        self.assertIn(reason, stderr, f"{label}: 标准错误应说明原因 {reason!r}，实际 {stderr!r}")

    def _snapshot_files(self) -> dict[str, bytes]:
        return {
            str(p.relative_to(self.root)).replace(os.sep, "/"): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    # -- 1. 验收场景：甲乙丙Target丁戊己 / Target again ---------------------

    def test_acceptance_context_chars_2(self) -> None:
        self._write_text("a.txt", "甲乙丙Target丁戊己\nTarget again\n")

        results = self.assert_success_clean(
            run_args(self.root, "TARGET", IGNORE_CASE, CONTEXT_CHARS, "2"), "验收：上下文 2"
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "乙丙Target丁戊"}],
            "唯一结果：path 为 a.txt、line 为 1、snippet 为 乙丙Target丁戊",
        )
        for item in results:
            self.assert_item_shape(item, "验收：上下文 2")

    def test_acceptance_context_chars_0_all_lines(self) -> None:
        self._write_text("a.txt", "甲乙丙Target丁戊己\nTarget again\n")

        results = self.assert_success_clean(
            run_args(self.root, "TARGET", IGNORE_CASE, CONTEXT_CHARS, "0", ALL_LINES),
            "验收：上下文 0 + 逐行",
        )
        self.assertEqual(
            results,
            [
                {"path": "a.txt", "line": 1, "snippet": "Target"},
                {"path": "a.txt", "line": 2, "snippet": "Target"},
            ],
            "返回第 1、2 行两项，snippet 均为 Target（保留原文大小写）",
        )

    # -- 2. 默认值与边界值 ---------------------------------------------------

    def test_default_remains_30_code_points(self) -> None:
        content = "中" * 31 + "target" + "😀" * 31
        self._write_text("u.txt", content + "\n")

        results = self.assert_success_clean(run_args(self.root, "target"), "默认 30")
        self.assertEqual(
            results,
            [{"path": "u.txt", "line": 1, "snippet": "中" * 30 + "target" + "😀" * 30}],
            "不指定 --context-chars 时仍为前后各 30 个码点",
        )

    def test_boundary_values_0_and_200_accepted(self) -> None:
        line = "x" * 300 + "target" + "y" * 300
        self._write_text("big.txt", line + "\n")

        zero = self.assert_success_clean(
            run_args(self.root, "target", CONTEXT_CHARS, "0"), "边界 0"
        )
        self.assertEqual(zero, [{"path": "big.txt", "line": 1, "snippet": "target"}])

        two_hundred = self.assert_success_clean(
            run_args(self.root, "target", CONTEXT_CHARS, "200"), "边界 200"
        )
        self.assertEqual(
            two_hundred,
            [{"path": "big.txt", "line": 1, "snippet": "x" * 200 + "target" + "y" * 200}],
        )

    def test_leading_zeros_accepted(self) -> None:
        self._write_text("a.txt", "甲乙丙Target丁戊己\n")

        results = self.assert_success_clean(
            run_args(self.root, "Target", CONTEXT_CHARS, "002"), "前导零"
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "乙丙Target丁戊"}],
            "取值 002 应按 2 处理",
        )

    # -- 3. 码点语义：中文与补充平面字符各算一个；关键词长度不计入额度 --------

    def test_astral_and_cjk_each_count_as_one_code_point(self) -> None:
        self._write_text("a.txt", "😀😀甲乙Target丙丁😀😀\n")

        results = self.assert_success_clean(
            run_args(self.root, "Target", CONTEXT_CHARS, "4"), "码点计数"
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "😀😀甲乙Target丙丁😀😀"}],
            "前后各 4 个码点：😀 与中文各算一个",
        )
        self.assertEqual(len(results[0]["snippet"]), 14)

    def test_keyword_length_not_counted_in_budget(self) -> None:
        self._write_text("a.txt", "abTARGETWORDcd\n")

        results = self.assert_success_clean(
            run_args(self.root, "TARGETWORD", CONTEXT_CHARS, "2"), "关键词不占额度"
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "abTARGETWORDcd"}],
            "10 字符关键词完整保留，前后各 2 个码点",
        )

    # -- 4. 截取规则：最左侧命中为中心、到行首/行尾停止、不借行不加省略号 ----

    def test_leftmost_hit_anchors_snippet(self) -> None:
        self._write("a.txt", b"target" + b"x" * 10 + b"target" + b"y" * 10 + b"\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", CONTEXT_CHARS, "3"), "最左侧命中"
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "targetxxx"}],
            "同一行两次命中只一项，片段以最左侧命中为中心",
        )

    def test_clipping_stops_at_line_boundaries(self) -> None:
        self._write_text("a.txt", "P" * 40 + "\ntarget\n" + "N" * 40 + "\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", CONTEXT_CHARS, "10"), "行界停止"
        )
        self.assertEqual(results, [{"path": "a.txt", "line": 2, "snippet": "target"}])
        self.assertNotIn("P", results[0]["snippet"])
        self.assertNotIn("N", results[0]["snippet"])

    def test_snippet_taken_from_source_preserving_case(self) -> None:
        self._write_text("a.txt", "12tArGeT34\n")

        results = self.assert_success_clean(
            run_args(self.root, "TARGET", IGNORE_CASE, CONTEXT_CHARS, "2"), "保留大小写"
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "12tArGeT34"}],
            "匹配忽略大小写，片段仍取自源文本",
        )

    # -- 5. 与既有选项以任意顺序同用 -----------------------------------------

    def test_combines_with_other_options_in_any_order(self) -> None:
        self._write_text("notes/a.txt", "甲乙Target丙丁\n戊Target己\n")
        self._write_text("other/b.txt", "Target elsewhere\n")

        expected = [
            {"path": "notes/a.txt", "line": 1, "snippet": "乙Target丙"},
            {"path": "notes/a.txt", "line": 2, "snippet": "戊Target己"},
        ]
        for argv in (
            ("TARGET", CONTEXT_CHARS, "1", IGNORE_CASE, ALL_LINES, PATH_OPTION, "notes/"),
            ("TARGET", ALL_LINES, PATH_OPTION, "notes/", CONTEXT_CHARS, "1", IGNORE_CASE),
            ("TARGET", IGNORE_CASE, ALL_LINES, CONTEXT_CHARS, "1", PATH_OPTION, "notes/"),
        ):
            with self.subTest(argv=argv):
                results = self.assert_success_clean(run_args(self.root, *argv), "任意顺序")
                self.assertEqual(results, expected)

    # -- 6. 字面边界：位置参数与 --path-contains 取值可恰为 --context-chars --

    def test_keyword_equal_to_option_name_is_literal(self) -> None:
        self._write_text("cfg.txt", "see --context-chars here\n")

        results = self.assert_success_clean(
            run_args(self.root, CONTEXT_CHARS), "关键词恰为选项名"
        )
        self.assertEqual(
            results,
            [{"path": "cfg.txt", "line": 1, "snippet": "see --context-chars here"}],
            "第二个位置参数即使写作 --context-chars 也仍是关键词",
        )

    def test_path_filter_value_equal_to_option_name_is_literal(self) -> None:
        self._write_text("--context-chars.md", "target odd\n")
        self._write_text("plain.md", "target plain\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", PATH_OPTION, CONTEXT_CHARS), "片段恰为选项名"
        )
        self.assertEqual(
            results,
            [{"path": "--context-chars.md", "line": 1, "snippet": "target odd"}],
            "--path-contains 的取值 --context-chars 必须是字面片段而非选项",
        )

    # -- 7. 参数错误：缺值、重复、格式、范围 → 退出 2、标准输出为空 ----------

    def test_missing_value_is_argument_error(self) -> None:
        proc = run_args(self.root, "target", CONTEXT_CHARS)
        self.assert_argument_error(proc, "缺少值", "缺少取值")

    def test_value_looking_like_flag_is_consumed_literally_then_rejected(self) -> None:
        # 取值始终按字面消费：--all-lines 被当作取值，随后因格式不合报错，
        # 而不是被解释为开关。
        proc = run_args(self.root, "target", CONTEXT_CHARS, ALL_LINES)
        self.assert_argument_error(proc, "ASCII 十进制数字串", "取值恰为开关名")
        self.assertIn(ALL_LINES, proc.stderr.decode("utf-8"))

    def test_duplicate_option_is_argument_error(self) -> None:
        proc = run_args(self.root, "target", CONTEXT_CHARS, "5", CONTEXT_CHARS, "6")
        self.assert_argument_error(proc, "只能指定一次", "重复指定")

    def test_invalid_formats_are_argument_errors(self) -> None:
        cases = [
            ("", "空串"),
            (" 5", "首尾空白"),
            ("5 ", "尾随空白"),
            ("+5", "正号"),
            ("-1", "负号"),
            ("2.5", "小数"),
            ("5a", "含字母"),
            ("１２", "全角数字"),
            ("²", "上标数字"),
        ]
        for value, label in cases:
            with self.subTest(value=value, label=label):
                proc = run_args(self.root, "target", CONTEXT_CHARS, value)
                self.assert_argument_error(proc, "ASCII 十进制数字串", f"格式非法 {label}")

    def test_out_of_range_is_argument_error(self) -> None:
        for value in ("201", "1000", "0201"):
            with self.subTest(value=value):
                proc = run_args(self.root, "target", CONTEXT_CHARS, value)
                self.assert_argument_error(proc, "超出范围", f"超范围 {value}")

    def test_argument_error_happens_before_any_scan(self) -> None:
        # 目录不存在时，--context-chars 的参数错误仍优先报出（不开始目录扫描）。
        proc = run_args(self.root / "不存在的目录", "target", CONTEXT_CHARS, "x")
        self.assert_argument_error(proc, "ASCII 十进制数字串", "先校验选项")

    # -- 8. 无命中与只读语义 --------------------------------------------------

    def test_no_hit_returns_empty_array(self) -> None:
        self._write_text("a.txt", "alpha\nbeta\n")
        proc = run_args(self.root, "target", CONTEXT_CHARS, "5")
        results = self.assert_success_clean(proc, "无命中")
        self.assertEqual(results, [])
        self.assertEqual(proc.stdout.decode("utf-8").strip(), "[]")

    def test_query_is_read_only_and_leaves_no_index(self) -> None:
        self._write_text("keep.txt", "甲乙Target丙丁\n")
        before = self._snapshot_files()
        proc = run_args(self.root, "Target", CONTEXT_CHARS, "1")
        after = self._snapshot_files()
        self.assertEqual(before, after, "查询后源文件字节不得变化，且不得新增任何文件/索引")
        results = self.assert_success_clean(proc, "只读/无索引")
        self.assertEqual(results, [{"path": "keep.txt", "line": 1, "snippet": "乙Target丙"}])


if __name__ == "__main__":
    unittest.main()

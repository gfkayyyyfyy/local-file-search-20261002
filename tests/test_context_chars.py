"""``--context-chars`` 选项的端到端回归测试。

通过命令入口 ``python -m local_search <目录> <关键词> --context-chars <码点数>``
固定从命令行输入到 JSON 结果输出的行为：

- 取值只接受非空的 ASCII 十进制数字串，允许前导零，数值范围 0 至 200；
  首尾空白、正负号、小数及非 ASCII 数字一律拒绝；
- 不传该选项时片段仍为命中关键词前后各至多 30 个 Unicode 码点；
  传入 0 时片段只保留完整命中关键词，原文大小写保持不变；
- 片段仍以该行最左侧匹配为中心，前后各截取至多指定数量的码点，到行首或
  行尾停止，不借用相邻行、不添加省略号；关键词自身长度不计入上下文额度；
- 中文与补充平面字符各按一个码点计算；
- 同一行多次命中仍只返回一项；默认仍取每个文件的首个命中，
  ``--all-lines`` 仍返回每个命中行；
- 选项可与 ``--path-contains``、``--ignore-case``、``--all-lines`` 在两个
  位置参数之后以任意顺序同用；第二个位置参数或 ``--path-contains`` 的取值
  即使写作 ``--context-chars`` 也仍按字面文本处理；
- 缺少取值、重复指定、取值格式不合要求或超出范围时退出码 2、标准输出为空、
  标准错误说明该选项及对应原因，且不开始目录扫描；
- 合法查询继续输出仅含 ``path``、``line``、``snippet`` 的 JSON 数组，沿用
  既有排序，无命中时输出 ``[]``。

所有资料由测试在 TemporaryDirectory 中独立准备并自动清理，仅使用 Python 标准库、
离线运行。期望值全部以字面量直接写出，不调用任何被测函数来生成期望结果。
"""

import json
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

    def _write_text(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))

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

    def assert_option_error(self, proc: subprocess.CompletedProcess, reason: str, label: str) -> None:
        """选项错误：退出码 2、标准输出为空、标准错误说明该选项及对应原因。"""
        self.assertEqual(proc.returncode, 2, f"{label}: 退出码应为 2，实际 {proc.returncode}")
        self.assertEqual(proc.stdout, b"", f"{label}: 出错时标准输出必须为空")
        stderr = proc.stderr.decode("utf-8")
        self.assertIn(CONTEXT_CHARS, stderr, f"{label}: 标准错误应提及该选项，实际 {stderr!r}")
        self.assertIn(reason, stderr, f"{label}: 标准错误应说明原因 {reason!r}，实际 {stderr!r}")

    # -- 1. 验收场景：--context-chars 2 与 --context-chars 0 ----------------

    def test_acceptance_context_two(self) -> None:
        # 资料目录仅含 a.txt，两行分别为 甲乙丙Target丁戊己 与 Target again。
        self._write_text("a.txt", "甲乙丙Target丁戊己\nTarget again\n")
        proc = run_args(self.root, "TARGET", IGNORE_CASE, CONTEXT_CHARS, "2")
        result = self.assert_success_clean(proc, "context 2")
        self.assertEqual(
            result,
            [{"path": "a.txt", "line": 1, "snippet": "乙丙Target丁戊"}],
        )

    def test_acceptance_context_zero_all_lines(self) -> None:
        self._write_text("a.txt", "甲乙丙Target丁戊己\nTarget again\n")
        proc = run_args(self.root, "TARGET", IGNORE_CASE, CONTEXT_CHARS, "0", ALL_LINES)
        result = self.assert_success_clean(proc, "context 0 + all-lines")
        self.assertEqual(
            result,
            [
                {"path": "a.txt", "line": 1, "snippet": "Target"},
                {"path": "a.txt", "line": 2, "snippet": "Target"},
            ],
        )

    # -- 2. 默认仍为前后各 30 个码点 ----------------------------------------

    def test_default_context_unchanged(self) -> None:
        line = "甲" * 40 + "Target" + "乙" * 40
        self._write_text("a.txt", line + "\n")
        proc = run_args(self.root, "Target")
        result = self.assert_success_clean(proc, "默认 30")
        self.assertEqual(
            result,
            [{"path": "a.txt", "line": 1, "snippet": "甲" * 30 + "Target" + "乙" * 30}],
        )

    # -- 3. 截取边界：行首行尾停止、不借相邻行、不加省略号 -------------------

    def test_snippet_stops_at_line_boundaries(self) -> None:
        self._write_text("a.txt", "abTargetcd\nefgh\n")
        proc = run_args(self.root, "Target", CONTEXT_CHARS, "10")
        result = self.assert_success_clean(proc, "行首行尾停止")
        self.assertEqual(result, [{"path": "a.txt", "line": 1, "snippet": "abTargetcd"}])

    def test_keyword_length_not_counted(self) -> None:
        # 关键词 6 个码点，前后各取 2 个码点，与关键词长度无关。
        self._write_text("a.txt", "0123456789Targetabcdefghij\n")
        proc = run_args(self.root, "Target", CONTEXT_CHARS, "2")
        result = self.assert_success_clean(proc, "关键词长度不计入")
        self.assertEqual(result, [{"path": "a.txt", "line": 1, "snippet": "89Targetab"}])

    def test_supplementary_plane_counts_as_one_code_point(self) -> None:
        # 😀 位于补充平面（U+1F600），前后各取 1 个码点仍各取到一个完整字符。
        self._write_text("a.txt", "😀Target😀\n")
        proc = run_args(self.root, "Target", CONTEXT_CHARS, "1")
        result = self.assert_success_clean(proc, "补充平面按 1 码点")
        self.assertEqual(result, [{"path": "a.txt", "line": 1, "snippet": "😀Target😀"}])

    def test_leftmost_match_anchors_snippet(self) -> None:
        self._write_text("a.txt", "xxTarget yy Targetzz\n")
        proc = run_args(self.root, "Target", CONTEXT_CHARS, "2")
        result = self.assert_success_clean(proc, "最左侧命中为中心")
        self.assertEqual(result, [{"path": "a.txt", "line": 1, "snippet": "xxTarget y"}])

    def test_snippet_keeps_source_case(self) -> None:
        self._write_text("a.txt", "abTARGETcd\n")
        proc = run_args(self.root, "target", IGNORE_CASE, CONTEXT_CHARS, "0")
        result = self.assert_success_clean(proc, "保留原文大小写")
        self.assertEqual(result, [{"path": "a.txt", "line": 1, "snippet": "TARGET"}])

    # -- 4. 取值边界：前导零、0、200 合法；201 及格式不合拒绝 ----------------

    def test_leading_zeros_accepted(self) -> None:
        self._write_text("a.txt", "abTargetcd\n")
        proc = run_args(self.root, "Target", CONTEXT_CHARS, "002")
        result = self.assert_success_clean(proc, "前导零")
        self.assertEqual(result, [{"path": "a.txt", "line": 1, "snippet": "abTargetcd"}])

    def test_upper_bound_200_accepted(self) -> None:
        self._write_text("a.txt", "甲" * 300 + "Target" + "乙" * 300 + "\n")
        proc = run_args(self.root, "Target", CONTEXT_CHARS, "200")
        result = self.assert_success_clean(proc, "边界 200")
        self.assertEqual(
            result,
            [{"path": "a.txt", "line": 1, "snippet": "甲" * 200 + "Target" + "乙" * 200}],
        )

    def test_out_of_range_rejected(self) -> None:
        self._write_text("a.txt", "Target\n")
        for value in ("201", "999", "1000"):
            with self.subTest(value=value):
                proc = run_args(self.root, "Target", CONTEXT_CHARS, value)
                self.assert_option_error(proc, "超出范围", f"超范围 {value}")

    def test_invalid_format_rejected(self) -> None:
        self._write_text("a.txt", "Target\n")
        for value in ("", " 5", "5 ", "+5", "-5", "5.0", "1e2", "abc", "５", "5\n"):
            with self.subTest(value=value):
                proc = run_args(self.root, "Target", CONTEXT_CHARS, value)
                self.assert_option_error(proc, "ASCII 十进制数字串", f"非法格式 {value!r}")

    def test_missing_value_rejected(self) -> None:
        self._write_text("a.txt", "Target\n")
        proc = run_args(self.root, "Target", CONTEXT_CHARS)
        self.assert_option_error(proc, "缺少值", "缺少取值")

    def test_duplicate_option_rejected(self) -> None:
        self._write_text("a.txt", "Target\n")
        proc = run_args(self.root, "Target", CONTEXT_CHARS, "5", CONTEXT_CHARS, "6")
        self.assert_option_error(proc, "只能指定一次", "重复指定")

    def test_value_looking_like_option_rejected(self) -> None:
        # 紧随的取值即使写作其他选项，也按字面消费并因格式不合而报错。
        self._write_text("a.txt", "Target\n")
        proc = run_args(self.root, "Target", CONTEXT_CHARS, IGNORE_CASE)
        self.assert_option_error(proc, "ASCII 十进制数字串", "取值写作选项")

    def test_error_before_scan(self) -> None:
        # 目录不存在时，非法的 --context-chars 仍先报错，不开始目录扫描。
        proc = run_args(self.root / "不存在", "Target", CONTEXT_CHARS, "abc")
        self.assert_option_error(proc, "ASCII 十进制数字串", "先解析后扫描")

    # -- 5. 与既有选项任意顺序同用；字面位置参数不受影响 ----------------------

    def test_combines_with_other_options_any_order(self) -> None:
        self._write_text("notes/b.md", "甲乙TARGET丙丁\n")
        self._write_text("a.txt", "其他\n")
        proc = run_args(
            self.root,
            "target",
            CONTEXT_CHARS,
            "1",
            ALL_LINES,
            IGNORE_CASE,
            PATH_OPTION,
            "notes/",
        )
        result = self.assert_success_clean(proc, "任意顺序同用")
        self.assertEqual(result, [{"path": "notes/b.md", "line": 1, "snippet": "乙TARGET丙"}])

    def test_keyword_written_as_option_is_literal(self) -> None:
        # 第二个位置参数即使写作 --context-chars 也仍是关键词。
        self._write_text("a.txt", "xx--context-chars yy\n")
        proc = run_args(self.root, CONTEXT_CHARS, CONTEXT_CHARS, "2")
        result = self.assert_success_clean(proc, "字面关键词")
        self.assertEqual(
            result,
            [{"path": "a.txt", "line": 1, "snippet": "xx--context-chars y"}],
        )

    def test_path_contains_value_written_as_option_is_literal(self) -> None:
        # --path-contains 的取值即使写作 --context-chars 也仍是字面片段。
        self._write_text("a.txt", "Target\n")
        proc = run_args(self.root, "Target", PATH_OPTION, CONTEXT_CHARS)
        result = self.assert_success_clean(proc, "字面路径片段")
        self.assertEqual(result, [])

    # -- 6. 结果形状与无命中 -------------------------------------------------

    def test_no_hit_returns_empty_array(self) -> None:
        self._write_text("a.txt", "nothing here\n")
        proc = run_args(self.root, "Target", CONTEXT_CHARS, "5")
        result = self.assert_success_clean(proc, "无命中")
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()

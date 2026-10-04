"""``--offset`` 结果起始偏移的端到端回归测试。

通过命令入口 ``python -m local_search <目录> <关键词> ... --offset <N>`` 与
公开入口 ``local_search.main`` 固定从命令行输入到结果输出的行为：

- 先得到按既有条件筛选、按 ``path`` 的区分大小写 Unicode 字典序、同路径
  ``line`` 升序排列的完整结果，再跳过前 N 项，最后应用 ``--limit``；未指定
  数量上限时返回全部剩余项；未指定 ``--offset`` 等同于 0，原有输出不变；
- N 按结果项计数：默认每个文件只返回首个合格行（一项），``--all-lines``
  下每个合格行分别计数；同一行关键词重复出现不增加计数；
- 偏移等于或大于合格项总数时返回码为 0，JSON 输出 ``[]``，CSV 仅输出原有
  表头及 CRLF；
- 取值只接受 0 至 1000 的非空 ASCII 十进制数字串，允许前导零；缺值、
  重复、空值、空白、带正负号、小数、非 ASCII 数字或数值越界时退出码 2、
  标准输出为空、标准错误包含 ``--offset`` 与对应原因，且在扫描前报错，
  不输出异常堆栈；
- 紧随该选项的参数即使看似开关（如 ``--all-lines``）也作为取值校验，
  不启用开关；它出现在关键词或其他选项取值中时仍按字面文本处理；
- 偏移不省略候选坏文件的既有告警；被路径或格式筛选排除的文件仍不读取、
  不告警；
- 查询只读源文件，不留下索引或其他文件。

所有资料由测试在 TemporaryDirectory 中独立准备并自动清理，仅使用 Python
标准库、离线运行。期望值全部以字面量直接写出，不调用任何被测函数来生成
期望结果。
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from local_search import main as main_entry

OFFSET = "--offset"
LIMIT = "--limit"
ALL_LINES = "--all-lines"
IGNORE_CASE = "--ignore-case"
CONTEXT_CHARS = "--context-chars"
PATH_OPTION = "--path-contains"
FORMAT = "--format"


def run_args(directory: Path, *argv: str) -> subprocess.CompletedProcess:
    """以任意命令行参数调用 python -m local_search，返回完成的子进程。"""
    return subprocess.run(
        [sys.executable, "-m", "local_search", str(directory), *argv],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )


def run_main_entry(*argv: str) -> tuple[int, bytes, bytes]:
    """以公开入口 local_search.main 调用，返回退出码与标准输出/错误字节。"""
    stdout_buffer = io.BytesIO()
    stderr_buffer = io.BytesIO()
    out = io.TextIOWrapper(stdout_buffer, encoding="utf-8", newline="")
    err = io.TextIOWrapper(stderr_buffer, encoding="utf-8", newline="")
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        code = main_entry(list(argv))
    finally:
        out.flush()
        err.flush()
        sys.stdout, sys.stderr = old_stdout, old_stderr
    return code, stdout_buffer.getvalue(), stderr_buffer.getvalue()


class OffsetTest(unittest.TestCase):
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

    def _write_demo_tree(self) -> None:
        """验收演示目录：a.txt 两行 + notes/b.md 一行。"""
        self._write_text("a.txt", "target one\ntarget two\n")
        self._write_text("notes/b.md", "target three\n")

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

    def assert_argument_error(self, proc: subprocess.CompletedProcess, reason: str, label: str) -> None:
        """输入边界错误：退出码 2、标准输出为空、标准错误含 --offset 与原因。"""
        self.assertEqual(proc.returncode, 2, f"{label}: 退出码应为 2，实际 {proc.returncode}")
        self.assertEqual(proc.stdout, b"", f"{label}: 出错时标准输出必须为空")
        stderr = proc.stderr.decode("utf-8")
        self.assertIn(OFFSET, stderr, f"{label}: 标准错误应包含 --offset，实际 {stderr!r}")
        self.assertIn(reason, stderr, f"{label}: 标准错误应说明原因 {reason!r}，实际 {stderr!r}")
        self.assertNotIn("Traceback", stderr, f"{label}: 标准错误不得含异常堆栈")

    def _snapshot_files(self) -> dict[str, bytes]:
        return {
            str(p.relative_to(self.root)).replace(os.sep, "/"): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    # -- 1. 验收演示场景：--offset 1 --limit 1 与 --offset 2 无上限 ---------

    def test_demo_scenario_offset_one_limit_one(self) -> None:
        self._write_demo_tree()

        proc = run_args(
            self.root, "target", ALL_LINES, CONTEXT_CHARS, "0", OFFSET, "1", LIMIT, "1"
        )
        results = self.assert_success_clean(proc, "演示：--offset 1 --limit 1")
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 2, "snippet": "target"}],
            "跳过首项后只应得到 a.txt 第 2 行，snippet 为 target",
        )
        for item in results:
            self.assertEqual(set(item.keys()), {"path", "line", "snippet"})

    def test_demo_scenario_offset_two_without_limit(self) -> None:
        self._write_demo_tree()

        results = self.assert_success_clean(
            run_args(self.root, "target", ALL_LINES, CONTEXT_CHARS, "0", OFFSET, "2"),
            "演示：--offset 2 无上限",
        )
        self.assertEqual(
            results,
            [{"path": "notes/b.md", "line": 1, "snippet": "target"}],
            "跳过前两项后只应得到 notes/b.md 第 1 行",
        )

    # -- 2. 未指定 --offset 或显式 0 时输出与既有行为完全一致 ----------------

    def test_without_offset_output_unchanged(self) -> None:
        self._write_demo_tree()
        expected = [
            {"path": "a.txt", "line": 1, "snippet": "target"},
            {"path": "a.txt", "line": 2, "snippet": "target"},
            {"path": "notes/b.md", "line": 1, "snippet": "target"},
        ]
        for argv in (
            ("target", ALL_LINES, CONTEXT_CHARS, "0"),
            ("target", ALL_LINES, CONTEXT_CHARS, "0", OFFSET, "0"),
            ("target", ALL_LINES, CONTEXT_CHARS, "0", OFFSET, "000"),
        ):
            with self.subTest(argv=argv):
                results = self.assert_success_clean(run_args(self.root, *argv), "无偏移")
                self.assertEqual(results, expected)

    # -- 3. 偏移依据排序后的结果序列，且在 --limit 之前应用 ------------------

    def test_offset_follows_sorted_order_not_scan_order(self) -> None:
        # 先写 z.txt 再写 a.txt：无论扫描顺序如何，--offset 1 必须跳过排序后
        # 的第一项（a.txt），而不是先被扫描到的文件。
        self._write_text("z.txt", "target zed\n")
        self._write_text("a.txt", "target alpha\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", OFFSET, "1"), "按排序跳过"
        )
        self.assertEqual(
            results,
            [{"path": "z.txt", "line": 1, "snippet": "target zed"}],
        )

    def test_offset_applies_before_limit(self) -> None:
        self._write_text("a.txt", "target a1\ntarget a2\ntarget a3\ntarget a4\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", ALL_LINES, OFFSET, "1", LIMIT, "2"),
            "先偏移后限量",
        )
        self.assertEqual(
            [(item["path"], item["line"]) for item in results],
            [("a.txt", 2), ("a.txt", 3)],
            "先跳过排序后的前 1 项，再对剩余项取前 2 项",
        )

    def test_offset_counts_items_across_files_in_sorted_order(self) -> None:
        self._write_text("b.txt", "target b1\ntarget b2\n")
        self._write_text("a.txt", "target a1\ntarget a2\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", ALL_LINES, OFFSET, "3"), "跨文件计项"
        )
        self.assertEqual(
            [(item["path"], item["line"]) for item in results],
            [("b.txt", 2)],
            "N 按结果项计数，跨文件按排序后的序列跳过",
        )

    # -- 4. 默认与 --all-lines 的计数口径；同行重复命中不重复计项 ------------

    def test_default_mode_counts_one_item_per_file(self) -> None:
        self._write_text("a.txt", "target a1\ntarget a2\n")
        self._write_text("b.txt", "target b1\ntarget b2\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", OFFSET, "1"), "默认每文件一项"
        )
        self.assertEqual(
            results,
            [{"path": "b.txt", "line": 1, "snippet": "target b1"}],
            "默认每个文件只返回首个合格行，--offset 1 只跳过这一项",
        )

    def test_all_lines_counts_each_qualified_line(self) -> None:
        self._write_text("a.txt", "target a1\ntarget a2\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", ALL_LINES, OFFSET, "1"), "逐行分别计项"
        )
        self.assertEqual(
            [(item["line"]) for item in results],
            [2],
            "--all-lines 下每个合格行分别计数",
        )

    def test_repeated_keyword_on_same_line_counts_once(self) -> None:
        self._write_text("a.txt", "target target target\n")
        self._write_text("b.txt", "target b1\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", ALL_LINES, OFFSET, "1"), "同行重复命中"
        )
        self.assertEqual(
            results,
            [{"path": "b.txt", "line": 1, "snippet": "target b1"}],
            "同一行关键词重复出现仍只算一项，--offset 1 跳过 a.txt 后只剩 b.txt",
        )

    # -- 5. 偏移等于或大于合格项总数 -----------------------------------------

    def test_offset_equal_to_total_returns_empty_json(self) -> None:
        self._write_demo_tree()
        proc = run_args(self.root, "target", ALL_LINES, OFFSET, "3")
        results = self.assert_success_clean(proc, "偏移等于总数")
        self.assertEqual(results, [])
        self.assertEqual(proc.stdout.decode("utf-8").strip(), "[]")

    def test_offset_beyond_total_returns_empty_json(self) -> None:
        self._write_demo_tree()
        proc = run_args(self.root, "target", ALL_LINES, OFFSET, "1000")
        results = self.assert_success_clean(proc, "偏移大于总数")
        self.assertEqual(results, [])

    def test_offset_beyond_total_csv_outputs_header_only(self) -> None:
        self._write_demo_tree()
        proc = run_args(self.root, "target", ALL_LINES, OFFSET, "3", FORMAT, "csv")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, b"")
        self.assertEqual(
            proc.stdout,
            b"path,line,snippet\r\n",
            "CSV 无剩余项时只输出原有表头及 CRLF",
        )

    def test_csv_format_applies_offset(self) -> None:
        self._write_demo_tree()
        proc = run_args(
            self.root, "target", ALL_LINES, CONTEXT_CHARS, "0", OFFSET, "2", FORMAT, "csv"
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, b"")
        self.assertEqual(
            proc.stdout,
            b"path,line,snippet\r\nnotes/b.md,1,target\r\n",
        )

    # -- 6. 取值边界：前导零合法，0 与 1000 合法 ------------------------------

    def test_leading_zeros_and_bounds_accepted(self) -> None:
        self._write_demo_tree()
        for raw, expected_count in (("0", 3), ("000", 3), ("1", 2), ("007", 0), ("01000", 0), ("1000", 0)):
            with self.subTest(raw=raw):
                results = self.assert_success_clean(
                    run_args(self.root, "target", ALL_LINES, OFFSET, raw),
                    f"取值 {raw!r}",
                )
                self.assertEqual(len(results), expected_count)

    def test_invalid_values_fail_before_scan(self) -> None:
        self._write_demo_tree()
        bad_format = ("", " ", " 2", "2 ", "+2", "-2", "2.5", "１２", "2a")
        for raw in bad_format:
            with self.subTest(raw=raw):
                proc = run_args(self.root, "target", OFFSET, raw)
                self.assert_argument_error(proc, "非空的 ASCII 十进制数字串", f"格式 {raw!r}")
        for raw in ("1001", "01001"):
            with self.subTest(raw=raw):
                proc = run_args(self.root, "target", OFFSET, raw)
                self.assert_argument_error(proc, "超出范围 0-1000", f"范围 {raw!r}")

    def test_missing_value_and_duplicate_fail_before_scan(self) -> None:
        self._write_demo_tree()
        proc = run_args(self.root, "target", OFFSET)
        self.assert_argument_error(proc, "缺少值", "缺值")
        proc = run_args(self.root, "target", OFFSET, "1", OFFSET, "2")
        self.assert_argument_error(proc, "只能指定一次", "重复指定")

    def test_value_equal_to_switch_is_consumed_as_value(self) -> None:
        self._write_demo_tree()
        # --all-lines 被当作 --offset 的取值消费：报取值格式错误，开关不启用。
        proc = run_args(self.root, "target", OFFSET, ALL_LINES)
        self.assert_argument_error(proc, "非空的 ASCII 十进制数字串", "取值看似开关")

    def test_option_token_as_keyword_stays_literal(self) -> None:
        self._write_text("cfg.txt", "line --offset here\n")
        results = self.assert_success_clean(
            run_args(self.root, OFFSET), "关键词恰为 --offset"
        )
        self.assertEqual(
            results,
            [{"path": "cfg.txt", "line": 1, "snippet": "line --offset here"}],
            "第二个位置参数即使写作 --offset 也仍是关键词",
        )

    def test_option_token_inside_other_option_value_stays_literal(self) -> None:
        self._write_text("notes/a.txt", "target here\n")
        self._write_text("other/b.txt", "target there\n")
        results = self.assert_success_clean(
            run_args(self.root, "target", PATH_OPTION, OFFSET),
            "--offset 作为其他选项的取值",
        )
        self.assertEqual(
            results,
            [],
            "--offset 作为 --path-contains 的字面取值，无任何路径包含它",
        )

    # -- 7. 与其他选项任意顺序同用 -------------------------------------------

    def test_combines_with_other_options_in_any_order(self) -> None:
        self._write_demo_tree()
        expected = [{"path": "a.txt", "line": 2, "snippet": "target"}]
        for argv in (
            ("target", ALL_LINES, CONTEXT_CHARS, "0", OFFSET, "1", LIMIT, "1"),
            ("target", OFFSET, "1", LIMIT, "1", CONTEXT_CHARS, "0", ALL_LINES),
            ("target", LIMIT, "1", ALL_LINES, OFFSET, "1", CONTEXT_CHARS, "0"),
        ):
            with self.subTest(argv=argv):
                results = self.assert_success_clean(run_args(self.root, *argv), "任意顺序")
                self.assertEqual(results, expected)

    # -- 8. 告警与筛选语义不因偏移改变 ---------------------------------------

    def test_warnings_not_omitted_when_offset_skips_all_results(self) -> None:
        # a.txt 的唯一命中被 --offset 1 跳过，坏文件 b.txt 的告警仍必须产生。
        self._write_text("a.txt", "target here\n")
        self._write("b.txt", b"bad \xff\xff\n")
        proc = run_args(self.root, "target", OFFSET, "1")

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout.decode("utf-8")), [])
        stderr = proc.stderr.decode("utf-8")
        self.assertIn("b.txt", stderr)
        self.assertIn("无法按 UTF-8 解码", stderr)

    def test_excluded_file_is_not_read_or_warned_with_offset(self) -> None:
        self._write("notes/broken.txt", b"target broken\n\xff\n")
        self._write_text("notes/ok.txt", "target ok\n")
        proc = run_args(
            self.root, "target", PATH_OPTION, "notes/ok", OFFSET, "1"
        )
        results = self.assert_success_clean(proc, "排除坏文件")
        self.assertEqual(results, [])
        self.assertEqual(proc.stderr, b"", "被路径筛选排除的文件不得产生告警")

    # -- 9. 公开入口 local_search.main 与命令行语义一致 -----------------------

    def test_main_entry_matches_command_line_semantics(self) -> None:
        self._write_demo_tree()
        argv = (
            str(self.root), "target", ALL_LINES, CONTEXT_CHARS, "0",
            OFFSET, "1", LIMIT, "1",
        )
        code, stdout, stderr = run_main_entry(*argv)
        self.assertEqual(code, 0)
        self.assertEqual(stderr, b"")
        self.assertEqual(
            json.loads(stdout.decode("utf-8")),
            [{"path": "a.txt", "line": 2, "snippet": "target"}],
        )

    def test_main_entry_invalid_offset_returns_2(self) -> None:
        self._write_demo_tree()
        code, stdout, stderr = run_main_entry(str(self.root), "target", OFFSET, "x")
        self.assertEqual(code, 2)
        self.assertEqual(stdout, b"")
        text = stderr.decode("utf-8")
        self.assertIn(OFFSET, text)
        self.assertIn("非空的 ASCII 十进制数字串", text)
        self.assertNotIn("Traceback", text)

    # -- 10. 查询只读源文件且不留下索引 ---------------------------------------

    def test_offset_query_is_read_only_and_leaves_no_index(self) -> None:
        self._write_demo_tree()
        before = self._snapshot_files()
        proc = run_args(
            self.root, "target", ALL_LINES, CONTEXT_CHARS, "0", OFFSET, "1", LIMIT, "1"
        )
        after = self._snapshot_files()
        self.assertEqual(before, after, "查询后源文件字节不得变化，且不得新增任何文件/索引")
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()

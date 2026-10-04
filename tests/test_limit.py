"""``--limit`` 结果数量上限的端到端回归测试。

通过命令入口 ``python -m local_search <目录> <关键词> ... --limit <N>`` 固定
从命令行输入到 JSON 结果输出的行为：

- 未指定 ``--limit`` 时输出与既有行为完全一致；指定后只返回同一查询未加
  限制时所得数组的前 N 项，仍按 ``path`` 的区分大小写 Unicode 字典序、
  同路径 ``line`` 升序选取，不按目录扫描顺序截断；
- N 按结果项计数：默认每个文件只返回首个合格行（一项），``--all-lines``
  下每个合格行分别计数；不足 N 项时全部返回，无命中时输出 ``[]``；
- 每项仍仅含 ``path``、``line``、``snippet``，片段与行号保持原样；
- 取值只接受 1 至 1000 的非空 ASCII 十进制数字串，允许前导零；缺值、
  重复、空值、空白、带正负号、小数、非 ASCII 数字或数值越界时退出码 2、
  标准输出为空、标准错误包含 ``--limit`` 与对应原因，且在扫描前报错；
- 紧随该选项的参数即使看似开关（如 ``--all-lines``）也作为取值校验，
  不启用开关；它出现在关键词或其他选项取值中时仍按字面文本处理；
- 数量上限不省略候选坏文件的既有告警（已得到足够结果时亦如此）；被路径
  或格式筛选排除的文件仍不读取、不告警；
- 查询只读源文件，不留下索引或其他文件。

所有资料由测试在 TemporaryDirectory 中独立准备并自动清理，仅使用 Python
标准库、离线运行。期望值全部以字面量直接写出，不调用任何被测函数来生成
期望结果。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

LIMIT = "--limit"
ALL_LINES = "--all-lines"
IGNORE_CASE = "--ignore-case"
CONTEXT_CHARS = "--context-chars"
PATH_OPTION = "--path-contains"


def run_args(directory: Path, *argv: str) -> subprocess.CompletedProcess:
    """以任意命令行参数调用 python -m local_search，返回完成的子进程。"""
    return subprocess.run(
        [sys.executable, "-m", "local_search", str(directory), *argv],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )


class LimitTest(unittest.TestCase):
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
        self._write_text("a.txt", "Target one\ntarget two\n")
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
        """输入边界错误：退出码 2、标准输出为空、标准错误含 --limit 与原因。"""
        self.assertEqual(proc.returncode, 2, f"{label}: 退出码应为 2，实际 {proc.returncode}")
        self.assertEqual(proc.stdout, b"", f"{label}: 出错时标准输出必须为空")
        stderr = proc.stderr.decode("utf-8")
        self.assertIn(LIMIT, stderr, f"{label}: 标准错误应包含 --limit，实际 {stderr!r}")
        self.assertIn(reason, stderr, f"{label}: 标准错误应说明原因 {reason!r}，实际 {stderr!r}")

    def _snapshot_files(self) -> dict[str, bytes]:
        return {
            str(p.relative_to(self.root)).replace(os.sep, "/"): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    # -- 1. 验收演示场景：--limit 2 截前两项，去掉后末尾多出第三项 ---------

    def test_demo_scenario_limit_two_returns_first_two_items(self) -> None:
        self._write_demo_tree()

        proc = run_args(
            self.root, "TARGET", IGNORE_CASE, ALL_LINES, CONTEXT_CHARS, "0", LIMIT, "2"
        )
        results = self.assert_success_clean(proc, "演示：--limit 2")
        self.assertEqual(
            results,
            [
                {"path": "a.txt", "line": 1, "snippet": "Target"},
                {"path": "a.txt", "line": 2, "snippet": "target"},
            ],
            "应依次返回 a.txt 第 1、2 行，snippet 分别为 Target、target",
        )
        for item in results:
            self.assertEqual(set(item.keys()), {"path", "line", "snippet"})

    def test_demo_scenario_without_limit_appends_third_item(self) -> None:
        self._write_demo_tree()

        results = self.assert_success_clean(
            run_args(self.root, "TARGET", IGNORE_CASE, ALL_LINES, CONTEXT_CHARS, "0"),
            "演示：无 --limit",
        )
        self.assertEqual(
            results,
            [
                {"path": "a.txt", "line": 1, "snippet": "Target"},
                {"path": "a.txt", "line": 2, "snippet": "target"},
                {"path": "notes/b.md", "line": 1, "snippet": "target"},
            ],
            "去掉 --limit 2 后应在末尾增加 notes/b.md 第 1 行",
        )

    # -- 2. 截断依据排序后的结果序列，而非目录扫描顺序 ---------------------

    def test_truncation_follows_sorted_order_not_scan_order(self) -> None:
        # 先写 z.txt 再写 a.txt：无论扫描顺序如何，--limit 1 必须取排序后
        # 的第一项（a.txt），而不是先被扫描到的文件。
        self._write_text("z.txt", "target zed\n")
        self._write_text("a.txt", "target alpha\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", LIMIT, "1"), "按排序截断"
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "target alpha"}],
        )

    def test_same_path_items_selected_by_ascending_line(self) -> None:
        self._write_text("a.txt", "target one\ntarget two\ntarget three\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", ALL_LINES, LIMIT, "2"), "同路径按行号截取"
        )
        self.assertEqual(
            [(item["path"], item["line"]) for item in results],
            [("a.txt", 1), ("a.txt", 2)],
            "同一路径内按 line 升序选取前 N 项",
        )

    def test_limit_counts_items_across_files_in_sorted_order(self) -> None:
        self._write_text("b.txt", "target b1\ntarget b2\n")
        self._write_text("a.txt", "target a1\ntarget a2\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", ALL_LINES, LIMIT, "3"), "跨文件计项"
        )
        self.assertEqual(
            [(item["path"], item["line"]) for item in results],
            [("a.txt", 1), ("a.txt", 2), ("b.txt", 1)],
            "N 按结果项计数，跨文件按排序后的序列截取",
        )

    # -- 3. 默认与 --all-lines 的计数口径 ---------------------------------

    def test_default_mode_counts_one_item_per_file(self) -> None:
        self._write_text("a.txt", "target a1\ntarget a2\n")
        self._write_text("b.txt", "target b1\ntarget b2\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", LIMIT, "1"), "默认每文件一项"
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "target a1"}],
            "默认每个文件只返回首个合格行，--limit 1 只留这一项",
        )

    def test_all_lines_counts_each_qualified_line(self) -> None:
        self._write_text("a.txt", "target a1\ntarget a2\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", ALL_LINES, LIMIT, "2"), "逐行分别计项"
        )
        self.assertEqual(
            [(item["line"]) for item in results],
            [1, 2],
            "--all-lines 下每个合格行分别计数",
        )

    # -- 4. 不足 N 项与无命中 ----------------------------------------------

    def test_fewer_results_than_limit_returns_all(self) -> None:
        self._write_demo_tree()

        results = self.assert_success_clean(
            run_args(self.root, "TARGET", IGNORE_CASE, ALL_LINES, LIMIT, "1000"),
            "不足 N 项",
        )
        self.assertEqual(len(results), 3)

    def test_no_hit_returns_empty_array(self) -> None:
        self._write_text("a.txt", "alpha\nbeta\n")
        proc = run_args(self.root, "target", LIMIT, "5")
        results = self.assert_success_clean(proc, "无命中")
        self.assertEqual(results, [])
        self.assertEqual(proc.stdout.decode("utf-8").strip(), "[]")

    # -- 5. 取值边界：前导零合法，1 与 1000 合法 ---------------------------

    def test_leading_zeros_and_bounds_accepted(self) -> None:
        self._write_demo_tree()
        for raw, expected_count in (("1", 1), ("007", 3), ("01000", 3), ("1000", 3)):
            with self.subTest(raw=raw):
                results = self.assert_success_clean(
                    run_args(
                        self.root, "TARGET", IGNORE_CASE, ALL_LINES, LIMIT, raw
                    ),
                    f"取值 {raw!r}",
                )
                self.assertEqual(len(results), expected_count)

    def test_invalid_values_fail_before_scan(self) -> None:
        self._write_demo_tree()
        bad_format = ("", " ", " 2", "2 ", "+2", "-2", "2.5", "１２", "2a")
        for raw in bad_format:
            with self.subTest(raw=raw):
                proc = run_args(self.root, "TARGET", LIMIT, raw)
                self.assert_argument_error(proc, "非空的 ASCII 十进制数字串", f"格式 {raw!r}")
        for raw in ("0", "000", "1001", "01001"):
            with self.subTest(raw=raw):
                proc = run_args(self.root, "TARGET", LIMIT, raw)
                self.assert_argument_error(proc, "超出范围 1-1000", f"范围 {raw!r}")

    def test_missing_value_and_duplicate_fail_before_scan(self) -> None:
        self._write_demo_tree()
        proc = run_args(self.root, "TARGET", LIMIT)
        self.assert_argument_error(proc, "缺少值", "缺值")
        proc = run_args(self.root, "TARGET", LIMIT, "1", LIMIT, "2")
        self.assert_argument_error(proc, "只能指定一次", "重复指定")

    def test_value_equal_to_switch_is_consumed_as_value(self) -> None:
        self._write_demo_tree()
        # --all-lines 被当作 --limit 的取值消费：报取值格式错误，开关不启用。
        proc = run_args(self.root, "TARGET", LIMIT, ALL_LINES)
        self.assert_argument_error(proc, "非空的 ASCII 十进制数字串", "取值看似开关")

    def test_option_token_as_keyword_stays_literal(self) -> None:
        self._write_text("cfg.txt", "line --limit here\n")
        results = self.assert_success_clean(
            run_args(self.root, LIMIT), "关键词恰为 --limit"
        )
        self.assertEqual(
            results,
            [{"path": "cfg.txt", "line": 1, "snippet": "line --limit here"}],
            "第二个位置参数即使写作 --limit 也仍是关键词",
        )

    # -- 6. 与其他选项任意顺序同用 -----------------------------------------

    def test_combines_with_other_options_in_any_order(self) -> None:
        self._write_demo_tree()
        expected = [
            {"path": "a.txt", "line": 1, "snippet": "Target"},
            {"path": "a.txt", "line": 2, "snippet": "target"},
        ]
        for argv in (
            ("TARGET", IGNORE_CASE, ALL_LINES, CONTEXT_CHARS, "0", LIMIT, "2"),
            ("TARGET", LIMIT, "2", CONTEXT_CHARS, "0", ALL_LINES, IGNORE_CASE),
            ("TARGET", ALL_LINES, LIMIT, "2", IGNORE_CASE, CONTEXT_CHARS, "0"),
        ):
            with self.subTest(argv=argv):
                results = self.assert_success_clean(run_args(self.root, *argv), "任意顺序")
                self.assertEqual(results, expected)

    # -- 7. 告警与筛选语义不因上限改变 -------------------------------------

    def test_warnings_not_omitted_when_limit_already_satisfied(self) -> None:
        # a.txt 已足以填满 --limit 1，坏文件 b.txt 的告警仍必须产生。
        self._write_text("a.txt", "target here\n")
        self._write("b.txt", b"bad \xff\xff\n")
        proc = run_args(self.root, "target", LIMIT, "1")

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8")),
            [{"path": "a.txt", "line": 1, "snippet": "target here"}],
        )
        stderr = proc.stderr.decode("utf-8")
        self.assertIn("b.txt", stderr)
        self.assertIn("无法按 UTF-8 解码", stderr)

    def test_excluded_file_is_not_read_or_warned_with_limit(self) -> None:
        self._write("notes/broken.txt", b"target broken\n\xff\n")
        self._write_text("notes/ok.txt", "target ok\n")
        proc = run_args(
            self.root, "target", PATH_OPTION, "notes/ok", LIMIT, "5"
        )
        results = self.assert_success_clean(proc, "排除坏文件")
        self.assertEqual(
            results,
            [{"path": "notes/ok.txt", "line": 1, "snippet": "target ok"}],
        )
        self.assertEqual(proc.stderr, b"", "被路径筛选排除的文件不得产生告警")

    # -- 8. 查询只读源文件且不留下索引 -------------------------------------

    def test_limit_query_is_read_only_and_leaves_no_index(self) -> None:
        self._write_demo_tree()
        before = self._snapshot_files()
        proc = run_args(
            self.root, "TARGET", IGNORE_CASE, ALL_LINES, CONTEXT_CHARS, "0", LIMIT, "2"
        )
        after = self._snapshot_files()
        self.assertEqual(before, after, "查询后源文件字节不得变化，且不得新增任何文件/索引")
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()

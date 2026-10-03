"""``--all-lines`` 开关的端到端回归测试。

通过命令入口 ``python -m local_search <目录> <关键词> --all-lines`` 固定从命令行
输入到 JSON 结果输出的行为：

- 不指定开关时完全保留既有行为：每个文件只返回按行号、行内位置确定的首个命中，
  同一行的第二次出现及后续命中行都不重复返回；
- 指定开关后同一文件的每个命中行各返回一项，按行号递增；同一行多次出现关键词
  仍只产生一项，片段以该行最左侧命中为中心；
- 片段仍为完整关键词及同一行前后各至多 30 个 Unicode 码点，取自源文本、保留
  原始大小写，不含换行或省略号，不借用相邻行；
- 开关可与 ``--path-contains``、``--ignore-case`` 在位置参数之后以任意顺序同用；
  路径筛选仍区分大小写，忽略大小写仍仅折叠 ASCII；
- 第二个位置参数即使恰好写作 ``--all-lines`` 也仍是关键词；``--path-contains``
  的取值即使写作 ``--all-lines`` 也仍是字面片段；
- 结果先按路径的区分大小写 Unicode 码点序排列，同一路径按行号递增；
- 重复开关、无法识别的多余参数（含把开关写在关键词之前）时退出码 2、标准输出
  为空、原因写入标准错误；
- 含非法 UTF-8 字节的入选文件仍被整份跳过并向标准错误告警，其余文件的逐行结果
  正常输出且退出 0；无命中输出 ``[]``；每项结果只含 ``path``、``line``、``snippet``。

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


class AllLinesTest(unittest.TestCase):
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
        self.assertIn(reason, stderr, f"{label}: 标准错误应说明原因 {reason!r}，实际 {stderr!r}")

    def _snapshot_files(self) -> dict[str, bytes]:
        return {
            str(p.relative_to(self.root)).replace(os.sep, "/"): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    # -- 1. 演示场景：逐行返回第 1、3 行；默认只返回第 1 行 ----------------

    def test_demo_scenario_each_hit_line_returned(self) -> None:
        # 演示目录仅含 a.txt，三行依次为 target target / other / Target later。
        self._write_text("a.txt", "target target\nother\nTarget later\n")

        results = self.assert_success_clean(
            run_args(self.root, "TARGET", IGNORE_CASE, ALL_LINES), "演示：逐行模式"
        )
        self.assertEqual(
            results,
            [
                {"path": "a.txt", "line": 1, "snippet": "target target"},
                {"path": "a.txt", "line": 3, "snippet": "Target later"},
            ],
            "逐行模式：应返回第 1、3 行，片段分别为 target target 与 Target later",
        )
        for item in results:
            self.assert_item_shape(item, "演示：逐行模式")

    def test_demo_scenario_default_keeps_first_hit_only(self) -> None:
        self._write_text("a.txt", "target target\nother\nTarget later\n")

        results = self.assert_success_clean(
            run_args(self.root, "TARGET", IGNORE_CASE), "演示：默认模式"
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "target target"}],
            "去掉 --all-lines 后只返回第 1 行；同一行的两次命中不增加条目",
        )

    # -- 2. 同一行多次命中：只一项，片段以最左侧命中为中心 -----------------

    def test_same_line_multiple_hits_yield_one_item_with_leftmost_snippet(self) -> None:
        # 第 1 行行首即 target，40 个 x 之后再次出现 target：逐行模式下仍只一项，
        # 片段以最左侧命中为中心（target + 30 个 x），第二次命中被截在片段之外。
        self._write(
            "a.txt",
            b"target" + b"x" * 40 + b"target\nother line\n",
        )

        results = self.assert_success_clean(run_args(self.root, "target", ALL_LINES), "行内多命中")

        expected_snippet = "target" + "x" * 30
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": expected_snippet}],
            "同一行的两次命中只产生一项，片段以最左侧命中为中心",
        )

    def test_leftmost_hit_is_used_even_when_it_is_not_case_exact(self) -> None:
        # 第 1 行先出现异大小写 tArget，之后才是完全同大小写 TARGET；
        # 折叠匹配后片段必须以左侧的 tArget 为中心并保留源文本大小写。
        self._write_text("a.txt", "tArget" + "m" * 35 + "TARGET\nTARGET second\n")

        results = self.assert_success_clean(
            run_args(self.root, "TARGET", IGNORE_CASE, ALL_LINES), "行内首命中位置"
        )
        self.assertEqual(
            results,
            [
                {"path": "a.txt", "line": 1, "snippet": "tArget" + "m" * 30},
                {"path": "a.txt", "line": 2, "snippet": "TARGET second"},
            ],
            "每行一项；第 1 行片段以左侧的 tArget 为中心，第 2 行照常返回",
        )

    # -- 3. 多个命中行全部返回，按行号递增；片段不借用相邻行 ---------------

    def test_every_matching_line_returned_in_line_order(self) -> None:
        self._write_text(
            "multi.txt",
            "alpha\ntarget one\nbeta\ntarget two\ntarget three\ngamma\n",
        )

        results = self.assert_success_clean(run_args(self.root, "target", ALL_LINES), "多行命中")
        self.assertEqual(
            results,
            [
                {"path": "multi.txt", "line": 2, "snippet": "target one"},
                {"path": "multi.txt", "line": 4, "snippet": "target two"},
                {"path": "multi.txt", "line": 5, "snippet": "target three"},
            ],
            "每个命中行各一项，按行号递增，未命中行不出现",
        )

    def test_snippets_do_not_borrow_adjacent_lines(self) -> None:
        # 第 2 行命中 target，前后行分别是 40 个 P 和 40 个 N；片段只能是 target。
        self._write_text("n.txt", "P" * 40 + "\ntarget\n" + "N" * 40 + "\n")

        results = self.assert_success_clean(run_args(self.root, "target", ALL_LINES), "相邻行不借用")
        self.assertEqual(results, [{"path": "n.txt", "line": 2, "snippet": "target"}])
        self.assertNotIn("P", results[0]["snippet"])
        self.assertNotIn("N", results[0]["snippet"])

    # -- 4. 片段仍按 Unicode 码点截前后各 30 个 ---------------------------

    def test_snippet_context_still_30_code_points(self) -> None:
        # 31 个“中” + target + 31 个 😀；逐行模式下仍只截前后各 30 个码点。
        content = "中" * 31 + "target" + "😀" * 31
        self._write_text("u.md", content + "\ntarget again\n")

        results = self.assert_success_clean(run_args(self.root, "target", ALL_LINES), "码点片段")
        self.assertEqual(
            results,
            [
                {"path": "u.md", "line": 1, "snippet": "中" * 30 + "target" + "😀" * 30},
                {"path": "u.md", "line": 2, "snippet": "target again"},
            ],
        )
        self.assertEqual(len(results[0]["snippet"]), 66)

    # -- 5. 多文件：先按路径码点序，同一路径按行号递增 ---------------------

    def test_results_sorted_by_path_then_line(self) -> None:
        # 文件收集顺序不影响输出：b.txt 的命中行乱序出现，a.txt 多行命中。
        self._write_text("b.txt", "x\ntarget b2\ntarget b1\n")
        self._write_text("a.txt", "target a2\ntarget a1\n")
        self._write_text("A.txt", "target upper\n")

        results = self.assert_success_clean(run_args(self.root, "target", ALL_LINES), "排序")
        self.assertEqual(
            [(item["path"], item["line"]) for item in results],
            [("A.txt", 1), ("a.txt", 1), ("a.txt", 2), ("b.txt", 2), ("b.txt", 3)],
            "区分大小写码点序中大写 A 排在小写 a 前；同一路径按行号递增",
        )

    # -- 6. LF/CRLF 行号一致；末行无换行仍命中 -----------------------------

    def test_lf_crlf_and_final_line_without_newline(self) -> None:
        self._write("crlf.txt", b"head\r\ntarget one\r\ntarget two\r\n")
        self._write_text("lf.txt", "head\ntarget one\ntarget two")

        results = self.assert_success_clean(run_args(self.root, "target", ALL_LINES), "LF/CRLF/末行")
        expected = [
            {"path": "crlf.txt", "line": 2, "snippet": "target one"},
            {"path": "crlf.txt", "line": 3, "snippet": "target two"},
            {"path": "lf.txt", "line": 2, "snippet": "target one"},
            {"path": "lf.txt", "line": 3, "snippet": "target two"},
        ]
        self.assertEqual(results, expected)
        for item in results:
            self.assertNotIn("\r", item["snippet"])

    # -- 7. 与 --ignore-case 组合：仍只折叠 ASCII，片段保留源大小写 ---------

    def test_combines_with_ignore_case_ascii_folding_only(self) -> None:
        # 三行均为纯 ASCII 的 strasse 大小写变体；另一文件含 ß，不得被 ss 命中。
        self._write_text("ascii.txt", "die strasse eins\ndie STRASSE zwei\ndie Strasse drei\n")
        self._write_text("de.txt", "die straße hier\n")

        results = self.assert_success_clean(
            run_args(self.root, "STRASSE", IGNORE_CASE, ALL_LINES), "忽略大小写+逐行"
        )
        # ß 不等价于 ss：straße 所在行不得返回。
        self.assertEqual(
            results,
            [
                {"path": "ascii.txt", "line": 1, "snippet": "die strasse eins"},
                {"path": "ascii.txt", "line": 2, "snippet": "die STRASSE zwei"},
                {"path": "ascii.txt", "line": 3, "snippet": "die Strasse drei"},
            ],
            "仅 ASCII 折叠；片段保留源文本大小写",
        )
        # 反向同样不成立：小写 ss 命中 ascii.txt 的三行，但不得命中含 ß 的 de.txt。
        ss_only = self.assert_success_clean(
            run_args(self.root, "ss", IGNORE_CASE, ALL_LINES), "ss 不命中 ß"
        )
        self.assertEqual({item["path"] for item in ss_only}, {"ascii.txt"})
        self.assertEqual([item["line"] for item in ss_only], [1, 2, 3])

    # -- 8. 与 --path-contains 组合：任意顺序，路径筛选区分大小写 ----------

    def test_combines_with_path_filter_in_either_order_case_sensitive(self) -> None:
        self._write_text("notes/a.txt", "Target one\nTarget two\n")
        self._write_text("Notes/b.txt", "TARGET upper one\nTARGET upper two\n")

        for argv in (
            ("TARGET", ALL_LINES, IGNORE_CASE, PATH_OPTION, "notes/"),
            ("TARGET", IGNORE_CASE, PATH_OPTION, "notes/", ALL_LINES),
            ("TARGET", PATH_OPTION, "notes/", ALL_LINES, IGNORE_CASE),
        ):
            with self.subTest(argv=argv):
                results = self.assert_success_clean(run_args(self.root, *argv), "选项任意顺序")
                self.assertEqual(
                    results,
                    [
                        {"path": "notes/a.txt", "line": 1, "snippet": "Target one"},
                        {"path": "notes/a.txt", "line": 2, "snippet": "Target two"},
                    ],
                )

        upper = self.assert_success_clean(
            run_args(self.root, "TARGET", ALL_LINES, IGNORE_CASE, PATH_OPTION, "Notes/"),
            "大写路径片段",
        )
        self.assertEqual(
            [(item["path"], item["line"]) for item in upper],
            [("Notes/b.txt", 1), ("Notes/b.txt", 2)],
            "路径筛选仍区分大小写",
        )
        neither = self.assert_success_clean(
            run_args(self.root, "TARGET", ALL_LINES, IGNORE_CASE, PATH_OPTION, "NOTES/"),
            "全大写路径片段",
        )
        self.assertEqual(neither, [])

    def test_excluded_file_is_not_read_or_warned_even_in_all_lines(self) -> None:
        self._write("notes/broken.txt", b"TARGET broken\n\xff\nTARGET again\n")
        self._write_text("notes/ok.txt", "target ok\ntarget ok2\n")
        proc = run_args(self.root, "TARGET", ALL_LINES, IGNORE_CASE, PATH_OPTION, "notes/ok")
        results = self.assert_success_clean(proc, "排除坏文件")
        self.assertEqual(
            results,
            [
                {"path": "notes/ok.txt", "line": 1, "snippet": "target ok"},
                {"path": "notes/ok.txt", "line": 2, "snippet": "target ok2"},
            ],
        )
        self.assertEqual(proc.stderr, b"", "被路径筛选排除的文件不得产生告警")

    # -- 9. 字面边界：位置参数与 --path-contains 取值可恰为 --all-lines ----

    def test_keyword_equal_to_flag_name_is_literal(self) -> None:
        self._write_text("cfg.txt", "line --all-lines here\nagain --all-lines now\n")

        # 只有目录 + 字面关键词 "--all-lines"：开关未启用，默认每文件一项。
        default = self.assert_success_clean(
            run_args(self.root, ALL_LINES), "关键词恰为开关名（默认）"
        )
        self.assertEqual(
            default,
            [{"path": "cfg.txt", "line": 1, "snippet": "line --all-lines here"}],
            "第二个位置参数即使写作 --all-lines 也仍是关键词",
        )
        # 再来一个真正的开关：两行各返回一项。
        flagged = self.assert_success_clean(
            run_args(self.root, ALL_LINES, ALL_LINES), "关键词+同名开关"
        )
        self.assertEqual(
            flagged,
            [
                {"path": "cfg.txt", "line": 1, "snippet": "line --all-lines here"},
                {"path": "cfg.txt", "line": 2, "snippet": "again --all-lines now"},
            ],
        )

    def test_path_filter_value_equal_to_flag_name_is_a_literal_fragment(self) -> None:
        self._write_text("--all-lines.md", "target odd\ntarget odd2\n")
        self._write_text("plain.md", "target plain\n")

        # --all-lines 被当作片段取值消费，逐行模式未开启：每文件只回首命中。
        results = self.assert_success_clean(
            run_args(self.root, "target", PATH_OPTION, ALL_LINES), "片段恰为开关名"
        )
        self.assertEqual(
            results,
            [{"path": "--all-lines.md", "line": 1, "snippet": "target odd"}],
            "--path-contains 的取值 --all-lines 必须是字面片段而非开关",
        )

    # -- 10. 无命中与跨行：[]、退出 0、标准错误为空 ------------------------

    def test_no_hit_returns_empty_array(self) -> None:
        self._write_text("a.txt", "alpha\nbeta\n")
        proc = run_args(self.root, "target", ALL_LINES)
        results = self.assert_success_clean(proc, "无命中")
        self.assertEqual(results, [])
        self.assertEqual(proc.stdout.decode("utf-8").strip(), "[]")

    def test_keyword_split_across_lines_does_not_match(self) -> None:
        self._write("split.txt", b"tar\nget\ntar\nget\n")
        results = self.assert_success_clean(
            run_args(self.root, "target", ALL_LINES), "跨行不命中"
        )
        self.assertEqual(results, [])

    # -- 11. 非法 UTF-8：整份文件不返回，其余文件逐行结果照常，退出 0 ------

    def test_broken_file_skipped_entirely_others_return_all_lines(self) -> None:
        self._write("broken.txt", b"target before\n\xff\ntarget after\n")
        self._write_text("ok.txt", "target one\ntarget two\n")
        proc = run_args(self.root, "target", ALL_LINES)

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8")),
            [
                {"path": "ok.txt", "line": 1, "snippet": "target one"},
                {"path": "ok.txt", "line": 2, "snippet": "target two"},
            ],
            "坏文件前后两处命中都不得返回；其余文件逐行结果照常",
        )
        stderr = proc.stderr.decode("utf-8")
        self.assertIn("broken.txt", stderr)
        self.assertIn("无法按 UTF-8 解码", stderr)

    # -- 12. 参数错误：重复开关、多余参数、开关在关键词之前 → 退出 2 -------

    def test_duplicate_flag_is_argument_error(self) -> None:
        proc = run_args(self.root, "target", ALL_LINES, ALL_LINES)
        self.assert_argument_error(proc, "只能指定一次", "重复开关")
        self.assertIn(ALL_LINES, proc.stderr.decode("utf-8"))

    def test_flag_before_keyword_is_an_unrecognized_argument(self) -> None:
        proc = run_args(self.root, ALL_LINES, "target")
        self.assert_argument_error(proc, "无法识别的参数", "开关在关键词之前")
        self.assertIn("target", proc.stderr.decode("utf-8"))

    def test_extra_arguments_are_argument_errors(self) -> None:
        cases = [
            (("target", ALL_LINES, "trailing"), "trailing"),
            (("target", "--bogus", ALL_LINES), "--bogus"),
            (("target", ALL_LINES, PATH_OPTION, "notes/", "extra"), "extra"),
        ]
        for argv, token in cases:
            with self.subTest(argv=argv):
                proc = run_args(self.root, *argv)
                self.assert_argument_error(proc, "无法识别的参数", f"多余参数 {token!r}")
                self.assertIn(token, proc.stderr.decode("utf-8"))

    def test_dangling_path_option_is_argument_error(self) -> None:
        proc = run_args(self.root, "target", ALL_LINES, PATH_OPTION)
        self.assert_argument_error(proc, "缺少值", "path-contains 缺值")
        self.assertEqual(proc.stdout, b"")

    # -- 13. 查询只读源文件且不留下索引 -----------------------------------

    def test_all_lines_query_is_read_only_and_leaves_no_index(self) -> None:
        self._write_text("keep.txt", "before\ntarget one\ntarget two\nafter\n")
        before = self._snapshot_files()
        proc = run_args(self.root, "target", ALL_LINES)
        after = self._snapshot_files()
        self.assertEqual(before, after, "查询后源文件字节不得变化，且不得新增任何文件/索引")
        results = self.assert_success_clean(proc, "只读/无索引")
        self.assertEqual(
            results,
            [
                {"path": "keep.txt", "line": 2, "snippet": "target one"},
                {"path": "keep.txt", "line": 3, "snippet": "target two"},
            ],
        )


if __name__ == "__main__":
    unittest.main()

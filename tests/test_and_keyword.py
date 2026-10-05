"""``--and-keyword`` 同行多关键词筛选的端到端回归测试。

通过命令入口
``python -m local_search <目录> <关键词> --and-keyword <附加关键词>...``
固定从命令行输入到 JSON 结果输出的行为：

- ``--and-keyword`` 可重复指定以要求多个附加词：候选行须在同一行内同时
  包含主关键词（或替代关键词）与全部附加关键词才算命中，某个附加词只在
  其他行出现不能合并；附加词的指定顺序与重复值不影响结果，相同词或重叠
  匹配可共用文字；
- 各词均按连续字面子串匹配（不拆词、不解释正则或通配符，首尾空格保留），
  默认区分大小写；``--ignore-case`` 同时作用于全部关键词，仍只折叠 ASCII
  字母；
- 默认每个文件返回行号最小的合格行；``--all-lines`` 每个合格行各返回一项，
  行内重复出现不增加结果；
- snippet 围绕主关键词（或替代关键词）在该行最左侧的命中，保留源文本
  大小写，``--context-chars`` 沿用既有规则，不为展示附加关键词扩大片段；
- 附加值即使写作 ``--all-lines`` 也按文本处理，不开启开关；位置参数或路径
  选项取值中的 ``--and-keyword`` 仍按字面处理；
- 缺值、任一值为空或全为空白时在扫描前结束：退出码 2、标准输出为空、
  标准错误包含选项名及对应原因；未提供该选项或只提供一次时行为与此前一致；
- 路径与格式筛选继续生效，被过滤文件不读取、不告警；选中文件无法读取或含
  非法 UTF-8 时整文件告警跳过，其他文件继续，退出码仍为 0；
- 无合格行返回 ``[]``；查询只读源文件，不生成索引或其他文件。

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

AND_KEYWORD = "--and-keyword"
ALL_LINES = "--all-lines"
IGNORE_CASE = "--ignore-case"
CONTEXT_CHARS = "--context-chars"
PATH_OPTION = "--path-contains"
FILE_TYPE = "--file-type"


def run_args(directory: Path, *argv: str) -> subprocess.CompletedProcess:
    """以任意命令行参数调用 python -m local_search，返回完成的子进程。"""
    return subprocess.run(
        [sys.executable, "-m", "local_search", str(directory), *argv],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )


class AndKeywordTest(unittest.TestCase):
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

    def assert_argument_error(self, proc: subprocess.CompletedProcess, reason: str, label: str) -> None:
        """输入边界错误：退出码 2、标准输出为空、标准错误说明对应原因。"""
        self.assertEqual(proc.returncode, 2, f"{label}: 退出码应为 2，实际 {proc.returncode}")
        self.assertEqual(proc.stdout, b"", f"{label}: 出错时标准输出必须为空")
        stderr = proc.stderr.decode("utf-8")
        self.assertIn(reason, stderr, f"{label}: 标准错误应说明原因 {reason!r}，实际 {stderr!r}")
        self.assertIn(AND_KEYWORD, stderr, f"{label}: 标准错误应包含选项名，实际 {stderr!r}")

    def _snapshot_files(self) -> dict[str, bytes]:
        return {
            str(p.relative_to(self.root)).replace(os.sep, "/"): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    def _write_acceptance_sample(self) -> None:
        # 验收目录布局：四行依次为 target / budget / Target budget / budget target target。
        self._write_text("a.txt", "target\nbudget\nTarget budget\nbudget target target\n")

    # -- 1. 验收场景：默认只返回第 3 行，--all-lines 追加第 4 行 -----------

    def test_acceptance_default_returns_lowest_qualified_line(self) -> None:
        self._write_acceptance_sample()

        results = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, "budget", IGNORE_CASE,
                     CONTEXT_CHARS, "0"),
            "验收：默认模式",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 3, "snippet": "Target"}],
            "只返回行号最小的合格行（第 3 行），片段为主关键词命中 Target",
        )

    def test_acceptance_all_lines_adds_fourth_line(self) -> None:
        self._write_acceptance_sample()

        results = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, "budget", IGNORE_CASE,
                     CONTEXT_CHARS, "0", ALL_LINES),
            "验收：逐行模式",
        )
        self.assertEqual(
            results,
            [
                {"path": "a.txt", "line": 3, "snippet": "Target"},
                {"path": "a.txt", "line": 4, "snippet": "target"},
            ],
            "第 3、4 行各返回一项；第 4 行片段是主关键词 target 而非 budget",
        )

    # -- 2. 多附加词验收：全部附加词须在同一行；重复值与顺序不影响结果 ------

    def _write_multi_acceptance_sample(self) -> None:
        # 验收目录布局：三行依次为 target red / target blue red /
        # target red blue。
        self._write_text(
            "a.txt", "target red\ntarget blue red\ntarget red blue\n"
        )

    def test_multi_and_default_returns_only_second_line(self) -> None:
        self._write_multi_acceptance_sample()

        results = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, "red", AND_KEYWORD,
                     "blue", CONTEXT_CHARS, "0"),
            "多附加词：默认模式",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 2, "snippet": "target"}],
            "第 1 行缺 blue、第 3 行... 合格：默认只返回最早的第 2 行",
        )

    def test_multi_and_all_lines_returns_second_and_third_line(self) -> None:
        self._write_multi_acceptance_sample()

        results = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, "red", AND_KEYWORD,
                     "blue", CONTEXT_CHARS, "0", ALL_LINES),
            "多附加词：逐行模式",
        )
        self.assertEqual(
            results,
            [
                {"path": "a.txt", "line": 2, "snippet": "target"},
                {"path": "a.txt", "line": 3, "snippet": "target"},
            ],
            "只有同时含 red 与 blue 的第 2、3 行合格，各返回一项",
        )

    def test_multi_and_duplicate_value_and_order_do_not_change_result(self) -> None:
        self._write_multi_acceptance_sample()
        tails = (
            (AND_KEYWORD, "blue", AND_KEYWORD, "red"),
            (AND_KEYWORD, "red", AND_KEYWORD, "blue", AND_KEYWORD, "red"),
            (AND_KEYWORD, "red", AND_KEYWORD, "red", AND_KEYWORD, "blue",
             AND_KEYWORD, "blue"),
            (AND_KEYWORD, "blue", AND_KEYWORD, "blue", AND_KEYWORD, "red",
             AND_KEYWORD, "red"),
        )
        for tail in tails:
            with self.subTest(tail=tail):
                results = self.assert_success_clean(
                    run_args(self.root, "target", *tail, CONTEXT_CHARS, "0",
                             ALL_LINES),
                    "重复值/顺序无关",
                )
                self.assertEqual(
                    results,
                    [
                        {"path": "a.txt", "line": 2, "snippet": "target"},
                        {"path": "a.txt", "line": 3, "snippet": "target"},
                    ],
                    "附加词顺序与重复值不影响结果",
                )

    def test_multi_and_each_missing_on_different_lines_does_not_combine(self) -> None:
        # red 与 blue 分处不同行：没有任何一行同时含两者。
        self._write_text("a.txt", "target red\ntarget blue\ntarget\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, "red", AND_KEYWORD,
                     "blue", ALL_LINES, CONTEXT_CHARS, "0"),
            "多附加词跨行不合并",
        )
        self.assertEqual(results, [], "附加词分散在不同行不能合并为命中")

    def test_multi_and_three_keywords_same_line(self) -> None:
        self._write_text(
            "a.txt",
            "target red blue green\n"
            "target red blue\n"
            "target red green\n",
        )

        results = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, "red", AND_KEYWORD,
                     "green", AND_KEYWORD, "blue", ALL_LINES,
                     CONTEXT_CHARS, "0"),
            "三个附加词",
        )
        self.assertEqual(
            [item["line"] for item in results],
            [1],
            "只有第 1 行同时含全部三个附加词",
        )

    def test_multi_and_ignore_case_folds_all_keywords_ascii_only(self) -> None:
        self._write_text("a.txt", "TARGET RED BLUE\ntarget red blue\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, "RED", AND_KEYWORD,
                     "Blue", IGNORE_CASE, ALL_LINES, CONTEXT_CHARS, "0"),
            "全部附加词折叠",
        )
        self.assertEqual(
            [item["line"] for item in results],
            [1, 2],
            "--ignore-case 对全部附加词折叠 ASCII 字母",
        )

        # 非 ASCII 不折叠：附加词 É 不命中含 é 的第 1 行。
        self._write_text("u.txt", "target red été\ntarget red Été\n")
        uni = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, "red", AND_KEYWORD,
                     "Été", IGNORE_CASE, ALL_LINES),
            "非 ASCII 不折叠",
        )
        self.assertEqual(
            [item["path"] for item in uni],
            ["u.txt"],
            "仅含 Été 的第 2 行合格，含 été 的行不合格",
        )
        self.assertEqual(uni[0]["line"], 2)

    def test_multi_and_overlapping_words_share_text(self) -> None:
        # 附加词 ab 与 ba 可在 aba 中重叠共用文字；再要求与主词同现。
        self._write_text("a.txt", "x aba y\ntarget ab ba\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, "ab", AND_KEYWORD,
                     "ba", CONTEXT_CHARS, "0"),
            "重叠共用文字",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 2, "snippet": "target"}],
            "两个附加词可共用重叠文字；第 1 行还缺主词 target",
        )

    # -- 3. 同行语义：分处不同行不能合并，顺序不限 -------------------------

    def test_keywords_on_different_lines_do_not_combine(self) -> None:
        self._write_text("a.txt", "target only\nbudget only\nneither here\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, "budget"), "跨行不合并"
        )
        self.assertEqual(results, [])

    def test_either_order_on_same_line_matches(self) -> None:
        self._write_text("a.txt", "budget before target\nafter target budget\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, "budget", ALL_LINES,
                     CONTEXT_CHARS, "0"),
            "顺序不限",
        )
        self.assertEqual(
            results,
            [
                {"path": "a.txt", "line": 1, "snippet": "target"},
                {"path": "a.txt", "line": 2, "snippet": "target"},
            ],
            "附加关键词在主关键词之前或之后均合格",
        )

    def test_same_keyword_twice_and_overlapping_matches_share_text(self) -> None:
        # 主词与附加词相同：同一处出现即可同时满足两者。
        self._write_text("same.txt", "target\nother\n")
        same = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, "target", CONTEXT_CHARS, "0"),
            "两词相同",
        )
        self.assertEqual(same, [{"path": "same.txt", "line": 1, "snippet": "target"}])

        # 重叠匹配共用文字：主词 aba 与附加词 bab 在 abab 中重叠出现。
        self._write_text("overlap.txt", "xxababxx\naba\nbab\n")
        overlap = self.assert_success_clean(
            run_args(self.root, "aba", AND_KEYWORD, "bab", CONTEXT_CHARS, "0"),
            "重叠匹配",
        )
        self.assertEqual(overlap, [{"path": "overlap.txt", "line": 1, "snippet": "aba"}])

    # -- 4. 大小写：默认区分大小写；--ignore-case 同时折叠全部词且仅 ASCII -

    def test_case_sensitive_by_default_for_both_keywords(self) -> None:
        self._write_text("a.txt", "Target budget\ntarget Budget\ntarget budget\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, "budget", ALL_LINES,
                     CONTEXT_CHARS, "0"),
            "默认区分大小写",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 3, "snippet": "target"}],
            "主词或附加词大小写不同的行均不合格",
        )

    def test_ignore_case_applies_to_both_keywords_ascii_only(self) -> None:
        self._write_text("ascii.txt", "TARGET BUDGET\ntarget budget\n")
        results = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, "budget", IGNORE_CASE,
                     ALL_LINES, CONTEXT_CHARS, "0"),
            "双词同时折叠",
        )
        self.assertEqual(
            results,
            [
                {"path": "ascii.txt", "line": 1, "snippet": "TARGET"},
                {"path": "ascii.txt", "line": 2, "snippet": "target"},
            ],
            "两个关键词都被 ASCII 折叠；片段保留源文本大小写",
        )

        # 非 ASCII 不折叠：附加词 Été 不命中 été。
        self._write_text("uni.txt", "target été\ntarget Été\n")
        uni = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, "Été", IGNORE_CASE, ALL_LINES),
            "非 ASCII 不折叠",
        )
        self.assertEqual(
            [item["line"] for item in uni],
            [2],
            "--ignore-case 不把 É 折叠为 é，仅第 2 行合格",
        )

    # -- 5. 字面匹配：不拆词、不解释正则或通配符、首尾空格保留 -------------

    def test_both_keywords_are_literal_substrings(self) -> None:
        self._write_text("lit.txt", "a.b*c? and x+y\ntarget budget\n")

        results = self.assert_success_clean(
            run_args(self.root, "a.b*c?", AND_KEYWORD, "x+y", CONTEXT_CHARS, "0"),
            "正则元字符按字面",
        )
        self.assertEqual(
            results,
            [{"path": "lit.txt", "line": 1, "snippet": "a.b*c?"}],
            "两个关键词中的正则元字符都按字面文本处理",
        )

    def test_leading_trailing_spaces_in_and_keyword_are_significant(self) -> None:
        self._write_text(
            "sp.txt",
            "target budget \n"      # 第 1 行：' budget ' 完整出现
            "target budget\n"       # 第 2 行：budget 后无空格
            "target  budget \n"     # 第 3 行：双空格中仍含 ' budget '
            "target xbudget \n",    # 第 4 行：budget 前不是空格
        )

        results = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, " budget ", ALL_LINES),
            "附加词首尾空格",
        )
        self.assertEqual(
            [item["line"] for item in results],
            [1, 3],
            "附加词 ' budget ' 只命中两侧确为空格的第 1、3 行，不拆词",
        )

    # -- 6. 片段：围绕主关键词最左侧命中，不为附加词扩大 -------------------

    def test_snippet_centers_on_main_keyword_leftmost_hit_only(self) -> None:
        # 附加词 budget 在行尾远处；片段只围绕主词 target 的最左侧命中。
        line = "target" + "m" * 40 + "budget"
        self._write_text("a.txt", line + "\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, "budget"),
            "片段不扩大",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "target" + "m" * 30}],
            "片段仍为主词前后各 30 码点，远处的 budget 不进入片段",
        )

    def test_snippet_uses_leftmost_main_hit_when_main_repeats(self) -> None:
        self._write_text("a.txt", "budget target target\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, "budget", CONTEXT_CHARS, "4"),
            "主词最左侧命中",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "get target tar"}],
            "行内主词重复出现时片段以最左侧命中为中心，该行仍只一项",
        )

    # -- 7. 逐行模式：每个合格行各一项，行内重复不增加结果 -----------------

    def test_all_lines_returns_each_qualified_line_once(self) -> None:
        self._write_text(
            "a.txt",
            "target budget\n"
            "target only\n"
            "budget only\n"
            "target target budget budget\n",
        )

        results = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, "budget", ALL_LINES,
                     CONTEXT_CHARS, "0"),
            "逐行合格行",
        )
        self.assertEqual(
            results,
            [
                {"path": "a.txt", "line": 1, "snippet": "target"},
                {"path": "a.txt", "line": 4, "snippet": "target"},
            ],
            "只含一个词的行不合格；第 4 行两词各重复出现仍只一项",
        )

    # -- 8. 多文件：排序与结果字段沿用原约定 -------------------------------

    def test_results_sorted_by_path_then_line_with_three_fields(self) -> None:
        self._write_text("b.txt", "target budget b\n")
        self._write_text("a.txt", "target budget a1\ntarget budget a2\n")
        self._write_text("A.md", "target budget A\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, "budget", ALL_LINES,
                     CONTEXT_CHARS, "0"),
            "多文件排序",
        )
        self.assertEqual(
            [(item["path"], item["line"]) for item in results],
            [("A.md", 1), ("a.txt", 1), ("a.txt", 2), ("b.txt", 1)],
        )
        for item in results:
            self.assertEqual(set(item.keys()), {"path", "line", "snippet"})

    # -- 9. 字面边界：选项记号出现在取值或位置参数中仍按文本处理 -----------

    def test_and_keyword_value_equal_to_flag_name_is_literal_text(self) -> None:
        self._write_text("cfg.txt", "target --all-lines\ntarget plain\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, ALL_LINES),
            "附加值恰为开关名",
        )
        self.assertEqual(
            results,
            [{"path": "cfg.txt", "line": 1, "snippet": "target --all-lines"}],
            "--all-lines 被当作附加关键词文本消费，逐行开关未开启",
        )

    def test_positional_keyword_equal_to_option_name_stays_literal(self) -> None:
        self._write_text("a.txt", "--and-keyword here\ntarget\n")

        results = self.assert_success_clean(
            run_args(self.root, AND_KEYWORD), "关键词恰为选项名"
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "--and-keyword here"}],
            "第二个位置参数即使写作 --and-keyword 也仍是关键词",
        )

    def test_path_option_value_equal_to_option_name_stays_literal(self) -> None:
        self._write_text("--and-keyword/a.txt", "target\n")
        self._write_text("other/b.txt", "target\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", PATH_OPTION, AND_KEYWORD),
            "路径片段恰为选项名",
        )
        self.assertEqual(
            results,
            [{"path": "--and-keyword/a.txt", "line": 1, "snippet": "target"}],
            "--path-contains 的取值 --and-keyword 是字面片段而非选项",
        )

    # -- 10. 与既有选项任意排序组合；路径与格式筛选继续生效 ----------------

    def test_combines_with_other_options_in_any_order(self) -> None:
        self._write_text("notes/a.txt", "Target Budget\n")
        self._write_text("notes/b.md", "Target Budget\n")

        argv_orders = (
            ("TARGET", AND_KEYWORD, "BUDGET", IGNORE_CASE, CONTEXT_CHARS, "0",
             PATH_OPTION, "notes/", FILE_TYPE, "txt"),
            ("TARGET", FILE_TYPE, "txt", PATH_OPTION, "notes/", IGNORE_CASE,
             AND_KEYWORD, "BUDGET", CONTEXT_CHARS, "0"),
            ("TARGET", IGNORE_CASE, CONTEXT_CHARS, "0", AND_KEYWORD, "BUDGET",
             FILE_TYPE, "txt", PATH_OPTION, "notes/"),
        )
        for argv in argv_orders:
            with self.subTest(argv=argv):
                results = self.assert_success_clean(run_args(self.root, *argv), "任意排序")
                self.assertEqual(
                    results,
                    [{"path": "notes/a.txt", "line": 1, "snippet": "Target"}],
                    "选项任意排序结果一致；格式筛选排除 b.md",
                )

    def test_filtered_out_broken_file_is_not_read_or_warned(self) -> None:
        self._write("broken.txt", b"target budget\n\xff\n")
        self._write_text("ok.md", "target budget\n")

        proc = run_args(self.root, "target", AND_KEYWORD, "budget", FILE_TYPE, "md")
        results = self.assert_success_clean(proc, "被过滤坏文件")
        self.assertEqual(
            results,
            [{"path": "ok.md", "line": 1, "snippet": "target budget"}],
            "被格式条件排除的坏文件不读取、不告警",
        )

    # -- 11. 选中文件无法解码：整文件告警跳过，其余继续，退出码 0 ----------

    def test_broken_selected_file_warned_and_skipped(self) -> None:
        self._write("broken.txt", b"target budget before\n\xff\ntarget budget after\n")
        self._write_text("ok.txt", "target budget ok\n")

        proc = run_args(self.root, "target", AND_KEYWORD, "budget", CONTEXT_CHARS, "0")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8")),
            [{"path": "ok.txt", "line": 1, "snippet": "target"}],
            "坏文件的合格行一律不返回；其余文件照常",
        )
        stderr = proc.stderr.decode("utf-8")
        self.assertIn("broken.txt", stderr)
        self.assertIn("无法按 UTF-8 解码", stderr)

    # -- 12. 参数错误：缺值、空值、全空白 → 扫描前退出 2 -------------------

    def test_missing_value_is_argument_error(self) -> None:
        proc = run_args(self.root, "target", AND_KEYWORD)
        self.assert_argument_error(proc, "缺少值", "缺值")

    def test_repeated_option_accumulates_instead_of_duplicate_error(self) -> None:
        # 重复指定 --and-keyword 不再报错：各次取值全部作为附加词累积。
        self._write_text("a.txt", "target a b\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, "a", AND_KEYWORD, "b",
                     CONTEXT_CHARS, "0"),
            "重复指定合法",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "target"}],
            "重复 --and-keyword 累积为两个附加词，不再报重复错误",
        )

    def test_empty_or_blank_value_is_argument_error(self) -> None:
        for raw in ("", " ", "   ", "\t"):
            with self.subTest(raw=raw):
                proc = run_args(self.root, "target", AND_KEYWORD, raw)
                self.assert_argument_error(proc, "为空或全为空白", f"空白值 {raw!r}")

    def test_blank_value_among_several_is_argument_error(self) -> None:
        # 多个附加词中任一个为空或全为空白都在扫描前报错。
        for tail in (
            (AND_KEYWORD, "red", AND_KEYWORD, ""),
            (AND_KEYWORD, "red", AND_KEYWORD, "   "),
            (AND_KEYWORD, "", AND_KEYWORD, "red"),
            (AND_KEYWORD, "red", AND_KEYWORD, "blue", AND_KEYWORD, "\t"),
        ):
            with self.subTest(tail=tail):
                proc = run_args(self.root, "target", *tail)
                self.assert_argument_error(
                    proc, "为空或全为空白", f"多个附加词中的空白值 {tail!r}"
                )

    def test_missing_value_among_several_is_argument_error(self) -> None:
        # 最后一个 --and-keyword 缺少紧随参数时同样报“缺少值”。
        proc = run_args(
            self.root, "target", AND_KEYWORD, "red", AND_KEYWORD, "blue",
            AND_KEYWORD,
        )
        self.assert_argument_error(proc, "缺少值", "多次中的缺值")

    def test_argument_errors_happen_before_any_scan(self) -> None:
        # 目录不存在时，--and-keyword 的参数错误仍优先报出（扫描前结束）。
        proc = run_args(Path("不存在的目录"), "target", AND_KEYWORD, "  ")
        self.assert_argument_error(proc, "为空或全为空白", "扫描前报错")
        # 多个附加词之一为空白时，同样在扫描前报出。
        proc = run_args(
            Path("不存在的目录"), "target", AND_KEYWORD, "red",
            AND_KEYWORD, " ",
        )
        self.assert_argument_error(proc, "为空或全为空白", "多值扫描前报错")

    # -- 13. 未提供新选项时既有行为不变；无合格行返回 [] -------------------

    def test_without_option_behavior_is_unchanged(self) -> None:
        self._write_acceptance_sample()

        results = self.assert_success_clean(
            run_args(self.root, "target", IGNORE_CASE, CONTEXT_CHARS, "0"),
            "无新选项",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "target"}],
            "未指定 --and-keyword 时仍只按主关键词返回首个命中",
        )

    def test_no_qualified_line_returns_empty_array(self) -> None:
        self._write_text("a.txt", "alpha\nbeta\n")
        proc = run_args(self.root, "target", AND_KEYWORD, "budget")
        results = self.assert_success_clean(proc, "无合格行")
        self.assertEqual(results, [])
        self.assertEqual(proc.stdout.decode("utf-8").strip(), "[]")

    # -- 14. 查询只读源文件且不留下索引 -------------------------------------

    def test_query_is_read_only_and_leaves_no_index(self) -> None:
        self._write_text("keep.txt", "target budget\n")
        before = self._snapshot_files()
        proc = run_args(self.root, "target", AND_KEYWORD, "budget")
        after = self._snapshot_files()
        self.assertEqual(before, after, "查询后源文件字节不得变化，且不得新增任何文件/索引")
        self.assert_success_clean(proc, "只读/无索引")


if __name__ == "__main__":
    unittest.main()

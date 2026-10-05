"""``--and-keyword`` 同行多关键词筛选的端到端回归测试。

通过命令入口 ``python -m local_search <目录> <关键词> [--and-keyword <附加关键词>]...``
固定从命令行输入到 JSON 结果输出的行为：

- 只有同一行分别包含主关键词与每个附加关键词才算命中，分处不同行不能合并；
  各词出现顺序不限，相同关键词或重叠匹配可共用文字；
- ``--and-keyword`` 可重复指定以同时要求多个附加关键词：各次取值按出现顺序
  累积，附加词的顺序与重复值不影响结果；未指定或只指定一次时行为与此前一致；
- 各词均按连续字面子串匹配（不拆词、不解释正则或通配符，首尾空格保留），
  默认区分大小写；``--ignore-case`` 同时作用于全部附加词，仍只折叠 ASCII 字母；
- 默认每个文件返回行号最小的合格行；``--all-lines`` 每个合格行各返回一项，
  行内重复出现不增加结果；
- snippet 围绕主关键词在该行最左侧的命中，保留源文本大小写，
  ``--context-chars`` 沿用既有规则，不为展示附加关键词扩大片段；
- 附加值即使写作 ``--all-lines`` 也按文本处理，不开启开关；位置参数或路径
  选项取值中的 ``--and-keyword`` 仍按字面处理；
- 缺值、任一值为空或全为空白时在扫描前结束：退出码 2、标准输出为空、
  标准错误包含选项名及对应原因；未提供该选项时既有行为完全不变；
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

    # -- 1b. 多附加词验收场景：默认只返回第 2 行，--all-lines 追加第 3 行 --

    def _write_multi_acceptance_sample(self) -> None:
        # 多附加词验收目录布局：三行依次为 target red / target blue red /
        # target red blue。
        self._write_text("a.txt", "target red\ntarget blue red\ntarget red blue\n")

    def test_multi_acceptance_default_returns_lowest_qualified_line(self) -> None:
        self._write_multi_acceptance_sample()

        results = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, "red", AND_KEYWORD, "blue",
                     CONTEXT_CHARS, "0"),
            "多附加词验收：默认模式",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 2, "snippet": "target"}],
            "只有同时含 red 与 blue 的第 2 行合格，片段为主关键词命中 target",
        )

    def test_multi_acceptance_all_lines_adds_third_line(self) -> None:
        self._write_multi_acceptance_sample()

        results = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, "red", AND_KEYWORD, "blue",
                     CONTEXT_CHARS, "0", ALL_LINES),
            "多附加词验收：逐行模式",
        )
        self.assertEqual(
            results,
            [
                {"path": "a.txt", "line": 2, "snippet": "target"},
                {"path": "a.txt", "line": 3, "snippet": "target"},
            ],
            "第 2、3 行各返回一项；第 1 行缺少 blue 不合格",
        )

    def test_multi_acceptance_duplicate_and_reordered_values_same_result(self) -> None:
        self._write_multi_acceptance_sample()
        expected = [
            {"path": "a.txt", "line": 2, "snippet": "target"},
            {"path": "a.txt", "line": 3, "snippet": "target"},
        ]

        reordered = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, "blue", AND_KEYWORD, "red",
                     CONTEXT_CHARS, "0", ALL_LINES),
            "调换附加词顺序",
        )
        self.assertEqual(reordered, expected, "附加词顺序不影响结果")

        duplicated = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, "red", AND_KEYWORD, "blue",
                     AND_KEYWORD, "red", CONTEXT_CHARS, "0", ALL_LINES),
            "重复相同附加词",
        )
        self.assertEqual(duplicated, expected, "重复值不改变结果")

    def test_all_and_keywords_must_appear_on_the_same_line(self) -> None:
        # red 与 blue 分处不同行不能合并；三个附加词也须全部落在同一行。
        self._write_text(
            "a.txt",
            "target red\ntarget blue\ntarget red blue\ntarget red blue green\n",
        )

        results = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, "red", AND_KEYWORD, "blue",
                     AND_KEYWORD, "green", ALL_LINES, CONTEXT_CHARS, "0"),
            "三附加词同行",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 4, "snippet": "target"}],
            "只有同时含 red、blue、green 的第 4 行合格",
        )

    # -- 2. 同行语义：分处不同行不能合并，顺序不限 -------------------------

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

    # -- 3. 大小写：默认区分大小写；--ignore-case 同时折叠两词且仅 ASCII ---

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

    # -- 4. 字面匹配：不拆词、不解释正则或通配符、首尾空格保留 -------------

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

    # -- 5. 片段：围绕主关键词最左侧命中，不为附加词扩大 -------------------

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

    # -- 6. 逐行模式：每个合格行各一项，行内重复不增加结果 -----------------

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

    # -- 7. 多文件：排序与结果字段沿用原约定 -------------------------------

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

    # -- 8. 字面边界：选项记号出现在取值或位置参数中仍按文本处理 -----------

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

    # -- 9. 与既有选项任意排序组合；路径与格式筛选继续生效 -----------------

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

    # -- 10. 选中文件无法解码：整文件告警跳过，其余继续，退出码 0 ----------

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

    # -- 11. 参数错误：缺值、空值、全空白 → 扫描前退出 2 --------------------

    def test_missing_value_is_argument_error(self) -> None:
        proc = run_args(self.root, "target", AND_KEYWORD)
        self.assert_argument_error(proc, "缺少值", "缺值")

    def test_missing_value_after_valid_value_is_argument_error(self) -> None:
        # 可重复指定后，任何一次缺紧随参数仍报缺值。
        proc = run_args(self.root, "target", AND_KEYWORD, "red", AND_KEYWORD)
        self.assert_argument_error(proc, "缺少值", "重复后缺值")

    def test_repeated_option_is_accepted_not_an_error(self) -> None:
        # --and-keyword 可重复指定：重复出现不再报“只能指定一次”。
        self._write_multi_acceptance_sample()
        results = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, "red", AND_KEYWORD, "blue",
                     CONTEXT_CHARS, "0"),
            "重复指定合法",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 2, "snippet": "target"}],
        )

    def test_empty_or_blank_value_is_argument_error(self) -> None:
        for raw in ("", " ", "   ", "\t"):
            with self.subTest(raw=raw):
                proc = run_args(self.root, "target", AND_KEYWORD, raw)
                self.assert_argument_error(proc, "为空或全为空白", f"空白值 {raw!r}")

    def test_blank_value_among_valid_values_is_argument_error(self) -> None:
        # 任一附加词为空或全为空白都在扫描前报错，即使其他取值合法。
        proc = run_args(self.root, "target", AND_KEYWORD, "red", AND_KEYWORD, " ")
        self.assert_argument_error(proc, "为空或全为空白", "多值中含空白值")

    def test_argument_errors_happen_before_any_scan(self) -> None:
        # 目录不存在时，--and-keyword 的参数错误仍优先报出（扫描前结束）。
        proc = run_args(Path("不存在的目录"), "target", AND_KEYWORD, "  ")
        self.assert_argument_error(proc, "为空或全为空白", "扫描前报错")

    # -- 12. 未提供新选项时既有行为不变；无合格行返回 [] -------------------

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

    # -- 13. 查询只读源文件且不留下索引 -------------------------------------

    def test_query_is_read_only_and_leaves_no_index(self) -> None:
        self._write_text("keep.txt", "target budget\n")
        before = self._snapshot_files()
        proc = run_args(self.root, "target", AND_KEYWORD, "budget")
        after = self._snapshot_files()
        self.assertEqual(before, after, "查询后源文件字节不得变化，且不得新增任何文件/索引")
        self.assert_success_clean(proc, "只读/无索引")


if __name__ == "__main__":
    unittest.main()

"""``--or-keyword`` 主词或替代词同行命中的端到端回归测试。

通过命令入口 ``python -m local_search <目录> <关键词> --or-keyword <替代关键词>``
固定从命令行输入到 JSON 结果输出的行为：

- 每行独立判断，行内包含主关键词或替代关键词之一即满足关键词条件；两词均
  按连续字面子串匹配（不拆词、不解释正则或通配符，首尾空格保留），默认
  区分大小写；``--ignore-case`` 同时折叠两词，仍只折叠 ASCII 字母；
- 同时提供 ``--and-keyword`` 时本行仍须包含附加词；同时提供
  ``--not-keyword`` 时本行任何位置（含片段之外）出现排除词即不合格；
- 默认每个文件只返回最早的合格行；``--all-lines`` 每个合格行各返回一项，
  两词都出现或重复出现不增加项数；
- snippet 围绕两词起始位置最小的命中，同位置时选主词，长度按所选词计算；
  ``--context-chars`` 沿用前后 Unicode 码点额度，零时仅保留完整命中，
  片段保留源文大小写、不跨行、不加省略号；
- 替代词值即使写作 ``--all-lines`` 也按文本消费，不开启开关；位置参数或
  其他选项取值中的 ``--or-keyword`` 仍按字面处理；
- 缺值、重复指定、值为空或全为空白时在扫描前结束：退出码 2、标准输出为空、
  标准错误包含选项名及对应原因；未提供该选项时既有行为完全不变；
- 无命中返回 ``[]``（CSV 仅表头）；文件无法读取或含非法 UTF-8 时告警跳过、
  退出码仍为 0；查询只读源文件，不生成索引或其他文件。

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

OR_KEYWORD = "--or-keyword"
AND_KEYWORD = "--and-keyword"
NOT_KEYWORD = "--not-keyword"
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


class OrKeywordTest(unittest.TestCase):
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
        self.assertIn(OR_KEYWORD, stderr, f"{label}: 标准错误应包含选项名，实际 {stderr!r}")

    def _snapshot_files(self) -> dict[str, bytes]:
        return {
            str(p.relative_to(self.root)).replace(os.sep, "/"): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    def _write_acceptance_sample(self) -> None:
        # 验收目录布局：三行依次为 plan target / plan / target。
        self._write_text("a.txt", "plan target\nplan\ntarget\n")

    # -- 1. 验收场景：默认只返回第 1 行，--all-lines 依次返回三行 ----------

    def test_acceptance_default_returns_earliest_qualified_line(self) -> None:
        self._write_acceptance_sample()

        results = self.assert_success_clean(
            run_args(self.root, "target", OR_KEYWORD, "plan", CONTEXT_CHARS, "0"),
            "验收：默认模式",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "plan"}],
            "只返回最早的合格行；第 1 行两词同现，片段围绕起始位置更小的 plan",
        )

    def test_acceptance_all_lines_returns_each_qualified_line(self) -> None:
        self._write_acceptance_sample()

        results = self.assert_success_clean(
            run_args(self.root, "target", OR_KEYWORD, "plan", CONTEXT_CHARS, "0",
                     ALL_LINES),
            "验收：逐行模式",
        )
        self.assertEqual(
            results,
            [
                {"path": "a.txt", "line": 1, "snippet": "plan"},
                {"path": "a.txt", "line": 2, "snippet": "plan"},
                {"path": "a.txt", "line": 3, "snippet": "target"},
            ],
            "三个合格行各返回一项；只含替代词或只含主词的行同样合格",
        )

    # -- 2. 或语义：每行独立判断，任一词出现即合格 --------------------------

    def test_either_keyword_alone_qualifies_the_line(self) -> None:
        self._write_text("a.txt", "only plan here\nneither\nonly target here\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", OR_KEYWORD, "plan", ALL_LINES,
                     CONTEXT_CHARS, "0"),
            "任一词即合格",
        )
        self.assertEqual(
            results,
            [
                {"path": "a.txt", "line": 1, "snippet": "plan"},
                {"path": "a.txt", "line": 3, "snippet": "target"},
            ],
            "只含替代词或只含主词的行各自合格，不含任一词的行不合格",
        )

    def test_both_keywords_on_one_line_still_one_item(self) -> None:
        self._write_text("a.txt", "plan target plan target\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", OR_KEYWORD, "plan", ALL_LINES,
                     CONTEXT_CHARS, "0"),
            "两词重复出现",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "plan"}],
            "两词都出现且各自重复出现仍只产生一项",
        )

    # -- 3. 片段选择：起始位置最小者优先，同位置选主词，长度按所选词 --------

    def test_snippet_uses_earliest_start_among_both_keywords(self) -> None:
        # 主词 target 先于替代词 plan 出现：片段围绕主词。
        self._write_text("main_first/a.txt", "target xx plan\n")
        main_first = self.assert_success_clean(
            run_args(self.root / "main_first", "target", OR_KEYWORD, "plan",
                     CONTEXT_CHARS, "2"),
            "主词在前",
        )
        self.assertEqual(
            main_first,
            [{"path": "a.txt", "line": 1, "snippet": "target x"}],
            "主词起始位置更小时片段围绕主词，长度按主词计算",
        )

        # 替代词 plan 先于主词 target 出现：片段围绕替代词。
        self._write_text("alt_first/a.txt", "plan xx target\n")
        alt_first = self.assert_success_clean(
            run_args(self.root / "alt_first", "target", OR_KEYWORD, "plan",
                     CONTEXT_CHARS, "2"),
            "替代词在前",
        )
        self.assertEqual(
            alt_first,
            [{"path": "a.txt", "line": 1, "snippet": "plan x"}],
            "替代词起始位置更小时片段围绕替代词，长度按替代词计算",
        )

    def test_same_start_position_prefers_main_keyword(self) -> None:
        # 两词同位置起始（主词 abc 与替代词 ab 同起于 0）：选主词，长度按主词。
        self._write_text("a.txt", "abc tail\n")

        results = self.assert_success_clean(
            run_args(self.root, "abc", OR_KEYWORD, "ab", CONTEXT_CHARS, "0"),
            "同位置选主词",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "abc"}],
            "起始位置相同（含前缀重叠）时片段按主词及其长度截取",
        )

    def test_snippet_length_follows_chosen_keyword(self) -> None:
        # 替代词比主词长：context 额度加在替代词长度之外。
        self._write_text("a.txt", "xxplanningyy\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", OR_KEYWORD, "planning", CONTEXT_CHARS, "1"),
            "长度按替代词",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "xplanningy"}],
            "命中替代词时片段长度按替代词计算，前后各保留 1 个码点",
        )

    def test_snippet_keeps_source_case_and_stays_on_line(self) -> None:
        self._write_text("a.txt", "xxPLANyy\nzzPLANww\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", OR_KEYWORD, "plan", IGNORE_CASE,
                     ALL_LINES, CONTEXT_CHARS, "1"),
            "片段保留源文大小写",
        )
        self.assertEqual(
            results,
            [
                {"path": "a.txt", "line": 1, "snippet": "xPLANy"},
                {"path": "a.txt", "line": 2, "snippet": "zPLANw"},
            ],
            "片段取自源文本、保留大小写，不跨行也不加省略号",
        )

    # -- 4. 大小写：默认区分大小写；--ignore-case 折叠两词且仅 ASCII -------

    def test_case_sensitive_by_default_for_both_keywords(self) -> None:
        self._write_text("a.txt", "TARGET\nPLAN\ntarget\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", OR_KEYWORD, "plan", ALL_LINES,
                     CONTEXT_CHARS, "0"),
            "默认区分大小写",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 3, "snippet": "target"}],
            "大写的 TARGET 与 PLAN 在默认模式下均不合格",
        )

    def test_ignore_case_folds_both_keywords_ascii_only(self) -> None:
        self._write_text("ascii/a.txt", "TARGET\nPlan\n")
        results = self.assert_success_clean(
            run_args(self.root / "ascii", "target", OR_KEYWORD, "plan", IGNORE_CASE,
                     ALL_LINES, CONTEXT_CHARS, "0"),
            "双词同时折叠",
        )
        self.assertEqual(
            results,
            [
                {"path": "a.txt", "line": 1, "snippet": "TARGET"},
                {"path": "a.txt", "line": 2, "snippet": "Plan"},
            ],
            "两个关键词都被 ASCII 折叠；片段保留源文本大小写",
        )

        # 非 ASCII 不折叠：替代词 Été 不命中 été。
        self._write_text("uni/a.txt", "été\nÉté\n")
        uni = self.assert_success_clean(
            run_args(self.root / "uni", "target", OR_KEYWORD, "Été", IGNORE_CASE,
                     ALL_LINES),
            "非 ASCII 不折叠",
        )
        self.assertEqual(
            [item["line"] for item in uni],
            [2],
            "--ignore-case 不把 É 折叠为 é，仅第 2 行合格",
        )

    # -- 5. 字面匹配：不拆词、不解释正则或通配符、首尾空格保留 -------------

    def test_or_keyword_is_literal_substring(self) -> None:
        self._write_text("lit.txt", "see a.b*c? here\nsee abc here\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", OR_KEYWORD, "a.b*c?", CONTEXT_CHARS, "0"),
            "正则元字符按字面",
        )
        self.assertEqual(
            results,
            [{"path": "lit.txt", "line": 1, "snippet": "a.b*c?"}],
            "替代词中的正则元字符按字面文本处理，第 2 行不命中",
        )

    def test_leading_trailing_spaces_in_or_keyword_are_significant(self) -> None:
        self._write_text(
            "sp.txt",
            " plan \n"      # 第 1 行：' plan ' 完整出现
            " plan\n"       # 第 2 行：plan 后无空格
            "x  plan  y\n",  # 第 3 行：双空格中仍含 ' plan '
        )

        results = self.assert_success_clean(
            run_args(self.root, "target", OR_KEYWORD, " plan ", ALL_LINES,
                     CONTEXT_CHARS, "0"),
            "替代词首尾空格",
        )
        self.assertEqual(
            [item["line"] for item in results],
            [1, 3],
            "替代词 ' plan ' 只命中两侧确为空格的第 1、3 行，不拆词",
        )

    # -- 6. 与 --and-keyword / --not-keyword 组合 --------------------------

    def test_and_keyword_still_required_on_same_line(self) -> None:
        self._write_text(
            "a.txt",
            "plan budget\n"   # 替代词与附加词同行：合格
            "plan only\n"     # 缺附加词：不合格
            "target budget\n" # 主词与附加词同行：合格
            "budget only\n",  # 缺主词与替代词：不合格
        )

        results = self.assert_success_clean(
            run_args(self.root, "target", OR_KEYWORD, "plan", AND_KEYWORD, "budget",
                     ALL_LINES, CONTEXT_CHARS, "0"),
            "附加词仍必需",
        )
        self.assertEqual(
            results,
            [
                {"path": "a.txt", "line": 1, "snippet": "plan"},
                {"path": "a.txt", "line": 3, "snippet": "target"},
            ],
            "提供 --and-keyword 时本行仍须包含附加词，主词或替代词满足其一即可",
        )

    def test_not_keyword_excludes_line_anywhere(self) -> None:
        self._write_text(
            "a.txt",
            "plan here secret far away\n"  # 排除词在片段之外：仍不合格
            "plan here\n"                  # 合格
            "secret target\n",             # 排除词在主词命中之前：不合格
        )

        results = self.assert_success_clean(
            run_args(self.root, "target", OR_KEYWORD, "plan", NOT_KEYWORD, "secret",
                     ALL_LINES, CONTEXT_CHARS, "0"),
            "排除词覆盖整行",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 2, "snippet": "plan"}],
            "排除词出现在本行任何位置（含片段范围之外）即排除该行",
        )

    # -- 7. 逐行模式与多文件排序、结果字段 ---------------------------------

    def test_results_sorted_by_path_then_line_with_three_fields(self) -> None:
        self._write_text("b.txt", "plan b\n")
        self._write_text("a.txt", "target a1\nplan a2\n")
        self._write_text("A.md", "plan A\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", OR_KEYWORD, "plan", ALL_LINES,
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

    def test_or_keyword_value_equal_to_flag_name_is_literal_text(self) -> None:
        self._write_text("cfg.txt", "--all-lines here\ntarget\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", OR_KEYWORD, ALL_LINES),
            "替代词值恰为开关名",
        )
        self.assertEqual(
            results,
            [{"path": "cfg.txt", "line": 1, "snippet": "--all-lines here"}],
            "--all-lines 被当作替代关键词文本消费，逐行开关未开启",
        )

    def test_positional_keyword_equal_to_option_name_stays_literal(self) -> None:
        self._write_text("a.txt", "--or-keyword here\ntarget\n")

        results = self.assert_success_clean(
            run_args(self.root, OR_KEYWORD), "关键词恰为选项名"
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "--or-keyword here"}],
            "第二个位置参数即使写作 --or-keyword 也仍是关键词",
        )

    def test_path_option_value_equal_to_option_name_stays_literal(self) -> None:
        self._write_text("--or-keyword/a.txt", "target\n")
        self._write_text("other/b.txt", "target\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", PATH_OPTION, OR_KEYWORD),
            "路径片段恰为选项名",
        )
        self.assertEqual(
            results,
            [{"path": "--or-keyword/a.txt", "line": 1, "snippet": "target"}],
            "--path-contains 的取值 --or-keyword 是字面片段而非选项",
        )

    # -- 9. 与既有选项任意排序组合；路径筛选继续生效 ------------------------

    def test_combines_with_other_options_in_any_order(self) -> None:
        self._write_text("notes/a.txt", "Plan Budget\n")

        argv_orders = (
            ("TARGET", OR_KEYWORD, "PLAN", IGNORE_CASE, CONTEXT_CHARS, "0",
             PATH_OPTION, "notes/"),
            ("TARGET", PATH_OPTION, "notes/", IGNORE_CASE, OR_KEYWORD, "PLAN",
             CONTEXT_CHARS, "0"),
            ("TARGET", IGNORE_CASE, CONTEXT_CHARS, "0", OR_KEYWORD, "PLAN",
             PATH_OPTION, "notes/"),
        )
        for argv in argv_orders:
            with self.subTest(argv=argv):
                results = self.assert_success_clean(run_args(self.root, *argv), "任意排序")
                self.assertEqual(
                    results,
                    [{"path": "notes/a.txt", "line": 1, "snippet": "Plan"}],
                    "选项任意排序结果一致；替代词命中且片段保留源文大小写",
                )

    # -- 10. 选中文件无法解码：整文件告警跳过，其余继续，退出码 0 ----------

    def test_broken_selected_file_warned_and_skipped(self) -> None:
        self._write("broken.txt", b"plan before\n\xff\nplan after\n")
        self._write_text("ok.txt", "plan ok\n")

        proc = run_args(self.root, "target", OR_KEYWORD, "plan", CONTEXT_CHARS, "0")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8")),
            [{"path": "ok.txt", "line": 1, "snippet": "plan"}],
            "坏文件的合格行一律不返回；其余文件照常",
        )
        stderr = proc.stderr.decode("utf-8")
        self.assertIn("broken.txt", stderr)
        self.assertIn("无法按 UTF-8 解码", stderr)

    # -- 11. 参数错误：缺值、重复、空值、全空白 → 扫描前退出 2 -------------

    def test_missing_value_is_argument_error(self) -> None:
        proc = run_args(self.root, "target", OR_KEYWORD)
        self.assert_argument_error(proc, "缺少值", "缺值")

    def test_duplicate_option_is_argument_error(self) -> None:
        proc = run_args(self.root, "target", OR_KEYWORD, "a", OR_KEYWORD, "b")
        self.assert_argument_error(proc, "只能指定一次", "重复指定")

    def test_empty_or_blank_value_is_argument_error(self) -> None:
        for raw in ("", " ", "   ", "\t"):
            with self.subTest(raw=raw):
                proc = run_args(self.root, "target", OR_KEYWORD, raw)
                self.assert_argument_error(proc, "为空或全为空白", f"空白值 {raw!r}")

    def test_argument_errors_happen_before_any_scan(self) -> None:
        # 目录不存在时，--or-keyword 的参数错误仍优先报出（扫描前结束）。
        proc = run_args(Path("不存在的目录"), "target", OR_KEYWORD, "  ")
        self.assert_argument_error(proc, "为空或全为空白", "扫描前报错")

    # -- 12. 未提供新选项时既有行为不变；无命中时空结果 --------------------

    def test_without_option_behavior_is_unchanged(self) -> None:
        self._write_acceptance_sample()

        results = self.assert_success_clean(
            run_args(self.root, "target", CONTEXT_CHARS, "0"),
            "无新选项",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "target"}],
            "未指定 --or-keyword 时仍只按主关键词返回首个命中",
        )

    def test_no_match_returns_empty_json_array(self) -> None:
        self._write_text("a.txt", "alpha\nbeta\n")
        proc = run_args(self.root, "target", OR_KEYWORD, "plan")
        results = self.assert_success_clean(proc, "无命中 JSON")
        self.assertEqual(results, [])
        self.assertEqual(proc.stdout.decode("utf-8").strip(), "[]")

    def test_no_match_csv_outputs_header_only(self) -> None:
        self._write_text("a.txt", "alpha\nbeta\n")
        proc = run_args(self.root, "target", OR_KEYWORD, "plan", FORMAT, "csv")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, b"")
        self.assertEqual(proc.stdout, b"path,line,snippet\r\n")

    # -- 13. 查询只读源文件且不留下索引 -------------------------------------

    def test_query_is_read_only_and_leaves_no_index(self) -> None:
        self._write_text("keep.txt", "plan target\n")
        before = self._snapshot_files()
        proc = run_args(self.root, "target", OR_KEYWORD, "plan")
        after = self._snapshot_files()
        self.assertEqual(before, after, "查询后源文件字节不得变化，且不得新增任何文件/索引")
        self.assert_success_clean(proc, "只读/无索引")


if __name__ == "__main__":
    unittest.main()

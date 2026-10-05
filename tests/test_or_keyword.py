"""``--or-keyword`` 主词或替代词检索的端到端回归测试。

通过命令入口 ``python -m local_search <目录> <关键词> --or-keyword <替代关键词>``
固定从命令行输入到 JSON/CSV 结果输出的行为：

- 每行独立判断：行内包含主关键词或替代词之一即满足关键词条件，两词均按
  连续字面子串比较（不拆词、不解释正则或通配符，首尾空格保留）；
- 默认区分大小写；``--ignore-case`` 同时折叠两词，仍只折叠 ASCII 字母；
- 与 ``--and-keyword`` 同用时本行仍须包含附加词；与 ``--not-keyword`` 同用
  时本行任何位置（含片段范围之外）出现排除词即不合格；
- 默认每个文件只返回最早的合格行；``--all-lines`` 每个合格行各返回一项，
  两词都出现或重复出现不增加项数；
- 片段围绕两词各自最左侧命中中起始位置更小的一处，同位置（含两词相同）时
  锚定主关键词，长度按所选词计算；``--context-chars`` 沿用前后码点额度，
  零时仅保留完整命中；片段保留源文大小写，不跨行、不加省略号；
- 替代词即使写作 ``--all-lines`` 也按文本消费，不开启开关；位置参数或其他
  选项取值中的 ``--or-keyword`` 仍按字面处理；
- 缺值、重复指定、值为空或全为空白时在扫描前结束：退出码 2、标准输出为空、
  标准错误包含选项名及对应原因；未提供该选项时既有行为完全不变；
- 无命中仍输出空 JSON 数组或 CSV 仅表头；文件读取或解码失败仍告警后跳过
  且退出码 0；查询只读源文件，不生成索引或其他文件。

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
FILE_TYPE = "--file-type"
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

    # -- 1. 验收场景：默认只返回第 1 行 plan；--all-lines 依次三行 ----------

    def test_acceptance_default_returns_first_qualified_line(self) -> None:
        self._write_acceptance_sample()

        results = self.assert_success_clean(
            run_args(self.root, "target", OR_KEYWORD, "plan", CONTEXT_CHARS, "0"),
            "验收：默认模式",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "plan"}],
            "第 1 行替代词 plan 起始位置更小，片段锚定 plan",
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
            "第 1、2 行锚定替代词 plan，第 3 行锚定主词 target",
        )

    # -- 2. 或语义：任一词出现即合格，每行独立判断 ---------------------------

    def test_either_keyword_alone_qualifies_the_line(self) -> None:
        self._write_text("a.txt", "only target here\nonly plan here\nneither here\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", OR_KEYWORD, "plan", ALL_LINES,
                     CONTEXT_CHARS, "0"),
            "任一词即合格",
        )
        self.assertEqual(
            results,
            [
                {"path": "a.txt", "line": 1, "snippet": "target"},
                {"path": "a.txt", "line": 2, "snippet": "plan"},
            ],
            "只含主词或只含替代词的行都合格；两词都不含的行不合格",
        )

    def test_both_keywords_on_one_line_still_one_item(self) -> None:
        self._write_text("a.txt", "plan target plan target\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", OR_KEYWORD, "plan", ALL_LINES,
                     CONTEXT_CHARS, "0"),
            "两词同行仍一项",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "plan"}],
            "两词都出现且各自重复出现仍只返回一项",
        )

    # -- 3. 片段锚定：起始位置最小者，同位置选主词，长度按所选词 -------------

    def test_snippet_anchors_on_earliest_of_both_keywords(self) -> None:
        # 主词 target 在替代词 plan 之前时锚定主词。
        self._write_text("a.txt", "target plan\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", OR_KEYWORD, "plan", CONTEXT_CHARS, "0"),
            "主词更早",
        )
        self.assertEqual(results, [{"path": "a.txt", "line": 1, "snippet": "target"}])

    def test_snippet_length_uses_the_chosen_keyword(self) -> None:
        # 替代词 planning 比主词长：锚定替代词时片段长度按替代词计算。
        self._write_text("a.txt", "xxplanningyy target\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", OR_KEYWORD, "planning", CONTEXT_CHARS, "0"),
            "长度按替代词",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "planning"}],
            "锚定替代词时片段为完整替代词，不按主词长度截断",
        )

    def test_same_start_position_prefers_main_keyword(self) -> None:
        # 主词 target 与替代词 tar 同位置起始：锚定主词，片段为主词全长。
        self._write_text("a.txt", "target\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", OR_KEYWORD, "tar", CONTEXT_CHARS, "0"),
            "同位置选主词",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "target"}],
            "同位置时片段为主词 target 而非替代词 tar",
        )

    def test_identical_keywords_anchor_main_keyword(self) -> None:
        self._write_text("a.txt", "target target\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", OR_KEYWORD, "target", CONTEXT_CHARS, "0"),
            "两词相同",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "target"}],
            "两词相同仍只一项，片段为最左侧命中",
        )

    def test_context_chars_apply_around_chosen_hit(self) -> None:
        self._write_text("a.txt", "0123plan4567\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", OR_KEYWORD, "plan", CONTEXT_CHARS, "2"),
            "上下文围绕替代词",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "23plan45"}],
            "替代词命中前后各保留 2 个码点，片段取自源文本",
        )

    # -- 4. 字面匹配：不拆词、不解释正则或通配符、首尾空格保留 -------------

    def test_or_keyword_is_literal_substring(self) -> None:
        self._write_text("lit.txt", "a.b*c? here\naxbxc here\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", OR_KEYWORD, "a.b*c?", ALL_LINES,
                     CONTEXT_CHARS, "0"),
            "正则元字符按字面",
        )
        self.assertEqual(
            results,
            [{"path": "lit.txt", "line": 1, "snippet": "a.b*c?"}],
            "替代词中的正则元字符按字面文本处理，第 2 行不合格",
        )

    def test_leading_trailing_spaces_in_or_keyword_are_significant(self) -> None:
        self._write_text(
            "sp.txt",
            "x plan y\n"        # 第 1 行：' plan ' 完整出现
            "x plan\n"          # 第 2 行：plan 后无空格
            "x  plan y\n"       # 第 3 行：双空格中仍含 ' plan '
            "xplan y\n",        # 第 4 行：plan 前不是空格
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

    # -- 5. 大小写：默认区分大小写；--ignore-case 折叠两词且仅 ASCII --------

    def test_case_sensitive_by_default_for_or_keyword(self) -> None:
        self._write_text("a.txt", "Plan here\nPLAN here\nplan here\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", OR_KEYWORD, "plan", ALL_LINES,
                     CONTEXT_CHARS, "0"),
            "默认区分大小写",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 3, "snippet": "plan"}],
            "替代词大小写不同的行均不合格",
        )

    def test_ignore_case_folds_or_keyword_ascii_only(self) -> None:
        self._write_text("ascii.txt", "PLAN here\nplan here\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", OR_KEYWORD, "plan", IGNORE_CASE,
                     ALL_LINES, CONTEXT_CHARS, "0"),
            "替代词 ASCII 折叠",
        )
        self.assertEqual(
            results,
            [
                {"path": "ascii.txt", "line": 1, "snippet": "PLAN"},
                {"path": "ascii.txt", "line": 2, "snippet": "plan"},
            ],
            "替代词被 ASCII 折叠；片段保留源文本大小写",
        )

        # 非 ASCII 不折叠：替代词 Été 不命中 été。
        self._write_text("uni.txt", "été here\nÉté here\n")
        uni = self.assert_success_clean(
            run_args(self.root, "target", OR_KEYWORD, "Été", IGNORE_CASE, ALL_LINES,
                     CONTEXT_CHARS, "0"),
            "非 ASCII 不折叠",
        )
        self.assertEqual(
            [item["line"] for item in uni],
            [2],
            "--ignore-case 不把 É 折叠为 é，仅第 2 行合格",
        )

    # -- 6. 与 --and-keyword / --not-keyword 组合 ---------------------------

    def test_and_keyword_still_required_on_the_line(self) -> None:
        self._write_text(
            "a.txt",
            "plan budget\n"     # 替代词+附加词：合格
            "plan only\n"       # 只有替代词：缺附加词，不合格
            "target only\n"     # 只有主词：缺附加词，不合格
            "target budget\n",  # 主词+附加词：合格
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
                {"path": "a.txt", "line": 4, "snippet": "target"},
            ],
            "满足或条件但仍须同行包含附加词",
        )

    def test_not_keyword_excludes_line_anywhere(self) -> None:
        self._write_text(
            "a.txt",
            "plan draft\n"        # 替代词命中但含排除词：不合格
            "plan clean\n"        # 合格
            "target clean draft\n"  # 主词命中但含排除词：不合格
            "target clean\n",     # 合格
        )

        results = self.assert_success_clean(
            run_args(self.root, "target", OR_KEYWORD, "plan", NOT_KEYWORD, "draft",
                     ALL_LINES, CONTEXT_CHARS, "0"),
            "排除词覆盖整行",
        )
        self.assertEqual(
            results,
            [
                {"path": "a.txt", "line": 2, "snippet": "plan"},
                {"path": "a.txt", "line": 4, "snippet": "target"},
            ],
            "排除词出现在行内任何位置（含片段之外）即排除该行",
        )

    # -- 7. 默认首个合格行；逐行模式每行一项；多文件排序与字段不变 ----------

    def test_default_returns_earliest_qualified_line_per_file(self) -> None:
        self._write_text("a.txt", "nothing\nplan\ntarget\n")
        self._write_text("b.txt", "plan\ntarget\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", OR_KEYWORD, "plan", CONTEXT_CHARS, "0"),
            "默认每文件首个合格行",
        )
        self.assertEqual(
            results,
            [
                {"path": "a.txt", "line": 2, "snippet": "plan"},
                {"path": "b.txt", "line": 1, "snippet": "plan"},
            ],
            "每个文件只返回行号最小的合格行",
        )

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
        self._write_text("cfg.txt", "target --all-lines\ntarget plain\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", OR_KEYWORD, ALL_LINES),
            "替代词恰为开关名",
        )
        self.assertEqual(
            results,
            [{"path": "cfg.txt", "line": 1, "snippet": "target --all-lines"}],
            "--all-lines 被当作替代词文本消费，逐行开关未开启",
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

    def test_other_option_value_equal_to_option_name_stays_literal(self) -> None:
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

    # -- 9. 与既有选项任意排序组合；路径与格式筛选继续生效 -----------------

    def test_combines_with_other_options_in_any_order(self) -> None:
        self._write_text("notes/a.txt", "PLAN here\n")
        self._write_text("notes/b.md", "PLAN here\n")

        argv_orders = (
            ("target", OR_KEYWORD, "plan", IGNORE_CASE, CONTEXT_CHARS, "0",
             PATH_OPTION, "notes/", FILE_TYPE, "txt"),
            ("target", FILE_TYPE, "txt", PATH_OPTION, "notes/", IGNORE_CASE,
             OR_KEYWORD, "plan", CONTEXT_CHARS, "0"),
            ("target", IGNORE_CASE, CONTEXT_CHARS, "0", OR_KEYWORD, "plan",
             FILE_TYPE, "txt", PATH_OPTION, "notes/"),
        )
        for argv in argv_orders:
            with self.subTest(argv=argv):
                results = self.assert_success_clean(run_args(self.root, *argv), "任意排序")
                self.assertEqual(
                    results,
                    [{"path": "notes/a.txt", "line": 1, "snippet": "PLAN"}],
                    "选项任意排序结果一致；格式筛选排除 b.md",
                )

    # -- 10. 选中文件无法解码：整文件告警跳过，其余继续，退出码 0 -----------

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

    # -- 11. 参数错误：缺值、重复、空值、全空白 → 扫描前退出 2 --------------

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

    # -- 12. 未提供新选项时既有行为不变；无命中返回空结果 -------------------

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

    def test_no_hit_returns_empty_json_array(self) -> None:
        self._write_text("a.txt", "alpha\nbeta\n")
        proc = run_args(self.root, "target", OR_KEYWORD, "plan")
        results = self.assert_success_clean(proc, "无命中")
        self.assertEqual(results, [])
        self.assertEqual(proc.stdout.decode("utf-8").strip(), "[]")

    def test_no_hit_csv_outputs_header_only(self) -> None:
        self._write_text("a.txt", "alpha\nbeta\n")
        proc = run_args(self.root, "target", OR_KEYWORD, "plan", FORMAT, "csv")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, b"")
        self.assertEqual(proc.stdout, b"path,line,snippet\r\n")

    def test_csv_output_with_or_keyword_hits(self) -> None:
        self._write_acceptance_sample()

        proc = run_args(self.root, "target", OR_KEYWORD, "plan", CONTEXT_CHARS, "0",
                        ALL_LINES, FORMAT, "csv")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, b"")
        self.assertEqual(
            proc.stdout,
            b"path,line,snippet\r\n"
            b"a.txt,1,plan\r\n"
            b"a.txt,2,plan\r\n"
            b"a.txt,3,target\r\n",
        )

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

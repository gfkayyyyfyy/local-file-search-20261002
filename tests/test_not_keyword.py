"""``--not-keyword`` 排除词筛选的端到端回归测试。

通过命令入口 ``python -m local_search <目录> <关键词> --not-keyword <排除关键词>``
固定从命令行输入到 JSON 结果输出的行为：

- 一行只有满足主关键词及已提供的 ``--and-keyword`` 条件、同时完整当前行
  不含排除词，才进入结果；排除词在片段之外仍能排除该行，其他行出现该词
  不影响本行；
- 排除词按连续字面子串比较（不拆词、不解释正则或通配符，首尾空格保留），
  默认区分大小写；``--ignore-case`` 同时作用于排除词，仍只折叠 ASCII 字母；
- 默认每个文件返回行号最小的合格行，较早行被排除后继续寻找后续行；
  ``--all-lines`` 每个合格行各返回一项，行内重复出现不增加结果；
- snippet 围绕主关键词在该行最左侧的命中，保留源文本大小写，
  ``--context-chars`` 沿用既有规则，排除词不改变片段取法；
- 排除值即使写作 ``--all-lines`` 也按文本处理，不开启开关；位置参数或其他
  选项取值中的 ``--not-keyword`` 仍按字面处理；
- 缺值、重复指定、值为空或全为空白时在扫描前结束：退出码 2、标准输出为空、
  标准错误包含选项名及对应原因；未提供该选项时既有行为完全不变；
- 排除词与主关键词相同或没有合格行时返回 ``[]``、退出码 0；
- 路径与格式筛选继续生效，被过滤文件不读取、不告警；选中文件无法读取或含
  非法 UTF-8 时整文件告警跳过，其他文件继续，退出码仍为 0；
- 查询只读源文件，不生成索引或其他文件。

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

NOT_KEYWORD = "--not-keyword"
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


class NotKeywordTest(unittest.TestCase):
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
        self.assertIn(NOT_KEYWORD, stderr, f"{label}: 标准错误应包含选项名，实际 {stderr!r}")

    def _snapshot_files(self) -> dict[str, bytes]:
        return {
            str(p.relative_to(self.root)).replace(os.sep, "/"): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    def _write_acceptance_sample(self) -> None:
        # 验收目录布局：四行依次为 Target DRAFT budget / budget target target /
        # draft target budget / target budget。
        self._write_text(
            "a.txt",
            "Target DRAFT budget\n"
            "budget target target\n"
            "draft target budget\n"
            "target budget\n",
        )

    # -- 1. 验收场景：默认只返回第 2 行，--all-lines 追加第 4 行 -----------

    def test_acceptance_default_returns_first_qualified_line(self) -> None:
        self._write_acceptance_sample()

        results = self.assert_success_clean(
            run_args(self.root, "target", NOT_KEYWORD, "draft", AND_KEYWORD, "budget",
                     IGNORE_CASE, CONTEXT_CHARS, "0"),
            "验收：默认模式",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 2, "snippet": "target"}],
            "第 1、3 行含排除词被跳过，只返回行号最小的合格行（第 2 行）",
        )

    def test_acceptance_all_lines_adds_fourth_line(self) -> None:
        self._write_acceptance_sample()

        results = self.assert_success_clean(
            run_args(self.root, "target", NOT_KEYWORD, "draft", AND_KEYWORD, "budget",
                     IGNORE_CASE, CONTEXT_CHARS, "0", ALL_LINES),
            "验收：逐行模式",
        )
        self.assertEqual(
            results,
            [
                {"path": "a.txt", "line": 2, "snippet": "target"},
                {"path": "a.txt", "line": 4, "snippet": "target"},
            ],
            "第 2、4 行各返回一项；片段均为主关键词 target 的最左侧命中",
        )

    # -- 2. 排除语义：覆盖完整当前行，逐行独立判断 -------------------------

    def test_exclusion_covers_whole_line_beyond_snippet(self) -> None:
        # 排除词远离主关键词命中、落在片段范围之外，仍排除该行。
        line = "target" + "m" * 40 + "draft"
        self._write_text("a.txt", line + "\ntarget clean\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", NOT_KEYWORD, "draft", CONTEXT_CHARS, "0"),
            "片段之外仍排除",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 2, "snippet": "target"}],
            "第 1 行的排除词在 30 码点片段之外，该行仍被排除",
        )

    def test_exclusion_is_per_line_not_per_file(self) -> None:
        self._write_text("a.txt", "draft alone\ntarget here\ntarget and draft\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", NOT_KEYWORD, "draft", ALL_LINES,
                     CONTEXT_CHARS, "0"),
            "逐行独立",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 2, "snippet": "target"}],
            "排除词出现在其他行不影响本行；含排除词的第 3 行被排除",
        )

    def test_exclusion_combines_with_and_keyword(self) -> None:
        self._write_text(
            "a.txt",
            "target budget draft\n"   # 两词齐全但含排除词
            "target only\n"           # 缺附加词
            "target budget\n",        # 合格
        )

        results = self.assert_success_clean(
            run_args(self.root, "target", AND_KEYWORD, "budget", NOT_KEYWORD, "draft",
                     CONTEXT_CHARS, "0"),
            "与附加词组合",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 3, "snippet": "target"}],
            "须同时满足主关键词、附加词且不含排除词",
        )

    # -- 3. 大小写：默认区分大小写；--ignore-case 折叠排除词且仅 ASCII -----

    def test_case_sensitive_by_default_for_not_keyword(self) -> None:
        self._write_text("a.txt", "target DRAFT\ntarget draft\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", NOT_KEYWORD, "draft", ALL_LINES,
                     CONTEXT_CHARS, "0"),
            "默认区分大小写",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "target"}],
            "DRAFT 不等于 draft，仅第 2 行被排除",
        )

    def test_ignore_case_applies_to_not_keyword_ascii_only(self) -> None:
        self._write_text("ascii.txt", "Target DRAFT\ntarget clean\n")
        results = self.assert_success_clean(
            run_args(self.root, "target", NOT_KEYWORD, "draft", IGNORE_CASE,
                     ALL_LINES, CONTEXT_CHARS, "0"),
            "排除词折叠",
        )
        self.assertEqual(
            results,
            [{"path": "ascii.txt", "line": 2, "snippet": "target"}],
            "--ignore-case 把 DRAFT 折叠为 draft，第 1 行被排除",
        )

        # 非 ASCII 不折叠：排除词 Été 不排除含 été 的行。
        self._write_text("uni.txt", "target été\ntarget Été\n")
        uni = self.assert_success_clean(
            run_args(self.root, "target", NOT_KEYWORD, "Été", IGNORE_CASE, ALL_LINES,
                     CONTEXT_CHARS, "0"),
            "非 ASCII 不折叠",
        )
        self.assertEqual(
            [item["line"] for item in uni if item["path"] == "uni.txt"],
            [1],
            "--ignore-case 不把 É 折叠为 é，仅第 2 行被排除",
        )

    # -- 4. 字面匹配：不拆词、不解释正则或通配符、首尾空格保留 -------------

    def test_not_keyword_is_literal_substring(self) -> None:
        self._write_text("lit.txt", "target a.b*c?\ntarget axbxc?\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", NOT_KEYWORD, "a.b*c?", ALL_LINES,
                     CONTEXT_CHARS, "0"),
            "正则元字符按字面",
        )
        self.assertEqual(
            results,
            [{"path": "lit.txt", "line": 2, "snippet": "target"}],
            "排除词中的正则元字符按字面文本处理，只排除第 1 行",
        )

    def test_leading_trailing_spaces_in_not_keyword_are_significant(self) -> None:
        self._write_text(
            "sp.txt",
            "target draft \n"     # 第 1 行：' draft ' 完整出现，排除
            "target draft\n"      # 第 2 行：draft 后无空格，保留
            "target  draft \n"    # 第 3 行：双空格中仍含 ' draft '，排除
            "target xdraft \n",   # 第 4 行：draft 前不是空格，保留
        )

        results = self.assert_success_clean(
            run_args(self.root, "target", NOT_KEYWORD, " draft ", ALL_LINES,
                     CONTEXT_CHARS, "0"),
            "排除词首尾空格",
        )
        self.assertEqual(
            [item["line"] for item in results],
            [2, 4],
            "排除词 ' draft ' 只排除两侧确为空格的第 1、3 行，不拆词",
        )

    # -- 5. 片段：排除不改变取法，仍围绕主关键词最左侧命中 -----------------

    def test_snippet_unchanged_by_exclusion(self) -> None:
        line = "target" + "m" * 40 + "budget"
        self._write_text("a.txt", line + "\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", NOT_KEYWORD, "draft"),
            "片段不扩大",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "target" + "m" * 30}],
            "排除词未出现时片段仍为主词前后各 30 码点",
        )

    def test_snippet_uses_leftmost_main_hit_when_main_repeats(self) -> None:
        self._write_text("a.txt", "budget target target draft\ntarget target\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", NOT_KEYWORD, "draft", CONTEXT_CHARS, "4"),
            "主词最左侧命中",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 2, "snippet": "target tar"}],
            "第 1 行被排除；第 2 行片段以最左侧命中为中心，该行仍只一项",
        )

    # -- 6. 逐行模式：每个合格行各一项，行内重复不增加结果 -----------------

    def test_all_lines_returns_each_qualified_line_once(self) -> None:
        self._write_text(
            "a.txt",
            "target draft\n"
            "target\n"
            "draft\n"
            "target target draft draft\n"
            "target target\n",
        )

        results = self.assert_success_clean(
            run_args(self.root, "target", NOT_KEYWORD, "draft", ALL_LINES,
                     CONTEXT_CHARS, "0"),
            "逐行合格行",
        )
        self.assertEqual(
            results,
            [
                {"path": "a.txt", "line": 2, "snippet": "target"},
                {"path": "a.txt", "line": 5, "snippet": "target"},
            ],
            "含排除词的行被跳过；第 5 行主词重复出现仍只一项",
        )

    # -- 7. 多文件：排序与结果字段沿用原约定 -------------------------------

    def test_results_sorted_by_path_then_line_with_three_fields(self) -> None:
        self._write_text("b.txt", "target b\n")
        self._write_text("a.txt", "target a1 draft\ntarget a2\n")
        self._write_text("A.md", "target A\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", NOT_KEYWORD, "draft", ALL_LINES,
                     CONTEXT_CHARS, "0"),
            "多文件排序",
        )
        self.assertEqual(
            [(item["path"], item["line"]) for item in results],
            [("A.md", 1), ("a.txt", 2), ("b.txt", 1)],
        )
        for item in results:
            self.assertEqual(set(item.keys()), {"path", "line", "snippet"})

    # -- 8. 字面边界：选项记号出现在取值或位置参数中仍按文本处理 -----------

    def test_not_keyword_value_equal_to_flag_name_is_literal_text(self) -> None:
        self._write_text("cfg.txt", "target --all-lines\ntarget plain\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", NOT_KEYWORD, ALL_LINES),
            "排除值恰为开关名",
        )
        self.assertEqual(
            results,
            [{"path": "cfg.txt", "line": 2, "snippet": "target plain"}],
            "--all-lines 被当作排除词文本消费，逐行开关未开启",
        )

    def test_positional_keyword_equal_to_option_name_stays_literal(self) -> None:
        self._write_text("a.txt", "--not-keyword here\ntarget\n")

        results = self.assert_success_clean(
            run_args(self.root, NOT_KEYWORD), "关键词恰为选项名"
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "--not-keyword here"}],
            "第二个位置参数即使写作 --not-keyword 也仍是关键词",
        )

    def test_path_option_value_equal_to_option_name_stays_literal(self) -> None:
        self._write_text("--not-keyword/a.txt", "target\n")
        self._write_text("other/b.txt", "target\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", PATH_OPTION, NOT_KEYWORD),
            "路径片段恰为选项名",
        )
        self.assertEqual(
            results,
            [{"path": "--not-keyword/a.txt", "line": 1, "snippet": "target"}],
            "--path-contains 的取值 --not-keyword 是字面片段而非选项",
        )

    # -- 9. 与既有选项任意排序组合；路径与格式筛选继续生效 -----------------

    def test_combines_with_other_options_in_any_order(self) -> None:
        self._write_text("notes/a.txt", "Target Budget Draft\nTarget Budget\n")
        self._write_text("notes/b.md", "Target Budget\n")

        argv_orders = (
            ("TARGET", NOT_KEYWORD, "DRAFT", IGNORE_CASE, CONTEXT_CHARS, "0",
             PATH_OPTION, "notes/", FILE_TYPE, "txt"),
            ("TARGET", FILE_TYPE, "txt", PATH_OPTION, "notes/", IGNORE_CASE,
             NOT_KEYWORD, "DRAFT", CONTEXT_CHARS, "0"),
            ("TARGET", IGNORE_CASE, CONTEXT_CHARS, "0", NOT_KEYWORD, "DRAFT",
             FILE_TYPE, "txt", PATH_OPTION, "notes/"),
        )
        for argv in argv_orders:
            with self.subTest(argv=argv):
                results = self.assert_success_clean(run_args(self.root, *argv), "任意排序")
                self.assertEqual(
                    results,
                    [{"path": "notes/a.txt", "line": 2, "snippet": "Target"}],
                    "选项任意排序结果一致；第 1 行被排除词排除，格式筛选排除 b.md",
                )

    def test_filtered_out_broken_file_is_not_read_or_warned(self) -> None:
        self._write("broken.txt", b"target\n\xff\n")
        self._write_text("ok.md", "target\n")

        proc = run_args(self.root, "target", NOT_KEYWORD, "draft", FILE_TYPE, "md")
        results = self.assert_success_clean(proc, "被过滤坏文件")
        self.assertEqual(
            results,
            [{"path": "ok.md", "line": 1, "snippet": "target"}],
            "被格式条件排除的坏文件不读取、不告警",
        )

    # -- 10. 选中文件无法解码：整文件告警跳过，其余继续，退出码 0 ----------

    def test_broken_selected_file_warned_and_skipped(self) -> None:
        self._write("broken.txt", b"target before\n\xff\ntarget after\n")
        self._write_text("ok.txt", "target ok\n")

        proc = run_args(self.root, "target", NOT_KEYWORD, "draft", CONTEXT_CHARS, "0")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8")),
            [{"path": "ok.txt", "line": 1, "snippet": "target"}],
            "坏文件的合格行一律不返回；其余文件照常",
        )
        stderr = proc.stderr.decode("utf-8")
        self.assertIn("broken.txt", stderr)
        self.assertIn("无法按 UTF-8 解码", stderr)

    # -- 11. 参数错误：缺值、重复、空值、全空白 → 扫描前退出 2 -------------

    def test_missing_value_is_argument_error(self) -> None:
        proc = run_args(self.root, "target", NOT_KEYWORD)
        self.assert_argument_error(proc, "缺少值", "缺值")

    def test_duplicate_option_is_argument_error(self) -> None:
        proc = run_args(self.root, "target", NOT_KEYWORD, "a", NOT_KEYWORD, "b")
        self.assert_argument_error(proc, "只能指定一次", "重复指定")

    def test_empty_or_blank_value_is_argument_error(self) -> None:
        for raw in ("", " ", "   ", "\t"):
            with self.subTest(raw=raw):
                proc = run_args(self.root, "target", NOT_KEYWORD, raw)
                self.assert_argument_error(proc, "为空或全为空白", f"空白值 {raw!r}")

    def test_argument_errors_happen_before_any_scan(self) -> None:
        # 目录不存在时，--not-keyword 的参数错误仍优先报出（扫描前结束）。
        proc = run_args(Path("不存在的目录"), "target", NOT_KEYWORD, "  ")
        self.assert_argument_error(proc, "为空或全为空白", "扫描前报错")

    # -- 12. 未提供新选项时既有行为不变；排除词相同或无合格行返回 [] -------

    def test_without_option_behavior_is_unchanged(self) -> None:
        self._write_acceptance_sample()

        results = self.assert_success_clean(
            run_args(self.root, "target", IGNORE_CASE, CONTEXT_CHARS, "0"),
            "无新选项",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "Target"}],
            "未指定 --not-keyword 时仍只按主关键词返回首个命中",
        )

    def test_not_keyword_equal_to_main_keyword_returns_empty_array(self) -> None:
        self._write_acceptance_sample()
        proc = run_args(self.root, "target", NOT_KEYWORD, "target", IGNORE_CASE)
        results = self.assert_success_clean(proc, "排除词同主关键词")
        self.assertEqual(results, [])
        self.assertEqual(proc.stdout.decode("utf-8").strip(), "[]")

    def test_no_qualified_line_returns_empty_array(self) -> None:
        self._write_text("a.txt", "target draft\ntarget draft again\n")
        proc = run_args(self.root, "target", NOT_KEYWORD, "draft")
        results = self.assert_success_clean(proc, "无合格行")
        self.assertEqual(results, [])
        self.assertEqual(proc.stdout.decode("utf-8").strip(), "[]")

    # -- 13. 查询只读源文件且不留下索引 -------------------------------------

    def test_query_is_read_only_and_leaves_no_index(self) -> None:
        self._write_text("keep.txt", "target draft\ntarget\n")
        before = self._snapshot_files()
        proc = run_args(self.root, "target", NOT_KEYWORD, "draft")
        after = self._snapshot_files()
        self.assertEqual(before, after, "查询后源文件字节不得变化，且不得新增任何文件/索引")
        self.assert_success_clean(proc, "只读/无索引")


if __name__ == "__main__":
    unittest.main()

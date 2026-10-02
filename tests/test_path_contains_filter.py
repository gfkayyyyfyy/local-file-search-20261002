"""``--path-contains`` 路径筛选的端到端回归测试。

通过命令入口 ``python -m local_search <目录> <关键词> --path-contains <片段>``
固定从命令行输入到 JSON 结果输出的既有行为：

- 筛选基于**相对于选定目录、统一使用正斜杠**的路径，区分大小写、按**字面子串**
  匹配；选定目录本身的名称不参与匹配；
- 片段前后的空格不被裁剪，不解释正则表达式或通配符；以连字符开头的取值仍是
  字面值，而不是选项；
- 没有路径符合时输出 ``[]``，退出码 0，标准错误为空；
- 被筛选排除的文件既不读取也不做 UTF-8 校验，因此不产生解码告警；被选中且
  含非法 UTF-8 字节的文件输出 ``[]``，在标准错误报告其相对路径，退出码仍为 0；
- 不指定筛选时返回全部合法命中（按路径字典序），仅为无法解码的文件告警；
- 输入边界错误（缺值、空值/纯空白、重复选项、无法识别的尾随参数）一律退出码 2、
  标准输出为空、原因写入标准错误；
- 每项结果仍只含 ``path``、``line``、``snippet``，保留首个单行命中及前后各至多
  30 个 Unicode 字符的片段语义；查询只读源文件且不留下索引。

所有资料由测试在 TemporaryDirectory 中独立准备并自动清理（目录名刻意包含中文与
空格），仅使用 Python 标准库、离线运行。期望值全部以字面量直接写出，不调用任何
被测函数来生成期望结果。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

KEYWORD = "target"
PATH_OPTION = "--path-contains"
# 选定目录的名称刻意同时包含中文与空格；该名称不应参与路径筛选匹配。
ROOT_NAME = "资料 目录"


def run_args(directory: Path, *argv: str) -> subprocess.CompletedProcess:
    """以任意命令行参数调用 python -m local_search，返回完成的子进程。"""
    return subprocess.run(
        [sys.executable, "-m", "local_search", str(directory), *argv],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )


def run_filter(directory: Path, fragment: str, keyword: str = KEYWORD) -> subprocess.CompletedProcess:
    """目录 + 关键词 + 单次 --path-contains <片段> 的标准调用。"""
    return run_args(directory, keyword, PATH_OPTION, fragment)


class PathContainsFilterTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # 临时根目录下再放一层“资料 目录”，真正作为检索的选定目录。
        self.root = Path(self._tmp.name) / ROOT_NAME
        self.root.mkdir(parents=True)

        (self.root / "a.txt").write_bytes(b"target root\n")
        (self.root / "notes").mkdir()
        (self.root / "notes" / "b.MD").write_bytes(b"alpha\ntarget note\n")
        (self.root / "Notes").mkdir()
        (self.root / "Notes" / "c.txt").write_bytes(b"target upper\n")
        (self.root / "outside").mkdir()
        # 含非法 UTF-8 字节 0xff；被筛选排除时不应产生任何告警。
        (self.root / "outside" / "broken.txt").write_bytes(b"target broken\n\xff\n")

    # -- 辅助 ------------------------------------------------------------

    def _write(self, rel: str, data: bytes) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

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
        self.assertIsInstance(item["snippet"], str)
        self.assertNotIn("\n", item["snippet"], f"{label}: 片段不得包含换行符")
        self.assertNotIn("\r", item["snippet"], f"{label}: 片段不得包含回车符")

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

    # -- 1. 小写片段 notes/：只命中 notes/b.MD 第 2 行 -------------------

    def test_lowercase_fragment_selects_only_lowercase_notes(self) -> None:
        proc = run_filter(self.root, "notes/")

        results = self.assert_success_clean(proc, "小写 notes/")
        self.assertEqual(
            results,
            [{"path": "notes/b.MD", "line": 2, "snippet": "target note"}],
            "notes/ 场景：应只返回 notes/b.MD 的第 2 行 target note",
        )
        self.assertEqual(len(results), 1)
        self.assert_item_shape(results[0], "notes/")
        # broken.txt 被排除：标准错误已在上面断言为空，即不产生解码告警。

    # -- 2. 大写片段 Notes/：只命中 Notes/c.txt 第 1 行 -------------------

    def test_uppercase_fragment_selects_only_uppercase_notes(self) -> None:
        proc = run_filter(self.root, "Notes/")

        results = self.assert_success_clean(proc, "大写 Notes/")
        self.assertEqual(
            results,
            [{"path": "Notes/c.txt", "line": 1, "snippet": "target upper"}],
            "Notes/ 场景：应只返回 Notes/c.txt 的第 1 行 target upper",
        )
        self.assertEqual(len(results), 1)
        self.assert_item_shape(results[0], "Notes/")

    # -- 3. 区分大小写：两个 notes 目录只按片段大小写择一 -----------------

    def test_matching_is_case_sensitive(self) -> None:
        # 片段 "notes/" 不得带入 Notes/c.txt；片段 "Notes/" 不得带入 notes/b.MD。
        lower = self.assert_success_clean(run_filter(self.root, "notes/"), "小写")
        upper = self.assert_success_clean(run_filter(self.root, "Notes/"), "大写")
        self.assertEqual([item["path"] for item in lower], ["notes/b.MD"])
        self.assertEqual([item["path"] for item in upper], ["Notes/c.txt"])
        # 大小写都不与另一目录匹配：片段 "NOTES/" 一个路径都选不中。
        neither = self.assert_success_clean(run_filter(self.root, "NOTES/"), "全大写")
        self.assertEqual(neither, [])

    # -- 4. 选定目录本身的名称不参与匹配 ---------------------------------

    def test_selected_directory_name_does_not_participate(self) -> None:
        # 若错误地把选定目录名拼进匹配串，含“资料/目录”的片段会选中全部文件。
        for fragment in ("资料", "目录", ROOT_NAME):
            with self.subTest(fragment=fragment):
                results = self.assert_success_clean(
                    run_filter(self.root, fragment), f"目录名片段 {fragment!r}"
                )
                self.assertEqual(results, [], "选定目录的名称不得参与路径子串匹配")

    # -- 5. 基于正斜杠相对路径：反斜杠不匹配 ------------------------------

    def test_matching_uses_forward_slash_relative_path(self) -> None:
        ok = self.assert_success_clean(run_filter(self.root, "notes/"), "正斜杠")
        self.assertEqual([item["path"] for item in ok], ["notes/b.MD"])
        # 同样的片段用反斜杠书写，在任何相对路径中都不是字面子串。
        backslash = self.assert_success_clean(run_filter(self.root, "notes\\"), "反斜杠")
        self.assertEqual(backslash, [])

    # -- 6. 前后空格不被裁剪 ---------------------------------------------

    def test_leading_and_trailing_spaces_are_not_trimmed(self) -> None:
        for fragment in (" notes/", "notes/ ", "  notes/  "):
            with self.subTest(fragment=fragment):
                results = self.assert_success_clean(
                    run_filter(self.root, fragment), f"带空格片段 {fragment!r}"
                )
                self.assertEqual(results, [], "片段前后空格必须按字面保留，不应被裁剪后命中")

    # -- 7. 不解释正则或通配符，一律字面子串 ------------------------------

    def test_regex_and_glob_metacharacters_are_literal(self) -> None:
        # 正则语义下 "notes?" 等价于 "note" + 可选 s，会命中 notes/b.MD；
        # 字面子串语义下没有任何路径包含问号，必须返回 []。
        regex_like = self.assert_success_clean(run_filter(self.root, "notes?"), "正则 notes?")
        self.assertEqual(regex_like, [])
        # "note." 若按正则会匹配 "notes"；字面语义下句点必须真实存在。
        regex_dot = self.assert_success_clean(run_filter(self.root, "note."), "正则 note.")
        self.assertEqual(regex_dot, [])
        # 通配符 "*" 只按字面星号匹配，任何路径都不含星号。
        glob_star = self.assert_success_clean(run_filter(self.root, "*.txt"), "通配 *.txt")
        self.assertEqual(glob_star, [])
        # 反证：真实存在的字面子串 ".MD" 仍能正常命中，说明并非“一律空结果”。
        literal = self.assert_success_clean(run_filter(self.root, ".MD"), "字面 .MD")
        self.assertEqual([item["path"] for item in literal], ["notes/b.MD"])

    # -- 8. 取值以连字符开头时仍作为字面片段 ------------------------------

    def test_value_starting_with_hyphen_is_a_literal_fragment(self) -> None:
        # 准备一个文件名本身以连字符开头、且夹在连字符之间的命中文件。
        self._write("notes/-dash-.txt", b"target dash\n")

        # 能在真实路径中匹配到：证明 "-dash-" 被当作取值而非未知选项（否则退出 2）。
        hit = self.assert_success_clean(run_filter(self.root, "-dash-"), "连字符取值命中")
        self.assertEqual(
            hit,
            [{"path": "notes/-dash-.txt", "line": 1, "snippet": "target dash"}],
        )
        # 匹配不到时同样正常返回 []、退出 0，而不是把它当成选项报错。
        miss = self.assert_success_clean(run_filter(self.root, "-no-such-frag"), "连字符取值落空")
        self.assertEqual(miss, [])

    # -- 9. 没有路径符合：[]、退出 0、标准错误为空 ------------------------

    def test_no_matching_path_returns_empty_array_clean(self) -> None:
        proc = run_filter(self.root, "nope/")
        results = self.assert_success_clean(proc, "无路径符合")
        self.assertEqual(results, [])
        self.assertEqual(proc.stdout.decode("utf-8").strip(), "[]")

    # -- 10. 选中含非法 UTF-8 的文件：[] 且标准错误报告其相对路径 ---------

    def test_selected_broken_file_warns_but_still_exits_zero(self) -> None:
        proc = run_filter(self.root, "outside/")
        self.assertEqual(proc.returncode, 0, "选中坏文件时退出码仍应为 0")
        self.assertEqual(
            proc.stdout.decode("utf-8").strip(), "[]", "唯一候选文件被跳过时应输出 []"
        )
        stderr = proc.stderr.decode("utf-8")
        self.assertIn("outside/broken.txt", stderr, "告警必须报告正斜杠相对路径")
        self.assertIn("无法按 UTF-8 解码", stderr, "告警必须说明无法按 UTF-8 解码")

    def test_broken_file_selected_by_its_own_name_warns(self) -> None:
        # 用文件名片段选中 broken.txt，结论与上一场景一致。
        proc = run_filter(self.root, "broken")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.decode("utf-8").strip(), "[]")
        stderr = proc.stderr.decode("utf-8")
        self.assertIn("outside/broken.txt", stderr)
        self.assertIn("无法按 UTF-8 解码", stderr)

    # -- 11. 被排除的坏文件不产生解码告警（已在场景 1 断言，此处独立固化） -

    def test_excluded_broken_file_produces_no_warning(self) -> None:
        proc = run_filter(self.root, "notes/")
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn(b"broken.txt", proc.stderr, "被排除的文件不得出现在告警中")
        self.assertEqual(proc.stderr, b"")

    # -- 12. 不指定筛选：三个合法文件全部命中并按路径字典序 ---------------

    def test_no_filter_returns_all_valid_hits_sorted_and_warns_broken_only(self) -> None:
        proc = run_args(self.root, KEYWORD)
        self.assertEqual(proc.returncode, 0)

        results = json.loads(proc.stdout.decode("utf-8"))
        expected = [
            {"path": "Notes/c.txt", "line": 1, "snippet": "target upper"},
            {"path": "a.txt", "line": 1, "snippet": "target root"},
            {"path": "notes/b.MD", "line": 2, "snippet": "target note"},
        ]
        self.assertEqual(results, expected, "无筛选时应返回三个合法命中，按路径码点序排列")
        for item in results:
            self.assert_item_shape(item, "无筛选")

        stderr = proc.stderr.decode("utf-8")
        warning_lines = [line for line in stderr.splitlines() if line.strip()]
        self.assertEqual(len(warning_lines), 1, "只能为 broken.txt 产生一条告警")
        self.assertIn("outside/broken.txt", warning_lines[0])
        self.assertIn("无法按 UTF-8 解码", warning_lines[0])

    # -- 13. 带筛选的查询同样只读源文件且不留下索引 -----------------------

    def test_filtered_query_is_read_only_and_leaves_no_index(self) -> None:
        before = self._snapshot_files()
        proc = run_filter(self.root, "notes/")
        after = self._snapshot_files()

        self.assertEqual(before, after, "查询后源文件字节不得变化，且不得新增任何文件/索引")
        results = self.assert_success_clean(proc, "只读/无索引")
        self.assertEqual(
            results,
            [{"path": "notes/b.MD", "line": 2, "snippet": "target note"}],
        )

    # -- 14. 输入边界：缺值、空值/纯空白、重复选项、尾随杂参 ---------------

    def test_missing_value_is_argument_error(self) -> None:
        proc = run_args(self.root, KEYWORD, PATH_OPTION)
        self.assert_argument_error(proc, "缺少值", "缺少取值")
        self.assertIn(PATH_OPTION, proc.stderr.decode("utf-8"))

    def test_empty_value_is_argument_error(self) -> None:
        proc = run_args(self.root, KEYWORD, PATH_OPTION, "")
        self.assert_argument_error(proc, "为空或全为空白", "空字符串取值")

    def test_whitespace_only_value_is_argument_error(self) -> None:
        for value in ("   ", "\t", " \t "):
            with self.subTest(value=value):
                proc = run_args(self.root, KEYWORD, PATH_OPTION, value)
                self.assert_argument_error(proc, "为空或全为空白", f"纯空白取值 {value!r}")

    def test_duplicate_option_is_argument_error(self) -> None:
        proc = run_args(self.root, KEYWORD, PATH_OPTION, "notes/", PATH_OPTION, "Notes/")
        self.assert_argument_error(proc, "只能指定一次", "重复选项")

    def test_unrecognized_trailing_arguments_are_argument_errors(self) -> None:
        cases = [
            ([PATH_OPTION, "notes/", "trailing"], "trailing"),
            (["extra-positional"], "extra-positional"),
            (["--bogus"], "--bogus"),
        ]
        for extra, token in cases:
            with self.subTest(extra=extra):
                proc = run_args(self.root, KEYWORD, *extra)
                self.assert_argument_error(proc, "无法识别的参数", f"尾随参数 {token!r}")
                self.assertIn(token, proc.stderr.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()

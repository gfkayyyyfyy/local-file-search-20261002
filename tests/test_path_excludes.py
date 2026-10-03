"""``--path-excludes`` 路径片段排除选项的端到端回归测试。

通过命令入口 ``python -m local_search <目录> <关键词> --path-excludes <片段>``
固定从命令行输入到 JSON 结果输出的行为：

- 排除基于**相对于选定目录、统一使用正斜杠**的路径，区分大小写、按连续
  **字面子串**匹配；不解释正则或通配符，不受 ``--ignore-case`` 影响；选定
  目录本身的名称不参与匹配，片段首尾空格原样比较；
- 被排除的文件**不读取内容**，因此既不会产生读取告警，也不会因非法 UTF-8
  字节产生解码告警；
- 与 ``--path-contains`` 同用时，文件须符合包含条件且不符合排除条件才参与
  内容检索；两个选项可与其他选项以任意先后顺序同用；
- 取值即使看似开关（如 ``--ignore-case``）也按字面片段处理，不启用开关；
  第二个位置参数即使恰好写作 ``--path-excludes`` 也仍是关键词；
- 缺值、重复指定、值为空或全为空白时在扫描前退出码 2、标准输出为空、标准
  错误指出选项名称及原因；
- 有效筛选后无内容命中时输出 ``[]``、退出码 0、标准错误为空；
- 保留的候选文件无法完整解码为 UTF-8 时仍整文件跳过并向标准错误告警，其余
  文件正常检索，退出码为 0。

所有资料由测试在 TemporaryDirectory 中独立准备并自动清理（目录名刻意包含
中文与空格），仅使用 Python 标准库、离线运行。期望值全部以字面量直接写出，
不调用任何被测函数来生成期望结果。
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
CONTAINS_OPTION = "--path-contains"
EXCLUDES_OPTION = "--path-excludes"
IGNORE_CASE_OPTION = "--ignore-case"
ALL_LINES_OPTION = "--all-lines"
CONTEXT_CHARS_OPTION = "--context-chars"
# 选定目录的名称刻意同时包含中文与空格；该名称不应参与路径片段匹配。
ROOT_NAME = "资料 目录"


def run_args(directory: Path, *argv: str) -> subprocess.CompletedProcess:
    """以任意命令行参数调用 python -m local_search，返回完成的子进程。"""
    return subprocess.run(
        [sys.executable, "-m", "local_search", str(directory), *argv],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )


class PathExcludesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # 临时根目录下再放一层“资料 目录”，真正作为检索的选定目录。
        self.root = Path(self._tmp.name) / ROOT_NAME
        self.root.mkdir(parents=True)

        (self.root / "a.txt").write_bytes(b"target root\n")
        (self.root / "notes").mkdir()
        (self.root / "notes" / "b.md").write_bytes(
            b"Target note\nother\ntarget later\n"
        )
        (self.root / "Notes").mkdir()
        (self.root / "Notes" / "upper.txt").write_bytes(b"target upper\n")
        (self.root / "notes" / "private").mkdir(parents=True)
        # 仅含非法 UTF-8 字节 0xff；被排除时不应产生任何告警。
        (self.root / "notes" / "private" / "c.txt").write_bytes(b"\xff")

    # -- 辅助 ------------------------------------------------------------

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
        self.assertIn(EXCLUDES_OPTION, stderr, f"{label}: 标准错误应指出选项名称，实际 {stderr!r}")
        self.assertIn(reason, stderr, f"{label}: 标准错误应说明原因 {reason!r}，实际 {stderr!r}")

    def _snapshot_files(self) -> dict[str, bytes]:
        return {
            str(p.relative_to(self.root)).replace(os.sep, "/"): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    # -- 1. 验收场景：contains notes/ 且 excludes private/，--all-lines ----

    def test_acceptance_contains_notes_excludes_private_all_lines(self) -> None:
        proc = run_args(
            self.root,
            "TARGET",
            IGNORE_CASE_OPTION,
            ALL_LINES_OPTION,
            CONTAINS_OPTION,
            "notes/",
            EXCLUDES_OPTION,
            "private/",
            CONTEXT_CHARS_OPTION,
            "0",
        )
        results = self.assert_success_clean(proc, "验收场景")
        self.assertEqual(
            results,
            [
                {"path": "notes/b.md", "line": 1, "snippet": "Target"},
                {"path": "notes/b.md", "line": 3, "snippet": "target"},
            ],
        )

    # -- 2. 单独排除：private/ 下的坏文件不读取、不告警 -------------------

    def test_excludes_alone_skips_excluded_without_warning(self) -> None:
        proc = run_args(
            self.root, "TARGET", IGNORE_CASE_OPTION, ALL_LINES_OPTION,
            CONTEXT_CHARS_OPTION, "0", EXCLUDES_OPTION, "private/",
        )
        results = self.assert_success_clean(proc, "单独排除 private/")
        self.assertEqual(
            results,
            [
                {"path": "Notes/upper.txt", "line": 1, "snippet": "target"},
                {"path": "a.txt", "line": 1, "snippet": "target"},
                {"path": "notes/b.md", "line": 1, "snippet": "Target"},
                {"path": "notes/b.md", "line": 3, "snippet": "target"},
            ],
            "排除 private/ 后返回其余文件的全部命中，按路径码点序与行号排列",
        )

    # -- 3. contains 与 excludes 组合：交集语义 ---------------------------

    def test_contains_and_excludes_combined_in_any_order(self) -> None:
        # 选项任意排序，结论一致：只保留 notes/ 下且不含 "b.md" 的路径——
        # notes/ 下仅剩坏文件 private/c.txt，又被解码跳过，故输出 [] 且告警。
        for argv in (
            (KEYWORD, CONTAINS_OPTION, "notes/", EXCLUDES_OPTION, "b.md"),
            (KEYWORD, EXCLUDES_OPTION, "b.md", CONTAINS_OPTION, "notes/"),
            (
                KEYWORD,
                IGNORE_CASE_OPTION,
                EXCLUDES_OPTION,
                "b.md",
                CONTEXT_CHARS_OPTION,
                "0",
                CONTAINS_OPTION,
                "notes/",
            ),
        ):
            with self.subTest(argv=argv):
                proc = run_args(self.root, *argv)
                self.assertEqual(proc.returncode, 0)
                self.assertEqual(proc.stdout.decode("utf-8").strip(), "[]")
                stderr = proc.stderr.decode("utf-8")
                self.assertIn("notes/private/c.txt", stderr)
                self.assertIn("无法按 UTF-8 解码", stderr)

    def test_contains_and_excludes_overlap_excludes_everything(self) -> None:
        # 包含片段与排除片段相同：没有任何文件能同时满足两个条件。
        proc = run_args(
            self.root, KEYWORD, CONTAINS_OPTION, "notes/", EXCLUDES_OPTION, "notes/"
        )
        results = self.assert_success_clean(proc, "包含与排除片段相同")
        self.assertEqual(results, [])

    # -- 4. 排除匹配区分大小写，不受 --ignore-case 影响 -------------------

    def test_excludes_matching_is_case_sensitive(self) -> None:
        # 片段 "private/" 排除小写 private/c.txt：无解码告警。
        lower = run_args(
            self.root, KEYWORD, IGNORE_CASE_OPTION, EXCLUDES_OPTION, "private/"
        )
        self.assertEqual(lower.returncode, 0)
        self.assertEqual(lower.stderr, b"", "private/ 必须排除坏文件，不得告警")
        # 片段 "PRIVATE/" 一个路径都不包含：坏文件保留为候选，产生解码告警。
        upper = run_args(
            self.root, KEYWORD, IGNORE_CASE_OPTION, EXCLUDES_OPTION, "PRIVATE/"
        )
        self.assertEqual(upper.returncode, 0)
        self.assertIn("notes/private/c.txt", upper.stderr.decode("utf-8"))

    # -- 5. 排除按目录名大小写区分 notes/ 与 Notes/ -----------------------

    def test_excludes_distinguishes_notes_case(self) -> None:
        proc = run_args(
            self.root,
            "TARGET",
            IGNORE_CASE_OPTION,
            ALL_LINES_OPTION,
            CONTEXT_CHARS_OPTION,
            "0",
            EXCLUDES_OPTION,
            "notes/",
        )
        results = self.assert_success_clean(proc, "排除小写 notes/")
        self.assertEqual(
            results,
            [
                {"path": "Notes/upper.txt", "line": 1, "snippet": "target"},
                {"path": "a.txt", "line": 1, "snippet": "target"},
            ],
        )

    # -- 6. 选定目录名称不参与排除匹配 -----------------------------------

    def test_selected_directory_name_does_not_participate(self) -> None:
        # 若错误地把选定目录名拼进比较串，含“资料”的片段会排除全部文件。
        for fragment in ("资料", "目录", ROOT_NAME):
            with self.subTest(fragment=fragment):
                proc = run_args(self.root, KEYWORD, EXCLUDES_OPTION, fragment)
                self.assertEqual(proc.returncode, 0)
                # c.txt 未被排除：应出现其解码告警，且其他文件正常命中。
                stderr = proc.stderr.decode("utf-8")
                self.assertIn("notes/private/c.txt", stderr)
                results = json.loads(proc.stdout.decode("utf-8"))
                self.assertEqual(
                    [item["path"] for item in results],
                    ["Notes/upper.txt", "a.txt", "notes/b.md"],
                )

    # -- 7. 正斜杠相对路径：反斜杠片段不排除任何文件 ----------------------

    def test_matching_uses_forward_slash_relative_path(self) -> None:
        ok = run_args(self.root, KEYWORD, EXCLUDES_OPTION, "private/")
        self.assertEqual(ok.returncode, 0)
        self.assertEqual(ok.stderr, b"", "正斜杠片段必须排除坏文件")
        backslash = run_args(self.root, KEYWORD, EXCLUDES_OPTION, "private\\")
        self.assertEqual(backslash.returncode, 0)
        self.assertIn(
            "notes/private/c.txt",
            backslash.stderr.decode("utf-8"),
            "反斜杠片段不是任何相对路径的子串，坏文件应仍参与检索并告警",
        )

    # -- 8. 首尾空格原样比较，不裁剪 -------------------------------------

    def test_leading_and_trailing_spaces_are_not_trimmed(self) -> None:
        for fragment in (" private/", "private/ ", "  private/  "):
            with self.subTest(fragment=fragment):
                proc = run_args(self.root, KEYWORD, EXCLUDES_OPTION, fragment)
                self.assertEqual(proc.returncode, 0)
                self.assertIn(
                    "notes/private/c.txt",
                    proc.stderr.decode("utf-8"),
                    f"片段 {fragment!r} 的首尾空格必须按字面保留，坏文件不应被排除",
                )

    # -- 9. 不解释正则或通配符 -------------------------------------------

    def test_regex_and_glob_metacharacters_are_literal(self) -> None:
        # "private?" 按正则可匹配 private；字面子串语义下不排除任何文件。
        for fragment in ("private?", "c.txt?", "*.txt"):
            with self.subTest(fragment=fragment):
                proc = run_args(self.root, KEYWORD, EXCLUDES_OPTION, fragment)
                self.assertEqual(proc.returncode, 0)
                self.assertIn("notes/private/c.txt", proc.stderr.decode("utf-8"))
        # 反证：真实存在的字面子串 "c.txt" 能排除坏文件，stderr 为空。
        literal = run_args(self.root, KEYWORD, EXCLUDES_OPTION, "c.txt")
        self.assertEqual(literal.returncode, 0)
        self.assertEqual(literal.stderr, b"")

    # -- 10. 取值看似开关时仍按字面片段处理，不启用开关 -------------------

    def test_value_looking_like_switch_is_literal_fragment(self) -> None:
        # --path-excludes 的取值是字符串 "--ignore-case"：不启用忽略大小写，
        # 也不排除任何真实路径，因此大写 TARGET 在区分大小写时无命中，
        # 坏文件仍参与检索并告警。
        proc = run_args(self.root, "TARGET", EXCLUDES_OPTION, IGNORE_CASE_OPTION)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.decode("utf-8").strip(), "[]")
        self.assertIn("notes/private/c.txt", proc.stderr.decode("utf-8"))

        # 取值为 "--all-lines" 同理：不开启多行模式。
        proc2 = run_args(self.root, "target", EXCLUDES_OPTION, ALL_LINES_OPTION)
        self.assertEqual(proc2.returncode, 0)
        results = json.loads(proc2.stdout.decode("utf-8"))
        self.assertEqual(
            results,
            [
                {"path": "Notes/upper.txt", "line": 1, "snippet": "target upper"},
                {"path": "a.txt", "line": 1, "snippet": "target root"},
                # b.md 有两行命中，若误开 --all-lines 会多出第 3 行。
                {"path": "notes/b.md", "line": 3, "snippet": "target later"},
            ],
        )

    # -- 11. 第二个位置参数恰为新选项名时仍是字面关键词 -------------------

    def test_keyword_equal_to_option_name_is_literal(self) -> None:
        proc = run_args(self.root, EXCLUDES_OPTION)
        # 没有文件包含字面文本 "--path-excludes"：[]、退出 0、stderr 仅有
        # 坏文件的解码告警（证明已正常扫描而非把关键词当作选项）。
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.decode("utf-8").strip(), "[]")
        self.assertIn("notes/private/c.txt", proc.stderr.decode("utf-8"))

    # -- 12. --path-contains 的取值恰为新选项名时仍是字面片段 -------------

    def test_contains_value_equal_to_new_option_name_is_literal(self) -> None:
        proc = run_args(self.root, KEYWORD, CONTAINS_OPTION, EXCLUDES_OPTION)
        # 没有路径包含字面 "--path-excludes"：[]、退出 0、无告警（无候选）。
        results = self.assert_success_clean(proc, "contains 取值为新选项名")
        self.assertEqual(results, [])

    def test_excludes_value_equal_to_contains_name_consumes_literal(self) -> None:
        # --path-excludes 消费 "--path-contains" 作为字面值；随后的 notes/
        # 成为无法识别的尾随参数，退出 2。
        proc = run_args(
            self.root, KEYWORD, EXCLUDES_OPTION, CONTAINS_OPTION, "notes/"
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, b"")
        self.assertIn("无法识别的参数", proc.stderr.decode("utf-8"))
        self.assertIn("notes/", proc.stderr.decode("utf-8"))

    # -- 13. 有效筛选后无内容命中：[]、退出 0、标准错误为空 ---------------

    def test_filtered_no_content_hit_returns_empty_clean(self) -> None:
        # 排除片段命中坏文件（不告警），剩余文件中大写 TARGET 无命中。
        proc = run_args(self.root, "TARGET", EXCLUDES_OPTION, "private/")
        results = self.assert_success_clean(proc, "无内容命中")
        self.assertEqual(results, [])
        self.assertEqual(proc.stdout.decode("utf-8").strip(), "[]")

    # -- 14. 保留的候选坏文件仍整文件跳过并告警，退出 0 -------------------

    def test_retained_broken_file_warns_others_still_searched(self) -> None:
        # 排除 a.txt（合法文件），c.txt 仍是候选：c.txt 告警，b.md 正常命中。
        proc = run_args(self.root, KEYWORD, EXCLUDES_OPTION, "a.txt")
        self.assertEqual(proc.returncode, 0)
        results = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            results,
            [
                {"path": "Notes/upper.txt", "line": 1, "snippet": "target upper"},
                {"path": "notes/b.md", "line": 3, "snippet": "target later"},
            ],
        )
        stderr = proc.stderr.decode("utf-8")
        warning_lines = [line for line in stderr.splitlines() if line.strip()]
        self.assertEqual(len(warning_lines), 1)
        self.assertIn("notes/private/c.txt", warning_lines[0])
        self.assertIn("无法按 UTF-8 解码", warning_lines[0])

    # -- 15. 只读、不落盘 ------------------------------------------------

    def test_query_is_read_only_and_leaves_no_index(self) -> None:
        before = self._snapshot_files()
        proc = run_args(
            self.root,
            "TARGET",
            IGNORE_CASE_OPTION,
            ALL_LINES_OPTION,
            CONTAINS_OPTION,
            "notes/",
            EXCLUDES_OPTION,
            "private/",
        )
        after = self._snapshot_files()
        self.assertEqual(before, after, "查询后源文件字节不得变化，且不得新增任何文件")
        results = self.assert_success_clean(proc, "只读/无索引")
        self.assertEqual(
            results,
            [
                {"path": "notes/b.md", "line": 1, "snippet": "Target note"},
                {"path": "notes/b.md", "line": 3, "snippet": "target later"},
            ],
        )

    # -- 16. 输入边界：缺值、空值/纯空白、重复 ----------------------------

    def test_missing_value_is_argument_error(self) -> None:
        proc = run_args(self.root, KEYWORD, EXCLUDES_OPTION)
        self.assert_argument_error(proc, "缺少值", "缺少取值")

    def test_empty_value_is_argument_error(self) -> None:
        proc = run_args(self.root, KEYWORD, EXCLUDES_OPTION, "")
        self.assert_argument_error(proc, "为空或全为空白", "空字符串取值")

    def test_whitespace_only_value_is_argument_error(self) -> None:
        for value in ("   ", "\t", " \t "):
            with self.subTest(value=value):
                proc = run_args(self.root, KEYWORD, EXCLUDES_OPTION, value)
                self.assert_argument_error(proc, "为空或全为空白", f"纯空白取值 {value!r}")

    def test_duplicate_option_is_argument_error(self) -> None:
        proc = run_args(
            self.root, KEYWORD, EXCLUDES_OPTION, "a", EXCLUDES_OPTION, "b"
        )
        self.assert_argument_error(proc, "只能指定一次", "重复选项")

    def test_errors_occur_before_scanning(self) -> None:
        # 参数非法时即使目录不存在也只报参数错误，且标准输出为空、退出 2。
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "local_search",
                str(self.root / "不存在的子目录"),
                KEYWORD,
                EXCLUDES_OPTION,
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, b"")
        stderr = proc.stderr.decode("utf-8")
        self.assertIn(EXCLUDES_OPTION, stderr)
        self.assertIn("缺少值", stderr)


if __name__ == "__main__":
    unittest.main()

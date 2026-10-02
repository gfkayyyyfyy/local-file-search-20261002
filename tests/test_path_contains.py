"""``--path-contains`` 路径筛选的端到端回归测试。

通过命令入口 ``python -m local_search`` 核对标准输出 JSON、退出码与标准错误，
固定该选项从命令行输入到结果输出的既有行为：

- 筛选作用于使用正斜杠 ``/`` 的相对路径，区分大小写、按字面子串匹配，
  不解释正则或通配符，选定目录自身的名称不参与匹配；
- 取值前后空格不被裁剪；取值以连字符开头时仍作为字面片段；
- 被排除的文件既不读取也不校验（含非法 UTF-8 字节的文件不产生告警），
  被选中但无法解码的文件在标准错误告警、输出空数组、退出码仍为 0；
- 选项只允许出现在目录与关键词之后且只能指定一次；缺少取值、取值为空
  或全为空白、重复指定、出现无法识别的尾随参数均退出 2，标准输出为空；
- 不指定筛选时返回全部合法文件的命中，按路径字典序排列，只读源文件，
  不留下索引；每项结果仅含 ``path``、``line``、``snippet``。

测试资料在名称含中文和空格的临时目录（``资料 目录``）中准备并自动清理，
仅依赖标准库、离线运行。期望值全部以字面量直接写出，
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

# 各场景共用的关键词（与 README 公开约定一致的字面文本）。
KEYWORD = "target"


def run_search(argv: list) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "local_search", *argv],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )


class PathContainsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # 选定目录名称含中文和空格：验证目录名可正常作为命令行输入，
        # 且目录名本身不参与路径片段匹配。
        self.root = Path(self._tmp.name) / "资料 目录"
        (self.root / "notes").mkdir(parents=True)
        (self.root / "Notes").mkdir(parents=True)
        (self.root / "outside").mkdir(parents=True)
        (self.root / "a.txt").write_bytes(b"target root\n")
        (self.root / "notes" / "b.MD").write_bytes(b"alpha\ntarget note\n")
        (self.root / "Notes" / "c.txt").write_bytes(b"target upper\n")
        (self.root / "outside" / "broken.txt").write_bytes(b"target broken\n\xff\xff\n")

    def _run_filtered(self, *extra: str) -> subprocess.CompletedProcess:
        return run_search([str(self.root), KEYWORD, *extra])

    def assert_success_and_parse(self, proc: subprocess.CompletedProcess, label: str) -> list:
        """场景 label 下：退出码必须为 0、标准错误必须为空，返回解析后的 JSON。"""
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

    def assert_argument_error(
        self, proc: subprocess.CompletedProcess, label: str, reason: str
    ) -> None:
        """场景 label 下：退出码必须为 2、标准输出必须为空、标准错误说明原因。"""
        self.assertEqual(
            proc.returncode,
            2,
            f"{label}: 退出码应为 2，实际 {proc.returncode}，stderr={proc.stderr!r}",
        )
        self.assertEqual(
            proc.stdout,
            b"",
            f"{label}: 标准输出应为空，实际 {proc.stdout!r}",
        )
        stderr = proc.stderr.decode("utf-8")
        self.assertIn(reason, stderr, f"{label}: 标准错误应说明原因 {reason!r}，实际 {stderr!r}")

    def _snapshot_files(self) -> dict:
        return {
            str(p.relative_to(self.root)).replace(os.sep, "/"): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    # ------------------------------------------------------------------
    # 1. 筛选 notes/：只命中 notes/b.MD 第 2 行，大小写敏感故 Notes/ 不参与
    # ------------------------------------------------------------------
    def test_filter_notes_matches_only_lowercase_notes_file(self) -> None:
        results = self.assert_success_and_parse(
            self._run_filtered("--path-contains", "notes/"), "筛选 notes/"
        )

        self.assertEqual(
            results,
            [{"path": "notes/b.MD", "line": 2, "snippet": "target note"}],
            "筛选 notes/：应只返回 notes/b.MD 第 2 行的首个命中",
        )
        self.assertEqual(
            sorted(results[0].keys()),
            ["line", "path", "snippet"],
            "筛选 notes/：结果项只能包含 path、line、snippet 三个字段",
        )

    # ------------------------------------------------------------------
    # 2. 筛选 Notes/：只命中 Notes/c.txt 第 1 行（与小写 notes/ 互不匹配）
    # ------------------------------------------------------------------
    def test_filter_capital_notes_matches_only_uppercase_notes_file(self) -> None:
        results = self.assert_success_and_parse(
            self._run_filtered("--path-contains", "Notes/"), "筛选 Notes/"
        )

        self.assertEqual(
            results,
            [{"path": "Notes/c.txt", "line": 1, "snippet": "target upper"}],
            "筛选 Notes/：区分大小写，应只返回 Notes/c.txt 第 1 行",
        )

    # ------------------------------------------------------------------
    # 3. 被排除的 broken.txt 不产生解码告警（上面两场景已断言标准错误为空，
    #    这里显式固定：筛选掉 broken.txt 时标准错误必须没有任何输出）
    # ------------------------------------------------------------------
    def test_excluded_broken_file_produces_no_decode_warning(self) -> None:
        proc = self._run_filtered("--path-contains", "notes/")

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(
            proc.stderr,
            b"",
            "被路径筛选排除的 broken.txt 不应产生任何解码告警",
        )

    # ------------------------------------------------------------------
    # 4. 选中 broken.txt：输出 []，标准错误报告其相对路径与解码失败，退出 0
    # ------------------------------------------------------------------
    def test_selecting_broken_file_warns_and_returns_empty(self) -> None:
        proc = self._run_filtered("--path-contains", "broken")

        self.assertEqual(proc.returncode, 0, f"退出码应为 0，stderr={proc.stderr!r}")
        results = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(results, [], "选中 broken.txt：无法解码应被跳过，输出空数组")
        self.assertEqual(proc.stdout.decode("utf-8").strip(), "[]")
        stderr = proc.stderr.decode("utf-8")
        self.assertIn("outside/broken.txt", stderr, "告警应包含使用正斜杠的相对路径")
        self.assertIn("无法按 UTF-8 解码", stderr, "告警应说明无法按 UTF-8 解码")

    # ------------------------------------------------------------------
    # 5. 选定目录的名称不参与匹配：筛选“资料”命中不到任何相对路径
    # ------------------------------------------------------------------
    def test_root_directory_name_does_not_participate_in_matching(self) -> None:
        results = self.assert_success_and_parse(
            self._run_filtered("--path-contains", "资料"), "目录名不参与匹配"
        )

        self.assertEqual(
            results,
            [],
            "筛选“资料”：匹配只针对相对路径，选定目录名“资料 目录”不参与，应为空数组",
        )

    # ------------------------------------------------------------------
    # 6. 取值前后空格不被裁剪：带空格的片段按原样匹配，命中不到任何路径
    # ------------------------------------------------------------------
    def test_filter_value_surrounding_spaces_are_not_trimmed(self) -> None:
        for value in (" notes/", "notes/ "):
            with self.subTest(value=value):
                results = self.assert_success_and_parse(
                    self._run_filtered("--path-contains", value), f"取值 {value!r}"
                )
                self.assertEqual(
                    results,
                    [],
                    f"取值 {value!r}：前后空格不裁剪，相对路径中不存在该片段，应为空数组",
                )

    # ------------------------------------------------------------------
    # 7. 字面子串匹配：不解释正则元字符，也不解释通配符
    # ------------------------------------------------------------------
    def test_filter_is_literal_substring_not_regex_or_glob(self) -> None:
        for value in ("n.tes/", "notes/*", "notes/[bc]"):
            with self.subTest(value=value):
                results = self.assert_success_and_parse(
                    self._run_filtered("--path-contains", value), f"字面取值 {value!r}"
                )
                self.assertEqual(
                    results,
                    [],
                    f"取值 {value!r}：应按字面文本匹配，正则/通配符不生效，应为空数组",
                )

    # ------------------------------------------------------------------
    # 8. 没有任何路径符合：输出 []，退出码 0，标准错误为空
    # ------------------------------------------------------------------
    def test_no_path_matches_returns_empty_array(self) -> None:
        proc = self._run_filtered("--path-contains", "missing/")

        results = self.assert_success_and_parse(proc, "无路径符合")
        self.assertEqual(results, [], "无路径符合：应输出空数组")
        self.assertEqual(proc.stdout.decode("utf-8").strip(), "[]")

    # ------------------------------------------------------------------
    # 9. 取值以连字符开头：仍作为字面片段，而不是无法识别的选项
    # ------------------------------------------------------------------
    def test_value_starting_with_hyphen_is_taken_literally(self) -> None:
        results = self.assert_success_and_parse(
            self._run_filtered("--path-contains", "-notes"), "连字符开头的取值"
        )

        self.assertEqual(
            results,
            [],
            "取值 -notes：应作为字面片段参与匹配（无命中），而不是报参数错误",
        )

    # ------------------------------------------------------------------
    # 10. 筛选下仍保持首个单行命中与前后各至多 30 个字符的片段语义
    # ------------------------------------------------------------------
    def test_snippet_semantics_preserved_under_filter(self) -> None:
        # 在 notes/ 下追加一个长行文件：命中前后各 40 个字符，应各截到 30 个。
        long_line = "x" * 40 + "target" + "y" * 40
        (self.root / "notes" / "long.txt").write_bytes(
            (long_line + "\n").encode("utf-8")
        )

        results = self.assert_success_and_parse(
            self._run_filtered("--path-contains", "notes/"), "筛选下的片段语义"
        )

        expected_snippet = "x" * 30 + "target" + "y" * 30
        self.assertEqual(
            results,
            [
                {"path": "notes/b.MD", "line": 2, "snippet": "target note"},
                {"path": "notes/long.txt", "line": 1, "snippet": expected_snippet},
            ],
            "筛选下的片段：首个命中、前后各至多 30 个字符的语义应保持不变",
        )

    # ------------------------------------------------------------------
    # 11. 不指定筛选：返回三个合法文件的命中，按路径字典序排列，
    #     只为 broken.txt 告警；每项仅含 path、line、snippet
    # ------------------------------------------------------------------
    def test_without_filter_returns_all_hits_sorted_and_warns_only_for_broken(self) -> None:
        proc = run_search([str(self.root), KEYWORD])

        self.assertEqual(proc.returncode, 0, f"退出码应为 0，stderr={proc.stderr!r}")
        results = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            results,
            [
                {"path": "Notes/c.txt", "line": 1, "snippet": "target upper"},
                {"path": "a.txt", "line": 1, "snippet": "target root"},
                {"path": "notes/b.MD", "line": 2, "snippet": "target note"},
            ],
            "不指定筛选：应返回全部三个合法文件的命中，按路径字典序排列",
        )
        for item in results:
            self.assertEqual(
                sorted(item.keys()),
                ["line", "path", "snippet"],
                "不指定筛选：每项结果只能包含 path、line、snippet",
            )
        stderr = proc.stderr.decode("utf-8")
        self.assertIn("outside/broken.txt", stderr, "应为 broken.txt 发出解码告警")
        self.assertIn("无法按 UTF-8 解码", stderr)
        self.assertNotIn("a.txt", stderr.replace("outside/broken.txt", ""), "合法文件不应被告警")
        self.assertNotIn("b.MD", stderr)
        self.assertNotIn("c.txt", stderr.replace("outside/broken.txt", ""))

    # ------------------------------------------------------------------
    # 12. 选项只能放在目录和关键词之后：提前出现视为无法识别的参数
    # ------------------------------------------------------------------
    def test_option_before_positional_arguments_is_rejected(self) -> None:
        proc = run_search(["--path-contains", "notes/", str(self.root), KEYWORD])

        self.assert_argument_error(proc, "选项前置", "无法识别的参数")

    # ------------------------------------------------------------------
    # 13. 缺少取值：退出 2，标准输出为空，标准错误说明缺少值
    # ------------------------------------------------------------------
    def test_missing_option_value_is_rejected(self) -> None:
        proc = self._run_filtered("--path-contains")

        self.assert_argument_error(proc, "缺少取值", "缺少值")

    # ------------------------------------------------------------------
    # 14. 取值为空或全为空白：退出 2，标准输出为空，标准错误说明原因
    # ------------------------------------------------------------------
    def test_empty_or_blank_option_value_is_rejected(self) -> None:
        for value in ("", "   ", " \t "):
            with self.subTest(value=value):
                proc = self._run_filtered("--path-contains", value)
                self.assert_argument_error(proc, f"空白取值 {value!r}", "为空或全为空白")

    # ------------------------------------------------------------------
    # 15. 重复指定选项：退出 2，标准输出为空，标准错误说明只能指定一次
    # ------------------------------------------------------------------
    def test_duplicate_option_is_rejected(self) -> None:
        proc = self._run_filtered("--path-contains", "notes/", "--path-contains", "Notes/")

        self.assert_argument_error(proc, "重复指定", "只能指定一次")

    # ------------------------------------------------------------------
    # 16. 无法识别的尾随参数：退出 2，标准输出为空，标准错误说明原因
    # ------------------------------------------------------------------
    def test_unrecognized_trailing_argument_is_rejected(self) -> None:
        for argv in (
            [str(self.root), KEYWORD, "extra"],
            [str(self.root), KEYWORD, "--path-contains", "notes/", "extra"],
            [str(self.root), KEYWORD, "--unknown"],
        ):
            with self.subTest(argv=argv):
                proc = run_search(argv)
                self.assert_argument_error(proc, f"尾随参数 {argv!r}", "无法识别的参数")

    # ------------------------------------------------------------------
    # 17. 带筛选的查询只读源文件且不留下索引文件
    # ------------------------------------------------------------------
    def test_filtered_query_is_read_only_and_leaves_no_index(self) -> None:
        before = self._snapshot_files()

        results = self.assert_success_and_parse(
            self._run_filtered("--path-contains", "notes/"), "筛选只读/无索引"
        )

        after = self._snapshot_files()
        self.assertEqual(before, after, "筛选查询后源文件内容不得变化，且不得新增索引文件")
        self.assertEqual(
            results,
            [{"path": "notes/b.MD", "line": 2, "snippet": "target note"}],
            "筛选只读场景：正常命中结果仍应正确返回",
        )


if __name__ == "__main__":
    unittest.main()

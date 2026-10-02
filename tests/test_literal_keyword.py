"""关键词字面匹配的端到端回归测试。

通过公开入口 ``python -m local_search <目录> <关键词>`` 核对标准输出 JSON、
标准错误与退出码，固定关键词的**字面匹配**语义：

- 含空格的多词关键词不拆词、不裁剪：``target note`` 只命中整行连续出现的
  ``target note``，且大小写敏感（``TARGET NOTE`` 返回 ``[]``）；
- 正则元字符按字面文本处理：``a.b*c?`` 只匹配连续的 ``a.b*c?`` 六个字符，
  不会命中 ``axbc``；
- 关键词边缘的空格原样保留：``" target "``（前后各一个空格）只命中同样带
  边缘空格的行，片段保留原有空格；
- 空字符串、纯空格、仅含制表符的关键词一律退出码 2，标准输出完全为空，
  标准错误包含“关键词为空或全为空白”，不输出 JSON 数组；
- 合法关键词没有命中时仍输出 ``[]``，退出码 0，标准错误为空；
- 每项结果只含 ``path``、``line``、``snippet``，字段类型与相对路径语义不变。

三个命中场景各自使用互相独立的临时目录，目录名刻意包含中文与空格；资料只
使用合法 UTF-8 的 ``.txt`` 与 ``.md`` 文件，由测试自行准备并在结束后自动清理，
不依赖固定外部文件或网络。期望值全部以字面量直接写出，不调用任何被测函数
来生成期望结果。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def run_search(directory: Path, keyword: str) -> subprocess.CompletedProcess:
    """以目录与关键词调用 python -m local_search，返回完成的子进程。"""
    return subprocess.run(
        [sys.executable, "-m", "local_search", str(directory), keyword],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )


class LiteralKeywordTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._tmp_root = Path(self._tmp.name)

    # -- 辅助 ------------------------------------------------------------

    def _make_root(self, name: str) -> Path:
        """在临时目录下建立一个名称含中文与空格的独立检索目录。"""
        root = self._tmp_root / name
        root.mkdir(parents=True)
        return root

    def _write_text(self, root: Path, name: str, text: str) -> None:
        (root / name).write_bytes(text.encode("utf-8"))

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

    def assert_result_shape(self, item: dict, label: str) -> None:
        """结果项只含 path/line/snippet 三个字段，且类型保持不变。"""
        self.assertEqual(
            set(item.keys()),
            {"path", "line", "snippet"},
            f"{label}: 结果项只能包含 path、line、snippet，实际 {sorted(item.keys())}",
        )
        self.assertIsInstance(item["path"], str, f"{label}: path 必须是字符串")
        self.assertIsInstance(item["line"], int, f"{label}: line 必须是整数")
        self.assertIsInstance(item["snippet"], str, f"{label}: snippet 必须是字符串")

    # ------------------------------------------------------------------
    # 场景一：含空格的多词关键词不拆词、不裁剪，且大小写敏感
    # ------------------------------------------------------------------
    def test_multiword_keyword_matches_literal_phrase(self) -> None:
        # 输入 a.txt：第 1 行 alpha，第 2 行 target note；
        #      b.md：只有 target other（含 target 但不含完整 target note）。
        # 查询 "target note"：只返回 a.txt，line=2，snippet 为 target note；
        # b.md 不得出现——关键词不能被拆成单词分别匹配。
        root = self._make_root("场景 一 资料")
        self._write_text(root, "a.txt", "alpha\ntarget note\n")
        self._write_text(root, "b.md", "target other\n")

        results = self.assert_success_and_parse(
            run_search(root, "target note"), "多词关键词"
        )

        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 2, "snippet": "target note"}],
            "多词场景：应只返回 a.txt 第 2 行的完整 target note",
        )
        self.assert_result_shape(results[0], "多词场景")
        self.assertNotIn("b.md", [item["path"] for item in results])

    def test_multiword_keyword_is_case_sensitive(self) -> None:
        # 同样的资料，查询全大写 "TARGET NOTE"：字面匹配区分大小写，
        # 期望输出 []，退出码 0，标准错误为空。
        root = self._make_root("场景 一 大小写")
        self._write_text(root, "a.txt", "alpha\ntarget note\n")
        self._write_text(root, "b.md", "target other\n")

        proc = run_search(root, "TARGET NOTE")
        results = self.assert_success_and_parse(proc, "多词大小写")

        self.assertEqual(results, [], "大小写场景：TARGET NOTE 不应命中任何小写内容")
        self.assertEqual(proc.stdout.decode("utf-8").strip(), "[]")

    # ------------------------------------------------------------------
    # 场景二：正则元字符按字面文本处理
    # ------------------------------------------------------------------
    def test_regex_metacharacters_are_treated_literally(self) -> None:
        # 输入 literal.txt：只有 a.b*c?；similar.md：只有 axbc。
        # 查询 "a.b*c?"：若被当作正则，axbc 也会命中；
        # 期望只返回 literal.txt，line=1，snippet 原样为 a.b*c?。
        root = self._make_root("场景 二 资料")
        self._write_text(root, "literal.txt", "a.b*c?\n")
        self._write_text(root, "similar.md", "axbc\n")

        results = self.assert_success_and_parse(
            run_search(root, "a.b*c?"), "正则元字符"
        )

        self.assertEqual(
            results,
            [{"path": "literal.txt", "line": 1, "snippet": "a.b*c?"}],
            "元字符场景：a.b*c? 只能按字面命中 literal.txt，不得命中 axbc",
        )
        self.assert_result_shape(results[0], "元字符场景")
        self.assertNotIn("similar.md", [item["path"] for item in results])

    # ------------------------------------------------------------------
    # 场景三：关键词边缘的空格原样保留，不被裁剪
    # ------------------------------------------------------------------
    def test_edge_spaces_of_keyword_are_preserved(self) -> None:
        # 输入 padded.txt：只有 " target "（前后各一个空格）；
        #      plain.md：只有 target（无边缘空格）。
        # 查询 " target "（同样带两个边缘空格）：只返回 padded.txt，line=1，
        # snippet 保留原有空格，即 " target "；plain.md 不得命中。
        root = self._make_root("场景 三 资料")
        self._write_text(root, "padded.txt", " target \n")
        self._write_text(root, "plain.md", "target\n")

        results = self.assert_success_and_parse(
            run_search(root, " target "), "边缘空格"
        )

        self.assertEqual(
            results,
            [{"path": "padded.txt", "line": 1, "snippet": " target "}],
            "边缘空格场景：片段必须保留前后各一个空格，且 plain.md 不得命中",
        )
        self.assert_result_shape(results[0], "边缘空格场景")
        self.assertNotIn("plain.md", [item["path"] for item in results])

    # ------------------------------------------------------------------
    # 空白关键词：空字符串、纯空格、仅制表符一律退出 2，不输出 JSON
    # ------------------------------------------------------------------
    def assert_blank_keyword_rejected(self, keyword: str, label: str) -> None:
        root = self._make_root(f"空白 关键词 {label}")
        self._write_text(root, "notes.txt", "target here\n")

        proc = run_search(root, keyword)

        self.assertEqual(
            proc.returncode,
            2,
            f"{label}: 退出码应为 2，实际 {proc.returncode}",
        )
        self.assertEqual(
            proc.stdout,
            b"",
            f"{label}: 标准输出必须完全为空，实际 {proc.stdout!r}",
        )
        stderr_text = proc.stderr.decode("utf-8")
        self.assertIn(
            "关键词为空或全为空白",
            stderr_text,
            f"{label}: 标准错误应说明关键词为空或全为空白，实际 {stderr_text!r}",
        )
        self.assertNotIn("[", proc.stdout.decode("utf-8", errors="replace"))

    def test_empty_keyword_is_rejected(self) -> None:
        self.assert_blank_keyword_rejected("", "空字符串")

    def test_spaces_only_keyword_is_rejected(self) -> None:
        self.assert_blank_keyword_rejected("   ", "纯空格")

    def test_tab_only_keyword_is_rejected(self) -> None:
        self.assert_blank_keyword_rejected("\t", "仅制表符")

    # ------------------------------------------------------------------
    # 合法关键词没有命中：输出 []，退出 0，无告警
    # ------------------------------------------------------------------
    def test_legal_keyword_without_hits_returns_empty_array(self) -> None:
        # 输入：含内容但不含关键词的文件。
        root = self._make_root("无命中 资料")
        self._write_text(root, "notes.txt", "alpha\nbeta\n")
        self._write_text(root, "memo.md", "gamma\n")

        proc = run_search(root, "absent")
        results = self.assert_success_and_parse(proc, "合法关键词无命中")

        self.assertEqual(results, [], "无命中场景：应输出空数组")
        self.assertEqual(proc.stdout.decode("utf-8").strip(), "[]")


if __name__ == "__main__":
    unittest.main()

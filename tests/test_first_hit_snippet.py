"""local_search 首个命中与片段截取的回归测试。

以 README 描述的公开行为为准，通过 ``python -m local_search`` 命令入口核对
返回的 JSON、退出码与标准错误：

- 每个文件只返回首个命中（按行号、行内位置确定），同一文件不重复返回；
- snippet 为完整关键词及同一行前后各至多 30 个 Unicode 字符（按码点计，
  不是 UTF-8 字节数），不含换行符，不添加省略号，不向相邻行借上下文；
- LF 与 CRLF 文件得到相同的逻辑行号与片段；末行无换行也能命中；
- 关键词跨行分布时不构成命中，输出空数组；
- 命中与无命中均退出 0，标准错误为空；
- 检索只读源文件，查询结束后目录中不留下任何索引文件。

所有资料在临时目录中准备并清理，仅使用标准库，离线运行。
期望值均为测试内手工确定的字面内容，不调用被测实现生成。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def run_search(directory: Path, keyword: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "local_search", str(directory), keyword],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )


class FirstHitSnippetTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def assert_search(self, keyword: str, expected: list) -> None:
        """运行查询并核对退出码 0、标准错误为空、JSON 与期望完全一致。"""
        proc = run_search(self.root, keyword)
        self.assertEqual(
            proc.returncode,
            0,
            msg=f"查询 {keyword!r} 退出码非 0，标准错误: {proc.stderr!r}",
        )
        self.assertEqual(
            proc.stderr,
            b"",
            msg=f"查询 {keyword!r} 时标准错误应为空",
        )
        results = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            results,
            expected,
            msg=f"查询 {keyword!r} 的 JSON 结果与期望不符",
        )

    def test_first_hit_only_returned_once_per_file(self) -> None:
        """a.txt 第 2 行含两个 target、第 3 行还有一个：只返回第 2 行首个命中。

        输入行 2 为 target + 40 个 x + target，首个命中在行首，右侧上下文
        超过 30 字符，片段恰好为 target 后接 30 个 x（第二个 target 不出现）。
        """
        (self.root / "a.txt").write_bytes(
            b"alpha\n" + b"target" + b"x" * 40 + b"target\n" + b"target later\n"
        )

        self.assert_search(
            "target",
            [
                {
                    "path": "a.txt",
                    "line": 2,
                    "snippet": "target" + "x" * 30,
                }
            ],
        )

    def test_snippet_counts_unicode_code_points_not_bytes(self) -> None:
        """单行 31 个“中” + 检索 + 31 个 😀：片段按码点各取 30 个。

        “中”为 3 字节 UTF-8、“😀”为 4 字节 UTF-8，若按字节截取会得到
        残缺字符或数量不符；期望恰好 30 个中字 + 完整检索 + 30 个 😀。
        """
        line = "中" * 31 + "检索" + "😀" * 31
        (self.root / "emoji.md").write_bytes((line + "\n").encode("utf-8"))

        self.assert_search(
            "检索",
            [
                {
                    "path": "emoji.md",
                    "line": 1,
                    "snippet": "中" * 30 + "检索" + "😀" * 30,
                }
            ],
        )

    def test_keyword_at_line_start(self) -> None:
        """关键词位于行首：左侧无上下文，片段从关键词本身开始。"""
        (self.root / "start.txt").write_bytes("target 开头命中\n".encode("utf-8"))

        self.assert_search(
            "target",
            [{"path": "start.txt", "line": 1, "snippet": "target 开头命中"}],
        )

    def test_keyword_at_line_end(self) -> None:
        """关键词位于行尾：右侧无上下文，片段以完整关键词结束，不带换行。"""
        (self.root / "end.txt").write_bytes("结尾命中 target\n".encode("utf-8"))

        self.assert_search(
            "target",
            [{"path": "end.txt", "line": 1, "snippet": "结尾命中 target"}],
        )

    def test_short_line_context_under_30_chars(self) -> None:
        """整行不足 30 字符上下文：片段就是该行实际内容，不多不少。"""
        (self.root / "short.txt").write_bytes("ab target cd\n".encode("utf-8"))

        self.assert_search(
            "target",
            [{"path": "short.txt", "line": 1, "snippet": "ab target cd"}],
        )

    def test_exactly_30_chars_context_on_both_sides(self) -> None:
        """关键词前后恰好各 30 个字符：两侧全部保留，不发生截断。

        行内容 66 个字符（30 个 a + target + 30 个 b），片段应为整行，
        关键词完整，无省略号。
        """
        line = "a" * 30 + "target" + "b" * 30
        (self.root / "exact.txt").write_bytes((line + "\n").encode("utf-8"))

        self.assert_search(
            "target",
            [{"path": "exact.txt", "line": 1, "snippet": line}],
        )

    def test_adjacent_lines_not_borrowed_as_context(self) -> None:
        """命中行只有 target 一个词：片段仅为 target，不借相邻行内容。

        上一行与下一行都写满超过 30 个字符，若实现误把相邻行当上下文，
        片段会明显变长或带换行。
        """
        (self.root / "lines.txt").write_bytes(
            b"previous line padding padding padding\n"
            b"target\n"
            b"next line padding padding padding padding\n"
        )

        self.assert_search(
            "target",
            [{"path": "lines.txt", "line": 2, "snippet": "target"}],
        )

    def test_lf_and_crlf_give_same_line_and_snippet(self) -> None:
        """LF 与 CRLF 文件的逻辑行号与片段一致，CR 不进入片段。"""
        (self.root / "crlf.txt").write_bytes(
            b"first line\r\nsecond target here\r\nthird line\r\n"
        )
        (self.root / "lf.txt").write_bytes(
            b"first line\nsecond target here\nthird line\n"
        )

        self.assert_search(
            "target",
            [
                {"path": "crlf.txt", "line": 2, "snippet": "second target here"},
                {"path": "lf.txt", "line": 2, "snippet": "second target here"},
            ],
        )

    def test_last_line_without_trailing_newline(self) -> None:
        """末行没有换行符：仍能返回正确行号与片段，片段不带换行。"""
        (self.root / "nonewline.txt").write_bytes(b"first\nlast target")

        self.assert_search(
            "target",
            [{"path": "nonewline.txt", "line": 2, "snippet": "last target"}],
        )

    def test_keyword_split_across_lines_is_no_hit(self) -> None:
        """tar 与 get 分处两行：任何单行都不含完整关键词，输出空数组。"""
        (self.root / "split.txt").write_bytes(b"ends with tar\nget starts here\n")

        self.assert_search("target", [])

    def test_no_hit_returns_empty_array_exit_zero(self) -> None:
        """有可读文件但无命中：输出 []，退出码 0，标准错误为空。"""
        (self.root / "plain.txt").write_bytes("没有关键词的一行\n".encode("utf-8"))

        self.assert_search("target", [])

    def test_search_leaves_no_index_files_behind(self) -> None:
        """查询只读源文件：结束后目录内容与查询前完全一致，无索引残留。"""
        (self.root / "a.txt").write_bytes(b"target here\n")
        (self.root / "b.md").write_bytes(b"nothing\n")
        before = sorted(p.relative_to(self.root) for p in self.root.rglob("*"))

        self.assert_search(
            "target",
            [{"path": "a.txt", "line": 1, "snippet": "target here"}],
        )

        after = sorted(p.relative_to(self.root) for p in self.root.rglob("*"))
        self.assertEqual(after, before, msg="查询结束后目录中出现额外文件")


if __name__ == "__main__":
    unittest.main()

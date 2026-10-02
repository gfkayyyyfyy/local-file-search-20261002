"""首个命中与片段截取的端到端回归测试。

通过命令入口 ``python -m local_search`` 核对标准输出 JSON、退出码与标准错误，
以 README 公开行为为准：

- 每个文件只返回按行号、行内位置确定的**首个命中**，同文件后续命中不重复返回；
- ``snippet`` 为完整关键词及同一行前后各至多 30 个 **Unicode 字符（码点）**，
  只保留该行实际存在的内容，不借用相邻行，不含换行符，不添加省略号；
- 正常命中与无命中均退出 0 且标准错误为空；
- LF 与 CRLF 得到相同的逻辑行号与片段；末行无换行仍可命中；
- 查询只读源文件，结束后目录中不留下索引文件。

所有资料由测试在 TemporaryDirectory 中准备并自动清理，仅使用标准库、离线运行。
期望值全部在测试中以字面量（或字面量的定长重复）直接写出，
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

# 各场景共用的关键词与片段半径（与 README 公开约定一致）。
KEYWORD = "target"
CONTEXT = 30


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

    def _write(self, name: str, data: bytes) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def _write_text(self, name: str, text: str) -> None:
        self._write(name, text.encode("utf-8"))

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

    def _snapshot_files(self) -> dict[str, bytes]:
        return {
            str(p.relative_to(self.root)).replace(os.sep, "/"): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    # ------------------------------------------------------------------
    # 1. 首个命中：同一行的第二次出现、后续行的命中都不返回；后缀截到 30 个 x
    # ------------------------------------------------------------------
    def test_first_occurrence_wins_and_suffix_context_capped_at_30(self) -> None:
        # 输入 a.txt：
        #   第 1 行 alpha（无命中）
        #   第 2 行 target + 40 个 x + target（行内两处命中，只取第一处）
        #   第 3 行 target later（后续行命中，不重复返回）
        # 期望：仅 1 条结果，line=2，snippet 为 target 后接恰好 30 个 x
        #       （40 个 x 中最右边的 10 个及第 2 个 target 全部被舍弃）。
        self._write(
            "a.txt",
            b"alpha\n" + b"target" + b"x" * 40 + b"target\n" + b"target later\n",
        )

        results = self.assert_success_and_parse(run_search(self.root, KEYWORD), "a.txt 多命中")

        expected_snippet = "target" + "x" * 30
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 2, "snippet": expected_snippet}],
            "a.txt 场景：应只返回第 2 行首个命中，片段为 target + 30 个 x",
        )
        self.assertEqual(len(results), 1, "a.txt 场景：同一文件只能出现一次")
        self.assertEqual(
            results[0]["snippet"],
            expected_snippet,
            "a.txt 场景：片段必须是完整的 target 加恰好 30 个 x，不能只检查包含 target",
        )
        self.assertNotIn("\n", results[0]["snippet"], "a.txt 场景：片段不得带换行")
        self.assertNotIn("...", results[0]["snippet"], "a.txt 场景：片段不得添加省略号")

    # ------------------------------------------------------------------
    # 2. 上下文按 Unicode 码点计数，而不是 UTF-8 字节数
    # ------------------------------------------------------------------
    def test_snippet_context_counts_unicode_code_points(self) -> None:
        # 输入 unicode.md（单行）：31 个“中” + 检索 + 31 个 😀（U+1F600）。
        # 按码点：关键词起始位置为 31，前取 30 → 丢掉最前面的 1 个“中”；
        #         关键词结束位置为 33，后取 30 → 丢掉最后面的 1 个 😀。
        # 期望：恰好 30 个“中” + 完整“检索” + 30 个 😀，共 62 个码点。
        # 若错误地按 UTF-8 字节截取（中=3 字节、😀=4 字节），片段会明显不同。
        content = "中" * 31 + "检索" + "😀" * 31 + "\n"
        self._write_text("unicode.md", content)

        results = self.assert_success_and_parse(
            run_search(self.root, "检索"), "Unicode 码点计数"
        )

        expected_snippet = "中" * 30 + "检索" + "😀" * 30
        self.assertEqual(
            results,
            [{"path": "unicode.md", "line": 1, "snippet": expected_snippet}],
            "Unicode 场景：片段应为 30 个中 + 检索 + 30 个 😀",
        )
        self.assertEqual(len(results[0]["snippet"]), 62, "Unicode 场景：片段应恰为 62 个码点")
        self.assertTrue(results[0]["snippet"].startswith("中" * 30 + "检索"))
        self.assertTrue(results[0]["snippet"].endswith("检索" + "😀" * 30))
        self.assertNotIn("中" * 31, results[0]["snippet"], "最前面的第 31 个“中”必须被截掉")
        self.assertNotIn("😀" * 31, results[0]["snippet"], "最后面的第 31 个 😀 必须被截掉")

    # ------------------------------------------------------------------
    # 3. 关键词位于行首：前缀上下文为 0，片段以关键词开头
    # ------------------------------------------------------------------
    def test_keyword_at_line_start_has_no_prefix_context(self) -> None:
        # 输入：target 后接 35 个 a（超过 30）。
        # 期望：snippet = target + 30 个 a，首字符即关键词，line=1。
        self._write_text("start.txt", "target" + "a" * 35 + "\n")

        results = self.assert_success_and_parse(run_search(self.root, KEYWORD), "行首命中")

        expected_snippet = "target" + "a" * 30
        self.assertEqual(
            results,
            [{"path": "start.txt", "line": 1, "snippet": expected_snippet}],
            "行首场景：片段应直接以完整 target 开头，后接 30 个 a",
        )

    # ------------------------------------------------------------------
    # 4. 关键词位于行尾：后缀上下文为 0，片段以关键词结尾且不带换行
    # ------------------------------------------------------------------
    def test_keyword_at_line_end_has_no_suffix_context(self) -> None:
        # 输入：35 个 b 后以 target 结尾，文件带换行。
        # 期望：snippet = 30 个 b + target，target 在末尾，片段不含换行符。
        self._write_text("end.txt", "b" * 35 + "target\n")

        results = self.assert_success_and_parse(run_search(self.root, KEYWORD), "行尾命中")

        expected_snippet = "b" * 30 + "target"
        self.assertEqual(
            results,
            [{"path": "end.txt", "line": 1, "snippet": expected_snippet}],
            "行尾场景：片段应以完整 target 结尾，前面保留 30 个 b",
        )
        self.assertTrue(results[0]["snippet"].endswith("target"))
        self.assertNotIn("\n", results[0]["snippet"], "行尾场景：片段不得带入换行")
        self.assertNotIn("\r", results[0]["snippet"], "行尾场景：片段不得带入回车")

    # ------------------------------------------------------------------
    # 5. 行内可用上下文不足 30 个字符：只保留该行实际存在的全部内容
    # ------------------------------------------------------------------
    def test_context_shorter_than_30_keeps_whole_line(self) -> None:
        # 输入：ab + target + cd（前后都只有 2 个字符，整行 10 个字符）。
        # 期望：snippet 就是完整的整行 abtargetcd，不补齐、不填充。
        line = "ab" + "target" + "cd"
        self._write_text("short.txt", line + "\n")

        results = self.assert_success_and_parse(run_search(self.root, KEYWORD), "上下文不足 30")

        self.assertEqual(
            results,
            [{"path": "short.txt", "line": 1, "snippet": "abtargetcd"}],
            "短上下文场景：片段应等于该行实际存在的全部内容 abtargetcd",
        )
        self.assertEqual(results[0]["snippet"], line)

    # ------------------------------------------------------------------
    # 6. 前后恰好 30 个字符：边界值，整行恰为片段，一个字符都不丢
    # ------------------------------------------------------------------
    def test_exactly_30_chars_context_is_kept_entirely(self) -> None:
        # 输入：30 个 p + target + 30 个 s（前后各恰好 30）。
        # 期望：snippet 等于完整整行，start=0、end=行尾。
        line = "p" * 30 + "target" + "s" * 30
        self._write_text("exact30.txt", line + "\n")

        results = self.assert_success_and_parse(run_search(self.root, KEYWORD), "上下文恰好 30")

        self.assertEqual(
            results,
            [{"path": "exact30.txt", "line": 1, "snippet": line}],
            "恰好 30 场景：前后各 30 个字符应全部保留，片段等于整行",
        )

    # ------------------------------------------------------------------
    # 7. 前后各 31 个字符：仅截掉最外侧各 1 个字符，关键词完整保留
    # ------------------------------------------------------------------
    def test_context_31_chars_drops_only_the_outermost_one_each_side(self) -> None:
        # 输入：31 个 p + target + 31 个 s。
        # 期望：snippet = 30 个 p + target + 30 个 s，
        #       即仅去掉最左的 p 和最右的 s，完整关键词不能被截断。
        self._write_text("over31.txt", "p" * 31 + "target" + "s" * 31 + "\n")

        results = self.assert_success_and_parse(run_search(self.root, KEYWORD), "上下文 31")

        expected_snippet = "p" * 30 + "target" + "s" * 30
        self.assertEqual(
            results,
            [{"path": "over31.txt", "line": 1, "snippet": expected_snippet}],
            "31 字符场景：应只截掉最外侧各 1 个字符，target 必须完整",
        )
        self.assertIn("target", results[0]["snippet"])
        self.assertEqual(results[0]["snippet"].find("target"), 30)

    # ------------------------------------------------------------------
    # 8. 相邻行不得被借作上下文
    # ------------------------------------------------------------------
    def test_adjacent_lines_are_not_borrowed_as_context(self) -> None:
        # 输入：第 1 行 40 个 P，第 2 行只有 target，第 3 行 40 个 N。
        # 期望：命中 line=2，snippet 恰为 target，
        #       第 1、3 行的任何字符都不得进入片段。
        self._write_text("neighbors.txt", "P" * 40 + "\ntarget\n" + "N" * 40 + "\n")

        results = self.assert_success_and_parse(run_search(self.root, KEYWORD), "相邻行不借用")

        self.assertEqual(
            results,
            [{"path": "neighbors.txt", "line": 2, "snippet": "target"}],
            "相邻行场景：片段只能包含命中行本身，即 target",
        )
        self.assertNotIn("P", results[0]["snippet"])
        self.assertNotIn("N", results[0]["snippet"])

    # ------------------------------------------------------------------
    # 9. LF 与 CRLF：逻辑行号与片段完全一致，片段不含 \r
    # ------------------------------------------------------------------
    def test_lf_and_crlf_produce_same_line_and_snippet(self) -> None:
        # 两个文件逻辑内容相同：第 1 行 header line，
        # 第 2 行为 35 个 a + target + 35 个 b；分别用 LF 与 CRLF 落盘。
        # 期望：两个结果都为 line=2、snippet=30 个 a + target + 30 个 b，
        #       CRLF 的 \r 不得残留在片段中；结果按路径排序 crlf.txt 在 lf.txt 前。
        logical_line = "a" * 35 + "target" + "b" * 35
        self._write("lf.txt", b"header line\n" + logical_line.encode("utf-8") + b"\n")
        self._write("crlf.txt", b"header line\r\n" + logical_line.encode("utf-8") + b"\r\n")

        results = self.assert_success_and_parse(run_search(self.root, KEYWORD), "LF/CRLF 一致性")

        expected_snippet = "a" * 30 + "target" + "b" * 30
        self.assertEqual(
            results,
            [
                {"path": "crlf.txt", "line": 2, "snippet": expected_snippet},
                {"path": "lf.txt", "line": 2, "snippet": expected_snippet},
            ],
            "LF/CRLF 场景：两者逻辑行号与片段必须完全一致",
        )
        for item in results:
            self.assertNotIn("\r", item["snippet"], "LF/CRLF 场景：片段不得包含回车符")
            self.assertNotIn("\n", item["snippet"], "LF/CRLF 场景：片段不得包含换行符")

    # ------------------------------------------------------------------
    # 10. 末行没有换行符：仍可正确命中
    # ------------------------------------------------------------------
    def test_hit_on_last_line_without_trailing_newline(self) -> None:
        # 输入：第 1 行 first line，第 2 行 tail line target，文件末尾无 \n。
        # 期望：line=2，snippet 为完整的 tail line target。
        self._write_text("nonewline.txt", "first line\ntail line target")

        results = self.assert_success_and_parse(run_search(self.root, KEYWORD), "末行无换行")

        self.assertEqual(
            results,
            [{"path": "nonewline.txt", "line": 2, "snippet": "tail line target"}],
            "末行无换行场景：仍应在第 2 行返回完整命中",
        )

    # ------------------------------------------------------------------
    # 11. 关键词被拆在两行：任何单行都不含完整关键词 → 空数组
    # ------------------------------------------------------------------
    def test_keyword_split_across_two_lines_returns_empty(self) -> None:
        # 输入两个文件：tar\nget\n（LF）与 tar\r\nget\r\n（CRLF）。
        # target 跨越行边界，没有任何单行包含完整关键词。
        # 期望：输出 []，退出码 0，标准错误为空。
        self._write("split_lf.txt", b"tar\nget\n")
        self._write("split_crlf.txt", b"tar\r\nget\r\n")

        proc = run_search(self.root, KEYWORD)
        results = self.assert_success_and_parse(proc, "跨行关键词")

        self.assertEqual(results, [], "跨行场景：关键词跨行时必须返回空数组")
        self.assertEqual(proc.stdout.decode("utf-8").strip(), "[]")

    # ------------------------------------------------------------------
    # 12. 全部文件都不含关键词：空数组、退出 0、标准错误为空
    # ------------------------------------------------------------------
    def test_no_occurrence_anywhere_returns_empty_array(self) -> None:
        # 输入：多行文件，内容均不含 target。
        self._write_text("notes.txt", "alpha\nbeta\ngamma\n")

        proc = run_search(self.root, KEYWORD)
        results = self.assert_success_and_parse(proc, "全无命中")

        self.assertEqual(results, [], "无命中场景：应输出空数组")
        self.assertEqual(proc.stdout.decode("utf-8").strip(), "[]")

    # ------------------------------------------------------------------
    # 13. 查询只读源文件且不留下索引文件
    # ------------------------------------------------------------------
    def test_query_leaves_no_index_files_and_keeps_sources_intact(self) -> None:
        # 输入：一个含命中的文件；查询前后目录内文件集合与字节内容必须完全一致。
        self._write_text("keep.txt", "before\ntarget in middle\nafter\n")
        before = self._snapshot_files()

        results = self.assert_success_and_parse(run_search(self.root, KEYWORD), "只读/无索引")

        after = self._snapshot_files()
        self.assertEqual(before, after, "只读场景：查询后源文件内容不得变化，且不得新增索引文件")
        self.assertEqual(
            results,
            [{"path": "keep.txt", "line": 2, "snippet": "target in middle"}],
            "只读场景：正常命中结果仍应正确返回",
        )


if __name__ == "__main__":
    unittest.main()

"""关键词字面匹配的端到端回归测试。

通过公开命令入口 ``python -m local_search <目录> <关键词>`` 核对标准输出的
JSON、标准错误与退出码，固定以下关键词规则（与 README 公开约定一致）：

- 关键词是**字面文本**：不拆分多个词、不裁剪首尾空格、不解释正则元字符，
  且区分大小写；
- 为空或全为空白（空字符串、纯空格、仅制表符）的关键词一律退出码 2，
  标准输出完全为空，标准错误说明原因，不输出 JSON 数组；
- 合法关键词没有命中时仍输出 ``[]``，退出码 0，标准错误为空；
- 每项结果只含 ``path``、``line``、``snippet`` 三个字段，
  类型分别为 str、int、str，``path`` 为相对路径。

三个命中场景各自使用互相独立的临时目录，目录名刻意同时包含中文与空格；
资料只使用合法 UTF-8 的 ``.txt`` 与 ``.md`` 文件，由测试自行准备并在结束后
自动清理，不依赖任何固定外部文件或网络。期望值全部以字面量直接写出，
不调用任何被测函数来生成期望结果。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 空白关键词被拒绝时标准错误中必须出现的说明文字。
BLANK_KEYWORD_MESSAGE = "关键词为空或全为空白"


def run_search(directory: Path, keyword: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "local_search", str(directory), keyword],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )


class KeywordLiteralTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _make_scenario_dir(self, name: str) -> Path:
        """在临时根目录下建立一个独立场景目录（目录名含中文与空格）。"""
        directory = self.root / name
        directory.mkdir(parents=True)
        return directory

    def _write_text(self, directory: Path, name: str, text: str) -> None:
        (directory / name).write_bytes(text.encode("utf-8"))

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

    def assert_result_shape(self, results: list, label: str) -> None:
        """每项结果只含 path/line/snippet，类型分别为 str/int/str。"""
        for item in results:
            self.assertEqual(
                set(item.keys()),
                {"path", "line", "snippet"},
                f"{label}: 结果项只能含 path、line、snippet 三个字段，实际 {sorted(item.keys())}",
            )
            self.assertIsInstance(item["path"], str, f"{label}: path 必须是 str")
            self.assertIsInstance(item["line"], int, f"{label}: line 必须是 int")
            self.assertIsInstance(item["snippet"], str, f"{label}: snippet 必须是 str")
            self.assertNotIn("/", item["path"], f"{label}: 本场景 path 应为顶层相对路径")

    # ------------------------------------------------------------------
    # 1. 多词关键词整体字面匹配，不拆词；大小写不同则不命中
    # ------------------------------------------------------------------
    def test_multi_word_keyword_matches_literally_and_case_sensitively(self) -> None:
        # 目录「场景 一 词组」：
        #   a.txt 第 1 行 alpha，第 2 行 target note
        #   b.md  只有 target other（含 target 但不含完整词组 target note）
        # 查询 "target note"：只有 a.txt 第 2 行命中，snippet 恰为 target note；
        # b.md 不得命中（关键词不能被拆成单词分别匹配）。
        directory = self._make_scenario_dir("场景 一 词组")
        self._write_text(directory, "a.txt", "alpha\ntarget note\n")
        self._write_text(directory, "b.md", "target other\n")

        results = self.assert_success_and_parse(
            run_search(directory, "target note"), "多词字面匹配"
        )

        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 2, "snippet": "target note"}],
            "多词场景：只有 a.txt 第 2 行命中，snippet 恰为 target note",
        )
        self.assert_result_shape(results, "多词场景")

        # 同样的词组换成全大写：区分大小写，必须返回空数组。
        upper = self.assert_success_and_parse(
            run_search(directory, "TARGET NOTE"), "大写词组不命中"
        )
        self.assertEqual(upper, [], "大写场景：TARGET NOTE 区分大小写，应返回 []")

    # ------------------------------------------------------------------
    # 2. 正则元字符按字面文本处理，不被当成正则表达式
    # ------------------------------------------------------------------
    def test_regex_metacharacters_are_matched_literally(self) -> None:
        # 目录「场景 二 元字符」：
        #   literal.txt 只有 a.b*c?
        #   similar.md  只有 axbc（若把关键词当正则 a.b*c? 会被错误命中）
        # 查询 "a.b*c?"：只有 literal.txt 第 1 行命中，snippet 原样为 a.b*c?。
        directory = self._make_scenario_dir("场景 二 元字符")
        self._write_text(directory, "literal.txt", "a.b*c?\n")
        self._write_text(directory, "similar.md", "axbc\n")

        results = self.assert_success_and_parse(
            run_search(directory, "a.b*c?"), "正则元字符字面匹配"
        )

        self.assertEqual(
            results,
            [{"path": "literal.txt", "line": 1, "snippet": "a.b*c?"}],
            "元字符场景：只有 literal.txt 命中，snippet 原样保留 a.b*c?",
        )
        self.assert_result_shape(results, "元字符场景")

    # ------------------------------------------------------------------
    # 3. 关键词首尾空格原样保留，不被裁剪
    # ------------------------------------------------------------------
    def test_leading_and_trailing_spaces_in_keyword_are_preserved(self) -> None:
        # 目录「场景 三 空格」：
        #   padded.txt 只有 " target "（前后各一个空格）
        #   plain.md   只有 "target"（无空格，不应命中带空格的关键词）
        # 查询 " target "（前后各一个空格）：只有 padded.txt 第 1 行命中，
        # snippet 保留原有空格，恰为 " target "。
        directory = self._make_scenario_dir("场景 三 空格")
        self._write_text(directory, "padded.txt", " target \n")
        self._write_text(directory, "plain.md", "target\n")

        results = self.assert_success_and_parse(
            run_search(directory, " target "), "边缘空格保留"
        )

        self.assertEqual(
            results,
            [{"path": "padded.txt", "line": 1, "snippet": " target "}],
            "空格场景：只有 padded.txt 命中，snippet 必须保留首尾各一个空格",
        )
        self.assert_result_shape(results, "空格场景")

    # ------------------------------------------------------------------
    # 4. 空白关键词：空字符串、纯空格、仅制表符一律退出 2
    # ------------------------------------------------------------------
    def test_blank_keywords_are_rejected_with_exit_code_2(self) -> None:
        # 目录「场景 四 空白」：存在且可读，内含一个正常文件；
        # 三种空白关键词都必须在读取任何文件之前被拒绝。
        directory = self._make_scenario_dir("场景 四 空白")
        self._write_text(directory, "notes.txt", "target here\n")

        for label, keyword in [
            ("空字符串", ""),
            ("纯空格", "   "),
            ("仅制表符", "\t"),
        ]:
            with self.subTest(keyword=label):
                proc = run_search(directory, keyword)
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
                self.assertIn(
                    BLANK_KEYWORD_MESSAGE,
                    proc.stderr.decode("utf-8"),
                    f"{label}: 标准错误应说明关键词为空或全为空白，实际 {proc.stderr!r}",
                )
                self.assertNotIn(
                    "[",
                    proc.stdout.decode("utf-8", errors="replace"),
                    f"{label}: 不得输出 JSON 数组",
                )

    # ------------------------------------------------------------------
    # 5. 合法关键词没有命中：输出 []、退出 0、标准错误为空
    # ------------------------------------------------------------------
    def test_legal_keyword_without_hits_returns_empty_array(self) -> None:
        # 目录「场景 五 无命中」：文件内容均不含关键词 missing。
        directory = self._make_scenario_dir("场景 五 无命中")
        self._write_text(directory, "a.txt", "alpha\nbeta\n")
        self._write_text(directory, "b.md", "gamma delta\n")

        proc = run_search(directory, "missing")
        results = self.assert_success_and_parse(proc, "无命中")

        self.assertEqual(results, [], "无命中场景：应输出空数组")
        self.assertEqual(
            proc.stdout.decode("utf-8").strip(),
            "[]",
            "无命中场景：标准输出应恰为 []",
        )


if __name__ == "__main__":
    unittest.main()

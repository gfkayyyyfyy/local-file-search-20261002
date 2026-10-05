"""主关键词与替代关键词同起点（前缀重叠）时片段与列号一致性的回归测试。

固定 ``--or-keyword`` 竞争在“两词命中起点相同”这一边界情况下，通过命令入口
``python -m local_search <目录> ...`` 与 ``local_search.main`` 参数入口端到端
验证既有行为：

- 资料文件只有一行差异前缀时，两词在同一列同时命中：同起点一律选主关键词，
  与词长无关——主词较长时片段不得被替代词截短（``Abc`` 不能截成 ``Ab``/
  ``ab``），主词较短时同样选主词（片段为 ``Ab``/``ab``）；
- ``column`` 按当前行 Unicode 码点从 1 开始计数并指向共同起点：中文、补充
  平面字符、组合重音（U+0301）与制表符各按自身码点数累计；
- ``--all-lines`` 每行至多一项，同一行后面的重复出现不增加结果项，结果按
  行顺序排列；
- JSON 两项依次指向 ``a.txt`` 第 1、2 行；CSV 表头为
  ``path,line,snippet,column``，记录与 JSON 一一对应、以 CRLF 结束，输出为
  不带 BOM 的 UTF-8 字节；
- ``local_search.main`` 接收等价参数时，返回码与两个标准流的字节输出与命令
  行入口完全一致；
- 查询只读源文件：查询前后目录内的文件集合与每个文件的字节完全一致，不产生
  索引或导出文件。

所有资料由测试在 TemporaryDirectory 中独立准备并自动清理，仅使用 Python 标准
库、离线运行。期望值全部以字面量直接写出，不调用任何被测的匹配或裁剪函数来
生成期望结果。
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from local_search.search import main  # noqa: E402

# 唯一资料文件：第一行“甲😀Abc ab”，第二行“e + U+0301 组合重音 + 制表符 +
# abcd ab”，两行均以 LF 结束。
FIXTURE_TEXT = "甲😀Abc ab\né\tabcd ab\n"
FIXTURE_BYTES = FIXTURE_TEXT.encode("utf-8")

IGNORE_CASE = "--ignore-case"
ALL_LINES = "--all-lines"
CONTEXT_CHARS = "--context-chars"
OR_KEYWORD = "--or-keyword"
SHOW_COLUMN = "--show-column"
FORMAT = "--format"

# 除主词/替代词外完全一致的公共参数。
COMMON_TAIL = (IGNORE_CASE, ALL_LINES, CONTEXT_CHARS, "0", SHOW_COLUMN)


def run_cli(directory: Path, *argv: str) -> subprocess.CompletedProcess:
    """以任意命令行参数调用 python -m local_search，返回完成的子进程。"""
    return subprocess.run(
        [sys.executable, "-m", "local_search", str(directory), *argv],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )


class _BinaryStream:
    """仅暴露 ``.buffer`` 的标准流替身，供直接调用 main 时捕获字节输出。"""

    def __init__(self) -> None:
        self.buffer = io.BytesIO()


class OverlapColumnTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "a.txt").write_bytes(FIXTURE_BYTES)

    def _snapshot_files(self) -> dict[str, bytes]:
        return {
            str(p.relative_to(self.root)).replace(os.sep, "/"): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    def assert_success_clean(self, proc: subprocess.CompletedProcess, label: str) -> bytes:
        """场景 label：退出码必须为 0、标准错误必须为空，返回标准输出字节。"""
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
        return proc.stdout

    def assert_only_fixture_unchanged(self, before: dict[str, bytes], label: str) -> None:
        """查询后文件集合与字节必须与查询前一致，且目录中只有 a.txt。"""
        after = self._snapshot_files()
        self.assertEqual(after, before, f"{label}: 查询不得新增、删除或改动文件")
        self.assertEqual(
            set(after),
            {"a.txt"},
            f"{label}: 不得产生索引或导出文件，实际文件集合为 {sorted(after)}",
        )
        self.assertEqual(after["a.txt"], FIXTURE_BYTES, f"{label}: 源文件字节必须保持不变")

    # -- 1. 主词较长：同起点选主词，片段不得被替代词截短 ----------------------

    def test_longer_main_keyword_wins_same_start_json(self) -> None:
        before = self._snapshot_files()
        proc = run_cli(self.root, "ABC", OR_KEYWORD, "ab", *COMMON_TAIL)
        stdout = self.assert_success_clean(proc, "主词较长：JSON")

        items = json.loads(stdout.decode("utf-8"))
        # 期望值逐字写出：
        # 第 1 行码点序列 甲(1) 😀(2) A(3) ...，“ABC”与“ab”同在第 3 列起步，
        #   选主词 -> 片段 Abc，列 3（不得截成 Ab 或 ab）；
        # 第 2 行码点序列 e(1) 组合重音(2) 制表符(3) a(4) ...，“ABC”（忽略
        #   大小写匹配 abcd 的 abc）与“ab”同在第 4 列起步，选主词 -> 片段
        #   abc，列 4；行尾“ab”是同行的重复出现，不增加结果项。
        self.assertEqual(
            items,
            [
                {"path": "a.txt", "line": 1, "snippet": "Abc", "column": 3},
                {"path": "a.txt", "line": 2, "snippet": "abc", "column": 4},
            ],
        )
        # 顺序、片段、整数列号逐项显式断言。
        self.assertEqual([item["line"] for item in items], [1, 2], "结果必须按行序排列")
        self.assertEqual(
            [item["snippet"] for item in items],
            ["Abc", "abc"],
            "同起点选主词：片段必须为主词形态，不能截成 Ab 或 ab",
        )
        first_snippet = items[0]["snippet"]
        self.assertNotIn(
            first_snippet, ("Ab", "ab"),
            "第一行片段不得被较短的替代词截成 Ab 或 ab，必须为主词形态 Abc",
        )
        for item in items:
            self.assertIsInstance(item["column"], int, "column 必须是整数")
            self.assertEqual(item["path"], "a.txt")
        self.assertEqual(len(items), 2, "每行一项，同行后面的重复出现不得增加结果项")
        self.assert_only_fixture_unchanged(before, "主词较长：JSON")

    def test_longer_main_keyword_csv_bytes(self) -> None:
        before = self._snapshot_files()
        # 仅在 JSON 场景参数上追加 --format csv。
        proc = run_cli(
            self.root, "ABC", OR_KEYWORD, "ab", *COMMON_TAIL, FORMAT, "csv"
        )
        stdout = self.assert_success_clean(proc, "主词较长：CSV")

        # 表头与两条记录逐字节固定：四列表头、记录与 JSON 一一对应、CRLF 结束。
        self.assertEqual(
            stdout,
            b"path,line,snippet,column\r\n"
            b"a.txt,1,Abc,3\r\n"
            b"a.txt,2,abc,4\r\n",
        )
        self.assertFalse(stdout.startswith(b"\xef\xbb\xbf"), "CSV 输出不得带 BOM")
        self.assertTrue(all(line.endswith(b"\r") for line in stdout.split(b"\n")[:-1]))
        self.assert_only_fixture_unchanged(before, "主词较长：CSV")

    # -- 2. 主词较短：词长不决定同起点优先级 ----------------------------------

    def test_shorter_main_keyword_still_wins_same_start(self) -> None:
        before = self._snapshot_files()
        # 主词改为 ab、替代词改为 ABC，其他条件与场景一完全一致。
        proc = run_cli(self.root, "ab", OR_KEYWORD, "ABC", *COMMON_TAIL)
        stdout = self.assert_success_clean(proc, "主词较短：JSON")

        items = json.loads(stdout.decode("utf-8"))
        # 即使主词更短，同起点仍选主词：片段 Ab/ab，列号仍是 3、4。
        self.assertEqual(
            items,
            [
                {"path": "a.txt", "line": 1, "snippet": "Ab", "column": 3},
                {"path": "a.txt", "line": 2, "snippet": "ab", "column": 4},
            ],
        )
        self.assertEqual([item["line"] for item in items], [1, 2])
        self.assertEqual(
            [item["snippet"] for item in items],
            ["Ab", "ab"],
            "词长不决定优先级：同起点始终选主词",
        )
        self.assertEqual(
            [item["column"] for item in items],
            [3, 4],
            "共同起点不变：中文、补充平面字符、组合重音与制表符按码点计数",
        )
        for item in items:
            self.assertIsInstance(item["column"], int)
        self.assertEqual(len(items), 2, "每行一项，行尾重复出现不增加结果项")
        self.assert_only_fixture_unchanged(before, "主词较短：JSON")

    # -- 3. local_search.main 参数入口与命令行入口一致 ------------------------

    def test_main_entry_matches_cli(self) -> None:
        # 两种主/替代词配置 × JSON/CSV 四种等价参数组合，main 与命令行入口的
        # 返回码及两个标准流字节必须完全一致。
        cases = [
            ("主词较长-JSON", ["ABC", OR_KEYWORD, "ab", *COMMON_TAIL]),
            ("主词较长-CSV", ["ABC", OR_KEYWORD, "ab", *COMMON_TAIL, FORMAT, "csv"]),
            ("主词较短-JSON", ["ab", OR_KEYWORD, "ABC", *COMMON_TAIL]),
            ("主词较短-CSV", ["ab", OR_KEYWORD, "ABC", *COMMON_TAIL, FORMAT, "csv"]),
        ]
        for label, tail in cases:
            with self.subTest(label=label):
                before = self._snapshot_files()
                cli = run_cli(self.root, *tail)

                argv = [str(self.root), *tail]
                old_stdout, old_stderr = sys.stdout, sys.stderr
                sys.stdout, sys.stderr = _BinaryStream(), _BinaryStream()
                try:
                    code = main(argv)
                    out = sys.stdout.buffer.getvalue()
                    err = sys.stderr.buffer.getvalue()
                finally:
                    sys.stdout, sys.stderr = old_stdout, old_stderr

                self.assertEqual(code, cli.returncode, f"{label}: 返回码不一致")
                self.assertEqual(out, cli.stdout, f"{label}: 标准输出字节不一致")
                self.assertEqual(err, cli.stderr, f"{label}: 标准错误字节不一致")
                # 两个入口在本组参数下本身都应成功且无标准错误。
                self.assertEqual(code, 0, f"{label}: 返回码应为 0")
                self.assertEqual(err, b"", f"{label}: 标准错误应为空")
                self.assert_only_fixture_unchanged(before, label)


if __name__ == "__main__":
    unittest.main()

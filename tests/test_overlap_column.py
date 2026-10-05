"""主关键词与替代关键词同起点、前缀重叠时片段与列号一致性的回归测试。

背景：``--or-keyword`` 两词在同一行同一点出现且互为前缀时（如 ``ABC`` 与
``ab`` 在忽略大小写后都从 ``Abc`` 起点开始），既有规则是“起始位置最小者
胜出，起始位置相同选主关键词”。本组测试用一份固定 UTF-8 资料，把该规则与
``--show-column`` 的码点列号、JSON/CSV 序列化钉死在一起：

- 资料为临时目录中唯一的 ``a.txt``：第 1 行 ``甲😀Abc ab``，第 2 行
  ``e`` + U+0301 组合重音 + 制表符 + ``abcd ab``，两行均以 LF 结束；
- 场景一（主词 ``ABC``、替代词 ``ab``）：两行两词均同起点，选主词，
  JSON 两项依次指向第 1、2 行，snippet 为 ``Abc``/``abc``（不得截成
  ``Ab`` 或 ``ab``），column 为整数 3、4；追加 ``--format csv`` 后表头
  为 ``path,line,snippet,column``，两条记录与 JSON 对应，每条记录以
  CRLF 结束，输出为不带 BOM 的 UTF-8；
- 场景二（主词 ``ab``、替代词 ``ABC``，主词反而更短）：两行片段为
  ``Ab``/``ab``，列号仍为 3、4——词长不决定同起点的优先级；每行后面的
  再次出现（`` ab``）不增加结果项；
- 列号按当前行 Unicode 码点从 1 起计数：第 1 行甲（列 1）与补充平面的
  😀（列 2）各占一码点，命中从列 3 起；第 2 行 e（列 1）、组合重音
  U+0301（列 2）、制表符（列 3）各占一码点，命中从列 4 起；与字节数、
  显示宽度及 ``--context-chars 0`` 的裁剪无关；
- ``local_search.main`` 接收等价参数时，返回码及标准输出、标准错误两个
  字节流与 ``python -m local_search`` 命令入口完全一致；
- 查询只读资料：查询前后目录内文件集合与源文件字节保持一致，不产生索引
  或导出文件。

资料由测试在 TemporaryDirectory 中独立准备并自动清理，仅使用 Python 标准
库、离线运行。期望值全部以字面量直接写出，不调用任何被测匹配或裁剪函数
来生成期望结果。
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

from local_search import main  # noqa: E402

OR_KEYWORD = "--or-keyword"
IGNORE_CASE = "--ignore-case"
ALL_LINES = "--all-lines"
CONTEXT_CHARS = "--context-chars"
SHOW_COLUMN = "--show-column"
FORMAT = "--format"

UTF8_BOM = b"\xef\xbb\xbf"

# 资料字节：以 Unicode 转义字面构造，明确每个码点的组成，再编码为 UTF-8。
# 第 1 行：甲(U+7532)、😀(U+1F600，补充平面)、"Abc ab"
# 第 2 行：e、组合重音 U+0301、制表符、"abcd ab"
# 两行均以 LF 结束，不含 CR；整个文件没有 BOM。
# 组合重音必须用显式码点转义写出，避免源码中不可见字符被误写成别的码点。
FIXTURE_BYTES = (
    "甲😀Abc ab\n"
    "e\u0301\tabcd ab\n"
).encode("utf-8")


def run_args(directory: Path, *argv: str) -> subprocess.CompletedProcess:
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


class OverlapSameStartColumnTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _prepare_fixture(self) -> Path:
        """在临时目录写入唯一资料 a.txt，返回其路径。"""
        path = self.root / "a.txt"
        path.write_bytes(FIXTURE_BYTES)
        return path

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

    def _snapshot_files(self) -> dict[str, bytes]:
        return {
            str(p.relative_to(self.root)).replace(os.sep, "/"): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    # -- 0. 资料布局：列号字面期望的依据 -------------------------------------

    def test_fixture_is_unique_two_line_utf8_file(self) -> None:
        self._prepare_fixture()

        files = {
            str(p.relative_to(self.root)).replace(os.sep, "/")
            for p in self.root.rglob("*")
            if p.is_file()
        }
        self.assertEqual(files, {"a.txt"}, "目录中必须只有唯一资料 a.txt")
        self.assertEqual(
            (self.root / "a.txt").read_bytes(),
            FIXTURE_BYTES,
            "源文件字节必须与字面构造的资料一致",
        )
        # 两行、两个 LF，没有 CR 或 BOM。
        self.assertEqual(FIXTURE_BYTES.count(b"\n"), 2)
        self.assertNotIn(b"\r", FIXTURE_BYTES)
        self.assertFalse(FIXTURE_BYTES.startswith(UTF8_BOM))

        text = FIXTURE_BYTES.decode("utf-8")
        line1, line2, tail = text.split("\n")
        self.assertEqual(tail, "", "两行均以 LF 结束，末尾没有多余内容")
        self.assertEqual(line1, "甲😀Abc ab")
        self.assertEqual(line2, "e\u0301\tabcd ab")
        # 第 1 行码点布局：甲列 1、😀列 2，"Abc" 从列 3 起。
        self.assertEqual(line1[0:2], "甲😀")
        self.assertEqual(line1[2:5], "Abc")
        # 第 2 行码点布局：e 列 1、组合重音列 2、制表符列 3，"abcd" 从列 4 起。
        self.assertEqual(line2[0], "e")
        self.assertEqual(line2[1], "\u0301")
        self.assertEqual(line2[2], "\t")
        self.assertEqual(line2[3:7], "abcd")
        # 每行后面都另有一个 " ab"，用于验证重复出现不增加结果项。
        self.assertTrue(line1.endswith(" ab"))
        self.assertTrue(line2.endswith(" ab"))

    # -- 1. 场景一：主词 ABC、替代词 ab（同起点选主词，不截短） --------------

    def test_longer_main_keyword_json_snippet_and_column(self) -> None:
        self._prepare_fixture()
        before = self._snapshot_files()

        proc = run_args(
            self.root,
            "ABC", OR_KEYWORD, "ab", IGNORE_CASE, ALL_LINES,
            CONTEXT_CHARS, "0", SHOW_COLUMN,
        )
        stdout = self.assert_success_clean(proc, "主词较长：JSON")

        # 输出字节逐字节固定：顺序、序列化形式与末尾 LF 一并钉死。
        self.assertEqual(
            stdout,
            b'[{"path": "a.txt", "line": 1, "snippet": "Abc", "column": 3}, '
            b'{"path": "a.txt", "line": 2, "snippet": "abc", "column": 4}]\n',
        )

        data = json.loads(stdout.decode("utf-8"))
        expected = (
            ("a.txt", 1, "Abc", 3),
            ("a.txt", 2, "abc", 4),
        )
        self.assertEqual(len(data), len(expected), "每行一项，重复出现不增加结果项")
        # 顺序按下标逐对核对；片段、整数列号、行号分别显式断言。
        for item, (path, line, snippet, column) in zip(data, expected):
            self.assertEqual(set(item), {"path", "line", "snippet", "column"})
            self.assertEqual(item["path"], path)
            self.assertEqual(item["line"], line)
            self.assertEqual(item["snippet"], snippet, "同起点必须选主词 ABC，不能截成 Ab 或 ab")
            self.assertIsInstance(item["column"], int, "column 必须是整数")
            self.assertEqual(item["column"], column)

        self.assertEqual(self._snapshot_files(), before, "查询不得改动或新增文件")

    def test_longer_main_keyword_csv_bytes(self) -> None:
        self._prepare_fixture()
        before = self._snapshot_files()

        proc = run_args(
            self.root,
            "ABC", OR_KEYWORD, "ab", IGNORE_CASE, ALL_LINES,
            CONTEXT_CHARS, "0", SHOW_COLUMN, FORMAT, "csv",
        )
        stdout = self.assert_success_clean(proc, "主词较长：CSV")

        expected = (
            b"path,line,snippet,column\r\n"
            b"a.txt,1,Abc,3\r\n"
            b"a.txt,2,abc,4\r\n"
        )
        self.assertEqual(stdout, expected, "表头与两条记录逐字节一致")
        self.assertFalse(stdout.startswith(UTF8_BOM), "输出不得带 BOM")
        # 每条记录（含表头）都以 CRLF 结束，且不存在裸 LF。
        records = stdout.split(b"\r\n")
        self.assertEqual(records[-1], b"", "最后一条记录必须以 CRLF 结束")
        self.assertEqual(
            records[:-1],
            [
                b"path,line,snippet,column",
                b"a.txt,1,Abc,3",
                b"a.txt,2,abc,4",
            ],
        )
        self.assertNotIn(b"\n", stdout.replace(b"\r\n", b""), "不得出现不以 CR 引导的 LF")
        # 严格按不带 BOM 的 UTF-8 解码。
        self.assertEqual(stdout.decode("utf-8").encode("utf-8"), stdout)

        self.assertEqual(self._snapshot_files(), before, "查询不得改动或新增文件")

    # -- 2. 场景二：主词 ab、替代词 ABC（主词更短仍选主词） ------------------

    def test_shorter_main_keyword_json_snippet_and_column(self) -> None:
        self._prepare_fixture()
        before = self._snapshot_files()

        proc = run_args(
            self.root,
            "ab", OR_KEYWORD, "ABC", IGNORE_CASE, ALL_LINES,
            CONTEXT_CHARS, "0", SHOW_COLUMN,
        )
        stdout = self.assert_success_clean(proc, "主词较短：JSON")

        self.assertEqual(
            stdout,
            b'[{"path": "a.txt", "line": 1, "snippet": "Ab", "column": 3}, '
            b'{"path": "a.txt", "line": 2, "snippet": "ab", "column": 4}]\n',
        )

        data = json.loads(stdout.decode("utf-8"))
        expected = (
            ("a.txt", 1, "Ab", 3),
            ("a.txt", 2, "ab", 4),
        )
        self.assertEqual(len(data), len(expected), "每行一项，行尾的重复 ab 不增加结果项")
        for item, (path, line, snippet, column) in zip(data, expected):
            self.assertEqual(set(item), {"path", "line", "snippet", "column"})
            self.assertEqual(item["path"], path)
            self.assertEqual(item["line"], line)
            self.assertEqual(item["snippet"], snippet, "同起点选主词 ab，与词长无关")
            self.assertIsInstance(item["column"], int, "column 必须是整数")
            self.assertEqual(item["column"], column, "词长不影响码点列号，仍为 3、4")

        self.assertEqual(self._snapshot_files(), before, "查询不得改动或新增文件")

    def test_shorter_main_keyword_csv_bytes(self) -> None:
        self._prepare_fixture()
        before = self._snapshot_files()

        proc = run_args(
            self.root,
            "ab", OR_KEYWORD, "ABC", IGNORE_CASE, ALL_LINES,
            CONTEXT_CHARS, "0", SHOW_COLUMN, FORMAT, "csv",
        )
        stdout = self.assert_success_clean(proc, "主词较短：CSV")

        expected = (
            b"path,line,snippet,column\r\n"
            b"a.txt,1,Ab,3\r\n"
            b"a.txt,2,ab,4\r\n"
        )
        self.assertEqual(stdout, expected, "表头与两条记录逐字节一致")
        self.assertFalse(stdout.startswith(UTF8_BOM), "输出不得带 BOM")
        records = stdout.split(b"\r\n")
        self.assertEqual(records[-1], b"", "最后一条记录必须以 CRLF 结束")
        self.assertEqual(
            records[:-1],
            [
                b"path,line,snippet,column",
                b"a.txt,1,Ab,3",
                b"a.txt,2,ab,4",
            ],
        )
        self.assertNotIn(b"\n", stdout.replace(b"\r\n", b""), "不得出现不以 CR 引导的 LF")
        self.assertEqual(stdout.decode("utf-8").encode("utf-8"), stdout)

        self.assertEqual(self._snapshot_files(), before, "查询不得改动或新增文件")

    # -- 3. local_search.main 与命令入口完全一致 -----------------------------

    def test_main_entry_matches_command_entry(self) -> None:
        self._prepare_fixture()
        before = self._snapshot_files()

        cases = (
            ("主词较长：JSON", "ABC", "ab", False),
            ("主词较长：CSV", "ABC", "ab", True),
            ("主词较短：JSON", "ab", "ABC", False),
            ("主词较短：CSV", "ab", "ABC", True),
        )
        for label, keyword, or_keyword, use_csv in cases:
            tail = [
                keyword, OR_KEYWORD, or_keyword, IGNORE_CASE, ALL_LINES,
                CONTEXT_CHARS, "0", SHOW_COLUMN,
            ]
            if use_csv:
                tail += [FORMAT, "csv"]

            proc = run_args(self.root, *tail)
            cli_out = self.assert_success_clean(proc, f"{label}（命令入口）")

            old_stdout, old_stderr = sys.stdout, sys.stderr
            sys.stdout, sys.stderr = _BinaryStream(), _BinaryStream()
            try:
                code = main([str(self.root), *tail])
                out = sys.stdout.buffer.getvalue()
                err = sys.stderr.buffer.getvalue()
            finally:
                sys.stdout, sys.stderr = old_stdout, old_stderr

            self.assertEqual(code, 0, f"{label}: main 返回码应为 0")
            self.assertEqual(code, proc.returncode, f"{label}: 返回码与命令入口一致")
            self.assertEqual(err, b"", f"{label}: main 的标准错误应为空")
            self.assertEqual(err, proc.stderr, f"{label}: 标准错误与命令入口一致")
            self.assertEqual(
                out,
                cli_out,
                f"{label}: main(argv) 的标准输出字节必须与命令入口一致",
            )

        self.assertEqual(self._snapshot_files(), before, "查询不得改动或新增文件")


if __name__ == "__main__":
    unittest.main()

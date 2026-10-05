"""``--show-column`` 可选命中列号的端到端回归测试。

固定从命令行与 ``local_search.main`` 入口观察到的列号行为：

- 开关只能写在两个位置参数之后，与已有选项任意排序；缺省关闭时 JSON 与
  CSV 的输出形状与既有行为完全一致（JSON 无 ``column`` 键，CSV 仍为
  ``path,line,snippet`` 三列）；
- 开启时 JSON 每项在原三键之后新增整数 ``column``；CSV 在原三列后追加
  第四列，表头为 ``path,line,snippet,column``，列值为十进制数字，CRLF
  记录结束符、UTF-8 无 BOM、转义规则沿用现状；无命中时 JSON 为 ``[]``，
  CSV 只有四列表头及 CRLF；
- 列号按源文件当前行的 Unicode 码点从 1 计数，指向片段围绕的命中起点，
  不按裁剪后位置、字节或显示宽度计算：中文、补充平面字符与制表符各算
  一个码点，组合字符按实际码点数累计；
- 默认选每文件最早合格行中主关键词的最左命中，``--all-lines`` 逐行沿用
  此规则；与 ``--or-keyword`` 同用时选两词中起点最左的命中，同位置选
  主词；``column`` 与 ``snippet`` 始终指向同一命中，不随上下文长度、
  排序、偏移或上限改变；
- 重复开关在扫描前返回 2、标准输出为空、标准错误包含 ``--show-column``
  及“只能指定一次”；同名文本作为关键词或带值选项取值时仍按字面处理，
  不开启列号。

资料由测试在 TemporaryDirectory 中独立准备，仅使用 Python 标准库。
期望值全部以字面量直接写出，不调用任何被测函数生成期望结果。
"""

import csv
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from local_search.search import main  # noqa: E402

SHOW_COLUMN = "--show-column"
IGNORE_CASE = "--ignore-case"
ALL_LINES = "--all-lines"
CONTEXT_CHARS = "--context-chars"
OR_KEYWORD = "--or-keyword"
AND_KEYWORD = "--and-keyword"
NOT_KEYWORD = "--not-keyword"
FORMAT = "--format"
LIMIT = "--limit"
OFFSET = "--offset"
PATH_CONTAINS = "--path-contains"


def run_args(directory: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "local_search", str(directory), *argv],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )


class _BinaryStream:
    """仅暴露 ``.buffer`` 的标准流替身，供直接调用 main 时捕获字节输出。"""

    def __init__(self) -> None:
        self.buffer = io.BytesIO()


class ShowColumnTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _write_text(self, name: str, text: str, newline: str = "\n") -> None:
        path = self.root / name
        path.write_bytes(text.encode("utf-8").replace(b"\n", newline.encode("utf-8")))

    # -- 1. 验收演示场景 ---------------------------------------------------

    def test_acceptance_demo_json(self) -> None:
        # “甲😀Target target”：甲=码点1，😀=码点2，Target 起点=列 3；
        # “go target”：go 是替代词的最左命中，列 1。
        self._write_text(
            "a.txt", "甲😀Target target\ngo target\n"
        )
        proc = run_args(
            self.root, "TARGET", IGNORE_CASE, OR_KEYWORD, "go", ALL_LINES,
            CONTEXT_CHARS, "0", SHOW_COLUMN,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, b"")
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8")),
            [
                {"path": "a.txt", "line": 1, "snippet": "Target", "column": 3},
                {"path": "a.txt", "line": 2, "snippet": "go", "column": 1},
            ],
        )

    def test_acceptance_demo_csv(self) -> None:
        self._write_text("a.txt", "甲😀Target target\ngo target\n")
        proc = run_args(
            self.root, "TARGET", IGNORE_CASE, OR_KEYWORD, "go", ALL_LINES,
            CONTEXT_CHARS, "0", SHOW_COLUMN, FORMAT, "csv",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, b"")
        self.assertEqual(
            proc.stdout,
            b"path,line,snippet,column\r\n"
            b"a.txt,1,Target,3\r\n"
            b"a.txt,2,go,1\r\n",
        )
        rows = list(csv.reader(io.StringIO(proc.stdout.decode("utf-8"), newline="")))
        self.assertEqual(
            rows,
            [
                ["path", "line", "snippet", "column"],
                ["a.txt", "1", "Target", "3"],
                ["a.txt", "2", "go", "1"],
            ],
        )

    # -- 2. 关闭时输出完全不变 ---------------------------------------------

    def test_output_unchanged_without_flag(self) -> None:
        self._write_text("a.txt", "target one\ntarget two\n")
        tail = (IGNORE_CASE, ALL_LINES, CONTEXT_CHARS, "0")
        without = run_args(self.root, "TARGET", *tail)
        with_flag_off = run_args(self.root, "TARGET", *tail)
        self.assertEqual(without.stdout, with_flag_off.stdout)
        self.assertEqual(
            json.loads(without.stdout.decode("utf-8")),
            [
                {"path": "a.txt", "line": 1, "snippet": "target"},
                {"path": "a.txt", "line": 2, "snippet": "target"},
            ],
        )
        for item in json.loads(without.stdout.decode("utf-8")):
            self.assertNotIn("column", item)

        csv_without = run_args(self.root, "TARGET", *tail, FORMAT, "csv")
        self.assertEqual(
            csv_without.stdout,
            b"path,line,snippet\r\na.txt,1,target\r\na.txt,2,target\r\n",
        )

    def test_no_hit_json_empty_array_and_csv_header_only(self) -> None:
        self._write_text("a.txt", "nothing here\n")
        proc = run_args(self.root, "zzz", SHOW_COLUMN)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.decode("utf-8"), "[]\n")

        proc_csv = run_args(self.root, "zzz", SHOW_COLUMN, FORMAT, "csv")
        self.assertEqual(proc_csv.returncode, 0)
        self.assertEqual(proc_csv.stdout, b"path,line,snippet,column\r\n")

    # -- 3. 码点计数：中文、补充平面、组合字符、制表符 -----------------------

    def test_column_counts_unicode_code_points_not_bytes_or_width(self) -> None:
        # 两个中文字 + 一个补充平面 emoji 后接制表符，再命中：列 = 5。
        self._write_text("a.txt", "甲乙😀\tTarget\n")
        proc = run_args(
            self.root, "Target", IGNORE_CASE, CONTEXT_CHARS, "0", SHOW_COLUMN
        )
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8")),
            [{"path": "a.txt", "line": 1, "snippet": "Target", "column": 5}],
        )

    def test_combining_characters_count_by_actual_code_points(self) -> None:
        # 用显式转义构造分解序列：e + U+0301 组成 e\u0301（两个码点），
        # 随后 abc、空格再到 Target，命中前共 6 个码点，列号为 7。
        line = "e\u0301abc Target\n"
        self.assertEqual(len("e\u0301"), 2)
        self._write_text("a.txt", line)
        proc = run_args(
            self.root, "Target", CONTEXT_CHARS, "0", SHOW_COLUMN
        )
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8")),
            [{"path": "a.txt", "line": 1, "snippet": "Target", "column": 7}],
            "基字母、组合符、a、b、c、空格各占一个码点",
        )

    # -- 4. 命中选择与 snippet 一致 ----------------------------------------

    def test_column_points_to_leftmost_main_keyword_hit(self) -> None:
        self._write_text("a.txt", "xx target yy target zz\n")
        proc = run_args(self.root, "target", CONTEXT_CHARS, "0", SHOW_COLUMN)
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8")),
            [{"path": "a.txt", "line": 1, "snippet": "target", "column": 4}],
        )

    def test_column_follows_or_keyword_tie_breaks_to_main(self) -> None:
        # 主词在前：列指向主词。
        self._write_text("main.txt", "target go\n")
        proc = run_args(
            self.root, "target", OR_KEYWORD, "go", CONTEXT_CHARS, "0",
            SHOW_COLUMN, PATH_CONTAINS, "main.txt",
        )
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8")),
            [{"path": "main.txt", "line": 1, "snippet": "target", "column": 1}],
        )
        # 同位置（两词相同）选主词：片段与列仍一致。
        self._write_text("tie.txt", "abc\n")
        proc = run_args(
            self.root, "abc", OR_KEYWORD, "abc", CONTEXT_CHARS, "0",
            SHOW_COLUMN, PATH_CONTAINS, "tie.txt",
        )
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8")),
            [{"path": "tie.txt", "line": 1, "snippet": "abc", "column": 1}],
        )

    def test_column_independent_of_context_chars_sorting_offset_limit(self) -> None:
        self._write_text("a.txt", "甲乙 target\nnothing\n丙 target end\n")
        self._write_text("z.txt", " target\n")
        # 上下文长度不改变列号。
        for chars in ("0", "2", "30"):
            proc = run_args(
                self.root, "target", ALL_LINES, CONTEXT_CHARS, chars,
                SHOW_COLUMN, OFFSET, "0",
            )
            items = {
                (item["path"], item["line"]): item["column"]
                for item in json.loads(proc.stdout.decode("utf-8"))
            }
            self.assertEqual(items, {("a.txt", 1): 4, ("a.txt", 3): 3, ("z.txt", 1): 2})
        # 排序与分页只截取结果，列号保持原值。
        proc = run_args(
            self.root, "target", ALL_LINES, CONTEXT_CHARS, "0",
            SHOW_COLUMN, OFFSET, "1", LIMIT, "1",
        )
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8")),
            [{"path": "a.txt", "line": 3, "snippet": "target", "column": 3}],
        )

    def test_column_with_and_not_keywords_unchanged_rules(self) -> None:
        # 第一行含排除词被跳过；第二行同时含附加词才合格，列仍指向主词。
        self._write_text(
            "a.txt", "target draft\nx budget target end\n"
        )
        proc = run_args(
            self.root, "target", AND_KEYWORD, "budget", NOT_KEYWORD, "draft",
            CONTEXT_CHARS, "0", SHOW_COLUMN,
        )
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8")),
            [{"path": "a.txt", "line": 2, "snippet": "target", "column": 10}],
        )

    def test_crlf_line_endings_do_not_shift_column(self) -> None:
        # CRLF 的 \r 在命中之前时不影响列号（行尾只从末尾剥离）。
        self._write_text("a.txt", "ab target\n", newline="\r\n")
        proc = run_args(
            self.root, "target", CONTEXT_CHARS, "0", SHOW_COLUMN
        )
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8")),
            [{"path": "a.txt", "line": 1, "snippet": "target", "column": 4}],
        )

    # -- 5. CSV 细节：含特殊字符的 snippet 转义后 column 仍为独立第四列 ------

    def test_csv_quoted_snippet_keeps_column_fourth(self) -> None:
        self._write_text('q.txt', 'say "hi" , target after\n')
        proc = run_args(
            self.root, "target", CONTEXT_CHARS, "20", SHOW_COLUMN, FORMAT, "csv"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rows = list(csv.reader(io.StringIO(proc.stdout.decode("utf-8"), newline="")))
        self.assertEqual(rows[0], ["path", "line", "snippet", "column"])
        self.assertEqual(rows[1][0], "q.txt")
        self.assertEqual(rows[1][1], "1")
        self.assertEqual(rows[1][2], 'say "hi" , target after')
        self.assertEqual(rows[1][3], "12")
        self.assertTrue(proc.stdout.endswith(b"\r\n"))
        self.assertFalse(proc.stdout.startswith(b"\xef\xbb\xbf"))

    # -- 6. 选项位置、main 入口 --------------------------------------------

    def test_option_order_invariance(self) -> None:
        self._write_text("a.txt", "a target b\n")
        observed = set()
        tails = (
            (SHOW_COLUMN, IGNORE_CASE, CONTEXT_CHARS, "0"),
            (IGNORE_CASE, CONTEXT_CHARS, "0", SHOW_COLUMN),
            (CONTEXT_CHARS, "0", SHOW_COLUMN, IGNORE_CASE),
        )
        for tail in tails:
            proc = run_args(self.root, "TARGET", *tail)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            observed.add(proc.stdout)
        self.assertEqual(len(observed), 1)
        self.assertEqual(
            json.loads(next(iter(observed)).decode("utf-8")),
            [{"path": "a.txt", "line": 1, "snippet": "target", "column": 3}],
        )

    def test_main_entry_shows_and_hides_column(self) -> None:
        self._write_text("a.txt", "甲 target\n")

        def run_main(argv: list[str]) -> tuple[int, bytes, bytes]:
            stdout, stderr = _BinaryStream(), _BinaryStream()
            old_stdout, old_stderr = sys.stdout, sys.stderr
            sys.stdout, sys.stderr = stdout, stderr  # type: ignore[assignment]
            try:
                code = main(argv)
            finally:
                sys.stdout, sys.stderr = old_stdout, old_stderr
            return code, stdout.buffer.getvalue(), stderr.buffer.getvalue()

        code, out, err = run_main(
            [str(self.root), "target", CONTEXT_CHARS, "0", SHOW_COLUMN]
        )
        self.assertEqual((code, err), (0, b""))
        self.assertEqual(
            json.loads(out.decode("utf-8")),
            [{"path": "a.txt", "line": 1, "snippet": "target", "column": 3}],
        )
        code, out, err = run_main([str(self.root), "target", CONTEXT_CHARS, "0"])
        self.assertEqual((code, err), (0, b""))
        self.assertNotIn(b"column", out)

    # -- 7. 重复开关与同名文本 ---------------------------------------------

    def test_duplicate_flag_fails_before_scan(self) -> None:
        self._write_text("a.txt", "target\n")
        proc = run_args(self.root, "target", SHOW_COLUMN, SHOW_COLUMN)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, b"")
        stderr = proc.stderr.decode("utf-8")
        self.assertIn(SHOW_COLUMN, stderr)
        self.assertIn("只能指定一次", stderr)

    def test_same_text_as_keyword_or_value_does_not_enable_flag(self) -> None:
        self._write_text("a.txt", f"{SHOW_COLUMN} target\n")
        # 关键词恰为开关记号时按字面检索，且不开启列号。
        proc = run_args(self.root, SHOW_COLUMN, CONTEXT_CHARS, "0")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8")),
            [{"path": "a.txt", "line": 1, "snippet": SHOW_COLUMN}],
        )
        # 作为带值选项的取值时也不开启列号。
        proc = run_args(
            self.root, "target", PATH_CONTAINS, SHOW_COLUMN, CONTEXT_CHARS, "0"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8")),
            [],
            "路径片段恰为 --show-column 时没有文件匹配，且输出仍是无列号形状",
        )
        proc = run_args(
            self.root, "target", OR_KEYWORD, SHOW_COLUMN, CONTEXT_CHARS, "0"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        items = json.loads(proc.stdout.decode("utf-8"))
        # 替代词恰为开关记号时按字面命中；它位于行首，比 target 更靠左，
        # 故片段围绕替代词；开关本身仍未开启（无 column 键）。
        self.assertEqual(
            items,
            [{"path": "a.txt", "line": 1, "snippet": SHOW_COLUMN}],
        )
        self.assertNotIn("column", items[0])


if __name__ == "__main__":
    unittest.main()

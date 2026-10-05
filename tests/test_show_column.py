"""``--show-column`` 命中列号开关的端到端回归测试。

通过命令入口 ``python -m local_search <目录> <关键词> ... --show-column``
固定从命令行输入到列号输出的行为：

- 开关无取值，只允许出现在两个位置参数之后，可与其他选项任意排序；
  开启时 JSON 每项新增整数 ``column``，CSV 在原三列后追加 ``column``
  列（表头 ``path,line,snippet,column``，列值为十进制数字，记录仍以
  CRLF 结束、不带 BOM）；未开启时输出与既有格式完全一致；
- 列号按源文件当前行的 Unicode 码点从 1 开始计数，指向片段围绕的命中
  起点：中文、补充平面字符与制表符各算一码点，组合字符按实际码点数
  累计，与裁剪后位置、字节数及显示宽度无关；
- 默认选每文件最早合格行中主关键词的最左命中；``--all-lines`` 逐行沿用
  同一规则，同一行重复命中只返回一项；与 ``--or-keyword`` 同用时选两词
  中起点最左的命中，同位置选主词；``column`` 与 ``snippet`` 指向同一
  命中，不随 ``--context-chars``、排序或 ``--offset``/``--limit`` 改变；
- 重复指定在扫描前返回 2，标准输出为空，标准错误包含 ``--show-column``
  与“只能指定一次”；同名文本作为关键词或其他选项取值时仍按字面处理，
  不开启列号；
- 无结果时返回 0，JSON 为 ``[]``，CSV 仅有四列表头及 CRLF；
- ``local_search.main`` 的参数入口遵循相同规则；
- 查询只读源文件，不创建导出文件，不留下索引。

所有资料由测试在 TemporaryDirectory 中独立准备并自动清理，仅使用 Python
标准库、离线运行。期望值全部以字面量直接写出，不调用任何被测函数来生成
期望结果。
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

SHOW_COLUMN = "--show-column"
ALL_LINES = "--all-lines"
IGNORE_CASE = "--ignore-case"
CONTEXT_CHARS = "--context-chars"
OR_KEYWORD = "--or-keyword"
FORMAT = "--format"
LIMIT = "--limit"
OFFSET = "--offset"


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


class ShowColumnTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _write(self, rel: str, data: bytes) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def _write_demo(self) -> None:
        """验收演示目录：仅含 UTF-8 的 a.txt，两行，LF 行尾。"""
        self._write("a.txt", "甲😀Target target\ngo target\n".encode("utf-8"))

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

    # -- 1. 验收演示场景：JSON 与 CSV 的列号输出 -----------------------------

    def test_demo_scenario_json_columns(self) -> None:
        self._write_demo()
        before = self._snapshot_files()

        proc = run_args(
            self.root,
            "TARGET", IGNORE_CASE, OR_KEYWORD, "go", ALL_LINES,
            CONTEXT_CHARS, "0", SHOW_COLUMN,
        )
        stdout = self.assert_success_clean(proc, "演示：JSON")
        self.assertEqual(
            json.loads(stdout.decode("utf-8")),
            [
                {"path": "a.txt", "line": 1, "snippet": "Target", "column": 3},
                {"path": "a.txt", "line": 2, "snippet": "go", "column": 1},
            ],
        )
        self.assertEqual(self._snapshot_files(), before, "查询不得改动或新增文件")

    def test_demo_scenario_csv_columns(self) -> None:
        self._write_demo()

        proc = run_args(
            self.root,
            "TARGET", IGNORE_CASE, OR_KEYWORD, "go", ALL_LINES,
            CONTEXT_CHARS, "0", SHOW_COLUMN, FORMAT, "csv",
        )
        stdout = self.assert_success_clean(proc, "演示：CSV")
        self.assertEqual(
            stdout,
            b"path,line,snippet,column\r\n"
            b"a.txt,1,Target,3\r\n"
            b"a.txt,2,go,1\r\n",
            "四列表头及两条记录，CRLF 结束，不带 BOM",
        )
        self.assertFalse(stdout.startswith(b"\xef\xbb\xbf"), "输出不得带 BOM")

    def test_demo_scenario_option_order_invariance(self) -> None:
        self._write_demo()
        tail_a = (IGNORE_CASE, OR_KEYWORD, "go", ALL_LINES, CONTEXT_CHARS, "0", SHOW_COLUMN)
        tail_b = (SHOW_COLUMN, CONTEXT_CHARS, "0", ALL_LINES, OR_KEYWORD, "go", IGNORE_CASE)

        out_a = self.assert_success_clean(
            run_args(self.root, "TARGET", *tail_a), "顺序一"
        )
        out_b = self.assert_success_clean(
            run_args(self.root, "TARGET", *tail_b), "顺序二"
        )
        self.assertEqual(out_a, out_b, "开关与其他选项任意排序时输出必须一致")

    # -- 2. 未开启时输出不变 --------------------------------------------------

    def test_without_flag_output_unchanged(self) -> None:
        self._write_demo()
        tail = (IGNORE_CASE, OR_KEYWORD, "go", ALL_LINES, CONTEXT_CHARS, "0")

        stdout = self.assert_success_clean(
            run_args(self.root, "TARGET", *tail), "无开关：JSON"
        )
        self.assertEqual(
            json.loads(stdout.decode("utf-8")),
            [
                {"path": "a.txt", "line": 1, "snippet": "Target"},
                {"path": "a.txt", "line": 2, "snippet": "go"},
            ],
        )
        csv_out = self.assert_success_clean(
            run_args(self.root, "TARGET", *tail, FORMAT, "csv"), "无开关：CSV"
        )
        self.assertEqual(
            csv_out,
            b"path,line,snippet\r\na.txt,1,Target\r\na.txt,2,go\r\n",
        )

    # -- 3. 列号按码点计数 ----------------------------------------------------

    def test_column_counts_code_points(self) -> None:
        # 中文、补充平面字符、制表符各算一码点；组合字符（e + ́）算两码点。
        self._write("a.txt", "甲😀\téTarget\n".encode("utf-8"))

        stdout = self.assert_success_clean(
            run_args(self.root, "target", IGNORE_CASE, CONTEXT_CHARS, "0", SHOW_COLUMN),
            "码点计数",
        )
        # 甲(1) 😀(2) 制表符(3) e(4) 组合符(5) T(6)。
        self.assertEqual(
            json.loads(stdout.decode("utf-8")),
            [{"path": "a.txt", "line": 1, "snippet": "Target", "column": 6}],
        )

    def test_column_independent_of_context_chars(self) -> None:
        self._write("a.txt", "xxTargetxx\n".encode("utf-8"))

        for context, snippet in (("0", "Target"), ("2", "xxTargetxx"), ("30", "xxTargetxx")):
            stdout = self.assert_success_clean(
                run_args(
                    self.root, "target", IGNORE_CASE,
                    CONTEXT_CHARS, context, SHOW_COLUMN,
                ),
                f"context={context}",
            )
            item = json.loads(stdout.decode("utf-8"))[0]
            self.assertEqual(item["column"], 3, f"context={context} 时列号不变")
            self.assertEqual(item["snippet"], snippet, f"context={context} 时片段")

    def test_column_points_at_leftmost_hit_of_first_line_by_default(self) -> None:
        self._write("a.txt", "aa target\ntarget bb target\n".encode("utf-8"))

        stdout = self.assert_success_clean(
            run_args(self.root, "target", CONTEXT_CHARS, "0", SHOW_COLUMN),
            "默认首个合格行",
        )
        self.assertEqual(
            json.loads(stdout.decode("utf-8")),
            [{"path": "a.txt", "line": 1, "snippet": "target", "column": 4}],
        )

    def test_all_lines_repeated_hit_per_line_returns_one_item(self) -> None:
        self._write("a.txt", "target x target\ntarget\n".encode("utf-8"))

        stdout = self.assert_success_clean(
            run_args(self.root, "target", ALL_LINES, CONTEXT_CHARS, "0", SHOW_COLUMN),
            "同行重复命中",
        )
        self.assertEqual(
            json.loads(stdout.decode("utf-8")),
            [
                {"path": "a.txt", "line": 1, "snippet": "target", "column": 1},
                {"path": "a.txt", "line": 2, "snippet": "target", "column": 1},
            ],
        )

    def test_or_keyword_leftmost_wins_and_tie_prefers_main(self) -> None:
        # 替代词更靠左时选替代词；同位置（两词相同起点）时选主关键词。
        self._write("a.txt", "go target\ntarget go\n".encode("utf-8"))

        stdout = self.assert_success_clean(
            run_args(
                self.root, "target", OR_KEYWORD, "go", ALL_LINES,
                CONTEXT_CHARS, "0", SHOW_COLUMN,
            ),
            "or 最左",
        )
        self.assertEqual(
            json.loads(stdout.decode("utf-8")),
            [
                {"path": "a.txt", "line": 1, "snippet": "go", "column": 1},
                {"path": "a.txt", "line": 2, "snippet": "target", "column": 1},
            ],
        )

    def test_column_stable_under_offset_and_limit(self) -> None:
        self._write("a.txt", "target\n".encode("utf-8"))
        self._write("b.txt", "xx target\n".encode("utf-8"))

        stdout = self.assert_success_clean(
            run_args(
                self.root, "target", ALL_LINES, CONTEXT_CHARS, "0",
                OFFSET, "1", LIMIT, "1", SHOW_COLUMN,
            ),
            "offset/limit",
        )
        self.assertEqual(
            json.loads(stdout.decode("utf-8")),
            [{"path": "b.txt", "line": 1, "snippet": "target", "column": 4}],
        )

    # -- 4. 无结果与错误路径 --------------------------------------------------

    def test_no_hit_outputs_empty_results(self) -> None:
        self._write_demo()

        stdout = self.assert_success_clean(
            run_args(self.root, "zzz", SHOW_COLUMN), "无命中：JSON"
        )
        self.assertEqual(stdout, b"[]\n")
        csv_out = self.assert_success_clean(
            run_args(self.root, "zzz", SHOW_COLUMN, FORMAT, "csv"), "无命中：CSV"
        )
        self.assertEqual(csv_out, b"path,line,snippet,column\r\n")

    def test_duplicate_flag_fails_before_scan(self) -> None:
        proc = run_args(self.root, "target", SHOW_COLUMN, SHOW_COLUMN)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, b"")
        stderr = proc.stderr.decode("utf-8")
        self.assertIn(SHOW_COLUMN, stderr)
        self.assertIn("只能指定一次", stderr)

    def test_token_as_keyword_and_value_stays_literal(self) -> None:
        self._write("a.txt", f"x {SHOW_COLUMN} y\n".encode("utf-8"))

        # 作为关键词：按字面文本检索，不开启列号。
        stdout = self.assert_success_clean(
            run_args(self.root, SHOW_COLUMN), "同名关键词"
        )
        self.assertEqual(
            json.loads(stdout.decode("utf-8")),
            [{"path": "a.txt", "line": 1, "snippet": f"x {SHOW_COLUMN} y"}],
        )
        # 作为其他选项的取值：按字面文本消费，不开启列号。
        stdout = self.assert_success_clean(
            run_args(self.root, "x", "--and-keyword", SHOW_COLUMN), "同名取值"
        )
        self.assertEqual(
            json.loads(stdout.decode("utf-8")),
            [{"path": "a.txt", "line": 1, "snippet": f"x {SHOW_COLUMN} y"}],
        )

    # -- 5. main(argv) 参数入口一致 -------------------------------------------

    def test_main_entry_matches_cli(self) -> None:
        self._write_demo()
        argv = [
            str(self.root), "TARGET", IGNORE_CASE, OR_KEYWORD, "go",
            ALL_LINES, CONTEXT_CHARS, "0", SHOW_COLUMN,
        ]
        cli = self.assert_success_clean(run_args(self.root, *argv[1:]), "CLI")

        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = _BinaryStream(), _BinaryStream()
        try:
            code = main(argv)
            out = sys.stdout.buffer.getvalue()
            err = sys.stderr.buffer.getvalue()
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr
        self.assertEqual(code, 0)
        self.assertEqual(err, b"")
        self.assertEqual(out, cli, "main(argv) 与命令行输出必须一致")


if __name__ == "__main__":
    unittest.main()

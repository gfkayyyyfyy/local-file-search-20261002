"""``--format csv`` 输出格式的端到端回归测试。

通过命令入口 ``python -m local_search <目录> <关键词> ... --format csv`` 固定
从命令行输入到 CSV 文本输出的行为：

- 未指定 ``--format`` 或指定 ``json`` 时输出与既有 JSON 完全一致；``csv``
  时输出不带 BOM 的 UTF-8 CSV，表头固定为 ``path,line,snippet``，每个命中
  项一条记录，每条记录（含表头）以 CRLF 结束，无命中时只输出表头及 CRLF；
- 列值沿用结果项的相对路径、行号与片段；字段含逗号、双引号或换行时用双
  引号包围，内部双引号写成两个双引号，其余字符保持原样，经 CSV 读取后
  还原为原文本；
- 格式选择只影响序列化：结果排序、默认每文件首个合格行、``--all-lines``
  与 ``--limit`` 截断行为在两种格式下完全一致；
- 文件不可读或 UTF-8 解码失败时照常向标准错误告警并跳过，告警不混入
  CSV；数量限制仍不省略候选坏文件告警；
- 取值只接受小写字面值 ``json`` 或 ``csv``；缺值、重复、空值、空白、
  大写或其他取值时退出码 2、标准输出为空、标准错误包含 ``--format`` 与
  对应原因，且在扫描前报错；看似开关的取值仍作为格式值校验；
- ``local_search.main`` 的参数入口遵循相同规则；
- 查询只读源文件，不创建导出文件，不留下索引。

所有资料由测试在 TemporaryDirectory 中独立准备并自动清理，仅使用 Python
标准库、离线运行。期望值全部以字面量直接写出，不调用任何被测函数来生成
期望结果。
"""

import csv
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

FORMAT = "--format"
ALL_LINES = "--all-lines"
IGNORE_CASE = "--ignore-case"
CONTEXT_CHARS = "--context-chars"
LIMIT = "--limit"


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


class CsvFormatTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _write(self, rel: str, data: bytes) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def _write_text(self, rel: str, text: str) -> None:
        self._write(rel, text.encode("utf-8"))

    def _write_demo_tree(self) -> None:
        """验收演示目录：a.txt 两行 + notes/b.md 一行。"""
        self._write_text("a.txt", "Target one\ntarget two\n")
        self._write_text("notes/b.md", "target three\n")

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

    def assert_argument_error(self, proc: subprocess.CompletedProcess, reason: str, label: str) -> None:
        """输入边界错误：退出码 2、标准输出为空、标准错误含 --format 与原因。"""
        self.assertEqual(proc.returncode, 2, f"{label}: 退出码应为 2，实际 {proc.returncode}")
        self.assertEqual(proc.stdout, b"", f"{label}: 出错时标准输出必须为空")
        stderr = proc.stderr.decode("utf-8")
        self.assertIn(FORMAT, stderr, f"{label}: 标准错误应包含 --format，实际 {stderr!r}")
        self.assertIn(reason, stderr, f"{label}: 标准错误应说明原因 {reason!r}，实际 {stderr!r}")

    def _snapshot_files(self) -> dict[str, bytes]:
        return {
            str(p.relative_to(self.root)).replace(os.sep, "/"): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    # -- 1. 验收演示场景：CSV 字节级输出与 JSON 对照 -------------------------

    def test_demo_scenario_csv_exact_bytes(self) -> None:
        self._write_demo_tree()

        proc = run_args(
            self.root,
            "TARGET", IGNORE_CASE, ALL_LINES, CONTEXT_CHARS, "0", LIMIT, "2",
            FORMAT, "csv",
        )
        stdout = self.assert_success_clean(proc, "演示：--format csv")
        self.assertEqual(
            stdout,
            b"path,line,snippet\r\na.txt,1,Target\r\na.txt,2,target\r\n",
            "表头及两条记录，CRLF 结束，不带 BOM",
        )
        self.assertFalse(stdout.startswith(b"\xef\xbb\xbf"), "输出不得带 BOM")

    def test_demo_scenario_without_format_keeps_json(self) -> None:
        self._write_demo_tree()
        tail = (IGNORE_CASE, ALL_LINES, CONTEXT_CHARS, "0", LIMIT, "2")

        plain = self.assert_success_clean(
            run_args(self.root, "TARGET", *tail), "演示：无 --format"
        )
        explicit = self.assert_success_clean(
            run_args(self.root, "TARGET", *tail, FORMAT, "json"), "演示：--format json"
        )
        self.assertEqual(plain, explicit, "缺省与显式 json 的输出必须一致")
        self.assertEqual(
            json.loads(plain.decode("utf-8")),
            [
                {"path": "a.txt", "line": 1, "snippet": "Target"},
                {"path": "a.txt", "line": 2, "snippet": "target"},
            ],
        )

    def test_no_hit_outputs_header_only(self) -> None:
        self._write_demo_tree()

        proc = run_args(self.root, "zzz", FORMAT, "csv")
        stdout = self.assert_success_clean(proc, "无命中")
        self.assertEqual(stdout, b"path,line,snippet\r\n")

    # -- 2. 字段转义：逗号、双引号、换行 -----------------------------------

    def test_snippet_with_comma_and_quote_round_trips(self) -> None:
        self._write_text("q.txt", 'say "hi, there" target\nplain target\n')

        proc = run_args(
            self.root, "target", ALL_LINES, CONTEXT_CHARS, "20", FORMAT, "csv"
        )
        stdout = self.assert_success_clean(proc, "片段含逗号与双引号")
        self.assertEqual(
            stdout,
            b"path,line,snippet\r\n"
            b'q.txt,1,"say ""hi, there"" target"\r\n'
            b"q.txt,2,plain target\r\n",
        )
        rows = list(csv.reader(io.StringIO(stdout.decode("utf-8"), newline="")))
        self.assertEqual(
            rows,
            [
                ["path", "line", "snippet"],
                ["q.txt", "1", 'say "hi, there" target'],
                ["q.txt", "2", "plain target"],
            ],
            "经 CSV 读取后应还原为原文本",
        )

    def test_path_with_comma_is_quoted(self) -> None:
        self._write_text("weird,name.txt", "target here\n")

        stdout = self.assert_success_clean(
            run_args(self.root, "target", FORMAT, "csv"), "路径含逗号"
        )
        self.assertEqual(
            stdout,
            b"path,line,snippet\r\n\"weird,name.txt\",1,target here\r\n",
        )

    def test_path_with_newline_is_quoted(self) -> None:
        # 文件名中的换行进入 path 列：字段含换行时整体加引号，CSV 读取后还原。
        self._write_text("we\nird.txt", "target here\n")

        stdout = self.assert_success_clean(
            run_args(self.root, "target", FORMAT, "csv"), "路径含换行"
        )
        self.assertEqual(
            stdout,
            b'path,line,snippet\r\n"we\nird.txt",1,target here\r\n',
        )
        rows = list(csv.reader(io.StringIO(stdout.decode("utf-8"), newline="")))
        self.assertEqual(
            rows,
            [["path", "line", "snippet"], ["we\nird.txt", "1", "target here"]],
        )

    def test_utf8_fields_pass_through_unquoted(self) -> None:
        self._write_text("笔记.md", "目标 target 中文\n")

        stdout = self.assert_success_clean(
            run_args(self.root, "target", FORMAT, "csv"), "非 ASCII 字段"
        )
        self.assertEqual(
            stdout,
            "path,line,snippet\r\n笔记.md,1,目标 target 中文\r\n".encode("utf-8"),
            "不含特殊字符的非 ASCII 字段保持原样，不加引号",
        )

    # -- 3. 格式选择只影响序列化 ---------------------------------------------

    def test_csv_matches_json_items_order_and_limit(self) -> None:
        self._write_demo_tree()

        json_items = json.loads(
            self.assert_success_clean(
                run_args(
                    self.root,
                    "TARGET", IGNORE_CASE, ALL_LINES, CONTEXT_CHARS, "0", LIMIT, "2",
                ),
                "JSON 对照",
            ).decode("utf-8")
        )
        csv_stdout = self.assert_success_clean(
            run_args(
                self.root,
                "TARGET", IGNORE_CASE, ALL_LINES, CONTEXT_CHARS, "0", LIMIT, "2",
                FORMAT, "csv",
            ),
            "CSV 对照",
        )
        rows = list(csv.reader(io.StringIO(csv_stdout.decode("utf-8"), newline="")))
        self.assertEqual(rows[0], ["path", "line", "snippet"])
        self.assertEqual(
            rows[1:],
            [[item["path"], str(item["line"]), item["snippet"]] for item in json_items],
            "CSV 记录应与 JSON 结果项一一对应，顺序与 --limit 截断一致",
        )

    def test_default_first_hit_per_file_unchanged_in_csv(self) -> None:
        self._write_text("a.txt", "target one\ntarget two\n")

        stdout = self.assert_success_clean(
            run_args(self.root, "target", FORMAT, "csv"), "默认每文件首行"
        )
        self.assertEqual(
            stdout,
            b"path,line,snippet\r\na.txt,1,target one\r\n",
            "默认仍只返回每个文件的首个合格行",
        )

    def test_option_order_invariance_with_format(self) -> None:
        self._write_demo_tree()
        observed = []
        for tail in (
            (IGNORE_CASE, ALL_LINES, CONTEXT_CHARS, "0", LIMIT, "2", FORMAT, "csv"),
            (FORMAT, "csv", LIMIT, "2", CONTEXT_CHARS, "0", ALL_LINES, IGNORE_CASE),
            (ALL_LINES, FORMAT, "csv", IGNORE_CASE, LIMIT, "2", CONTEXT_CHARS, "0"),
        ):
            with self.subTest(tail=tail):
                proc = run_args(self.root, "TARGET", *tail)
                observed.append(self.assert_success_clean(proc, "任意顺序"))
        self.assertEqual(len(set(observed)), 1, "选项顺序不影响输出")

    # -- 4. 告警不混入 CSV，数量限制不省略告警 -------------------------------

    def test_decode_warning_goes_to_stderr_not_csv(self) -> None:
        self._write_text("good.txt", "target ok\n")
        self._write("bad.txt", b"target \xff\xfe bad\n")

        proc = run_args(self.root, "target", FORMAT, "csv")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(
            proc.stdout,
            b"path,line,snippet\r\ngood.txt,1,target ok\r\n",
            "CSV 只含表头与命中记录，不混入告警",
        )
        stderr = proc.stderr.decode("utf-8")
        self.assertIn("bad.txt", stderr)
        self.assertIn("无法按 UTF-8 解码", stderr)

    def test_limit_does_not_omit_bad_file_warning_in_csv(self) -> None:
        self._write_text("a.txt", "target here\n")
        self._write("b.txt", b"bad \xff\xff\n")

        proc = run_args(self.root, "target", FORMAT, "csv", LIMIT, "1")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(
            proc.stdout,
            b"path,line,snippet\r\na.txt,1,target here\r\n",
        )
        stderr = proc.stderr.decode("utf-8")
        self.assertIn("b.txt", stderr)
        self.assertIn("无法按 UTF-8 解码", stderr)

    # -- 5. 取值边界：缺值、重复、非法取值在扫描前报错 ------------------------

    def test_missing_value_and_duplicate_fail_before_scan(self) -> None:
        self._write_demo_tree()
        proc = run_args(self.root, "TARGET", FORMAT)
        self.assert_argument_error(proc, "缺少值", "缺值")
        proc = run_args(self.root, "TARGET", FORMAT, "csv", FORMAT, "json")
        self.assert_argument_error(proc, "只能指定一次", "重复指定")

    def test_invalid_values_fail_before_scan(self) -> None:
        self._write_demo_tree()
        for raw in ("", " ", " csv", "csv ", "JSON", "CSV", "Csv", "xml", "tsv"):
            with self.subTest(raw=raw):
                proc = run_args(self.root, "TARGET", FORMAT, raw)
                self.assert_argument_error(proc, "小写的 json 或 csv", f"取值 {raw!r}")

    def test_value_equal_to_switch_is_consumed_as_value(self) -> None:
        self._write_demo_tree()
        # --all-lines 被当作 --format 的取值消费：报取值错误，开关不启用。
        proc = run_args(self.root, "TARGET", FORMAT, ALL_LINES)
        self.assert_argument_error(proc, "小写的 json 或 csv", "取值看似开关")

    # -- 6. main 的参数入口遵循相同规则 ---------------------------------------

    def _run_main(self, argv: list[str]) -> tuple[int, bytes, bytes]:
        stdout, stderr = _BinaryStream(), _BinaryStream()
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = stdout, stderr  # type: ignore[assignment]
        try:
            code = main(argv)
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr
        return code, stdout.buffer.getvalue(), stderr.buffer.getvalue()

    def test_main_entry_csv_and_json(self) -> None:
        self._write_demo_tree()
        base = [str(self.root), "TARGET", IGNORE_CASE, ALL_LINES, CONTEXT_CHARS, "0"]

        code, stdout, stderr = self._run_main([*base, LIMIT, "2", FORMAT, "csv"])
        self.assertEqual(code, 0)
        self.assertEqual(stderr, b"")
        self.assertEqual(
            stdout,
            b"path,line,snippet\r\na.txt,1,Target\r\na.txt,2,target\r\n",
        )

        code, stdout, stderr = self._run_main([*base, LIMIT, "2"])
        self.assertEqual(code, 0)
        self.assertEqual(stderr, b"")
        self.assertEqual(
            json.loads(stdout.decode("utf-8")),
            [
                {"path": "a.txt", "line": 1, "snippet": "Target"},
                {"path": "a.txt", "line": 2, "snippet": "target"},
            ],
        )

    def test_main_entry_invalid_format_fails_before_scan(self) -> None:
        self._write_demo_tree()
        code, stdout, stderr = self._run_main([str(self.root), "TARGET", FORMAT, "CSV"])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, b"")
        self.assertIn(FORMAT, stderr.decode("utf-8"))
        self.assertIn("小写的 json 或 csv", stderr.decode("utf-8"))

    # -- 7. 查询只读源文件且不创建导出文件或索引 -------------------------------

    def test_csv_query_is_read_only_and_creates_no_files(self) -> None:
        self._write_demo_tree()
        before = self._snapshot_files()
        proc = run_args(
            self.root,
            "TARGET", IGNORE_CASE, ALL_LINES, CONTEXT_CHARS, "0", LIMIT, "2",
            FORMAT, "csv",
        )
        after = self._snapshot_files()
        self.assertEqual(before, after, "查询后源文件字节不得变化，且不得新增任何文件/索引")
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()

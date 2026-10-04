"""``--format`` 输出格式选择的端到端回归测试。

通过命令入口 ``python -m local_search <目录> <关键词> ... --format <json|csv>``
固定从命令行输入到结果序列化的行为：

- 未指定 ``--format`` 或显式指定 ``json`` 时输出与既有行为完全一致的
  JSON 数组；指定 ``csv`` 时改为输出 CSV 文本，表头固定为
  ``path,line,snippet``，每个命中项一条记录，列值沿用相对路径、行号与
  片段；
- CSV 为不带 BOM 的 UTF-8，每条记录以 CRLF 结束；字段含逗号、双引号或
  换行时用双引号包围，内部双引号写成两个双引号，其余字符保持原样，
  经标准 CSV 读取后还原为原文本；无命中时只输出表头及 CRLF，退出码零；
- 格式选择只影响序列化：结果排序、默认每文件首个合格行与 ``--all-lines``
  行为不变，``--limit`` 仍在排序后截取结果项；
- 文件不可读或 UTF-8 解码失败时照常向标准错误告警并跳过该文件，告警不
  混入 CSV；数量限制仍不省略候选坏文件告警；
- ``--format`` 与其他选项先后顺序不限、至多出现一次；缺值、重复或取值
  不是精确的小写 ``json``、``csv`` 时退出码 2、标准输出为空、标准错误
  包含 ``--format`` 与原因，且在扫描前报错；看似开关的取值仍作为格式值
  校验；
- CSV 只写入标准输出，不创建导出文件；查询只读源文件，不留下索引。

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

FORMAT = "--format"
LIMIT = "--limit"
ALL_LINES = "--all-lines"
IGNORE_CASE = "--ignore-case"
CONTEXT_CHARS = "--context-chars"

HEADER = b"path,line,snippet\r\n"


def run_args(directory: Path, *argv: str) -> subprocess.CompletedProcess:
    """以任意命令行参数调用 python -m local_search，返回完成的子进程。"""
    return subprocess.run(
        [sys.executable, "-m", "local_search", str(directory), *argv],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )


class FormatCsvTest(unittest.TestCase):
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

    def assert_success_clean(self, proc: subprocess.CompletedProcess, label: str) -> None:
        """场景 label：退出码必须为 0、标准错误必须为空。"""
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

    # -- 1. 验收演示场景：CSV 输出表头与两条记录，去掉 --format 仍为 JSON ---

    def test_demo_scenario_csv_output(self) -> None:
        self._write_demo_tree()

        proc = run_args(
            self.root, "TARGET", IGNORE_CASE, ALL_LINES, CONTEXT_CHARS, "0",
            LIMIT, "2", FORMAT, "csv",
        )
        self.assert_success_clean(proc, "演示：--format csv")
        self.assertEqual(
            proc.stdout,
            HEADER + b"a.txt,1,Target\r\na.txt,2,target\r\n",
            "应输出表头及 a.txt 第 1、2 行两条记录，CRLF 结束，不带 BOM",
        )

    def test_demo_scenario_without_format_still_json(self) -> None:
        self._write_demo_tree()

        proc = run_args(
            self.root, "TARGET", IGNORE_CASE, ALL_LINES, CONTEXT_CHARS, "0",
            LIMIT, "2",
        )
        self.assert_success_clean(proc, "演示：无 --format")
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8")),
            [
                {"path": "a.txt", "line": 1, "snippet": "Target"},
                {"path": "a.txt", "line": 2, "snippet": "target"},
            ],
        )

    def test_explicit_json_matches_default_output(self) -> None:
        self._write_demo_tree()
        argv = ("TARGET", IGNORE_CASE, ALL_LINES, CONTEXT_CHARS, "0", LIMIT, "2")

        default_proc = run_args(self.root, *argv)
        json_proc = run_args(self.root, *argv, FORMAT, "json")
        self.assert_success_clean(json_proc, "显式 --format json")
        self.assertEqual(json_proc.stdout, default_proc.stdout)

    # -- 2. CSV 字节形态：无 BOM、CRLF、表头固定、无命中只有表头 ------------

    def test_csv_has_no_bom_and_crlf_records(self) -> None:
        self._write_text("a.txt", "target here\n")

        proc = run_args(self.root, "target", FORMAT, "csv")
        self.assert_success_clean(proc, "字节形态")
        self.assertFalse(
            proc.stdout.startswith(b"\xef\xbb\xbf"), "CSV 不得带 UTF-8 BOM"
        )
        self.assertEqual(proc.stdout, HEADER + b"a.txt,1,target here\r\n")
        self.assertNotIn(b"\n", proc.stdout.replace(b"\r\n", b""), "记录只能以 CRLF 结束")

    def test_no_hit_outputs_header_only(self) -> None:
        self._write_text("a.txt", "alpha\nbeta\n")

        proc = run_args(self.root, "target", FORMAT, "csv")
        self.assert_success_clean(proc, "无命中")
        self.assertEqual(proc.stdout, HEADER, "无命中时只输出表头及 CRLF")

    # -- 3. 字段引用：逗号、双引号、换行包围，内部双引号写两个 --------------

    def test_fields_with_comma_and_quote_roundtrip(self) -> None:
        self._write_text("a.txt", 'say "target, hi" now\n')

        proc = run_args(self.root, "target", FORMAT, "csv")
        self.assert_success_clean(proc, "逗号与双引号")
        self.assertEqual(
            proc.stdout,
            HEADER + b'a.txt,1,"say ""target, hi"" now"\r\n',
        )
        rows = list(csv.reader(io.StringIO(proc.stdout.decode("utf-8"))))
        self.assertEqual(
            rows,
            [["path", "line", "snippet"], ["a.txt", "1", 'say "target, hi" now']],
            "经 CSV 读取后片段应还原为原文本",
        )

    def test_unicode_and_plain_fields_unquoted(self) -> None:
        self._write_text("a.txt", "目标 target 文本\n")

        proc = run_args(self.root, "target", FORMAT, "csv")
        self.assert_success_clean(proc, "非 ASCII 原样")
        self.assertEqual(
            proc.stdout,
            HEADER + "a.txt,1,目标 target 文本\r\n".encode("utf-8"),
            "不含逗号、双引号、换行的字段不加引号，非 ASCII 字符原样输出",
        )

    # -- 4. 格式选择只影响序列化：排序、--all-lines、--limit 行为不变 --------

    def test_csv_respects_sorted_order_and_limit(self) -> None:
        self._write_text("z.txt", "target zed\n")
        self._write_text("a.txt", "target a1\ntarget a2\n")

        proc = run_args(self.root, "target", ALL_LINES, LIMIT, "2", FORMAT, "csv")
        self.assert_success_clean(proc, "排序后截取")
        self.assertEqual(
            proc.stdout,
            HEADER + b"a.txt,1,target a1\r\na.txt,2,target a2\r\n",
            "--limit 仍在排序后的结果序列上截取",
        )

    def test_csv_default_mode_one_item_per_file(self) -> None:
        self._write_text("a.txt", "target a1\ntarget a2\n")

        proc = run_args(self.root, "target", FORMAT, "csv")
        self.assert_success_clean(proc, "默认每文件一项")
        self.assertEqual(proc.stdout, HEADER + b"a.txt,1,target a1\r\n")

    def test_format_combines_with_other_options_in_any_order(self) -> None:
        self._write_demo_tree()
        expected = HEADER + b"a.txt,1,Target\r\na.txt,2,target\r\n"
        for argv in (
            ("TARGET", IGNORE_CASE, ALL_LINES, CONTEXT_CHARS, "0", LIMIT, "2", FORMAT, "csv"),
            ("TARGET", FORMAT, "csv", LIMIT, "2", CONTEXT_CHARS, "0", ALL_LINES, IGNORE_CASE),
            ("TARGET", ALL_LINES, FORMAT, "csv", IGNORE_CASE, LIMIT, "2", CONTEXT_CHARS, "0"),
        ):
            with self.subTest(argv=argv):
                proc = run_args(self.root, *argv)
                self.assert_success_clean(proc, "任意顺序")
                self.assertEqual(proc.stdout, expected)

    # -- 5. 告警不混入 CSV，数量限制不省略坏文件告警 -------------------------

    def test_warning_goes_to_stderr_not_csv(self) -> None:
        self._write_text("a.txt", "target here\n")
        self._write("b.txt", b"bad \xff\xff\n")

        proc = run_args(self.root, "target", LIMIT, "1", FORMAT, "csv")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(
            proc.stdout,
            HEADER + b"a.txt,1,target here\r\n",
            "CSV 标准输出不得混入告警",
        )
        stderr = proc.stderr.decode("utf-8")
        self.assertIn("b.txt", stderr)
        self.assertIn("无法按 UTF-8 解码", stderr)

    # -- 6. 取值边界：缺值、重复、非小写 json/csv 均在扫描前报错 -------------

    def test_missing_value_and_duplicate_fail_before_scan(self) -> None:
        self._write_demo_tree()
        proc = run_args(self.root, "TARGET", FORMAT)
        self.assert_argument_error(proc, "缺少值", "缺值")
        proc = run_args(self.root, "TARGET", FORMAT, "csv", FORMAT, "json")
        self.assert_argument_error(proc, "只能指定一次", "重复指定")

    def test_invalid_values_fail_before_scan(self) -> None:
        self._write_demo_tree()
        for raw in ("", " ", " json", "json ", "JSON", "Csv", "CSV", "xml", "tsv"):
            with self.subTest(raw=raw):
                proc = run_args(self.root, "TARGET", FORMAT, raw)
                self.assert_argument_error(proc, "小写的 json 或 csv", f"取值 {raw!r}")

    def test_value_equal_to_switch_is_consumed_as_value(self) -> None:
        self._write_demo_tree()
        # --all-lines 被当作 --format 的取值消费：报取值错误，开关不启用。
        proc = run_args(self.root, "TARGET", FORMAT, ALL_LINES)
        self.assert_argument_error(proc, "小写的 json 或 csv", "取值看似开关")

    # -- 7. CSV 只写标准输出，查询只读源文件且不留下索引 ---------------------

    def test_csv_query_is_read_only_and_creates_no_files(self) -> None:
        self._write_demo_tree()
        before = self._snapshot_files()
        proc = run_args(
            self.root, "TARGET", IGNORE_CASE, ALL_LINES, CONTEXT_CHARS, "0",
            LIMIT, "2", FORMAT, "csv",
        )
        after = self._snapshot_files()
        self.assertEqual(before, after, "查询后源文件字节不得变化，且不得新增任何导出文件/索引")
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()

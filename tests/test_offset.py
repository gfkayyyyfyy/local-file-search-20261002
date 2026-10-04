"""``--offset`` 跳过结果项数量的端到端回归测试。

通过命令入口 ``python -m local_search <目录> <关键词> ... --offset <N>`` 固定
从命令行输入到 JSON / CSV 结果输出的行为：

- 先按既有条件筛选、按 ``path`` 的区分大小写 Unicode 字典序（同路径 ``line``
  升序）得到完整结果序列，再跳过前 N 项，最后应用 ``--limit``；未指定
  ``--limit`` 时返回全部剩余项；
- N 按结果项计数：默认每个文件只返回首个合格行（一项），``--all-lines`` 下
  每个合格行分别计数，同一行关键词重复出现不增加计数；
- 未指定 ``--offset`` 等同于 0，原有输出不变；偏移等于或大于合格项总数时
  退出码为 0，JSON 输出 ``[]``，CSV 仅输出原有表头及 CRLF；
- 取值只接受 0 至 1000 的非空 ASCII 十进制数字串，允许前导零；缺值、重复、
  空值、空白、首尾空白、正负号、小数、非 ASCII 数字及超范围值均在扫描前
  失败：退出码 2、标准输出为空、标准错误包含 ``--offset`` 与对应原因，
  不输出异常堆栈；
- 紧随该选项的参数即使看似开关（如 ``--all-lines``）也只作为数值校验，
  不开启开关；关键词或其他文本选项取值中的 ``--offset`` 仍按字面处理；
- 不可读或非法 UTF-8 候选文件仍逐文件告警并整体跳过，偏移不省略告警；
  被路径或格式排除的文件仍不读取、不告警；
- 结果字段、片段规则、输出格式及源文件只读行为不变，不留下索引或导出文件。

所有资料由测试在 TemporaryDirectory 中独立准备并自动清理，仅使用 Python
标准库、离线运行。期望值全部以字面量直接写出，不调用任何被测函数来生成
期望结果。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

OFFSET = "--offset"
LIMIT = "--limit"
ALL_LINES = "--all-lines"
CONTEXT_CHARS = "--context-chars"
PATH_EXCLUDES = "--path-excludes"
FORMAT = "--format"


def run_args(directory: Path, *argv: str) -> subprocess.CompletedProcess:
    """以任意命令行参数调用 python -m local_search，返回完成的子进程。"""
    return subprocess.run(
        [sys.executable, "-m", "local_search", str(directory), *argv],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )


class OffsetTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "root"
        (self.root / "notes").mkdir(parents=True)
        # 验收演示目录：a.txt 两行、notes/b.md 一行，共三个合格行。
        (self.root / "a.txt").write_text("target one\ntarget two\n", encoding="utf-8")
        (self.root / "notes" / "b.md").write_text("target three\n", encoding="utf-8")

    def _write(self, rel: str, data: bytes) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def assert_success_clean(self, proc: subprocess.CompletedProcess, label: str) -> list:
        """场景 label：退出码必须为 0、标准错误必须为空，返回解析后的 JSON。"""
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

    def assert_argument_error(self, proc: subprocess.CompletedProcess, reason: str, label: str) -> None:
        """输入边界错误：退出码 2、标准输出为空、标准错误含 --offset 与原因。"""
        self.assertEqual(proc.returncode, 2, f"{label}: 退出码应为 2，实际 {proc.returncode}")
        self.assertEqual(proc.stdout, b"", f"{label}: 出错时标准输出必须为空")
        stderr = proc.stderr.decode("utf-8")
        self.assertIn(OFFSET, stderr, f"{label}: 标准错误应包含 --offset，实际 {stderr!r}")
        self.assertIn(reason, stderr, f"{label}: 标准错误应说明原因 {reason!r}，实际 {stderr!r}")
        self.assertFalse(stderr.startswith("Traceback"), f"{label}: 不得输出异常堆栈")

    def _snapshot_files(self) -> dict[str, bytes]:
        return {
            str(p.relative_to(self.root)).replace(os.sep, "/"): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    # -- 1. 验收演示场景：--offset 1 --limit 1 只取 a.txt 第 2 行 ----------

    def test_demo_scenario_offset_one_limit_one(self) -> None:
        results = self.assert_success_clean(
            run_args(
                self.root, "target", ALL_LINES, CONTEXT_CHARS, "0",
                OFFSET, "1", LIMIT, "1",
            ),
            "演示：--offset 1 --limit 1",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 2, "snippet": "target"}],
        )

    def test_demo_scenario_offset_two_without_limit(self) -> None:
        results = self.assert_success_clean(
            run_args(self.root, "target", ALL_LINES, CONTEXT_CHARS, "0", OFFSET, "2"),
            "演示：--offset 2 无上限",
        )
        self.assertEqual(
            results,
            [{"path": "notes/b.md", "line": 1, "snippet": "target"}],
        )

    # -- 2. 偏移等于或大于合格项总数：退出码 0、空结果 ---------------------

    def test_offset_equal_to_total_returns_empty_json(self) -> None:
        results = self.assert_success_clean(
            run_args(self.root, "target", ALL_LINES, OFFSET, "3"), "偏移等于总数"
        )
        self.assertEqual(results, [])

    def test_offset_beyond_total_returns_empty_json(self) -> None:
        results = self.assert_success_clean(
            run_args(self.root, "target", ALL_LINES, OFFSET, "1000"), "偏移超过总数"
        )
        self.assertEqual(results, [])

    def test_offset_beyond_total_csv_keeps_header_with_crlf(self) -> None:
        proc = run_args(self.root, "target", ALL_LINES, OFFSET, "3", FORMAT, "csv")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, b"")
        self.assertEqual(proc.stdout, b"path,line,snippet\r\n")

    # -- 3. 计数口径与未指定时的不变性 -------------------------------------

    def test_default_mode_counts_first_qualified_line_per_file(self) -> None:
        # 默认每文件首个合格行算一项：共两项，跳 1 项后只剩 notes/b.md。
        results = self.assert_success_clean(
            run_args(self.root, "target", CONTEXT_CHARS, "0", OFFSET, "1"),
            "默认口径跳一项",
        )
        self.assertEqual(
            results,
            [{"path": "notes/b.md", "line": 1, "snippet": "target"}],
        )

    def test_repeated_keyword_on_same_line_counts_once(self) -> None:
        self._write("rep.txt", "target target target\n".encode("utf-8"))
        results = self.assert_success_clean(
            run_args(self.root, "target", ALL_LINES, CONTEXT_CHARS, "0", OFFSET, "3"),
            "行内重复不增加计数",
        )
        # 合格项共 4 项（a.txt 两行、notes/b.md 一行、rep.txt 一行），跳 3 剩 rep.txt。
        self.assertEqual(
            results,
            [{"path": "rep.txt", "line": 1, "snippet": "target"}],
        )

    def test_offset_zero_and_unspecified_leave_output_unchanged(self) -> None:
        baseline = run_args(self.root, "target", ALL_LINES, CONTEXT_CHARS, "0")
        for tail in ((), (OFFSET, "0"), (OFFSET, "000")):
            with self.subTest(tail=tail):
                proc = run_args(self.root, "target", ALL_LINES, CONTEXT_CHARS, "0", *tail)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stderr, b"")
                self.assertEqual(proc.stdout, baseline.stdout)

    def test_offset_applies_before_limit(self) -> None:
        # 完整序列三项，先跳 1 项再取前 1 项，得到 a.txt 第 2 行。
        results = self.assert_success_clean(
            run_args(self.root, "target", ALL_LINES, CONTEXT_CHARS, "0", LIMIT, "1", OFFSET, "1"),
            "先偏移后截断",
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 2, "snippet": "target"}],
        )

    # -- 4. 取值边界：前导零与 0、1000 合法 --------------------------------

    def test_leading_zeros_and_bounds_accepted(self) -> None:
        for raw, expected in (
            ("0", 3),
            ("000", 3),
            ("01", 2),
            ("002", 1),
            ("3", 0),
            ("01000", 0),
            ("1000", 0),
        ):
            with self.subTest(raw=raw):
                results = self.assert_success_clean(
                    run_args(self.root, "target", ALL_LINES, OFFSET, raw),
                    f"取值 {raw!r}",
                )
                self.assertEqual(len(results), expected)

    def test_invalid_values_fail_before_scan(self) -> None:
        bad_format = ("", " ", " 1", "1 ", "+1", "-1", "1.0", "１２", "1a")
        for raw in bad_format:
            with self.subTest(raw=raw):
                proc = run_args(self.root, "target", OFFSET, raw)
                self.assert_argument_error(proc, "非空的 ASCII 十进制数字串", f"格式 {raw!r}")
        for raw in ("1001", "01001", "9" * 5000):
            with self.subTest(raw=raw):
                proc = run_args(self.root, "target", OFFSET, raw)
                self.assert_argument_error(proc, "超出范围 0-1000", f"范围 {raw!r}")

    def test_missing_value_and_duplicate_fail_before_scan(self) -> None:
        proc = run_args(self.root, "target", OFFSET)
        self.assert_argument_error(proc, "缺少值", "缺值")
        proc = run_args(self.root, "target", OFFSET, "1", OFFSET, "2")
        self.assert_argument_error(proc, "只能指定一次", "重复指定")

    def test_value_equal_to_switch_is_consumed_as_value(self) -> None:
        # --all-lines 被当作 --offset 的取值消费：报取值格式错误，开关不启用。
        proc = run_args(self.root, "target", OFFSET, ALL_LINES)
        self.assert_argument_error(proc, "非空的 ASCII 十进制数字串", "取值看似开关")

    def test_offset_token_as_keyword_and_value_stays_literal(self) -> None:
        self._write("cfg.txt", "line --offset here\n".encode("utf-8"))
        # 关键词恰为 --offset 时仍按字面文本检索。
        results = self.assert_success_clean(
            run_args(self.root, OFFSET), "关键词恰为 --offset"
        )
        self.assertEqual(
            results,
            [{"path": "cfg.txt", "line": 1, "snippet": "line --offset here"}],
        )
        # 其他文本选项取值中的 --offset 同样按字面处理。
        results = self.assert_success_clean(
            run_args(self.root, "target", PATH_EXCLUDES, OFFSET), "排除片段恰为 --offset"
        )
        self.assertEqual(len(results), 2)

    # -- 5. 告警与筛选语义不因偏移改变 -------------------------------------

    def test_invalid_utf8_candidate_still_warns_with_offset(self) -> None:
        self._write("bad.txt", b"target bad\n\xff\xfe\n")
        proc = run_args(self.root, "target", ALL_LINES, CONTEXT_CHARS, "0", OFFSET, "1")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8")),
            [
                {"path": "a.txt", "line": 2, "snippet": "target"},
                {"path": "notes/b.md", "line": 1, "snippet": "target"},
            ],
        )
        stderr = proc.stderr.decode("utf-8")
        self.assertIn("bad.txt", stderr)
        self.assertIn("无法按 UTF-8 解码", stderr)

    def test_path_excluded_file_is_not_read_or_warned_with_offset(self) -> None:
        self._write("notes/bad.txt", b"target bad\n\xff\xfe\n")
        proc = run_args(
            self.root, "target", ALL_LINES, CONTEXT_CHARS, "0",
            PATH_EXCLUDES, "bad", OFFSET, "1",
        )
        results = self.assert_success_clean(proc, "排除坏文件")
        self.assertEqual(
            results,
            [
                {"path": "a.txt", "line": 2, "snippet": "target"},
                {"path": "notes/b.md", "line": 1, "snippet": "target"},
            ],
        )

    # -- 6. 查询只读源文件且不留下索引或导出文件 ----------------------------

    def test_offset_query_is_read_only_and_leaves_no_files(self) -> None:
        before = self._snapshot_files()
        proc = run_args(
            self.root, "target", ALL_LINES, CONTEXT_CHARS, "0", OFFSET, "1", LIMIT, "1"
        )
        after = self._snapshot_files()
        self.assertEqual(before, after, "查询后源文件字节不得变化，且不得新增任何文件/索引")
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()

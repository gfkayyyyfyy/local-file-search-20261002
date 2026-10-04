"""``--limit`` 结果数量上限选项的端到端回归测试。

通过命令入口 ``python -m local_search <目录> <关键词> --limit <数量>`` 固定从
命令行输入到 JSON 结果输出的行为：

- 不指定选项时完全保留既有输出；指定后只返回“同一查询未加限制时所得数组”的
  前 N 项；
- 截取发生在完整扫描与排序之后：仍先按 ``path`` 的区分大小写 Unicode 码点序、
  同路径按 ``line`` 升序排列，再取前 N 项，绝不按目录扫描顺序截断；
- N 按结果项计数：默认每个文件仍只返回首个合格行（一项），``--all-lines`` 下
  每个合格行各算一项；不足 N 项时全部返回，无命中时输出 ``[]``；
- 每项仍只含 ``path``、``line``、``snippet``，片段与行号保持原样；
- 选项可与其他选项以任意顺序同用；紧随 ``--limit`` 的参数即使是
  ``--all-lines`` 也作为取值校验、不启用开关；关键词或其他选项取值中的
  ``--limit`` 仍按字面文本处理；
- 数量上限不省略候选坏文件的既有告警（即使已得到足够结果）；被路径/格式筛选
  排除的文件仍不读取、不告警；
- 取值只接受非空 ASCII 十进制数字串、允许前导零、范围 1-1000；缺值、重复、
  空值、空白、带正负号、小数、非 ASCII 数字或越界均在扫描前以退出码 2、
  空标准输出报错；
- 查询只读源文件，不留下索引或其他文件；仅使用 Python 标准库、离线运行。

所有资料由测试在 TemporaryDirectory 中独立准备并自动清理。期望值全部以字面量
直接写出，不调用任何被测函数来生成期望结果。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

LIMIT = "--limit"
ALL_LINES = "--all-lines"
IGNORE_CASE = "--ignore-case"
CONTEXT_CHARS = "--context-chars"
PATH_OPTION = "--path-contains"
EXCLUDES_OPTION = "--path-excludes"


def run_args(directory: Path, *argv: str) -> subprocess.CompletedProcess:
    """以任意命令行参数调用 python -m local_search，返回完成的子进程。"""
    return subprocess.run(
        [sys.executable, "-m", "local_search", str(directory), *argv],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )


class LimitTest(unittest.TestCase):
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

    def assert_item_shape(self, item: dict, label: str) -> None:
        self.assertEqual(set(item.keys()), {"path", "line", "snippet"})
        self.assertIsInstance(item["path"], str)
        self.assertIsInstance(item["line"], int)
        self.assertNotIn("\n", item["snippet"])
        self.assertNotIn("\r", item["snippet"])

    def assert_argument_error(self, proc: subprocess.CompletedProcess, reason: str, label: str) -> None:
        """输入边界错误：退出码 2、标准输出为空、标准错误同时含 --limit 与原因。"""
        self.assertEqual(proc.returncode, 2, f"{label}: 退出码应为 2，实际 {proc.returncode}")
        self.assertEqual(proc.stdout, b"", f"{label}: 出错时标准输出必须为空")
        stderr = proc.stderr.decode("utf-8")
        self.assertIn(LIMIT, stderr, f"{label}: 标准错误应包含 {LIMIT!r}，实际 {stderr!r}")
        self.assertIn(reason, stderr, f"{label}: 标准错误应说明原因 {reason!r}，实际 {stderr!r}")
        self.assertFalse(stderr.startswith("Traceback"))

    def _snapshot_files(self) -> dict[str, bytes]:
        return {
            str(p.relative_to(self.root)).replace(os.sep, "/"): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    # -- 1. 验收场景 -------------------------------------------------------

    def test_acceptance_demo_scenario(self) -> None:
        # 新建且仅含 a.txt 与 notes/b.md 的演示目录：
        # a.txt 两行依次为 Target one、target two；notes/b.md 只有 target three。
        self._write_text("a.txt", "Target one\ntarget two\n")
        self._write_text("notes/b.md", "target three\n")

        proc = run_args(
            self.root, "TARGET", IGNORE_CASE, ALL_LINES, CONTEXT_CHARS, "0", LIMIT, "2"
        )
        results = self.assert_success_clean(proc, "验收：limit 2")
        self.assertEqual(
            results,
            [
                {"path": "a.txt", "line": 1, "snippet": "Target"},
                {"path": "a.txt", "line": 2, "snippet": "target"},
            ],
        )
        for item in results:
            self.assert_item_shape(item, "验收：limit 2")

        # 去掉 --limit 2 后，末尾增加 notes/b.md 第 1 行。
        full = self.assert_success_clean(
            run_args(self.root, "TARGET", IGNORE_CASE, ALL_LINES, CONTEXT_CHARS, "0"),
            "验收：无 limit",
        )
        self.assertEqual(
            full,
            [
                {"path": "a.txt", "line": 1, "snippet": "Target"},
                {"path": "a.txt", "line": 2, "snippet": "target"},
                {"path": "notes/b.md", "line": 1, "snippet": "target"},
            ],
        )
        self.assertEqual(full[:2], results)

    # -- 2. 按排序后的数组截取，而非目录扫描顺序 ---------------------------

    def test_limit_applies_after_sorting_not_scan_order(self) -> None:
        # 构造排序序与“每文件一项”的扫描序不同的情形：
        # z.txt 与 a.txt 各有命中；区分大小写码点序中 A.txt 在 a.txt 前。
        self._write_text("z.txt", "target z\n")
        self._write_text("a.txt", "target a\n")
        self._write_text("A.txt", "target A\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", LIMIT, "2"), "排序后截取"
        )
        self.assertEqual(
            results,
            [
                {"path": "A.txt", "line": 1, "snippet": "target A"},
                {"path": "a.txt", "line": 1, "snippet": "target a"},
            ],
            "上限按排序后的前 N 项生效，与目录扫描顺序无关",
        )

    def test_limit_all_lines_takes_first_n_sorted_items(self) -> None:
        self._write_text("b.txt", "target b1\ntarget b2\n")
        self._write_text("a.txt", "target a1\ntarget a2\ntarget a3\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", ALL_LINES, LIMIT, "3"), "逐行+limit"
        )
        self.assertEqual(
            [(item["path"], item["line"]) for item in results],
            [("a.txt", 1), ("a.txt", 2), ("a.txt", 3)],
            "逐行模式下每个合格行各算一项，按排序序取前 3 项",
        )

        results = self.assert_success_clean(
            run_args(self.root, "target", ALL_LINES, LIMIT, "4"), "逐行+limit 4"
        )
        self.assertEqual(
            [(item["path"], item["line"]) for item in results],
            [("a.txt", 1), ("a.txt", 2), ("a.txt", 3), ("b.txt", 1)],
        )

    # -- 3. 默认模式每文件一项，N 按结果项计数 -----------------------------

    def test_limit_counts_one_item_per_file_by_default(self) -> None:
        # 两个文件各有多行命中；默认每文件只一项，limit 1 只取排序第一的文件。
        self._write_text("b.txt", "target b1\ntarget b2\ntarget b3\n")
        self._write_text("a.txt", "target a1\ntarget a2\ntarget a3\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", LIMIT, "1"), "默认模式 limit 1"
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "target a1"}],
            "默认每文件只返回首个合格行，按一项计数",
        )

    # -- 4. 不足 N 项全部返回；无命中输出 []；N 恰等于结果数 ---------------

    def test_fewer_items_than_limit_returns_all(self) -> None:
        self._write_text("a.txt", "target one\n")
        results = self.assert_success_clean(
            run_args(self.root, "target", ALL_LINES, LIMIT, "1000"), "不足 N 项"
        )
        self.assertEqual(results, [{"path": "a.txt", "line": 1, "snippet": "target one"}])

    def test_no_hit_with_limit_outputs_empty_array(self) -> None:
        self._write_text("a.txt", "alpha\nbeta\n")
        results = self.assert_success_clean(
            run_args(self.root, "target", LIMIT, "5"), "无命中"
        )
        self.assertEqual(results, [])

    def test_limit_equal_to_total_count_returns_all(self) -> None:
        self._write_text("a.txt", "target one\ntarget two\n")
        results = self.assert_success_clean(
            run_args(self.root, "target", ALL_LINES, CONTEXT_CHARS, "0", LIMIT, "2"),
            "N 恰等于总数",
        )
        self.assertEqual(
            results,
            [
                {"path": "a.txt", "line": 1, "snippet": "target"},
                {"path": "a.txt", "line": 2, "snippet": "target"},
            ],
        )

    # -- 5. 边界取值 1 与 1000 合法，允许前导零 ----------------------------

    def test_boundary_values_accepted(self) -> None:
        self._write_text("a.txt", "target\n")
        for raw, expected_len in (("1", 1), ("0001", 1), ("1000", 1), ("01000", 1)):
            with self.subTest(raw=raw):
                results = self.assert_success_clean(
                    run_args(self.root, "target", LIMIT, raw), f"边界值 {raw!r}"
                )
                self.assertEqual(len(results), expected_len)

    # -- 6. 与其他选项任意顺序同用，输出一致 -------------------------------

    def test_option_order_invariance(self) -> None:
        self._write_text("notes/a.txt", "Target one\nTarget two\nTarget three\n")
        orders = (
            (IGNORE_CASE, ALL_LINES, CONTEXT_CHARS, "0", LIMIT, "2"),
            (LIMIT, "2", IGNORE_CASE, CONTEXT_CHARS, "0", ALL_LINES),
            (CONTEXT_CHARS, "000", LIMIT, "02", ALL_LINES, IGNORE_CASE),
            (ALL_LINES, LIMIT, "2", PATH_OPTION, "notes/", IGNORE_CASE, CONTEXT_CHARS, "0"),
        )
        observed = []
        for tail in orders:
            proc = run_args(self.root, "TARGET", *tail)
            results = self.assert_success_clean(proc, f"选项顺序 {tail}")
            self.assertEqual(
                results,
                [
                    {"path": "notes/a.txt", "line": 1, "snippet": "Target"},
                    {"path": "notes/a.txt", "line": 2, "snippet": "Target"},
                ],
            )
            observed.append(proc.stdout)
        self.assertEqual(len(set(observed)), 1)

    def test_limit_combines_with_path_excludes(self) -> None:
        # 排除 notes/ 后只剩 a.txt 的命中；limit 不改变筛选语义。
        self._write_text("a.txt", "target one\ntarget two\n")
        self._write_text("notes/b.md", "target note\n")
        results = self.assert_success_clean(
            run_args(
                self.root, "target", ALL_LINES, EXCLUDES_OPTION, "notes/", LIMIT, "5"
            ),
            "limit+排除",
        )
        self.assertEqual(
            [(item["path"], item["line"]) for item in results],
            [("a.txt", 1), ("a.txt", 2)],
        )

    # -- 7. 坏文件告警不被省略；被筛选排除的文件不读取、不告警 -------------

    def test_bad_file_still_warned_when_limit_already_satisfied(self) -> None:
        # A.txt 排序在 broken.txt 前且单独即可凑够 limit 1；
        # 坏文件的告警仍必须出现（完整扫描先于截取）。
        self._write("A.txt", b"target first\n")
        self._write("broken.txt", b"target before\n\xff\n")
        proc = run_args(self.root, "target", LIMIT, "1")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8")),
            [{"path": "A.txt", "line": 1, "snippet": "target first"}],
        )
        stderr = proc.stderr.decode("utf-8")
        self.assertIn("broken.txt", stderr)
        self.assertIn("无法按 UTF-8 解码", stderr)

    def test_excluded_bad_file_not_read_or_warned_even_with_limit(self) -> None:
        # 路径筛选先于读取：排除的坏文件既不读取也不告警，limit 不改变这一点。
        self._write("keep.txt", b"target keep\n")
        self._write("notes/broken.txt", b"target x\n\xff\n")
        proc = run_args(
            self.root, "target", ALL_LINES, LIMIT, "1", PATH_OPTION, "keep"
        )
        results = self.assert_success_clean(proc, "排除坏文件+limit")
        self.assertEqual(
            results, [{"path": "keep.txt", "line": 1, "snippet": "target keep"}]
        )
        self.assertEqual(proc.stderr, b"")

    # -- 8. 字面边界：关键词与其他取值中的 --limit 仍是字面文本 -------------

    def test_keyword_equal_to_limit_token_is_literal(self) -> None:
        self._write_text("f.txt", "x --limit y\n")
        # 第二个位置参数恰为 --limit 时仍是关键词。
        results = self.assert_success_clean(
            run_args(self.root, LIMIT, LIMIT, "1"), "关键词恰为 --limit"
        )
        self.assertEqual(
            results,
            [{"path": "f.txt", "line": 1, "snippet": "x --limit y"}],
        )

    def test_limit_token_inside_other_value_is_literal(self) -> None:
        self._write_text("--limit.md", "target odd\n")
        # --path-contains 的取值 --limit 是字面片段，而非数量选项。
        results = self.assert_success_clean(
            run_args(self.root, "target", PATH_OPTION, LIMIT), "片段恰为 --limit"
        )
        self.assertEqual(
            results,
            [{"path": "--limit.md", "line": 1, "snippet": "target odd"}],
        )

    # -- 9. 非法取值：退出码 2、空标准输出、标准错误含 --limit 与原因 -------

    def test_invalid_values_fail_before_scan(self) -> None:
        cases = {
            "缺值": (LIMIT,),
            "空值": (LIMIT, ""),
            "纯空白": (LIMIT, "   "),
            "前导空白": (LIMIT, " 1"),
            "尾随空白": (LIMIT, "1 "),
            "正号": (LIMIT, "+1"),
            "负号": (LIMIT, "-1"),
            "小数": (LIMIT, "1.5"),
            "非 ASCII 数字": (LIMIT, "１"),
            "夹带字母": (LIMIT, "1a"),
            "零": (LIMIT, "0"),
            "全零": (LIMIT, "0000"),
            "超上限": (LIMIT, "1001"),
            "前导零后超上限": (LIMIT, "001001"),
        }
        for label, tail in cases.items():
            with self.subTest(label=label):
                proc = run_args(self.root, "target", *tail)
                self.assert_argument_error(proc, "错误", label)

    def test_duplicate_limit_fails_before_scan(self) -> None:
        proc = run_args(self.root, "target", LIMIT, "1", LIMIT, "2")
        self.assert_argument_error(proc, "只能指定一次", "重复 --limit")

    def test_limit_value_equal_to_all_lines_switch_does_not_enable_it(self) -> None:
        # --all-lines 被当作 --limit 的取值消费并校验失败：
        # 退出码 2、标准输出为空，开关未启用（也未开始扫描）。
        proc = run_args(self.root, "TARGET", LIMIT, ALL_LINES)
        self.assert_argument_error(proc, "ASCII 十进制数字串", "取值恰为开关")

    # -- 10. 只读源文件，不留下索引或其他文件 ------------------------------

    def test_query_is_read_only_and_leaves_no_index(self) -> None:
        self._write_text("a.txt", "target one\ntarget two\ntarget three\n")
        before = self._snapshot_files()
        proc = run_args(self.root, "target", ALL_LINES, LIMIT, "2")
        after = self._snapshot_files()
        self.assertEqual(before, after, "查询后源文件字节不得变化，且不得新增任何文件/索引")
        results = self.assert_success_clean(proc, "只读/无索引")
        self.assertEqual(
            [(item["path"], item["line"]) for item in results],
            [("a.txt", 1), ("a.txt", 2)],
        )


if __name__ == "__main__":
    unittest.main()

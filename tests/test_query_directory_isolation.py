"""同一进程内切换检索目录的回归测试。

local_search 每次查询都独立解析参数、重新扫描指定目录，不在进程内保留任何
跨调用状态。本文件在同一 Python 进程内通过公开入口 ``local_search.main``
连续发起四次调用，核对每次调用的目录与选项都独立生效，互不影响：

- 第 1 次：在目录 A 用默认选项查询 ``target``。A 只含 ``notes/a.txt``，
  内容为 ``target A\\nTarget later\\n``（UTF-8，LF 换行）；区分大小写时
  仅第 1 行命中，默认只返回首个命中，片段为整行 ``target A``。
- 第 2 次：切换到并列目录 B，查询 ``TARGET`` 并开启 ``--ignore-case``、
  ``--all-lines`` 与 ``--context-chars 0``。B 只含 ``notes/a.txt``，
  内容为 ``TARGET B\\ntarget tail\\n``；忽略大小写后两行均命中，各返回
  一项，上下文为 0 使片段只保留命中关键词原文，分别为 ``TARGET`` 与
  ``target``。两个目录的相对路径同为 ``notes/a.txt``，本步骤同时确认
  结果确实来自 B 而非 A 的残留。
- 第 3 次：查询一个从未创建的目录，关键词仍为 ``target``，返回 2、标准
  输出为空，标准错误包含该路径与“目录不存在”的原因。
- 第 4 次：用与第 1 次完全相同的参数再次查询 A，完整输出（返回值、标准
  输出、标准错误）与第 1 次逐字节一致，确认 B 的选项组合与失败调用均未
  在进程内留下影响后续查询的状态。

每次调用单独捕获 ``sys.stdout``/``sys.stderr`` 的 ``.buffer`` 核对 UTF-8
字节输出；成功调用的标准输出为以换行结束的 UTF-8 JSON 数组，每项仅含
``path``、``line``、``snippet`` 三个键，路径使用正斜杠，核对完整内容与
顺序。期望值全部以字面量直接写出，不调用任何被测检索逻辑生成。每次查询
后核对 A、B 两个目录下资料文件的集合与原始字节完全不变，确认查询本身
既不写入资料也不生成索引或结果文件。

所有资料由用例在 TemporaryDirectory 中独立准备并自动清理，仅使用标准库、
离线运行，不依赖演示目录、网络或额外权限。

可独立执行：``python -m unittest tests.test_query_directory_isolation``，
也能被既有的 unittest 测试发现方式（tests 包下 test_*.py）收集。
"""

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import local_search

# 两个并列目录各自的 notes/a.txt 固定内容（字符串中的 \n 即 LF 换行，按
# UTF-8 编码成字节）：A 仅第 1 行含小写 target；B 两行分别含 TARGET 与
# target，用于检验 --ignore-case 与 --all-lines 的组合。
A_BYTES = "target A\nTarget later\n".encode("utf-8")
B_BYTES = "TARGET B\ntarget tail\n".encode("utf-8")


class _CapturedStream:
    """sys.stdout/sys.stderr 的最小替身：被测代码只使用其 .buffer 写字节。"""

    def __init__(self) -> None:
        self.buffer = io.BytesIO()


def run_main(argv: list) -> tuple:
    """在当前进程内调用公开入口 local_search.main。

    返回 (返回值, 标准输出字节, 标准错误字节)。
    """
    stdout = _CapturedStream()
    stderr = _CapturedStream()
    with mock.patch.object(sys, "stdout", stdout), mock.patch.object(sys, "stderr", stderr):
        code = local_search.main(argv)
    return code, stdout.buffer.getvalue(), stderr.buffer.getvalue()


def snapshot_tree(root: Path) -> dict:
    """目录下全部文件的 相对路径 -> 字节 快照（正斜杠相对路径）。"""
    return {
        str(p.relative_to(root)).replace(os.sep, "/"): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
    }


class QueryDirectoryIsolationTest(unittest.TestCase):
    """同一进程内连续切换检索目录：每次调用的目录与选项独立生效。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.parent = Path(self._tmp.name)
        # 并列目录 A、B，各自只含 notes/a.txt，相对路径刻意相同。
        self.dir_a = self.parent / "A"
        self.dir_b = self.parent / "B"
        (self.dir_a / "notes").mkdir(parents=True)
        (self.dir_b / "notes").mkdir(parents=True)
        (self.dir_a / "notes" / "a.txt").write_bytes(A_BYTES)
        (self.dir_b / "notes" / "a.txt").write_bytes(B_BYTES)
        # 从未创建的目录，供失败调用使用。
        self.dir_missing = self.parent / "MISSING"

    def assert_trees_unchanged(self, label: str) -> None:
        """核对 A、B 的文件集合与原始字节保持初始状态，无新增文件。"""
        self.assertEqual(
            snapshot_tree(self.dir_a),
            {"notes/a.txt": A_BYTES},
            f"{label}: 目录 A 的资料不得被查询改动（不得写入资料或生成索引等文件）",
        )
        self.assertEqual(
            snapshot_tree(self.dir_b),
            {"notes/a.txt": B_BYTES},
            f"{label}: 目录 B 的资料不得被查询改动（不得写入资料或生成索引等文件）",
        )

    def assert_items_shape(self, results: list, label: str) -> None:
        """核对每项仅含 path/line/snippet 三个键，路径使用正斜杠。"""
        for item in results:
            self.assertEqual(
                set(item),
                {"path", "line", "snippet"},
                f"{label}: 每项仅应含 path、line、snippet 三个键，实际 {sorted(item)}",
            )
            self.assertNotIn(
                "\\",
                item["path"],
                f"{label}: 路径应统一使用正斜杠，实际 {item['path']!r}",
            )

    def test_directory_and_options_are_independent_per_call(self) -> None:
        self.assert_trees_unchanged("初始状态")
        self.assertFalse(self.dir_missing.exists(), "失败调用所用目录应从未创建")

        # -- 第 1 次：A，默认选项，区分大小写，只返回首个命中 ----------------
        argv_a = [str(self.dir_a), "target"]
        code_a, out_a, err_a = run_main(argv_a)
        self.assert_trees_unchanged("第 1 次查询后")
        self.assertEqual(code_a, 0, f"第 1 次查询返回值应为 0，实际 {code_a}，stderr={err_a!r}")
        self.assertEqual(err_a, b"", f"第 1 次查询标准错误应为空，实际 {err_a!r}")
        expected_a_text = '[{"path": "notes/a.txt", "line": 1, "snippet": "target A"}]\n'
        self.assertEqual(
            out_a.decode("utf-8"),
            expected_a_text,
            "第 1 次查询：A 中仅 notes/a.txt 第 1 行含小写 target，"
            "默认只返回首个命中，片段为整行 target A",
        )
        self.assertTrue(out_a.endswith(b"\n"), "第 1 次查询标准输出应以换行结束")
        results_a = json.loads(out_a.decode("utf-8"))
        self.assertEqual(
            results_a,
            [{"path": "notes/a.txt", "line": 1, "snippet": "target A"}],
        )
        self.assert_items_shape(results_a, "第 1 次查询")

        # -- 第 2 次：B，--ignore-case --all-lines --context-chars 0 --------
        argv_b = [
            str(self.dir_b),
            "TARGET",
            "--ignore-case",
            "--all-lines",
            "--context-chars",
            "0",
        ]
        code_b, out_b, err_b = run_main(argv_b)
        self.assert_trees_unchanged("第 2 次查询后")
        self.assertEqual(code_b, 0, f"第 2 次查询返回值应为 0，实际 {code_b}，stderr={err_b!r}")
        self.assertEqual(err_b, b"", f"第 2 次查询标准错误应为空，实际 {err_b!r}")
        expected_b_text = (
            '[{"path": "notes/a.txt", "line": 1, "snippet": "TARGET"}, '
            '{"path": "notes/a.txt", "line": 2, "snippet": "target"}]\n'
        )
        self.assertEqual(
            out_b.decode("utf-8"),
            expected_b_text,
            "第 2 次查询：忽略大小写后 B 的两行均命中，--all-lines 各返回一项，"
            "--context-chars 0 使片段只保留命中关键词原文 TARGET 与 target",
        )
        self.assertTrue(out_b.endswith(b"\n"), "第 2 次查询标准输出应以换行结束")
        results_b = json.loads(out_b.decode("utf-8"))
        self.assertEqual(
            results_b,
            [
                {"path": "notes/a.txt", "line": 1, "snippet": "TARGET"},
                {"path": "notes/a.txt", "line": 2, "snippet": "target"},
            ],
            "两项必须按行号递增、完整一致；相对路径与 A 相同，"
            "片段 TARGET/target 只能来自 B 的内容，确认结果未混入 A",
        )
        self.assertEqual(
            [item["line"] for item in results_b],
            [1, 2],
            "同一路径内结果必须按行号递增",
        )
        self.assert_items_shape(results_b, "第 2 次查询")

        # -- 第 3 次：未创建的目录，返回 2，标准输出为空 --------------------
        argv_missing = [str(self.dir_missing), "target"]
        code_m, out_m, err_m = run_main(argv_missing)
        self.assert_trees_unchanged("第 3 次查询后")
        self.assertEqual(code_m, 2, f"第 3 次查询返回值应为 2，实际 {code_m}")
        self.assertEqual(out_m, b"", f"第 3 次查询标准输出应为空，实际 {out_m!r}")
        stderr_m = err_m.decode("utf-8")
        self.assertIn(
            str(self.dir_missing),
            stderr_m,
            f"第 3 次查询标准错误应包含未创建的目录路径，实际 {stderr_m!r}",
        )
        self.assertIn(
            "目录不存在",
            stderr_m,
            f"第 3 次查询标准错误应说明目录不存在，实际 {stderr_m!r}",
        )

        # -- 第 4 次：用首次参数再次查询 A，完整输出与首次逐字节一致 --------
        code_a2, out_a2, err_a2 = run_main(argv_a)
        self.assert_trees_unchanged("第 4 次查询后")
        self.assertEqual(
            (code_a2, out_a2, err_a2),
            (code_a, out_a, err_a),
            "第 4 次查询的返回值、标准输出、标准错误应与第 1 次完全一致，"
            "确认 B 的选项组合与失败调用未在进程内留下影响后续查询的状态",
        )
        self.assertEqual(out_a2.decode("utf-8"), expected_a_text)


if __name__ == "__main__":
    unittest.main()

"""同一进程内切换检索目录与选项的回归测试。

local_search 不在进程内保留任何“当前检索目录”或上一次查询的选项状态：每次
调用公开入口 ``local_search.main`` 都独立解析参数、独立扫描当次指定的目录，
先前调用使用过的目录、开关（``--ignore-case``、``--all-lines`` 等）与取值
选项（``--context-chars``）既不会泄漏到后续调用，一次失败的调用（目录不
存在，退出码 2）也不会影响后续调用。

本文件在一个临时父目录下准备两个并列目录 A、B，两者都只含
``notes/a.txt`` 这一个文件——两个目录中的相对路径完全相同，专门用于暴露
“结果从另一个目录混入却被相同相对路径掩盖”的问题：

- A/notes/a.txt 的 UTF-8 内容为 ``target A\\nTarget later\\n``；
- B/notes/a.txt 的 UTF-8 内容为 ``TARGET B\\ntarget tail\\n``。

同一进程内连续四次调用 ``local_search.main``：

1. 默认选项在 A 查询 ``target``（区分大小写、只取首个命中行、默认上下
   文）：第 2 行 ``Target later`` 因首字母大写不命中，结果仅含
   ``notes/a.txt`` 第 1 行，片段 ``target A``；
2. 在 B 查询 ``TARGET``，开启 ``--ignore-case``、``--all-lines`` 与
   ``--context-chars 0``：第 1、2 行各返回一项，0 上下文使片段恰好为命中
   词本身，按行号递增分别为 ``TARGET`` 与 ``target``；
3. 查询一个从未创建的并列目录，关键词仍为 ``target``：返回 2、标准输出
   完全为空，标准错误包含该路径与“目录不存在”的原因；
4. 再次以第 1 次的参数查询 A：完整标准输出（逐字节）与第 1 次一致——
   B 上使用过的忽略大小写/全行/0 上下文选项以及中间的失败调用都不得影响
   后续结果。

每次调用都各自新建替身单独捕获标准输出与标准错误，不串用。成功调用的
标准输出是 UTF-8 JSON 数组并以换行结束；每个结果项恰好含 path、line、
snippet 三个键，path 使用正斜杠。期望值（含完整内容与顺序）全部以字面量
直接写出，不调用任何被测检索/匹配逻辑生成；除解析 JSON 外还逐字节核对
完整输出文本，并显式断言另一个目录独有的片段文本（``target A``、
``TARGET B``、``target tail`` 等）没有跨目录混入。

每次查询后都对临时父目录（含 A、B 与未创建目录的父级）做完整文件快照
核对：A、B 的文件集合与每个文件的原始字节保持不变，没有新增索引、结果
或任何其他文件。所有资料由用例在 TemporaryDirectory 中自行准备并自动
清理，只依赖 Python 标准库，不依赖仓库中的演示目录、网络或额外权限。

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

# A/notes/a.txt：只有第 1 行以小写 target 开头；第 2 行 “Target later” 首
# 字母大写，区分大小写查询 target 时不命中。
A_TXT_BYTES = "target A\nTarget later\n".encode("utf-8")

# B/notes/a.txt：第 1 行全大写 TARGET，第 2 行小写 target；忽略大小写查询
# TARGET 且 --all-lines 时两行各产生一项。
B_TXT_BYTES = "TARGET B\ntarget tail\n".encode("utf-8")

# 临时父目录在任何查询之后都应保持的完整快照（正斜杠相对路径 -> 原始字节）。
EXPECTED_TREE = {
    "A/notes/a.txt": A_TXT_BYTES,
    "B/notes/a.txt": B_TXT_BYTES,
}

# 第 1 次（与第 4 次）查询 A 的完整期望输出：默认参数只取首个命中行，默认
# 上下文 30 足以覆盖整行，片段即 target A。
FIRST_QUERY_EXPECTED_TEXT = (
    '[{"path": "notes/a.txt", "line": 1, "snippet": "target A"}]\n'
)
FIRST_QUERY_EXPECTED = [
    {"path": "notes/a.txt", "line": 1, "snippet": "target A"},
]

# 第 2 次查询 B 的完整期望输出：两行均命中、按行号递增；--context-chars 0
# 使片段恰好为命中关键词本身（保留源行大小写）：TARGET、target。
SECOND_QUERY_EXPECTED_TEXT = (
    '[{"path": "notes/a.txt", "line": 1, "snippet": "TARGET"}, '
    '{"path": "notes/a.txt", "line": 2, "snippet": "target"}]\n'
)
SECOND_QUERY_EXPECTED = [
    {"path": "notes/a.txt", "line": 1, "snippet": "TARGET"},
    {"path": "notes/a.txt", "line": 2, "snippet": "target"},
]


class _CapturedStream:
    """sys.stdout/sys.stderr 的最小替身：被测代码只使用其 .buffer 写字节。"""

    def __init__(self) -> None:
        self.buffer = io.BytesIO()


def run_main(argv: list) -> tuple:
    """在当前进程内调用公开入口 local_search.main。

    每次调用都新建一对独立的替身流分别捕获标准输出与标准错误。
    返回 (返回值, 标准输出字节, 标准错误字节)。
    """
    stdout = _CapturedStream()
    stderr = _CapturedStream()
    with mock.patch.object(sys, "stdout", stdout), mock.patch.object(sys, "stderr", stderr):
        code = local_search.main(argv)
    return code, stdout.buffer.getvalue(), stderr.buffer.getvalue()


def snapshot_tree(root: Path) -> dict:
    """root 下全部文件的 相对路径 -> 字节 快照（正斜杠相对路径）。"""
    return {
        str(p.relative_to(root)).replace(os.sep, "/"): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
    }


class QueryDirectoryIsolationTest(unittest.TestCase):
    """同一进程内连续切换检索目录与选项：各次调用的目录和选项独立生效。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.parent = Path(self._tmp.name)
        self.dir_a = self.parent / "A"
        self.dir_b = self.parent / "B"
        (self.dir_a / "notes").mkdir(parents=True)
        (self.dir_b / "notes").mkdir(parents=True)
        (self.dir_a / "notes" / "a.txt").write_bytes(A_TXT_BYTES)
        (self.dir_b / "notes" / "a.txt").write_bytes(B_TXT_BYTES)
        # 故意不创建的并列目录，第 3 次查询指向它。
        self.missing_dir = self.parent / "missing"
        self.assertEqual(
            snapshot_tree(self.parent),
            EXPECTED_TREE,
            "用例初始父目录下应只有 A/notes/a.txt 与 B/notes/a.txt",
        )
        self.assertFalse(self.missing_dir.exists())

    def assert_tree_unchanged(self, label: str) -> None:
        """每次查询后：A、B 文件集合与原始字节不变，且无任何新增文件。"""
        self.assertEqual(
            snapshot_tree(self.parent),
            EXPECTED_TREE,
            f"{label}: 查询后父目录的文件集合与各文件字节必须完全不变"
            "（不得改写资料、不得生成索引或结果等额外文件）",
        )
        self.assertEqual(
            (self.dir_a / "notes" / "a.txt").read_bytes(),
            A_TXT_BYTES,
            f"{label}: A/notes/a.txt 原始字节必须保持不变",
        )
        self.assertEqual(
            (self.dir_b / "notes" / "a.txt").read_bytes(),
            B_TXT_BYTES,
            f"{label}: B/notes/a.txt 原始字节必须保持不变",
        )
        self.assertFalse(
            self.missing_dir.exists(),
            f"{label}: 查询不存在的目录不得把该目录或任何文件创建出来",
        )

    def assert_results_shape(self, results: list, label: str) -> None:
        """每个结果项必须且只能含 path、line、snippet 三个键，path 用正斜杠。"""
        for item in results:
            self.assertEqual(
                set(item.keys()),
                {"path", "line", "snippet"},
                f"{label}: 结果项只能含 path/line/snippet 三个键，实际 {item!r}",
            )
            self.assertNotIn(
                "\\", item["path"], f"{label}: path 必须使用正斜杠: {item['path']!r}"
            )

    def test_queries_to_different_directories_stay_independent(self) -> None:
        # 第 1 次查询：默认选项在 A 查询 target。
        first_argv = [str(self.dir_a), "target"]
        code, first_out, err = run_main(first_argv)
        self.assertEqual(code, 0, f"第 1 次查询返回值应为 0，实际 {code}，stderr={err!r}")
        self.assertEqual(err, b"", f"第 1 次查询标准错误应为空，实际 {err!r}")
        # UTF-8 JSON 数组、以换行结束、逐字节核对完整内容与顺序。
        first_text = first_out.decode("utf-8")
        self.assertTrue(first_text.endswith("\n"), "标准输出必须以换行结束")
        self.assertEqual(
            first_text,
            FIRST_QUERY_EXPECTED_TEXT,
            "第 1 次查询只应返回 A/notes/a.txt 第 1 行，片段 target A",
        )
        first_results = json.loads(first_text)
        self.assertEqual(first_results, FIRST_QUERY_EXPECTED)
        self.assert_results_shape(first_results, "第 1 次查询")
        # A 的第 2 行 “Target later” 不命中；B 中任何独有文本都不得混入。
        self.assertNotIn(b"Target later", first_out)
        self.assertNotIn(b"TARGET B", first_out)
        self.assertNotIn(b"target tail", first_out)
        self.assert_tree_unchanged("第 1 次查询后")

        # 第 2 次查询：在 B 查询 TARGET，开启忽略大小写、全行、0 上下文。
        second_argv = [
            str(self.dir_b),
            "TARGET",
            "--ignore-case",
            "--all-lines",
            "--context-chars",
            "0",
        ]
        code, second_out, err = run_main(second_argv)
        self.assertEqual(code, 0, f"第 2 次查询返回值应为 0，实际 {code}，stderr={err!r}")
        self.assertEqual(err, b"", f"第 2 次查询标准错误应为空，实际 {err!r}")
        second_text = second_out.decode("utf-8")
        self.assertTrue(second_text.endswith("\n"), "标准输出必须以换行结束")
        self.assertEqual(
            second_text,
            SECOND_QUERY_EXPECTED_TEXT,
            "第 2 次查询应按行号返回 B/notes/a.txt 第 1、2 行，片段恰为 TARGET、target",
        )
        second_results = json.loads(second_text)
        self.assertEqual(second_results, SECOND_QUERY_EXPECTED)
        self.assert_results_shape(second_results, "第 2 次查询")
        self.assertEqual(
            [item["line"] for item in second_results],
            [1, 2],
            "--all-lines 时两个命中行必须按行号递增各返回一项",
        )
        self.assertEqual(
            [item["snippet"] for item in second_results],
            ["TARGET", "target"],
            "--context-chars 0 时片段只保留命中词本身，并保留源行大小写",
        )
        # 相对路径与 A 中相同（都是 notes/a.txt），因此必须靠内容判定没有
        # 混入 A 的结果：A 独有的 target A / Target later 不得出现，且不得
        # 把 B 第 1 行的上下文 “ B” 带进片段。
        self.assertNotIn(b"target A", second_out)
        self.assertNotIn(b"Target later", second_out)
        self.assertNotIn(b"TARGET B", second_out)
        self.assertNotIn(b"target tail", second_out)
        self.assert_tree_unchanged("第 2 次查询后")

        # 第 3 次查询：未创建的目录，关键词仍为 target——失败调用。
        code, out, err = run_main([str(self.missing_dir), "target"])
        self.assertEqual(code, 2, "目录不存在时返回值必须为 2")
        self.assertEqual(out, b"", "失败调用的标准输出必须完全为空")
        stderr_text = err.decode("utf-8")
        self.assertIn(
            str(self.missing_dir),
            stderr_text,
            "标准错误必须包含查询时给定的不存在目录路径",
        )
        self.assertIn("目录不存在", stderr_text, "标准错误必须说明目录不存在的原因")
        self.assertTrue(stderr_text.endswith("\n"), "标准错误行必须以换行结束")
        self.assertNotIn("Traceback", stderr_text, "不得向用户打印异常堆栈")
        self.assert_tree_unchanged("第 3 次查询后")

        # 第 4 次查询：再次以第 1 次的参数查询 A——逐字节复现首次输出，
        # 证明 B 的选项与中间的失败调用都没有泄漏到后续调用。
        code, fourth_out, err = run_main(first_argv)
        self.assertEqual(code, 0, f"第 4 次查询返回值应为 0，实际 {code}，stderr={err!r}")
        self.assertEqual(err, b"", f"第 4 次查询标准错误应为空，实际 {err!r}")
        self.assertEqual(
            fourth_out,
            first_out,
            "再次以首次参数查询 A 时，完整标准输出必须与首次逐字节一致",
        )
        fourth_results = json.loads(fourth_out.decode("utf-8"))
        self.assertEqual(
            fourth_results,
            FIRST_QUERY_EXPECTED,
            "第 4 次查询结果必须与第 1 次完全一致：默认选项仍然生效",
        )
        self.assert_results_shape(fourth_results, "第 4 次查询")
        # 第 2 次的 --ignore-case 不得残留：A 第 2 行 Target later 仍不命中。
        self.assertNotIn(b"Target later", fourth_out)
        self.assert_tree_unchanged("第 4 次查询后")


if __name__ == "__main__":
    unittest.main()

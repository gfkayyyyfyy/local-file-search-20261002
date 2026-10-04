"""两次查询之间新增文件的回归测试。

local_search 每次查询都重新扫描选定目录、重新读取并按 UTF-8 解码候选文件，
不缓存目录列表、不留下索引文件，因此同一 Python 进程内连续调用公开入口
``local_search.main`` 时，两次查询之间新落入选定目录的文件应在下一次查询
中自然出现，并参与既有的排序。本文件覆盖两个相互独立的场景，场景内命令
行参数保持不变，新增操作只发生在两次查询之间：

- 正常场景：起初只有 ``z.txt``（``header\\ntarget stable\\n``），默认查询
  ``target`` 只返回 z.txt 第 2 行、片段 ``target stable``；首次查询后新增
  ``a.txt``（``other\\ntarget new\\ntarget later\\n``）与
  ``notes/b.MD``（``target nested\\n``，大写扩展名按既有规则同样纳入检索）；
  再次查询应依次返回 a.txt 第 2 行（``target new``）、notes/b.MD 第 1 行
  （``target nested``）、z.txt 第 2 行（``target stable``）——新结果按路径
  码点序插入原有排序，z.txt 的路径、行号、片段保持原样；a.txt 第 3 行的
  第二个命中按默认规则不另行返回。
- 解码边界场景：从相同的 z.txt 开始，首次查询后只新增 ``broken.txt``，其
  字节为 ``b'target\\n\\xff'``（首行其实含关键词，但整文件无法按 UTF-8
  解码）。再次查询仍只返回 z.txt 的原有命中，返回值为 0；标准错误恰有一条
  包含 ``broken.txt`` 与“无法按 UTF-8 解码”的告警，坏文件的前置命中不能
  出现。

新增文件只发生在查询之间，不涉及扫描期间文件变化的处理。每次查询前后都
核对选定目录下资料文件的集合与每个文件的字节完全相同，确保查询本身既不
改写资料也不生成索引等额外文件。每次查询都分别核对 UTF-8 标准输出中的
完整 JSON 数组（含默认分隔符与排序）、返回值与标准错误，并确认每个结果
项只含 path、line、snippet 三个键、path 使用正斜杠。

用例通过公开入口 ``local_search.main`` 在同一进程内驱动，以其返回值对应
退出码，并捕获 ``sys.stdout``/``sys.stderr`` 的 ``.buffer`` 核对 UTF-8
字节输出——与 ``python -m local_search`` 的输入输出约定一致。所有资料由
各用例在 TemporaryDirectory 中独立准备并自动清理，仅使用标准库、离线运行；
期望值全部以字面量直接写出，不调用任何被测检索逻辑生成。资料统一按 UTF-8
准备，唯独边界场景的 broken.txt 例外。

可独立执行：``python -m unittest tests.test_added_files_between_queries``，
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

# 两个场景共用的初始文件（UTF-8）：第 2 行命中，默认上下文 30 足以覆盖全
# 短行，片段即整行 target stable。
Z_TXT_BYTES = "header\ntarget stable\n".encode("utf-8")

# 正常场景首次查询后新增的两个文件：
# a.txt 第 2、3 行各有一个命中，默认参数只返回首个命中行（第 2 行）；
# notes/b.MD 第 1 行命中，大写 .MD 扩展名按既有规则同样纳入检索。
A_TXT_BYTES = "other\ntarget new\ntarget later\n".encode("utf-8")
B_MD_BYTES = "target nested\n".encode("utf-8")

# 解码边界场景首次查询后唯一新增的文件：首行含关键词，但末尾的 0xff 使整
# 个文件无法按 UTF-8 解码，必须整体跳过。
BROKEN_TXT_BYTES = b"target\n\xff"

# 首次查询（仅有 z.txt）的完整期望结果。
FIRST_RUN_EXPECTED = [
    {"path": "z.txt", "line": 2, "snippet": "target stable"},
]


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
    """选定目录下全部文件的 相对路径 -> 字节 快照（正斜杠相对路径）。"""
    return {
        str(p.relative_to(root)).replace(os.sep, "/"): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
    }


class _AddedFilesTestCase(unittest.TestCase):
    """公共夹具：每个用例一个独立临时选定目录，初始只有相同的 z.txt。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.z_path = self.root / "z.txt"
        self.z_path.write_bytes(Z_TXT_BYTES)

    def run_query(self, argv: list, label: str) -> tuple:
        """以不变的 argv 执行一次查询，核对查询前后资料快照完全一致。

        返回 (返回值, 标准输出字节, 标准错误字节)。
        """
        before = snapshot_tree(self.root)
        code, out, err = run_main(argv)
        self.assertEqual(
            snapshot_tree(self.root),
            before,
            f"{label}: 查询前后资料文件集合与各文件字节必须完全一致"
            "（查询不得改写资料、不得生成索引或其他文件）",
        )
        return code, out, err

    def assert_results_shape(self, results: list, label: str) -> None:
        """每个结果项必须且只能含 path、line、snippet 三个键，path 用正斜杠。"""
        for item in results:
            self.assertEqual(
                set(item.keys()),
                {"path", "line", "snippet"},
                f"{label}: 结果项只能含 path/line/snippet 三个键，实际 {item!r}",
            )
            self.assertNotIn("\\", item["path"], f"{label}: path 必须使用正斜杠: {item['path']!r}")


class AddedFilesBetweenQueriesTest(_AddedFilesTestCase):
    """同一进程内连续两次查询，之间新增文件，新结果参与既有排序。"""

    def test_added_files_appear_in_next_query_in_sorted_order(self) -> None:
        # 场景内参数保持不变：始终是 <目录> target（默认选项）。
        argv = [str(self.root), "target"]
        self.assertEqual(
            snapshot_tree(self.root),
            {"z.txt": Z_TXT_BYTES},
            "用例初始只应有 z.txt 且内容为 header\\ntarget stable\\n",
        )

        # 首次查询：只有 z.txt 第 2 行命中；返回值 0、标准错误为空。
        code, out, err = self.run_query(argv, "首次查询（仅有 z.txt）")
        self.assertEqual(code, 0, f"首次查询返回值应为 0，实际 {code}，stderr={err!r}")
        self.assertEqual(err, b"", f"首次查询标准错误应为空，实际 {err!r}")
        self.assertEqual(
            out.decode("utf-8"),
            '[{"path": "z.txt", "line": 2, "snippet": "target stable"}]\n',
            "首次查询的标准输出应为只含 z.txt 命中的完整 JSON 数组加换行",
        )
        first = json.loads(out.decode("utf-8"))
        self.assertEqual(first, FIRST_RUN_EXPECTED)
        self.assert_results_shape(first, "首次查询")
        self.assertEqual(self.z_path.read_bytes(), Z_TXT_BYTES, "查询不得改写 z.txt")

        # 两次查询之间新增 a.txt 与 notes/b.MD（目录也是新建的）。
        (self.root / "a.txt").write_bytes(A_TXT_BYTES)
        (self.root / "notes").mkdir()
        (self.root / "notes" / "b.MD").write_bytes(B_MD_BYTES)
        self.assertEqual(
            snapshot_tree(self.root),
            {
                "a.txt": A_TXT_BYTES,
                "notes/b.MD": B_MD_BYTES,
                "z.txt": Z_TXT_BYTES,
            },
            "新增后应有三个资料文件，z.txt 字节不变",
        )

        # 再次查询：新文件参与按路径码点序的既有排序——
        # a.txt < notes/b.MD < z.txt；原有 z.txt 的路径、行号、片段保持不变；
        # a.txt 第 3 行的第二个命中默认不返回。
        code, out, err = self.run_query(argv, "新增文件后的查询")
        self.assertEqual(code, 0, f"新增文件后返回值应为 0，实际 {code}，stderr={err!r}")
        self.assertEqual(err, b"", f"新增文件后标准错误应为空，实际 {err!r}")
        self.assertEqual(
            out.decode("utf-8"),
            '[{"path": "a.txt", "line": 2, "snippet": "target new"}, '
            '{"path": "notes/b.MD", "line": 1, "snippet": "target nested"}, '
            '{"path": "z.txt", "line": 2, "snippet": "target stable"}]\n',
            "第二次查询应按路径序返回三个首命中，原有 z.txt 命中保持原样",
        )
        second = json.loads(out.decode("utf-8"))
        self.assertEqual(
            second,
            [
                {"path": "a.txt", "line": 2, "snippet": "target new"},
                {"path": "notes/b.MD", "line": 1, "snippet": "target nested"},
                {"path": "z.txt", "line": 2, "snippet": "target stable"},
            ],
        )
        self.assert_results_shape(second, "新增文件后的查询")
        self.assertEqual(
            [item["path"] for item in second],
            ["a.txt", "notes/b.MD", "z.txt"],
            "新结果必须参与按路径码点序的既有排序，而非追加到末尾",
        )
        self.assertIn(
            {"path": "z.txt", "line": 2, "snippet": "target stable"},
            second,
            "原有 z.txt 命中的路径、行号、片段必须保持不变",
        )
        self.assertEqual(
            len(second),
            3,
            "默认参数下 a.txt 只返回首个命中行（第 2 行），第 3 行不得另行返回",
        )
        self.assertFalse(
            any(item["path"] == "a.txt" and item["line"] == 3 for item in second),
            "a.txt 第 3 行的第二个命中默认不应返回",
        )
        self.assertNotIn("target later", out.decode("utf-8"), "未返回行的片段不得出现在输出中")

        # 查询后三个文件（含原有 z.txt）字节均与新增后一致，且没有额外文件。
        self.assertEqual(
            snapshot_tree(self.root),
            {
                "a.txt": A_TXT_BYTES,
                "notes/b.MD": B_MD_BYTES,
                "z.txt": Z_TXT_BYTES,
            },
            "查询不得改写任何资料，也不得生成索引等额外文件",
        )


class AddedUndecodableFileBetweenQueriesTest(_AddedFilesTestCase):
    """两次查询之间新增无法按 UTF-8 解码的文件：整体跳过、一条告警。"""

    def test_added_undecodable_file_is_skipped_with_single_warning(self) -> None:
        argv = [str(self.root), "target"]
        self.assertEqual(snapshot_tree(self.root), {"z.txt": Z_TXT_BYTES})

        # 首次查询：只有 z.txt 命中，返回值 0、标准错误为空。
        code, out, err = self.run_query(argv, "首次查询（仅有 z.txt）")
        self.assertEqual(code, 0, f"首次查询返回值应为 0，实际 {code}，stderr={err!r}")
        self.assertEqual(err, b"", f"首次查询标准错误应为空，实际 {err!r}")
        self.assertEqual(
            out.decode("utf-8"),
            '[{"path": "z.txt", "line": 2, "snippet": "target stable"}]\n',
        )
        first = json.loads(out.decode("utf-8"))
        self.assertEqual(first, FIRST_RUN_EXPECTED)
        self.assert_results_shape(first, "首次查询")

        # 两次查询之间只新增 broken.txt：首行含关键词 target，但整文件因
        # 末尾 0xff 无法按 UTF-8 解码。
        broken_path = self.root / "broken.txt"
        broken_path.write_bytes(BROKEN_TXT_BYTES)
        self.assertEqual(
            snapshot_tree(self.root),
            {"broken.txt": BROKEN_TXT_BYTES, "z.txt": Z_TXT_BYTES},
        )

        # 再次查询：坏文件整体跳过，结果只剩 z.txt 的原有命中。
        code, out, err = self.run_query(argv, "新增坏文件后的查询")
        self.assertEqual(code, 0, "存在无法解码的新增文件时跳过它并告警，返回值仍为 0")
        self.assertEqual(
            out.decode("utf-8"),
            '[{"path": "z.txt", "line": 2, "snippet": "target stable"}]\n',
            "坏文件必须整体跳过：其首行的前置命中不能出现，只剩 z.txt 原有结果",
        )
        second = json.loads(out.decode("utf-8"))
        self.assertEqual(second, FIRST_RUN_EXPECTED, "z.txt 的路径、行号、片段保持不变")
        self.assert_results_shape(second, "新增坏文件后的查询")
        self.assertNotIn("broken.txt", out.decode("utf-8"), "标准输出不得出现坏文件的路径")
        self.assertNotIn("target later", out.decode("utf-8"))

        # 标准错误恰有一条告警：指向 broken.txt 且说明无法按 UTF-8 解码；
        # 合法的 z.txt 不得产生任何告警。
        stderr_text = err.decode("utf-8")
        warning_lines = stderr_text.splitlines()
        self.assertEqual(len(warning_lines), 1, f"标准错误应只有一条告警，实际 {warning_lines!r}")
        self.assertTrue(err.endswith(b"\n"), "告警行应以换行结束")
        self.assertIn("broken.txt", warning_lines[0], "告警应包含被跳过文件的相对路径")
        self.assertIn("无法按 UTF-8 解码", warning_lines[0], "告警应说明无法按 UTF-8 解码")
        self.assertNotIn("z.txt", stderr_text, "合法的 z.txt 不得产生告警")

        # 查询不得改写坏文件（保留原始字节）或 z.txt，也不得生成额外文件。
        self.assertEqual(broken_path.read_bytes(), BROKEN_TXT_BYTES, "查询不得改写 broken.txt")
        self.assertEqual(self.z_path.read_bytes(), Z_TXT_BYTES, "查询不得改写 z.txt")
        self.assertEqual(
            snapshot_tree(self.root),
            {"broken.txt": BROKEN_TXT_BYTES, "z.txt": Z_TXT_BYTES},
            "查询后文件集合与各文件字节必须与新增后完全一致",
        )


if __name__ == "__main__":
    unittest.main()

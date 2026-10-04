"""两次查询之间文件被改写的回归测试。

local_search 每次查询都重新扫描选定目录、重新读取并按 UTF-8 解码候选文件，
不缓存内容、不留下索引文件，因此同一 Python 进程内连续调用公开入口
``local_search.main`` 时，每次查询都应反映当时磁盘上的当前内容。本文件
覆盖三个场景，场景内命令行参数保持不变，只在查询之间改写资料：

- 场景一（默认参数）：只有 ``notes/a.txt``，初始内容为 ``target old\\n``，
  查询 ``target`` 返回第 1 行、片段 ``target old``；改写为
  ``header\\nnew target note\\nother\\nlast target\\n`` 后，默认查询只返回
  首个命中——第 2 行、片段 ``new target note``（第 4 行的第二个命中默认
  不返回）；再改写为 ``no match\\n`` 后输出 ``[]``。非空结果的 path 均为
  ``notes/a.txt``。
- 场景二（``--all-lines`` 与 ``--context-chars 0`` 的组合）：沿用场景一的
  同一次改写过程：初次片段仅为 ``target``；改写后第 2、4 行各返回一项，
  两项片段均为 ``target``；最后输出 ``[]``。
- 场景三（改写后的解码边界）：初始根目录下 ``a.txt``（``target old\\n``）
  与 ``b.md``（``target stable\\n``）两项均命中；在 a.txt 末尾追加单字节
  ``0xff`` 后再次查询，整个 a.txt 因无法按 UTF-8 解码被跳过并只产生一条
  包含 ``a.txt`` 与“无法按 UTF-8 解码”的标准错误告警，结果只剩 b.md
  第 1 行；把 a.txt 改写为合法的 ``target repaired\\n`` 后，它应重新以
  第 1 行、片段 ``target repaired`` 出现在 b.md 之前，告警消失。

所有改写只发生在查询之间；每次查询前后都核对选定目录下资料文件的集合与
每个文件的字节完全相同，确保查询本身既不写入资料也不生成索引等额外文件。
每次查询都分别核对 UTF-8 标准输出中的完整 JSON 数组（含默认分隔符与排序）、
返回值与标准错误。

用例通过公开入口 ``local_search.main`` 在同一进程内驱动，以其返回值对应
退出码，并捕获 ``sys.stdout``/``sys.stderr`` 的 ``.buffer`` 核对 UTF-8
字节输出——与 ``python -m local_search`` 的输入输出约定一致。所有资料由
各用例在 TemporaryDirectory 中独立准备并自动清理，仅使用标准库、离线运行；
期望值全部以字面量直接写出，不调用任何被测检索逻辑生成。资料统一按 UTF-8
准备，唯独场景三的非法字节阶段例外。

可独立执行：``python -m unittest tests.test_modified_files_between_queries``，
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

# 场景一/二共用的 notes/a.txt 三版固定内容（字符串中的 \n 即换行符，按 UTF-8
# 编码成字节）：初始只有第 1 行命中；改写后第 2、4 行各有一个命中；最后无命中。
INITIAL_A_BYTES = "target old\n".encode("utf-8")
REWRITTEN_A_BYTES = "header\nnew target note\nother\nlast target\n".encode("utf-8")
NO_MATCH_A_BYTES = "no match\n".encode("utf-8")

# 场景三的固定内容：b.md 在整个场景中始终不变；a.txt 经历“合法 -> 末尾追加
# 非法单字节 0xff -> 改写修复”三个阶段。
STABLE_B_MD_BYTES = "target stable\n".encode("utf-8")
BROKEN_A_BYTES = INITIAL_A_BYTES + b"\xff"
REPAIRED_A_BYTES = "target repaired\n".encode("utf-8")


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


class _ModifiedFilesTestCase(unittest.TestCase):
    """公共夹具：每个用例一个独立临时选定目录，并提供只读查询核对辅助。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

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
            "（查询不得写入资料、不得生成索引或其他文件）",
        )
        return code, out, err

    def assert_clean_json(self, code, out, err, expected_text, label):
        """场景 label：返回值 0、标准错误为空、标准输出逐字节等于期望 JSON 文本。

        返回按 UTF-8 解析后的 JSON 结果，供再做结构化断言。
        """
        self.assertEqual(
            code,
            0,
            f"{label}: 返回值应为 0，实际 {code}，stderr={err!r}",
        )
        self.assertEqual(err, b"", f"{label}: 标准错误应为空（无任何告警），实际 {err!r}")
        self.assertEqual(
            out.decode("utf-8"),
            expected_text,
            f"{label}: 标准输出应为完整 JSON 数组加换行",
        )
        return json.loads(out.decode("utf-8"))


class ModifiedFilesBetweenQueriesTest(_ModifiedFilesTestCase):
    """同一进程内连续查询，之间只改写 notes/a.txt，每次查询反映当前内容。"""

    def setUp(self) -> None:
        super().setUp()
        (self.root / "notes").mkdir()
        self.a_path = self.root / "notes" / "a.txt"
        self.a_path.write_bytes(INITIAL_A_BYTES)

    # -- 1. 默认参数：首命中随改写变化，最终无命中输出 [] -------------------

    def test_default_query_reflects_rewritten_content(self) -> None:
        # 场景内参数保持不变：始终是 <目录> target（默认选项）。
        argv = [str(self.root), "target"]
        self.assertEqual(
            snapshot_tree(self.root),
            {"notes/a.txt": INITIAL_A_BYTES},
            "用例初始只应有 notes/a.txt 且内容为 target old\\n",
        )

        # 初始内容 target old\n：第 1 行命中，默认上下文 30 足以覆盖全短行，
        # 片段即整行 target old。
        code, out, err = self.run_query(argv, "首次查询（初始内容）")
        first = self.assert_clean_json(
            code,
            out,
            err,
            '[{"path": "notes/a.txt", "line": 1, "snippet": "target old"}]\n',
            "首次查询",
        )
        self.assertEqual(
            first,
            [{"path": "notes/a.txt", "line": 1, "snippet": "target old"}],
        )
        self.assertTrue(
            all(item["path"] == "notes/a.txt" for item in first),
            "非空结果的 path 必须均为 notes/a.txt",
        )

        # 两次查询之间整体改写文件：第 2、4 行均含 target。
        self.a_path.write_bytes(REWRITTEN_A_BYTES)
        code, out, err = self.run_query(argv, "改写后默认查询")
        second = self.assert_clean_json(
            code,
            out,
            err,
            '[{"path": "notes/a.txt", "line": 2, "snippet": "new target note"}]\n',
            "改写后默认查询",
        )
        self.assertEqual(
            second,
            [{"path": "notes/a.txt", "line": 2, "snippet": "new target note"}],
            "默认查询每个文件只返回首个命中行（第 2 行），第 4 行不得返回",
        )
        self.assertTrue(
            all(item["path"] == "notes/a.txt" for item in second),
            "非空结果的 path 必须均为 notes/a.txt",
        )

        # 再次改写为完全不含关键词的内容。
        self.a_path.write_bytes(NO_MATCH_A_BYTES)
        code, out, err = self.run_query(argv, "改写为无命中后的查询")
        self.assert_clean_json(code, out, err, "[]\n", "无命中查询")

    # -- 2. --all-lines 与 --context-chars 0 的同一改写过程 -----------------

    def test_all_lines_context_zero_reflects_rewritten_content(self) -> None:
        # 场景内参数保持不变：--all-lines 每个命中行各一项，--context-chars 0
        # 使片段只保留完整命中关键词本身。
        argv = [str(self.root), "target", "--all-lines", "--context-chars", "0"]

        # 初始：第 1 行命中，上下文为 0，片段只有 target。
        code, out, err = self.run_query(argv, "首次查询（初始内容）")
        first = self.assert_clean_json(
            code,
            out,
            err,
            '[{"path": "notes/a.txt", "line": 1, "snippet": "target"}]\n',
            "首次查询",
        )
        self.assertEqual(
            first,
            [{"path": "notes/a.txt", "line": 1, "snippet": "target"}],
        )

        # 改写后：第 2、4 行各一项，按行号递增，片段均仅为 target。
        self.a_path.write_bytes(REWRITTEN_A_BYTES)
        code, out, err = self.run_query(argv, "改写后 --all-lines 查询")
        second = self.assert_clean_json(
            code,
            out,
            err,
            '[{"path": "notes/a.txt", "line": 2, "snippet": "target"}, '
            '{"path": "notes/a.txt", "line": 4, "snippet": "target"}]\n',
            "改写后 --all-lines 查询",
        )
        self.assertEqual(
            second,
            [
                {"path": "notes/a.txt", "line": 2, "snippet": "target"},
                {"path": "notes/a.txt", "line": 4, "snippet": "target"},
            ],
            "应只返回第 2、4 两个命中行，第 1、3、5 行不命中，两项片段均为 target",
        )
        self.assertEqual(
            [item["line"] for item in second],
            [2, 4],
            "同一路径内结果必须按行号递增",
        )
        self.assertTrue(
            all(
                item["path"] == "notes/a.txt" and item["snippet"] == "target"
                for item in second
            ),
            "两项的 path 均为 notes/a.txt、片段均为 target",
        )

        # 再次改写为无命中。
        self.a_path.write_bytes(NO_MATCH_A_BYTES)
        code, out, err = self.run_query(argv, "改写为无命中后的查询")
        self.assert_clean_json(code, out, err, "[]\n", "无命中查询")


class ModifiedFileUtf8BoundaryTest(_ModifiedFilesTestCase):
    """改写后 UTF-8 解码边界变化：合法 -> 末尾非法字节 -> 改写修复。"""

    def setUp(self) -> None:
        super().setUp()
        self.a_path = self.root / "a.txt"
        self.b_path = self.root / "b.md"
        self.a_path.write_bytes(INITIAL_A_BYTES)
        self.b_path.write_bytes(STABLE_B_MD_BYTES)

    def test_invalid_byte_after_rewrite_skips_file_until_repaired(self) -> None:
        # 场景内参数保持不变：默认查询 target。
        argv = [str(self.root), "target"]
        self.assertEqual(
            snapshot_tree(self.root),
            {"a.txt": INITIAL_A_BYTES, "b.md": STABLE_B_MD_BYTES},
        )

        # 首次查询：两个合法文件均命中，按路径码点序 a.txt 在 b.md 之前，
        # 返回值 0、标准错误为空。
        code, out, err = self.run_query(argv, "首次查询（两文件均合法）")
        first = self.assert_clean_json(
            code,
            out,
            err,
            '[{"path": "a.txt", "line": 1, "snippet": "target old"}, '
            '{"path": "b.md", "line": 1, "snippet": "target stable"}]\n',
            "首次查询",
        )
        self.assertEqual(
            first,
            [
                {"path": "a.txt", "line": 1, "snippet": "target old"},
                {"path": "b.md", "line": 1, "snippet": "target stable"},
            ],
        )

        # 两次查询之间在 a.txt 末尾追加单字节 0xff（本场景唯一的非 UTF-8 资料）。
        self.a_path.write_bytes(BROKEN_A_BYTES)

        code, out, err = self.run_query(argv, "追加 0xff 后的查询")
        self.assertEqual(code, 0, "存在无法解码的候选文件时跳过它并告警，返回值仍为 0")
        self.assertEqual(
            out.decode("utf-8"),
            '[{"path": "b.md", "line": 1, "snippet": "target stable"}]\n',
            "a.txt 整体无法解码时不得返回它的任何命中，只剩 b.md 第 1 行",
        )
        results = json.loads(out.decode("utf-8"))
        self.assertEqual(
            results,
            [{"path": "b.md", "line": 1, "snippet": "target stable"}],
        )
        self.assertNotIn("a.txt", out.decode("utf-8"), "标准输出不得残留被跳过文件的路径")
        self.assertNotIn("target old", out.decode("utf-8"), "标准输出不得残留被跳过文件的片段")

        # 标准错误有且仅有一条告警：指向 a.txt 且说明无法按 UTF-8 解码；
        # 合法的 b.md 不得产生任何告警。
        stderr_text = err.decode("utf-8")
        warning_lines = stderr_text.splitlines()
        self.assertEqual(
            len(warning_lines),
            1,
            f"标准错误应只有一条告警，实际 {warning_lines!r}",
        )
        self.assertTrue(err.endswith(b"\n"), "告警行应以换行结束")
        self.assertIn("a.txt", warning_lines[0], "告警应包含被跳过文件的相对路径 a.txt")
        self.assertIn("无法按 UTF-8 解码", warning_lines[0], "告警应说明无法按 UTF-8 解码")
        self.assertNotIn("b.md", stderr_text, "合法的 b.md 不得产生告警")

        # b.md 在整个场景中字节始终不变（快照等价性已由 run_query 逐次核对）。
        self.assertEqual(self.b_path.read_bytes(), STABLE_B_MD_BYTES)

        # 两次查询之间把 a.txt 整体改写为合法 UTF-8 内容。
        self.a_path.write_bytes(REPAIRED_A_BYTES)

        code, out, err = self.run_query(argv, "修复后的查询")
        repaired = self.assert_clean_json(
            code,
            out,
            err,
            '[{"path": "a.txt", "line": 1, "snippet": "target repaired"}, '
            '{"path": "b.md", "line": 1, "snippet": "target stable"}]\n',
            "修复后的查询",
        )
        self.assertEqual(
            repaired,
            [
                {"path": "a.txt", "line": 1, "snippet": "target repaired"},
                {"path": "b.md", "line": 1, "snippet": "target stable"},
            ],
            "修复后 a.txt 应重新以第 1 行命中，并按路径序排在 b.md 之前",
        )
        self.assertEqual(err, b"", "修复后告警必须消失，标准错误为空")
        self.assertEqual(self.b_path.read_bytes(), STABLE_B_MD_BYTES, "b.md 自始至终未被改动")


if __name__ == "__main__":
    unittest.main()

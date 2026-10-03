"""文件删除后重新查询的回归测试。

检索不留下索引文件：每次调用都重新扫描选定目录，因此两次查询之间删除的
文件不得再以任何形式出现在后续结果中。本文件在同一 Python 进程内连续调用
公开入口 ``local_search.main``，固定以下既有行为：

- 默认参数首次查询 ``target``：按路径顺序返回 a.txt 第 2 行 ``target note``
  与 notes/b.md 第 1 行 ``target again``；
- 两次查询之间删除 notes/b.md（保留 notes 目录）后，相同参数的第二次查询
  只返回 a.txt 的原有结果：不保留已删除文件的路径、片段或额外结果，也不
  报告该文件无法读取（标准错误为空）；
- 选定目录仍存在、全部命中文件都被删除时，第二次查询输出 ``[]``，返回值
  为 0，标准错误为空——这是成功的空数组，而不是错误；
- 带 ``--path-contains notes/`` 的相同删除场景：第二次同样得到成功的空
  数组，即使 a.txt 仍然存在也不因筛选之外的原因进入结果；
- 原有目录不存在时仍返回 2、标准输出为空、标准错误说明原因（含两次查询
  之间整个目录被删除的情形）；
- 每次查询都是只读的：剩余源文件字节不变，查询不生成文件，也不重建已删除
  的文件。

文件删除只发生在两次查询之间；扫描期间文件消失的处理不在本文件范围内。
所有资料由各用例在 TemporaryDirectory 中独立准备并自动清理，仅使用标准库、
离线运行；期望结果（包括完整的 UTF-8 JSON 输出字节）全部以字面量直接写出，
不调用任何被测检索逻辑生成。
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import local_search

KEYWORD = "target"

# 首次查询（默认参数）的完整标准输出：a.txt 第 2 行在前，notes/b.md 第 1 行在后。
FIRST_RUN_STDOUT = (
    b'[{"path": "a.txt", "line": 2, "snippet": "target note"}, '
    b'{"path": "notes/b.md", "line": 1, "snippet": "target again"}]\n'
)
FIRST_RUN_RESULTS = [
    {"path": "a.txt", "line": 2, "snippet": "target note"},
    {"path": "notes/b.md", "line": 1, "snippet": "target again"},
]
# 删除 notes/b.md 后的第二次查询：只剩 a.txt 的原有结果，行号与片段不变。
SECOND_RUN_STDOUT = b'[{"path": "a.txt", "line": 2, "snippet": "target note"}]\n'
SECOND_RUN_RESULTS = [{"path": "a.txt", "line": 2, "snippet": "target note"}]
# --path-contains notes/ 的首次查询：只有 notes/b.md 入选。
FILTERED_FIRST_STDOUT = b'[{"path": "notes/b.md", "line": 1, "snippet": "target again"}]\n'
# 成功的空数组（全部命中文件被删除，或筛选后无候选）。
EMPTY_RUN_STDOUT = b"[]\n"


class _CapturedStream:
    """sys.stdout/sys.stderr 的最小替身：被测代码只使用其 .buffer 写字节。"""

    def __init__(self) -> None:
        self.buffer = io.BytesIO()


def run_main(argv: list) -> tuple:
    """调用公开入口 local_search.main，返回 (返回值, 标准输出字节, 标准错误字节)。"""
    stdout = _CapturedStream()
    stderr = _CapturedStream()
    with mock.patch.object(sys, "stdout", stdout), mock.patch.object(sys, "stderr", stderr):
        code = local_search.main(argv)
    return code, stdout.buffer.getvalue(), stderr.buffer.getvalue()


class DeletionBetweenQueriesBase(unittest.TestCase):
    """公共资料：a.txt 两行 header/target note，notes/b.md 两行 target again/target later。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "a.txt").write_bytes("header\ntarget note\n".encode("utf-8"))
        (self.root / "notes").mkdir()
        (self.root / "notes" / "b.md").write_bytes(
            "target again\ntarget later\n".encode("utf-8")
        )

    # -- 辅助 ------------------------------------------------------------

    def _snapshot_files(self) -> dict:
        """选定目录下全部文件的相对路径 → 字节内容，用于核对查询只读。"""
        return {
            str(p.relative_to(self.root)).replace(os.sep, "/"): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    def assert_clean_run(self, run: tuple, expected_stdout: bytes, expected_results: list, label: str) -> None:
        """正常查询：返回 0、标准错误为空、标准输出逐字节等于期望的 JSON 数组。"""
        code, out, err = run
        self.assertEqual(code, 0, f"{label}: 返回值应为 0，实际 {code}，stderr={err!r}")
        self.assertEqual(err, b"", f"{label}: 标准错误应为空，实际 {err!r}")
        self.assertEqual(out, expected_stdout, f"{label}: 标准输出的完整 JSON 数组不符")
        self.assertEqual(
            json.loads(out.decode("utf-8")),
            expected_results,
            f"{label}: 解析后的结果列表不符",
        )


class DeleteOneHitFileTest(DeletionBetweenQueriesBase):
    """删除 notes/b.md（保留 notes 目录）后重新查询，只剩 a.txt 的原有结果。"""

    def test_second_query_returns_only_remaining_file(self) -> None:
        before = self._snapshot_files()

        first = run_main([str(self.root), KEYWORD])
        self.assert_clean_run(first, FIRST_RUN_STDOUT, FIRST_RUN_RESULTS, "首次查询")
        self.assertEqual(self._snapshot_files(), before, "首次查询不得改动或新增任何文件")

        # 两次查询之间删除一个命中文件；notes 目录本身保留。
        (self.root / "notes" / "b.md").unlink()
        self.assertTrue((self.root / "notes").is_dir(), "notes 目录应保留")

        second = run_main([str(self.root), KEYWORD])
        self.assert_clean_run(second, SECOND_RUN_STDOUT, SECOND_RUN_RESULTS, "第二次查询")

        # 已删除文件的路径与片段不得以任何形式残留，也不得有针对它的读取告警
        # （标准错误为空已在上面断言）。
        self.assertNotIn(b"notes/b.md", second[1], "已删除文件的路径不得出现在结果中")
        self.assertNotIn(
            "target again", second[1].decode("utf-8"), "已删除文件的片段不得出现在结果中"
        )
        self.assertNotIn(
            "target later", second[1].decode("utf-8"), "已删除文件的其他行也不得出现"
        )

        # 查询只读：a.txt 字节不变，notes 目录仍在，已删除文件未被重建，也没有新文件。
        self.assertEqual(
            self._snapshot_files(),
            {"a.txt": before["a.txt"]},
            "查询后应只剩字节不变的 a.txt，不得生成文件或重建已删除文件",
        )
        self.assertEqual((self.root / "a.txt").read_bytes(), before["a.txt"])
        self.assertFalse((self.root / "notes" / "b.md").exists(), "查询不得重建已删除文件")
        self.assertTrue((self.root / "notes").is_dir(), "查询不得删除保留的 notes 目录")


class DeleteAllHitFilesTest(DeletionBetweenQueriesBase):
    """选定目录仍存在、两个命中文件都被删除：第二次查询是成功的空数组。"""

    def test_second_query_returns_empty_array_when_all_hits_deleted(self) -> None:
        before = self._snapshot_files()

        first = run_main([str(self.root), KEYWORD])
        self.assert_clean_run(first, FIRST_RUN_STDOUT, FIRST_RUN_RESULTS, "首次查询")

        # 两次查询之间删除全部命中文件；选定目录本身保留。
        (self.root / "a.txt").unlink()
        (self.root / "notes" / "b.md").unlink()
        self.assertTrue(self.root.is_dir(), "选定目录应保留")

        second = run_main([str(self.root), KEYWORD])
        code, out, err = second
        self.assertEqual(code, 0, f"全部命中被删除时返回值应为 0，实际 {code}，stderr={err!r}")
        self.assertEqual(err, b"", f"标准错误应为空，实际 {err!r}")
        self.assertEqual(out, EMPTY_RUN_STDOUT, "选定目录仍在时应输出成功的空数组 []")
        self.assertEqual(json.loads(out.decode("utf-8")), [])

        # 查询只读：不生成任何文件，也不重建已删除的文件。
        self.assertEqual(self._snapshot_files(), {}, "查询不得生成文件或重建已删除文件")
        self.assertFalse((self.root / "a.txt").exists())
        self.assertFalse((self.root / "notes" / "b.md").exists())
        self.assertNotEqual(self._snapshot_files(), before, "资料确实已被删除（前置校验）")


class DeleteWithPathContainsTest(DeletionBetweenQueriesBase):
    """带 --path-contains notes/ 的相同删除场景：第二次得到成功的空数组。"""

    def test_filtered_second_query_returns_empty_array(self) -> None:
        argv = [str(self.root), KEYWORD, "--path-contains", "notes/"]
        before = self._snapshot_files()

        first = run_main(argv)
        self.assert_clean_run(
            first,
            FILTERED_FIRST_STDOUT,
            [{"path": "notes/b.md", "line": 1, "snippet": "target again"}],
            "筛选后的首次查询",
        )

        (self.root / "notes" / "b.md").unlink()

        second = run_main(argv)
        code, out, err = second
        self.assertEqual(code, 0, f"返回值应为 0，实际 {code}，stderr={err!r}")
        self.assertEqual(err, b"", f"标准错误应为空，实际 {err!r}")
        self.assertEqual(out, EMPTY_RUN_STDOUT, "唯一入选文件被删除后应输出成功的空数组 []")
        self.assertEqual(json.loads(out.decode("utf-8")), [])
        # a.txt 仍然存在且仍有命中，但被路径筛选排除，不得进入结果。
        self.assertNotIn(b"a.txt", out)

        # 查询只读：a.txt 字节不变，已删除文件未被重建，没有新文件。
        self.assertEqual(
            self._snapshot_files(),
            {"a.txt": before["a.txt"]},
            "查询后应只剩字节不变的 a.txt，不得生成文件或重建已删除文件",
        )


class MissingDirectoryTest(DeletionBetweenQueriesBase):
    """原有目录不存在：返回 2、标准输出为空、标准错误说明原因。"""

    def test_never_existed_directory_is_an_error(self) -> None:
        missing = str(self.root / "不存在的目录")
        code, out, err = run_main([missing, KEYWORD])
        self.assertEqual(code, 2, f"目录不存在时返回值应为 2，实际 {code}")
        self.assertEqual(out, b"", f"出错时标准输出必须为空，实际 {out!r}")
        stderr = err.decode("utf-8")
        self.assertIn("目录不存在", stderr, "标准错误应说明目录不存在")
        self.assertIn(missing, stderr, "标准错误应包含给定路径")

    def test_directory_removed_between_queries_turns_second_into_error(self) -> None:
        # 与“目录仍在但命中文件被删”相对照：整个选定目录被删除时不是空数组而是错误。
        first = run_main([str(self.root), KEYWORD])
        self.assert_clean_run(first, FIRST_RUN_STDOUT, FIRST_RUN_RESULTS, "首次查询")

        shutil.rmtree(self.root)

        code, out, err = run_main([str(self.root), KEYWORD])
        self.assertEqual(code, 2, f"目录被删除后返回值应为 2，实际 {code}")
        self.assertEqual(out, b"", f"出错时标准输出必须为空，实际 {out!r}")
        self.assertIn("目录不存在", err.decode("utf-8"), "标准错误应说明目录不存在")


if __name__ == "__main__":
    unittest.main()

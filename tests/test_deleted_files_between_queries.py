"""两次查询之间文件被删除的回归测试。

local_search 每次查询都重新扫描选定目录、不留下索引文件，因此同一 Python
进程内连续调用公开入口 ``local_search.main`` 时，第二次查询只应返回当时
仍存在的候选文件的命中：

- 首次查询后删除 ``notes/b.md``（保留 ``notes`` 目录），再以相同的默认参数
  查询：只返回 ``a.txt`` 的原有命中（行号、片段原样），已删除文件的路径与
  片段不得出现在结果中，也不得为它报告"无法读取"之类的告警；
- 首次查询后删除全部命中文件（保留选定目录），第二次查询输出 ``[]``、
  返回值 0、标准错误为空——选定目录仍存在时，"全部命中文件被删除"是成功的
  空数组，而不是错误；
- 同样的删除场景叠加 ``--path-contains notes/``：即使 ``a.txt`` 仍在，第二
  次查询也返回成功的空数组（路径筛选先于内容读取）；
- 对照行为：若整个选定目录本身不复存在，查询仍以返回值 2 终止、标准输出为
  空、标准错误说明原因——该既有行为不受本次改动影响。

文件删除只发生在两次查询之间，不涉及扫描期间文件消失的处理。每次查询都
分别核对 UTF-8 标准输出中的完整 JSON 数组、返回值与标准错误；测试前后
核对剩余源文件字节不变、查询没有生成文件、也没有重建已删除的文件。

用例通过公开入口 ``local_search.main`` 驱动，以其返回值对应退出码，并捕获
``sys.stdout``/``sys.stderr`` 的 ``.buffer`` 核对 UTF-8 字节输出——与
``python -m local_search`` 的输入输出约定一致。所有资料由各用例在
TemporaryDirectory 中独立准备并自动清理，仅使用标准库、离线运行；期望值
全部以字面量直接写出，不调用任何被测检索逻辑生成。
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

# 两个源文件的固定内容（UTF-8）：a.txt 第 2 行命中，notes/b.md 第 1、2 行
# 均含关键词，默认参数只返回首个命中行（第 1 行）。
A_TXT_BYTES = "header\ntarget note\n".encode("utf-8")
B_MD_BYTES = "target again\ntarget later\n".encode("utf-8")

# 首次查询（默认参数）的完整期望结果，按路径码点序排列。
FIRST_RUN_EXPECTED = [
    {"path": "a.txt", "line": 2, "snippet": "target note"},
    {"path": "notes/b.md", "line": 1, "snippet": "target again"},
]
# 删除 notes/b.md 后第二次查询的完整期望结果。
SECOND_RUN_EXPECTED = [
    {"path": "a.txt", "line": 2, "snippet": "target note"},
]


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


class DeletedFilesBetweenQueriesTest(unittest.TestCase):
    """同一进程内连续两次查询，之间删除命中文件，第二次只反映仍存在的文件。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "a.txt").write_bytes(A_TXT_BYTES)
        (self.root / "notes").mkdir()
        (self.root / "notes" / "b.md").write_bytes(B_MD_BYTES)

    # -- 辅助 ------------------------------------------------------------

    def assert_clean_success(self, code: int, out: bytes, err: bytes, label: str) -> list:
        """场景 label：返回值必须为 0、标准错误必须为空，返回解析后的 JSON。"""
        self.assertEqual(
            code,
            0,
            f"{label}: 返回值应为 0，实际 {code}，stderr={err!r}",
        )
        self.assertEqual(err, b"", f"{label}: 标准错误应为空（无任何告警），实际 {err!r}")
        return json.loads(out.decode("utf-8"))

    def _run_default_query(self, label: str) -> tuple:
        """以默认参数查询 target，断言成功且无告警，返回 (解析结果, 原始输出字节)。"""
        code, out, err = run_main([str(self.root), "target"])
        results = self.assert_clean_success(code, out, err, label)
        return results, out

    def _snapshot_tree(self) -> dict:
        """选定目录下全部文件的 相对路径 -> 字节 快照（正斜杠相对路径）。"""
        return {
            str(p.relative_to(self.root)).replace(os.sep, "/"): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    # -- 1. 删除一个命中文件：第二次查询只剩 a.txt 的原有结果 --------------

    def test_deleted_hit_file_disappears_from_next_query(self) -> None:
        first, first_out = self._run_default_query("首次查询")
        self.assertEqual(first, FIRST_RUN_EXPECTED, "首次查询应按路径顺序返回两个命中")
        # 完整 JSON 数组逐字节核对（UTF-8、ensure_ascii=False 的默认分隔符）。
        self.assertEqual(
            first_out.decode("utf-8"),
            '[{"path": "a.txt", "line": 2, "snippet": "target note"}, '
            '{"path": "notes/b.md", "line": 1, "snippet": "target again"}]\n',
            "首次查询的标准输出应为完整的 JSON 数组加换行",
        )

        # 两次查询之间删除 notes/b.md，保留 notes 目录本身。
        (self.root / "notes" / "b.md").unlink()
        self.assertTrue((self.root / "notes").is_dir(), "notes 目录应保留")
        before_second = self._snapshot_tree()

        second, second_out = self._run_default_query("删除后查询")
        self.assertEqual(
            second,
            SECOND_RUN_EXPECTED,
            "第二次查询只应返回 a.txt 的原有命中，行号与片段保持原样",
        )
        self.assertEqual(
            second_out.decode("utf-8"),
            '[{"path": "a.txt", "line": 2, "snippet": "target note"}]\n',
            "第二次查询的标准输出不得包含已删除文件的任何条目",
        )
        self.assertNotIn("b.md", second_out.decode("utf-8"), "已删除文件的路径不得残留")
        self.assertNotIn("target again", second_out.decode("utf-8"), "已删除文件的片段不得残留")
        # 标准错误为空已在 assert_clean_success 中断言：不得报告该文件无法读取。

        self.assertEqual(
            self._snapshot_tree(),
            before_second,
            "查询不得生成文件、不得重建已删除文件，剩余源文件字节不变",
        )
        self.assertEqual((self.root / "a.txt").read_bytes(), A_TXT_BYTES)
        self.assertFalse((self.root / "notes" / "b.md").exists(), "查询不得重建已删除文件")

    # -- 2. 删除全部命中文件：选定目录仍在，得到成功的空数组 ----------------

    def test_all_hit_files_deleted_returns_empty_array_success(self) -> None:
        first, _ = self._run_default_query("首次查询")
        self.assertEqual(first, FIRST_RUN_EXPECTED)

        # 删除两个命中文件，保留选定目录（notes 目录也保留）。
        (self.root / "a.txt").unlink()
        (self.root / "notes" / "b.md").unlink()
        self.assertTrue(self.root.is_dir(), "选定目录应保留")
        before_second = self._snapshot_tree()
        self.assertEqual(before_second, {}, "两个命中文件都应已删除")

        code, out, err = run_main([str(self.root), "target"])

        self.assertEqual(code, 0, "全部命中文件被删除但目录仍在时，返回值应为 0")
        self.assertEqual(err, b"", f"标准错误应为空，实际 {err!r}")
        self.assertEqual(
            out.decode("utf-8"),
            "[]\n",
            "选定目录仍存在时，全部命中文件被删除应输出成功的空数组",
        )
        self.assertEqual(
            self._snapshot_tree(),
            before_second,
            "查询不得生成任何文件，也不得重建已删除文件",
        )

    # -- 3. 叠加 --path-contains notes/ 的相同删除场景 ----------------------

    def test_path_contains_filter_after_delete_returns_empty_array(self) -> None:
        argv = [str(self.root), "target", "--path-contains", "notes/"]

        code, out, err = run_main(argv)
        first = self.assert_clean_success(code, out, err, "带筛选的首次查询")
        self.assertEqual(
            first,
            [{"path": "notes/b.md", "line": 1, "snippet": "target again"}],
            "带 notes/ 筛选的首次查询只应返回 notes/b.md",
        )

        # 删除 notes/b.md；a.txt 仍然存在但被筛选排除。
        (self.root / "notes" / "b.md").unlink()
        before_second = self._snapshot_tree()

        code, out, err = run_main(argv)
        self.assertEqual(code, 0, "筛选后无候选文件时返回值应为 0")
        self.assertEqual(err, b"", f"标准错误应为空，实际 {err!r}")
        self.assertEqual(
            out.decode("utf-8"),
            "[]\n",
            "即使 a.txt 仍在，--path-contains notes/ 下第二次查询也应是空数组",
        )
        self.assertEqual(
            self._snapshot_tree(),
            before_second,
            "查询不得改动剩余源文件，也不得重建已删除文件",
        )
        self.assertEqual((self.root / "a.txt").read_bytes(), A_TXT_BYTES)

    # -- 4. 对照：选定目录本身不存在时仍为返回值 2 --------------------------

    def test_missing_directory_still_fails_with_code_2(self) -> None:
        first, _ = self._run_default_query("首次查询")
        self.assertEqual(first, FIRST_RUN_EXPECTED)

        # 两次查询之间整个选定目录被删除。
        (self.root / "a.txt").unlink()
        (self.root / "notes" / "b.md").unlink()
        (self.root / "notes").rmdir()
        self.root.rmdir()
        self.assertFalse(self.root.exists())

        code, out, err = run_main([str(self.root), "target"])

        self.assertEqual(code, 2, "选定目录不存在时返回值应为 2")
        self.assertEqual(out, b"", "选定目录不存在时标准输出必须为空")
        stderr = err.decode("utf-8")
        self.assertIn("目录不存在", stderr, "标准错误应说明目录不存在")
        self.assertIn(str(self.root), stderr, "标准错误应包含选定目录路径")


if __name__ == "__main__":
    unittest.main()

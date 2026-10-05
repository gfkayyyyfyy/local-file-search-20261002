"""两次查询之间文件被移动的回归测试。

local_search 每次查询都重新扫描选定目录、不留下索引文件，因此同一 Python
进程内连续调用公开入口 ``local_search.main`` 时，第二次查询应按移动后的
相对路径重新排序并应用路径筛选：

- 首次查询后把 ``z/a.txt`` 移到 ``a/b.txt``（保留根目录与原 ``z`` 子目录），
  再以相同参数（``--all-lines --context-chars 0``）查询：结果按新路径
  ``a/b.txt`` 参与码点序排列，行号与片段不因移动而改变；旧路径不得残留，
  新旧路径不得同时出现，也不得为旧路径报告"无法读取"之类的告警；
- 同一移动过程叠加固定的 ``--path-contains z/`` 做独立对照：首次只返回
  ``z/a.txt`` 的命中，移动后 ``m.md`` 与 ``a/b.txt`` 均不符合筛选，输出
  成功的空数组。

文件移动只发生在两次查询之间，不涉及扫描期间的移动。每次查询都分别核对
UTF-8 标准输出中的完整 JSON 数组（顺序及 path、line、snippet 三个字段）、
返回值与标准错误；每次查询前后核对文件集合与字节，确认查询没有改写资料、
生成文件或重建旧路径。

用例通过公开入口 ``local_search.main`` 驱动，以其返回值对应退出码，并捕获
``sys.stdout``/``sys.stderr`` 的 ``.buffer`` 核对 UTF-8 字节输出——与
``python -m local_search`` 的输入输出约定一致。所有资料由各用例在
TemporaryDirectory 中独立准备并自动清理，仅使用标准库、离线运行，不依赖
仓库演示目录；期望值全部以字面量直接写出，不调用任何被测检索逻辑生成。
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

# 两个源文件的固定内容（UTF-8，各行以 LF 结束）：z/a.txt 第 2、3 行命中，
# m.md 第 1 行命中；--context-chars 0 使各项片段只保留完整命中关键词。
A_TXT_BYTES = b"header\ntarget one\ntarget two\n"
M_MD_BYTES = b"target stable\n"

# 两次查询共用的参数：--all-lines 返回每个命中行，--context-chars 0 使片段
# 仅为关键词本身。
QUERY_ARGS = ["--all-lines", "--context-chars", "0"]

# 首次查询（移动前）的完整期望结果，按路径码点序排列（m.md < z/a.txt）。
FIRST_RUN_EXPECTED = [
    {"path": "m.md", "line": 1, "snippet": "target"},
    {"path": "z/a.txt", "line": 2, "snippet": "target"},
    {"path": "z/a.txt", "line": 3, "snippet": "target"},
]
# 移动后第二次查询的完整期望结果，按新路径重新排序（a/b.txt < m.md），
# 行号与片段保持原样。
SECOND_RUN_EXPECTED = [
    {"path": "a/b.txt", "line": 2, "snippet": "target"},
    {"path": "a/b.txt", "line": 3, "snippet": "target"},
    {"path": "m.md", "line": 1, "snippet": "target"},
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


class MovedFilesBetweenQueriesTest(unittest.TestCase):
    """同一进程内连续两次查询，之间移动命中文件，第二次按新路径排序与筛选。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "z").mkdir()
        (self.root / "z" / "a.txt").write_bytes(A_TXT_BYTES)
        (self.root / "m.md").write_bytes(M_MD_BYTES)

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

    def _run_query(self, extra_args: list, label: str) -> tuple:
        """以共用参数查询 target，断言成功且无告警，返回 (解析结果, 原始输出字节)。"""
        code, out, err = run_main([str(self.root), "target", *extra_args])
        results = self.assert_clean_success(code, out, err, label)
        return results, out

    def _snapshot_tree(self) -> dict:
        """选定目录下全部文件的 相对路径 -> 字节 快照（正斜杠相对路径）。"""
        return {
            str(p.relative_to(self.root)).replace(os.sep, "/"): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    def _move_file(self) -> None:
        """两次查询之间唯一的改动：把 z/a.txt 移到 a/b.txt，保留根目录与 z 子目录。"""
        (self.root / "a").mkdir()
        (self.root / "z" / "a.txt").rename(self.root / "a" / "b.txt")
        self.assertFalse((self.root / "z" / "a.txt").exists(), "移动后旧路径应消失")
        self.assertTrue((self.root / "a" / "b.txt").is_file(), "移动后新路径应存在")
        self.assertTrue((self.root / "z").is_dir(), "原 z 子目录应保留")

    # -- 1. 移动命中文件：第二次查询按新路径重新排序，行号与片段不变 --------

    def test_moved_file_reordered_by_new_path_in_next_query(self) -> None:
        before_first = self._snapshot_tree()
        first, first_out = self._run_query(QUERY_ARGS, "首次查询")
        self.assertEqual(first, FIRST_RUN_EXPECTED, "首次查询应按路径顺序返回全部命中")
        # 完整 JSON 数组逐字节核对（UTF-8、ensure_ascii=False 的默认分隔符）。
        self.assertEqual(
            first_out.decode("utf-8"),
            '[{"path": "m.md", "line": 1, "snippet": "target"}, '
            '{"path": "z/a.txt", "line": 2, "snippet": "target"}, '
            '{"path": "z/a.txt", "line": 3, "snippet": "target"}]\n',
            "首次查询的标准输出应为完整的 JSON 数组加换行",
        )
        self.assertEqual(
            self._snapshot_tree(),
            before_first,
            "首次查询不得改写资料、生成文件或改动文件集合",
        )

        # 两次查询之间移动 z/a.txt -> a/b.txt，保留根目录与原 z 子目录。
        self._move_file()
        before_second = self._snapshot_tree()

        second, second_out = self._run_query(QUERY_ARGS, "移动后查询")
        self.assertEqual(
            second,
            SECOND_RUN_EXPECTED,
            "第二次查询应按新路径 a/b.txt 重新排序，行号与片段不因移动而改变",
        )
        self.assertEqual(
            second_out.decode("utf-8"),
            '[{"path": "a/b.txt", "line": 2, "snippet": "target"}, '
            '{"path": "a/b.txt", "line": 3, "snippet": "target"}, '
            '{"path": "m.md", "line": 1, "snippet": "target"}]\n',
            "第二次查询的标准输出不得包含旧路径的任何条目",
        )
        second_text = second_out.decode("utf-8")
        self.assertNotIn("z/a.txt", second_text, "旧路径不得在结果中残留")
        self.assertIn("a/b.txt", second_text, "新路径应出现在结果中")
        # 标准错误为空已在 assert_clean_success 中断言：旧路径消失不得产生
        # "无法读取"之类的告警。

        self.assertEqual(
            self._snapshot_tree(),
            before_second,
            "查询不得改写资料、生成文件或重建旧路径",
        )
        self.assertEqual((self.root / "a" / "b.txt").read_bytes(), A_TXT_BYTES)
        self.assertEqual((self.root / "m.md").read_bytes(), M_MD_BYTES)
        self.assertFalse((self.root / "z" / "a.txt").exists(), "查询不得重建旧路径")

    # -- 2. 对照：固定 --path-contains z/ 的同一移动过程 --------------------

    def test_path_contains_filter_after_move_returns_empty_array(self) -> None:
        argv_extra = [*QUERY_ARGS, "--path-contains", "z/"]

        first, first_out = self._run_query(argv_extra, "带筛选的首次查询")
        self.assertEqual(
            first,
            [
                {"path": "z/a.txt", "line": 2, "snippet": "target"},
                {"path": "z/a.txt", "line": 3, "snippet": "target"},
            ],
            "带 z/ 筛选的首次查询只应返回 z/a.txt 的两个命中行",
        )
        self.assertEqual(
            first_out.decode("utf-8"),
            '[{"path": "z/a.txt", "line": 2, "snippet": "target"}, '
            '{"path": "z/a.txt", "line": 3, "snippet": "target"}]\n',
            "m.md 不符合 z/ 筛选，不得出现在首次结果中",
        )

        # 同一移动过程：z/a.txt -> a/b.txt；m.md 与 a/b.txt 均不符合 z/ 筛选。
        self._move_file()
        before_second = self._snapshot_tree()

        code, out, err = run_main([str(self.root), "target", *argv_extra])
        self.assertEqual(code, 0, "筛选后无候选文件时返回值应为 0")
        self.assertEqual(err, b"", f"标准错误应为空，实际 {err!r}")
        self.assertEqual(
            out.decode("utf-8"),
            "[]\n",
            "移动后 m.md 与 a/b.txt 均不符合 z/ 筛选，应输出成功的空数组",
        )
        self.assertEqual(
            self._snapshot_tree(),
            before_second,
            "查询不得改写资料、生成文件或重建旧路径",
        )
        self.assertEqual((self.root / "a" / "b.txt").read_bytes(), A_TXT_BYTES)
        self.assertEqual((self.root / "m.md").read_bytes(), M_MD_BYTES)
        self.assertFalse((self.root / "z" / "a.txt").exists(), "查询不得重建旧路径")


if __name__ == "__main__":
    unittest.main()

"""两次查询之间文件被移动的回归测试。

local_search 每次查询都重新扫描选定目录、重新读取并按 UTF-8 解码候选文件，
不缓存目录列表、不留下索引文件，因此同一 Python 进程内连续调用公开入口
``local_search.main`` 时，两次查询之间被移动到新相对路径的文件应在下一次
查询中以新路径参与既有的按路径码点序排序与路径筛选，旧路径不得残留。本
文件覆盖两个相互独立的场景，场景内命令行参数保持不变，移动操作只发生在
两次查询之间：

- 排序场景（``--all-lines`` 与 ``--context-chars 0``）：初始为
  ``z/a.txt``（三行依次为 ``header``、``target one``、``target two``）与
  ``m.md``（仅一行 ``target stable``），均以 LF 结束。首次查询 ``target``
  依次返回 m.md 第 1 行、z/a.txt 第 2、3 行，各项片段均为 ``target``；
  随后仅把 ``z/a.txt`` 移动为 ``a/b.txt``（保留根目录与原 z 子目录），
  再次查询应依次返回 a/b.txt 第 2、3 行、m.md 第 1 行——同一批命中以
  移动后的相对路径重新参与排序，行号与片段不因移动而改变；旧路径
  ``z/a.txt`` 不得出现，新旧路径不得同时出现。
- 路径筛选对照场景：同一移动过程，但查询固定附加 ``--path-contains z/``
  （其余参数与排序场景相同）。首次仅返回 z/a.txt 第 2、3 行；移动后
  m.md 与 a/b.txt 的相对路径都不含 ``z/``，均不符合筛选，输出 ``[]``
  加一个换行。移动后的文件按新路径重新应用路径筛选，而不是沿用旧路径
  的筛选结论。

两个场景的每次查询返回值均为 0、标准错误为空：旧路径消失不得产生“无法
读取”等告警。移动只发生在查询之间，不涉及扫描期间文件变化的处理。每次
查询前后都核对选定目录下资料文件的集合与每个文件的字节完全相同，确保
查询本身既不改写资料、不生成索引等额外文件，也不重建旧路径。每次查询都
分别核对 UTF-8 标准输出中的完整 JSON 数组（含默认分隔符、排序与末尾换
行）、返回值与标准错误，并确认每个结果项只含 path、line、snippet 三个
键、path 使用正斜杠。

用例通过公开入口 ``local_search.main`` 在同一进程内驱动，以其返回值对应
退出码，并捕获 ``sys.stdout``/``sys.stderr`` 的 ``.buffer`` 核对 UTF-8
字节输出——与 ``python -m local_search`` 的输入输出约定一致。所有资料由
各用例在 TemporaryDirectory 中独立准备并自动清理，仅使用标准库、离线
运行，不依赖仓库内的演示目录；期望值全部以字面量直接写出，不调用任何
被测检索逻辑生成。资料统一按 UTF-8 准备。

可独立执行：``python -m unittest tests.test_moved_files_between_queries``，
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

# 两个场景共用的初始文件（UTF-8，各行以 LF 结束）：
# z/a.txt 第 2、3 行各有一个命中，--context-chars 0 时片段只保留完整命中
# 关键词 target；m.md 仅第 1 行一个命中。
Z_A_TXT_BYTES = "header\ntarget one\ntarget two\n".encode("utf-8")
M_MD_BYTES = "target stable\n".encode("utf-8")

# 移动只改变相对路径，不改变文件字节：a/b.txt 与 z/a.txt 内容完全一致。
A_B_TXT_BYTES = Z_A_TXT_BYTES

# 排序场景两次查询的完整期望结果（按路径码点序，同一路径按行号递增）。
FIRST_RUN_EXPECTED = [
    {"path": "m.md", "line": 1, "snippet": "target"},
    {"path": "z/a.txt", "line": 2, "snippet": "target"},
    {"path": "z/a.txt", "line": 3, "snippet": "target"},
]
SECOND_RUN_EXPECTED = [
    {"path": "a/b.txt", "line": 2, "snippet": "target"},
    {"path": "a/b.txt", "line": 3, "snippet": "target"},
    {"path": "m.md", "line": 1, "snippet": "target"},
]

# 路径筛选对照场景首次查询的完整期望结果（移动后无符合筛选的文件）。
FILTERED_FIRST_RUN_EXPECTED = [
    {"path": "z/a.txt", "line": 2, "snippet": "target"},
    {"path": "z/a.txt", "line": 3, "snippet": "target"},
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


class _MovedFilesTestCase(unittest.TestCase):
    """公共夹具：每个用例一个独立临时选定目录，初始为 z/a.txt 与 m.md。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.z_dir = self.root / "z"
        self.z_dir.mkdir()
        self.z_a_path = self.z_dir / "a.txt"
        self.z_a_path.write_bytes(Z_A_TXT_BYTES)
        self.m_md_path = self.root / "m.md"
        self.m_md_path.write_bytes(M_MD_BYTES)

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
            "（查询不得改写资料、不得生成索引或其他文件、不得重建旧路径）",
        )
        return code, out, err

    def assert_clean_query(self, code, out, err, expected_text, expected_results, label):
        """场景 label：返回值 0、标准错误为空、标准输出逐字节等于期望 JSON 文本。

        同时核对解析后的完整数组（顺序与 path/line/snippet 三个字段）以及
        每个结果项的键集合与正斜杠路径。
        """
        self.assertEqual(code, 0, f"{label}: 返回值应为 0，实际 {code}，stderr={err!r}")
        self.assertEqual(err, b"", f"{label}: 标准错误应为空，实际 {err!r}")
        self.assertEqual(
            out.decode("utf-8"),
            expected_text,
            f"{label}: 标准输出应为完整 JSON 数组加末尾换行",
        )
        self.assertTrue(out.endswith(b"\n"), f"{label}: 标准输出应以换行结束")
        results = json.loads(out.decode("utf-8"))
        self.assertEqual(results, expected_results, f"{label}: 结果数组的顺序与字段不符")
        for item in results:
            self.assertEqual(
                set(item.keys()),
                {"path", "line", "snippet"},
                f"{label}: 结果项只能含 path/line/snippet 三个键，实际 {item!r}",
            )
            self.assertNotIn(
                "\\", item["path"], f"{label}: path 必须使用正斜杠: {item['path']!r}"
            )
        return results

    def move_z_a_to_a_b(self) -> None:
        """两次查询之间唯一的变动：把 z/a.txt 移动为 a/b.txt，保留 z 子目录。"""
        a_dir = self.root / "a"
        a_dir.mkdir()
        self.z_a_path.rename(a_dir / "b.txt")
        self.assertEqual(
            snapshot_tree(self.root),
            {"a/b.txt": A_B_TXT_BYTES, "m.md": M_MD_BYTES},
            "移动后应只有 a/b.txt 与 m.md，旧路径 z/a.txt 不得残留",
        )
        self.assertFalse(
            self.z_a_path.exists(), "移动后旧路径 z/a.txt 不得存在"
        )
        self.assertTrue(
            self.z_dir.is_dir(), "移动后原 z 子目录应保留（即使为空）"
        )
        self.assertEqual(
            (self.root / "a" / "b.txt").read_bytes(),
            Z_A_TXT_BYTES,
            "移动只改变相对路径，文件字节不得改变",
        )


class MovedFileBetweenQueriesTest(_MovedFilesTestCase):
    """同一进程内连续两次查询，之间移动文件，命中以新路径重新参与排序。"""

    def test_moved_file_reordered_by_new_path_in_next_query(self) -> None:
        # 场景内参数保持不变：始终是 <目录> target --all-lines --context-chars 0。
        argv = [str(self.root), "target", "--all-lines", "--context-chars", "0"]
        self.assertEqual(
            snapshot_tree(self.root),
            {"m.md": M_MD_BYTES, "z/a.txt": Z_A_TXT_BYTES},
            "用例初始应只有 m.md 与 z/a.txt，内容分别为固定资料",
        )

        # 首次查询：m.md 第 1 行在前，z/a.txt 第 2、3 行随后，片段均为 target。
        code, out, err = self.run_query(argv, "首次查询（移动前）")
        first = self.assert_clean_query(
            code,
            out,
            err,
            '[{"path": "m.md", "line": 1, "snippet": "target"}, '
            '{"path": "z/a.txt", "line": 2, "snippet": "target"}, '
            '{"path": "z/a.txt", "line": 3, "snippet": "target"}]\n',
            FIRST_RUN_EXPECTED,
            "首次查询（移动前）",
        )
        self.assertEqual(
            [item["path"] for item in first],
            ["m.md", "z/a.txt", "z/a.txt"],
            "首次查询应按路径码点序排列，m.md 在 z/a.txt 之前",
        )
        self.assertNotIn("a/b.txt", out.decode("utf-8"), "移动前不得出现新路径")

        # 两次查询之间仅把 z/a.txt 移动为 a/b.txt（保留根目录与原 z 子目录）。
        self.move_z_a_to_a_b()

        # 再次查询：同一批命中以移动后的相对路径重新参与排序——
        # a/b.txt < m.md，故 a/b.txt 第 2、3 行在前，m.md 第 1 行随后；
        # 行号与片段不因移动而改变；旧路径 z/a.txt 不得残留。
        code, out, err = self.run_query(argv, "移动文件后的查询")
        second = self.assert_clean_query(
            code,
            out,
            err,
            '[{"path": "a/b.txt", "line": 2, "snippet": "target"}, '
            '{"path": "a/b.txt", "line": 3, "snippet": "target"}, '
            '{"path": "m.md", "line": 1, "snippet": "target"}]\n',
            SECOND_RUN_EXPECTED,
            "移动文件后的查询",
        )
        self.assertEqual(
            [item["path"] for item in second],
            ["a/b.txt", "a/b.txt", "m.md"],
            "移动后命中必须以新路径 a/b.txt 重新排序，而非沿用旧路径位置",
        )
        self.assertEqual(
            [(item["line"], item["snippet"]) for item in second],
            [(2, "target"), (3, "target"), (1, "target")],
            "行号与片段不因移动而改变",
        )
        self.assertNotIn(
            "z/a.txt", out.decode("utf-8"), "旧路径 z/a.txt 不得在结果中残留"
        )
        paths = {item["path"] for item in second}
        self.assertFalse(
            {"a/b.txt", "z/a.txt"} <= paths,
            "新旧路径不得同时出现在同一次查询结果中",
        )
        self.assertIn(
            {"path": "m.md", "line": 1, "snippet": "target"},
            second,
            "未移动的 m.md 命中的路径、行号、片段必须保持不变",
        )

        # 查询后文件集合与各文件字节仍与移动后一致，旧路径未被重建。
        self.assertEqual(
            snapshot_tree(self.root),
            {"a/b.txt": A_B_TXT_BYTES, "m.md": M_MD_BYTES},
            "查询不得改写任何资料、生成额外文件或重建旧路径 z/a.txt",
        )
        self.assertFalse(self.z_a_path.exists(), "查询不得重建旧路径 z/a.txt")


class MovedFileWithPathContainsTest(_MovedFilesTestCase):
    """同一移动过程在固定 --path-contains z/ 下的独立对照。"""

    def test_moved_file_no_longer_matches_path_filter(self) -> None:
        # 与排序场景仅差一个固定的 --path-contains z/，其余参数不变。
        argv = [
            str(self.root), "target",
            "--all-lines", "--context-chars", "0",
            "--path-contains", "z/",
        ]
        self.assertEqual(
            snapshot_tree(self.root),
            {"m.md": M_MD_BYTES, "z/a.txt": Z_A_TXT_BYTES},
            "用例初始应只有 m.md 与 z/a.txt，内容分别为固定资料",
        )

        # 首次查询：只有 z/a.txt 的相对路径含 z/，返回其第 2、3 行；
        # m.md 不符合筛选，既不检索也不出现在结果中。
        code, out, err = self.run_query(argv, "首次查询（移动前，--path-contains z/）")
        first = self.assert_clean_query(
            code,
            out,
            err,
            '[{"path": "z/a.txt", "line": 2, "snippet": "target"}, '
            '{"path": "z/a.txt", "line": 3, "snippet": "target"}]\n',
            FILTERED_FIRST_RUN_EXPECTED,
            "首次查询（移动前，--path-contains z/）",
        )
        self.assertEqual(
            [item["path"] for item in first],
            ["z/a.txt", "z/a.txt"],
            "首次查询只应返回符合 z/ 筛选的 z/a.txt 命中",
        )
        self.assertNotIn("m.md", out.decode("utf-8"), "m.md 不符合筛选，不得出现")

        # 两次查询之间仅把 z/a.txt 移动为 a/b.txt（保留根目录与原 z 子目录）。
        self.move_z_a_to_a_b()

        # 再次查询：移动后的 a/b.txt 与 m.md 的相对路径都不含 z/，均不符合
        # 筛选，输出空数组加一个换行；旧路径消失不得产生无法读取告警。
        code, out, err = self.run_query(argv, "移动文件后的查询（--path-contains z/）")
        self.assert_clean_query(
            code,
            out,
            err,
            "[]\n",
            [],
            "移动文件后的查询（--path-contains z/）",
        )
        self.assertEqual(
            out,
            "[]\n".encode("utf-8"),
            "移动后无符合筛选的文件，标准输出应逐字节等于 [] 加一个换行",
        )
        self.assertNotIn("z/a.txt", out.decode("utf-8"), "旧路径不得残留")
        self.assertNotIn("a/b.txt", out.decode("utf-8"), "新路径不符合筛选，不得出现")
        self.assertNotIn("m.md", out.decode("utf-8"), "m.md 不符合筛选，不得出现")

        # 查询后文件集合与各文件字节仍与移动后一致，旧路径未被重建。
        self.assertEqual(
            snapshot_tree(self.root),
            {"a/b.txt": A_B_TXT_BYTES, "m.md": M_MD_BYTES},
            "查询不得改写任何资料、生成额外文件或重建旧路径 z/a.txt",
        )
        self.assertFalse(self.z_a_path.exists(), "查询不得重建旧路径 z/a.txt")


if __name__ == "__main__":
    unittest.main()

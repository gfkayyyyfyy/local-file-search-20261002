"""两次查询之间新增文件的回归测试。

local_search 每次查询都重新扫描选定目录、重新读取并按 UTF-8 解码候选文件，
不缓存内容、不留下索引文件，因此同一 Python 进程内连续调用公开入口
``local_search.main`` 时，第二次查询应反映两次查询之间新增的文件。本文件
覆盖两个独立场景，场景内命令行参数保持不变，新增操作只发生在两次查询之间：

- 场景一（新增合法文件）：起初只有 ``z.txt``（``header\\ntarget stable\\n``），
  默认查询 ``target`` 只返回 z.txt 第 2 行、片段 ``target stable``；首次查询
  后新增 ``a.txt``（``other\\ntarget new\\ntarget later\\n``）并新建 ``notes``
  目录及 ``notes/b.MD``（``target nested\\n``），再次查询依次返回 a.txt
  第 2 行（片段 ``target new``）、notes/b.MD 第 1 行（片段 ``target nested``）、
  z.txt 第 2 行（片段 ``target stable``）——新结果按路径码点序参与原有排序，
  原有命中的路径、行号与片段保持不变；a.txt 第 3 行的第二个命中按默认规则
  不另行返回，大写扩展名 ``.MD`` 按既有规则纳入检索。
- 场景二（新增文件无法按 UTF-8 解码）：从相同的 z.txt 开始，首次查询后只新增
  ``broken.txt``，其字节为 ``b'target\\n\\xff'``；再次查询仍只返回 z.txt 的
  原有结果，返回值 0，标准错误恰有一条包含 ``broken.txt`` 与“无法按 UTF-8
  解码”的告警——整个文件解码失败即被跳过，坏文件中位置靠前的命中（第 1 行
  ``target``）不得出现在结果中。

每次查询前后都核对选定目录下资料文件的集合与每个文件的字节完全相同，确保
查询本身既不写入资料也不生成索引等额外文件。每次查询都分别核对返回值、
标准错误，以及按 UTF-8 解析的标准输出中的完整 JSON 数组（仅含 path、line、
snippet 三项，path 使用正斜杠）。

用例通过公开入口 ``local_search.main`` 在同一进程内驱动，以其返回值对应
退出码，并捕获 ``sys.stdout``/``sys.stderr`` 的 ``.buffer`` 核对 UTF-8
字节输出——与 ``python -m local_search`` 的输入输出约定一致。所有资料由
各用例在 TemporaryDirectory 中独立准备并自动清理，仅使用标准库、离线运行；
期望值全部以字面量直接写出，不调用任何被测检索逻辑生成。资料统一按 UTF-8
准备，唯独场景二的 broken.txt 例外。

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

# 两个场景共用的 z.txt 固定内容（字符串中的 \n 即换行符，按 UTF-8 编码成
# 字节）：第 2 行命中，默认上下文 30 足以覆盖全短行，片段即整行 target stable。
Z_TXT_BYTES = "header\ntarget stable\n".encode("utf-8")

# 场景一在两次查询之间新增的固定内容：a.txt 第 2、3 行均含关键词，默认参数只
# 返回首个命中行（第 2 行）；notes/b.MD 第 1 行命中，大写扩展名 .MD 按既有
# 规则（扩展名比较忽略大小写）纳入检索。
A_TXT_BYTES = "other\ntarget new\ntarget later\n".encode("utf-8")
B_MD_BYTES = "target nested\n".encode("utf-8")

# 场景二在两次查询之间新增的 broken.txt：首行是合法命中，末尾单字节 0xff 使
# 整个文件无法按 UTF-8 解码。
BROKEN_TXT_BYTES = b"target\n\xff"

# 首次查询（两个场景相同）的完整期望结果：只有 z.txt 的原有命中。
FIRST_RUN_EXPECTED = [
    {"path": "z.txt", "line": 2, "snippet": "target stable"},
]
# 场景一第二次查询的完整期望结果，按路径码点序排列，新结果参与原有排序。
SECOND_RUN_EXPECTED = [
    {"path": "a.txt", "line": 2, "snippet": "target new"},
    {"path": "notes/b.MD", "line": 1, "snippet": "target nested"},
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
    """公共夹具：每个用例一个独立临时选定目录，起初只有 z.txt。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "z.txt").write_bytes(Z_TXT_BYTES)

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


class AddedFilesBetweenQueriesTest(_AddedFilesTestCase):
    """同一进程内连续两次查询，之间新增合法文件，第二次包含新文件的命中。"""

    def test_added_files_appear_in_next_query(self) -> None:
        # 场景内参数保持不变：始终是 <目录> target（默认选项）。
        argv = [str(self.root), "target"]
        self.assertEqual(
            snapshot_tree(self.root),
            {"z.txt": Z_TXT_BYTES},
            "用例初始只应有 z.txt 且内容为 header\\ntarget stable\\n",
        )

        # 首次查询：只有 z.txt 第 2 行命中。
        code, out, err = self.run_query(argv, "首次查询")
        first = self.assert_clean_json(
            code,
            out,
            err,
            '[{"path": "z.txt", "line": 2, "snippet": "target stable"}]\n',
            "首次查询",
        )
        self.assertEqual(first, FIRST_RUN_EXPECTED)

        # 两次查询之间新增 a.txt、notes 目录及 notes/b.MD（大写扩展名）。
        (self.root / "a.txt").write_bytes(A_TXT_BYTES)
        (self.root / "notes").mkdir()
        (self.root / "notes" / "b.MD").write_bytes(B_MD_BYTES)
        self.assertEqual(
            snapshot_tree(self.root),
            {"z.txt": Z_TXT_BYTES, "a.txt": A_TXT_BYTES, "notes/b.MD": B_MD_BYTES},
            "新增操作只应发生在两次查询之间",
        )

        # 再次查询：新结果按路径码点序参与原有排序，依次为 a.txt、notes/b.MD、
        # z.txt；原有命中的路径、行号与片段保持不变。
        code, out, err = self.run_query(argv, "新增后查询")
        second = self.assert_clean_json(
            code,
            out,
            err,
            '[{"path": "a.txt", "line": 2, "snippet": "target new"}, '
            '{"path": "notes/b.MD", "line": 1, "snippet": "target nested"}, '
            '{"path": "z.txt", "line": 2, "snippet": "target stable"}]\n',
            "新增后查询",
        )
        self.assertEqual(second, SECOND_RUN_EXPECTED)
        self.assertEqual(
            [item["path"] for item in second],
            ["a.txt", "notes/b.MD", "z.txt"],
            "结果应按路径码点序排列，path 使用正斜杠",
        )
        self.assertTrue(
            all(set(item) == {"path", "line", "snippet"} for item in second),
            "每项结果应仅含 path、line、snippet 三个字段",
        )
        # a.txt 第 2、3 行均含 target，默认参数只返回首个命中行（第 2 行）。
        a_txt_hits = [item for item in second if item["path"] == "a.txt"]
        self.assertEqual(
            a_txt_hits,
            [{"path": "a.txt", "line": 2, "snippet": "target new"}],
            "a.txt 第 3 行的第二个命中默认不得另行返回",
        )
        self.assertNotIn("target later", out.decode("utf-8"), "a.txt 第 3 行的片段不得出现")
        # 大写扩展名 .MD 按既有规则纳入检索。
        self.assertIn(
            {"path": "notes/b.MD", "line": 1, "snippet": "target nested"},
            second,
            "大写扩展名 .MD 的文件应纳入检索",
        )
        # z.txt 的原有命中原样保留。
        self.assertIn(
            {"path": "z.txt", "line": 2, "snippet": "target stable"},
            second,
            "z.txt 的原有命中（路径、行号、片段）应保持不变",
        )
        self.assertEqual((self.root / "z.txt").read_bytes(), Z_TXT_BYTES)


class AddedUndecodableFileTest(_AddedFilesTestCase):
    """新增文件无法按 UTF-8 解码：整文件跳过并告警，原有结果不受影响。"""

    def test_added_undecodable_file_is_skipped_with_warning(self) -> None:
        # 场景内参数保持不变：默认查询 target。
        argv = [str(self.root), "target"]
        self.assertEqual(snapshot_tree(self.root), {"z.txt": Z_TXT_BYTES})

        # 首次查询：只有 z.txt 的原有命中，返回值 0、标准错误为空。
        code, out, err = self.run_query(argv, "首次查询")
        first = self.assert_clean_json(
            code,
            out,
            err,
            '[{"path": "z.txt", "line": 2, "snippet": "target stable"}]\n',
            "首次查询",
        )
        self.assertEqual(first, FIRST_RUN_EXPECTED)

        # 两次查询之间只新增 broken.txt：首行是合法命中，末尾 0xff 使整文件
        # 无法按 UTF-8 解码。
        (self.root / "broken.txt").write_bytes(BROKEN_TXT_BYTES)
        self.assertEqual(
            snapshot_tree(self.root),
            {"z.txt": Z_TXT_BYTES, "broken.txt": BROKEN_TXT_BYTES},
        )

        # 再次查询：broken.txt 整体被跳过，结果仍只有 z.txt 的原有命中，
        # 返回值仍为 0。
        code, out, err = self.run_query(argv, "新增坏文件后查询")
        self.assertEqual(code, 0, "存在无法解码的新增文件时跳过它并告警，返回值仍为 0")
        self.assertEqual(
            out.decode("utf-8"),
            '[{"path": "z.txt", "line": 2, "snippet": "target stable"}]\n',
            "broken.txt 整体无法解码时不得返回它的任何命中，只剩 z.txt 第 2 行",
        )
        results = json.loads(out.decode("utf-8"))
        self.assertEqual(results, FIRST_RUN_EXPECTED)
        self.assertNotIn("broken.txt", out.decode("utf-8"), "标准输出不得残留被跳过文件的路径")
        # broken.txt 第 1 行的 target 位置靠前，但整文件解码失败即被跳过，
        # 该前置命中不得出现在结果中。
        self.assertEqual(
            [item for item in results if item["snippet"] == "target"],
            [],
            "坏文件中位置靠前的命中（第 1 行 target）不得出现",
        )

        # 标准错误有且仅有一条告警：指向 broken.txt 且说明无法按 UTF-8 解码；
        # 合法的 z.txt 不得产生任何告警。
        stderr_text = err.decode("utf-8")
        warning_lines = stderr_text.splitlines()
        self.assertEqual(
            len(warning_lines),
            1,
            f"标准错误应恰有一条告警，实际 {warning_lines!r}",
        )
        self.assertTrue(err.endswith(b"\n"), "告警行应以换行结束")
        self.assertIn("broken.txt", warning_lines[0], "告警应包含被跳过文件的相对路径 broken.txt")
        self.assertIn("无法按 UTF-8 解码", warning_lines[0], "告警应说明无法按 UTF-8 解码")
        self.assertNotIn("z.txt", stderr_text, "合法的 z.txt 不得产生告警")

        # 两个文件的字节自始至终未被查询改动（快照等价性已由 run_query 逐次核对）。
        self.assertEqual((self.root / "z.txt").read_bytes(), Z_TXT_BYTES)
        self.assertEqual((self.root / "broken.txt").read_bytes(), BROKEN_TXT_BYTES)


if __name__ == "__main__":
    unittest.main()

"""两次查询之间文件被改写的回归测试。

local_search 每次查询都重新读取选定目录下的资料、不缓存内容也不留下索引
文件，因此同一 Python 进程内连续调用公开入口 ``local_search.main`` 时，
第二次查询必须反映文件的当前内容，而不是首次查询读到的旧内容。覆盖三个
场景，每个场景内命令行参数保持不变，只在两次查询之间改写资料：

- 普通场景（默认参数）：选定目录下仅有 ``notes/a.txt``，初始内容为
  ``target old\\n``，查询 ``target`` 返回第 1 行、片段 ``target old``；
  改写为 ``header\\nnew target note\\nother\\nlast target\\n`` 后，默认查询
  只返回首个命中行（第 2 行）、片段 ``new target note``（第 4 行的命中
  不返回）；再改写为 ``no match\\n`` 后输出 ``[]``。非空结果的 path 均为
  ``notes/a.txt``。

- 选项组合场景：沿用上一改写过程，但参数固定为
  ``--all-lines --context-chars 0``：初次只返回第 1 行、片段恰为
  ``target``；改写后返回第 2、4 两个命中行，两项片段均为 ``target``；
  最后输出 ``[]``。

- 改写后的解码边界：初始 ``a.txt`` 为 ``target old\\n``、``b.md`` 为
  ``target stable\\n``，两项均命中（按路径序 a.txt 在前）；在 a.txt
  末尾追加单字节 ``0xff`` 后，再次查询只返回 b.md 第 1 行及片段
  ``target stable``，返回值 0，标准错误只有一条包含 a.txt 与
  “无法按 UTF-8 解码”的告警；把 a.txt 改写为合法的
  ``target repaired\\n`` 后，它重新以第 1 行、片段 ``target repaired``
  出现在 b.md 之前，返回值仍为 0，告警消失。

所有改写都只发生在查询之间，不涉及扫描期间文件变化的处理。每次查询都
分别核对 UTF-8 标准输出中的完整 JSON 数组、返回值与标准错误，并在查询
前后核对选定目录下资料文件的集合（相对路径）与字节完全相同，确保查询
既不写入资料也不生成索引等额外文件。全部资料按 UTF-8 准备，唯一的例外
是解码场景中追加的单字节 0xff。

用例通过公开入口 ``local_search.main`` 驱动，以其返回值对应退出码，并
捕获 ``sys.stdout``/``sys.stderr`` 的 ``.buffer`` 核对 UTF-8 字节输出
——与 ``python -m local_search`` 的输入输出约定一致。所有资料由各用例
在 TemporaryDirectory 中独立准备并自动清理，仅使用标准库、离线运行；
期望值全部以字面量直接写出，不调用任何被测检索逻辑生成。
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

# 普通场景中 notes/a.txt 的三次内容（UTF-8）。
NOTES_A_INITIAL = "target old\n".encode("utf-8")
NOTES_A_REWRITTEN = "header\nnew target note\nother\nlast target\n".encode("utf-8")
NOTES_A_NO_MATCH = "no match\n".encode("utf-8")

# 解码边界场景中两个源文件的内容（UTF-8；坏字节例外追加）。
A_TXT_INITIAL = "target old\n".encode("utf-8")
B_MD_STABLE = "target stable\n".encode("utf-8")
A_TXT_REPAIRED = "target repaired\n".encode("utf-8")


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


class ModifiedFilesBetweenQueriesTest(unittest.TestCase):
    """同一进程内连续查询，之间改写文件：后续查询反映改写后的当前内容。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    # -- 辅助 ------------------------------------------------------------

    def _write(self, rel: str, data: bytes) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def _snapshot_tree(self) -> dict:
        """选定目录下全部文件的 相对路径 -> 字节 快照（正斜杠相对路径）。"""
        return {
            str(p.relative_to(self.root)).replace(os.sep, "/"): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    def run_query_checked(self, argv: list, label: str) -> tuple:
        """执行一次查询，核对查询前后资料集合与字节不变，返回 (code, out, err)。"""
        before = self._snapshot_tree()
        code, out, err = run_main(argv)
        after = self._snapshot_tree()
        self.assertEqual(
            after,
            before,
            f"{label}: 查询不得写入资料、不得改动字节、不得生成索引等额外文件",
        )
        return code, out, err

    def assert_clean_success(self, code: int, out: bytes, err: bytes, label: str) -> list:
        """场景 label：返回值必须为 0、标准错误必须为空，返回解析后的 JSON。"""
        self.assertEqual(
            code,
            0,
            f"{label}: 返回值应为 0，实际 {code}，stderr={err!r}",
        )
        self.assertEqual(err, b"", f"{label}: 标准错误应为空（无任何告警），实际 {err!r}")
        return json.loads(out.decode("utf-8"))

    # -- 1. 默认参数：改写后默认查询只反映当前内容 -------------------------

    def test_default_query_reflects_rewritten_file(self) -> None:
        self._write("notes/a.txt", NOTES_A_INITIAL)
        argv = [str(self.root), "target"]

        # 首次查询：第 1 行命中，片段即整行 target old。
        code, out, err = self.run_query_checked(argv, "首次查询")
        first = self.assert_clean_success(code, out, err, "首次查询")
        self.assertEqual(
            first,
            [{"path": "notes/a.txt", "line": 1, "snippet": "target old"}],
            "首次查询应返回 notes/a.txt 第 1 行与片段 target old",
        )
        self.assertEqual(
            out.decode("utf-8"),
            '[{"path": "notes/a.txt", "line": 1, "snippet": "target old"}]\n',
            "首次查询的标准输出应为完整 JSON 数组加换行",
        )

        # 两次查询之间整体改写文件：第 2、4 行含关键词，默认只取首个命中行。
        self._write("notes/a.txt", NOTES_A_REWRITTEN)
        code, out, err = self.run_query_checked(argv, "改写后查询")
        second = self.assert_clean_success(code, out, err, "改写后查询")
        self.assertEqual(
            second,
            [{"path": "notes/a.txt", "line": 2, "snippet": "new target note"}],
            "改写后默认查询应只返回第 2 行，旧内容 target old 与第 4 行均不得出现",
        )
        self.assertEqual(
            out.decode("utf-8"),
            '[{"path": "notes/a.txt", "line": 2, "snippet": "new target note"}]\n',
            "改写后查询的标准输出必须反映新内容，不得残留旧片段",
        )
        self.assertNotIn(b"target old", out, "首次查询的旧片段不得残留")
        self.assertNotIn("last target", out.decode("utf-8"), "默认模式不得返回第 4 行命中")

        # 再次改写为不含关键词的内容：成功的空数组。
        self._write("notes/a.txt", NOTES_A_NO_MATCH)
        code, out, err = self.run_query_checked(argv, "无命中查询")
        third = self.assert_clean_success(code, out, err, "无命中查询")
        self.assertEqual(third, [], "改写为 no match 后应返回空数组")
        self.assertEqual(
            out.decode("utf-8"),
            "[]\n",
            "无命中时标准输出应为空数组加换行",
        )

        # 自始至资料终只有 notes/a.txt 一个文件，且字节以最后一次改写为准。
        self.assertEqual(set(self._snapshot_tree()), {"notes/a.txt"})
        self.assertEqual((self.root / "notes" / "a.txt").read_bytes(), NOTES_A_NO_MATCH)

    # -- 2. --all-lines 与 --context-chars 0 组合：同样的改写过程 ------------

    def test_all_lines_context_zero_reflects_rewritten_file(self) -> None:
        self._write("notes/a.txt", NOTES_A_INITIAL)
        argv = [str(self.root), "target", "--all-lines", "--context-chars", "0"]

        # 首次查询：片段上下文为 0，片段恰为关键词本身。
        code, out, err = self.run_query_checked(argv, "首次查询（逐行+零片段）")
        first = self.assert_clean_success(code, out, err, "首次查询（逐行+零片段）")
        self.assertEqual(
            first,
            [{"path": "notes/a.txt", "line": 1, "snippet": "target"}],
            "context-chars 0 时片段应只保留命中词 target",
        )
        self.assertEqual(
            out.decode("utf-8"),
            '[{"path": "notes/a.txt", "line": 1, "snippet": "target"}]\n',
        )

        # 改写后：第 2、4 行两个命中行各一项，按行号递增，片段均恰为 target。
        self._write("notes/a.txt", NOTES_A_REWRITTEN)
        code, out, err = self.run_query_checked(argv, "改写后查询（逐行+零片段）")
        second = self.assert_clean_success(code, out, err, "改写后查询（逐行+零片段）")
        self.assertEqual(
            second,
            [
                {"path": "notes/a.txt", "line": 2, "snippet": "target"},
                {"path": "notes/a.txt", "line": 4, "snippet": "target"},
            ],
            "改写后逐行模式应只返回第 2、4 行，两项片段在零上下文下均为 target",
        )
        self.assertEqual(
            out.decode("utf-8"),
            '[{"path": "notes/a.txt", "line": 2, "snippet": "target"}, '
            '{"path": "notes/a.txt", "line": 4, "snippet": "target"}]\n',
            "逐行结果沿用既有 JSON 格式与按路径、行号的排序",
        )
        self.assertNotIn(b"target old", out, "旧内容片段不得残留")
        for item in second:
            self.assertEqual(item["snippet"], "target", "零上下文片段不得包含命中词外的字符")

        # 再改写为无命中：空数组、退出 0、无告警。
        self._write("notes/a.txt", NOTES_A_NO_MATCH)
        code, out, err = self.run_query_checked(argv, "无命中查询（逐行+零片段）")
        third = self.assert_clean_success(code, out, err, "无命中查询（逐行+零片段）")
        self.assertEqual(third, [])
        self.assertEqual(out.decode("utf-8"), "[]\n")

    # -- 3. 改写后的解码边界：坏字节整份跳过，修复后重新出现 ----------------

    def test_decoding_boundary_across_rewrites(self) -> None:
        self._write("a.txt", A_TXT_INITIAL)
        self._write("b.md", B_MD_STABLE)
        argv = [str(self.root), "target"]

        # 首次查询：两个合法 UTF-8 文件均命中，按路径码点序 a.txt 在前。
        code, out, err = self.run_query_checked(argv, "首次查询")
        first = self.assert_clean_success(code, out, err, "首次查询")
        self.assertEqual(
            first,
            [
                {"path": "a.txt", "line": 1, "snippet": "target old"},
                {"path": "b.md", "line": 1, "snippet": "target stable"},
            ],
            "初始状态下两个文件都应命中，a.txt 按路径序排在 b.md 之前",
        )

        # 查询之间在 a.txt 末尾追加单字节 0xff（唯一的非 UTF-8 资料）。
        a_path = self.root / "a.txt"
        self._write("a.txt", A_TXT_INITIAL + b"\xff")
        self.assertEqual(a_path.read_bytes(), A_TXT_INITIAL + b"\xff")

        code, out, err = self.run_query_checked(argv, "追加坏字节后查询")
        self.assertEqual(code, 0, "含坏字节文件被跳过时返回值仍应为 0")
        results = json.loads(out.decode("utf-8"))
        self.assertEqual(
            results,
            [{"path": "b.md", "line": 1, "snippet": "target stable"}],
            "a.txt 无法整份解码时应被整份跳过，只返回 b.md 的命中",
        )
        self.assertEqual(
            out.decode("utf-8"),
            '[{"path": "b.md", "line": 1, "snippet": "target stable"}]\n',
        )
        self.assertNotIn(b"a.txt", out, "被跳过文件的路径不得出现在标准输出")
        self.assertNotIn(b"target old", out, "被跳过文件的片段不得出现在标准输出")

        # 标准错误只有一条告警，且同时点名 a.txt 与 UTF-8 解码失败。
        stderr_text = err.decode("utf-8")
        warning_lines = [line for line in stderr_text.splitlines() if line.strip()]
        self.assertEqual(len(warning_lines), 1, f"标准错误应只有一条告警，实际 {stderr_text!r}")
        self.assertIn("a.txt", stderr_text, "告警应包含被跳过文件的相对路径 a.txt")
        self.assertIn("无法按 UTF-8 解码", stderr_text, "告警应说明无法按 UTF-8 解码")

        # 把 a.txt 整体改写回合法 UTF-8：它应重新出现在 b.md 之前，告警消失。
        self._write("a.txt", A_TXT_REPAIRED)
        code, out, err = self.run_query_checked(argv, "修复后查询")
        repaired = self.assert_clean_success(code, out, err, "修复后查询")
        self.assertEqual(
            repaired,
            [
                {"path": "a.txt", "line": 1, "snippet": "target repaired"},
                {"path": "b.md", "line": 1, "snippet": "target stable"},
            ],
            "改写为合法 UTF-8 后 a.txt 应重新以第 1 行命中并排在 b.md 之前",
        )
        self.assertEqual(
            out.decode("utf-8"),
            '[{"path": "a.txt", "line": 1, "snippet": "target repaired"}, '
            '{"path": "b.md", "line": 1, "snippet": "target stable"}]\n',
        )
        self.assertNotIn(b"target old", out, "修复前的旧内容不得残留")

        # 收尾核对：资料集合始终只有两个文件，最终字节与最后一次改写一致。
        self.assertEqual(set(self._snapshot_tree()), {"a.txt", "b.md"})
        self.assertEqual(a_path.read_bytes(), A_TXT_REPAIRED)
        self.assertEqual((self.root / "b.md").read_bytes(), B_MD_STABLE)


if __name__ == "__main__":
    unittest.main()

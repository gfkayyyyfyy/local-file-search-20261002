"""纯 CR 与混用行结束符的端到端回归测试。

现有测试已覆盖 LF 与 CRLF 两种行结束符，本模块补齐另外两种资料，并把既有
检索流程的行号与片段结果一并固定：

- 五个逻辑行依次为 ``header``、``😀Target甲``、空行、``other``、
  ``Target target``，最后一行没有行结束符；四处分隔符分别构造为：
  全部为 CR、依次为 CRLF/CR/CRLF/LF 的混用版本，并以全部为 LF 的版本作
  对照。三种版本各自在独立选定的临时目录中准备 UTF-8 的 ``a.txt``；
- 查询关键词 ``TARGET``，启用 ``--ignore-case`` 并设置 ``--context-chars 1``：
  默认模式只返回第 2 行一项，片段为 ``😀Target甲``；加入 ``--all-lines`` 后
  仍按行号递增返回第 2、5 行两项，第二项片段精确为 ``Target ``（含尾随
  空格），同一行的第二次小写命中不另产生一项；
- 因此同时固定：空行仍占一个行号、CRLF 只算一次换行、末行没有结束符仍能
  命中、片段不含 CR/LF 也不借用相邻行、😀 按一个 Unicode 码点计入上下文；
- 另以仅含 ``tar`` 与 ``get`` 两行、以 CR 分隔的文件核对跨行不匹配：
  查询 ``target`` 输出空 JSON 数组、退出码 0、标准错误为空；
- 正常查询均退出 0、标准错误为空；查询前后源文件字节与目录内文件集合保持
  一致，不留下索引或其他文件。

所有资料由测试在各自独立的 TemporaryDirectory 中准备并自动清理，不依赖仓库
内的演示文件；通过命令入口 ``python -m local_search`` 验证公开输出，仅使用
Python 标准库、离线运行。期望值全部以字面量直接写出，不调用任何被测函数来
生成期望结果。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

IGNORE_CASE = "--ignore-case"
ALL_LINES = "--all-lines"
CONTEXT_CHARS = "--context-chars"

# 五个逻辑行：header / 😀Target甲 / 空行 / other / Target target。
# 末行没有行结束符；第 3 个是空行，用于固定“空行仍占一个行号”。
LOGICAL_LINES = ("header", "😀Target甲", "", "other", "Target target")

# 三个换行版本：四个分隔符依次位于第 1-2、2-3、3-4、4-5 个逻辑行之间。
# 标签会进入 subTest 与断言消息，失败时可直接指出不符合预期的换行版本。
NEWLINE_VERSIONS = (
    ("纯 CR（四处分隔符均为 CR）", (b"\r", b"\r", b"\r", b"\r")),
    ("混用（CRLF/CR/CRLF/LF）", (b"\r\n", b"\r", b"\r\n", b"\n")),
    ("纯 LF 对照（四处分隔符均为 LF）", (b"\n", b"\n", b"\n", b"\n")),
)

FIRST_HIT_ITEM = {"path": "a.txt", "line": 2, "snippet": "😀Target甲"}
LAST_LINE_ITEM = {"path": "a.txt", "line": 5, "snippet": "Target "}


def run_args(directory: Path, *argv: str) -> subprocess.CompletedProcess:
    """以任意命令行参数调用 python -m local_search，返回完成的子进程。"""
    return subprocess.run(
        [sys.executable, "-m", "local_search", str(directory), *argv],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )


def build_file_bytes(separators: tuple[bytes, ...]) -> bytes:
    """按给定的四处分隔符把 LOGICAL_LINES 拼成 UTF-8 字节，末行不附加结束符。"""
    chunks = [LOGICAL_LINES[0].encode("utf-8")]
    for separator, line in zip(separators, LOGICAL_LINES[1:]):
        chunks.append(separator)
        chunks.append(line.encode("utf-8"))
    return b"".join(chunks)


class LineEndingRegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        # 供跨行不匹配等单独场景使用；三个换行版本各自另行申请独立目录。
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _fresh_root(self) -> Path:
        """为一个换行版本申请独立选定的临时目录，测试结束后自动清理。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def _write(self, root: Path, rel: str, data: bytes) -> Path:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def _snapshot(self, root: Path) -> tuple[set[str], dict[str, bytes]]:
        """快照目录内文件集合（相对路径）与每个文件的完整字节内容。"""
        files = {
            str(p.relative_to(root)).replace(os.sep, "/")
            for p in root.rglob("*")
            if p.is_file()
        }
        contents = {
            rel: (root / rel).read_bytes()
            for rel in files
        }
        return files, contents

    def assert_clean_success(self, proc: subprocess.CompletedProcess, label: str) -> list:
        """换行版本 label：退出码必须为 0、标准错误必须为空，返回解析后的 JSON。"""
        self.assertEqual(
            proc.returncode,
            0,
            f"{label}: 退出码应为 0，实际 {proc.returncode}，stderr={proc.stderr!r}",
        )
        self.assertEqual(
            proc.stderr,
            b"",
            f"{label}: 标准错误应为空，实际 {proc.stderr!r}",
        )
        return json.loads(proc.stdout.decode("utf-8"))

    def assert_item_shape(self, item: dict, label: str) -> None:
        self.assertEqual(
            set(item.keys()),
            {"path", "line", "snippet"},
            f"{label}: 每项结果只能含 path、line、snippet 三个键",
        )
        self.assertIsInstance(item["path"], str)
        self.assertIsInstance(item["line"], int)
        self.assertNotIn("\r", item["snippet"], f"{label}: 片段不得包含 CR")
        self.assertNotIn("\n", item["snippet"], f"{label}: 片段不得包含 LF")

    # -- 1. 默认模式：三种换行版本都只返回第 2 行一项 -----------------------

    def test_default_mode_same_first_hit_for_all_newline_versions(self) -> None:
        for label, separators in NEWLINE_VERSIONS:
            with self.subTest(version=label):
                root = self._fresh_root()
                data = build_file_bytes(separators)
                self._write(root, "a.txt", data)
                # 先确认准备出的字节以 UTF-8 解码后正是五个逻辑行的资料。
                self.assertEqual(data.decode("utf-8").encode("utf-8"), data)
                files_before, bytes_before = self._snapshot(root)

                proc = run_args(root, "TARGET", IGNORE_CASE, CONTEXT_CHARS, "1")
                results = self.assert_clean_success(proc, label)

                files_after, bytes_after = self._snapshot(root)
                self.assertEqual(
                    files_before, files_after, f"{label}: 查询前后目录内文件集合必须一致"
                )
                self.assertEqual(
                    bytes_before, bytes_after, f"{label}: 查询前后源文件字节必须一致"
                )

                # 完整结果项必须精确相等：只有第 2 行一项，片段为 😀Target甲。
                self.assertEqual(
                    results,
                    [FIRST_HIT_ITEM],
                    f"{label}: 默认模式只应返回第 2 行一项，片段为 😀Target甲",
                )
                self.assert_item_shape(results[0], label)
                # 固定公开输出的完整字节：JSON 序列化与结尾换行也不变。
                self.assertEqual(
                    proc.stdout,
                    json.dumps([FIRST_HIT_ITEM], ensure_ascii=False).encode("utf-8") + b"\n",
                    f"{label}: 标准输出字节应与既有序列化完全一致",
                )
                # 😀（U+1F600，UTF-8 占 4 字节）按一个码点计入上下文：
                # 上下文额度 1 恰好取到行首的 😀 与行尾的 甲，片段共 8 个码点。
                snippet = results[0]["snippet"]
                self.assertEqual(len(snippet), 8, f"{label}: 😀 应按一个 Unicode 码点计数")
                self.assertTrue(
                    snippet.startswith("😀") and snippet.endswith("甲"),
                    f"{label}: 片段不得借用相邻行，应恰好为整行 😀Target甲",
                )
                self.assertEqual(
                    snippet.encode("utf-8"),
                    "😀Target甲".encode("utf-8"),
                    f"{label}: 片段的 UTF-8 字节应与源行一致",
                )

    # -- 2. --all-lines：三种换行版本都返回第 2、5 行两项 -------------------

    def test_all_lines_same_two_items_for_all_newline_versions(self) -> None:
        expected = [FIRST_HIT_ITEM, LAST_LINE_ITEM]
        for label, separators in NEWLINE_VERSIONS:
            with self.subTest(version=label):
                root = self._fresh_root()
                self._write(root, "a.txt", build_file_bytes(separators))
                files_before, bytes_before = self._snapshot(root)

                proc = run_args(
                    root, "TARGET", IGNORE_CASE, CONTEXT_CHARS, "1", ALL_LINES
                )
                results = self.assert_clean_success(proc, label)

                files_after, bytes_after = self._snapshot(root)
                self.assertEqual(
                    files_before, files_after, f"{label}: 查询前后目录内文件集合必须一致"
                )
                self.assertEqual(
                    bytes_before, bytes_after, f"{label}: 查询前后源文件字节必须一致"
                )

                # 数组按行号递增；空行（第 3 行）仍占行号，故末行为第 5 行；
                # 混用版本中 CRLF 只算一次换行，行号与纯 LF 完全一致。
                self.assertEqual(
                    results,
                    expected,
                    f"{label}: 应返回第 2、5 行两项，第二项片段精确为 'Target '",
                )
                self.assertEqual(
                    [item["line"] for item in results],
                    [2, 5],
                    f"{label}: 结果必须按行号排列，空行占号且 CRLF 只算一次换行",
                )
                for item in results:
                    self.assert_item_shape(item, label)
                # 末行没有行结束符仍命中；其片段含一个尾随空格作为右侧上下文，
                # 同一行的第二次小写 target 不另产生一项，也不借用上一行 other。
                self.assertEqual(results[0], FIRST_HIT_ITEM)
                self.assertEqual(results[1], LAST_LINE_ITEM)
                self.assertEqual(results[1]["snippet"], "Target ")
                self.assertNotIn("other", results[1]["snippet"])
                self.assertNotIn("target", results[1]["snippet"][len("Target "):])
                self.assertEqual(
                    proc.stdout,
                    json.dumps(expected, ensure_ascii=False).encode("utf-8") + b"\n",
                    f"{label}: 标准输出字节应与既有序列化完全一致",
                )

    # -- 3. CR 分隔的跨行关键词不匹配 --------------------------------------

    def test_keyword_split_across_cr_separated_lines_does_not_match(self) -> None:
        # 仅含 tar 与 get 两个逻辑行、以单个 CR 分隔，末行无结束符：
        # target 被行边界切开，不得跨行拼成一次命中。
        self._write(self.root, "a.txt", b"tar\rget")
        files_before, bytes_before = self._snapshot(self.root)

        proc = run_args(self.root, "target")

        files_after, bytes_after = self._snapshot(self.root)
        self.assertEqual(files_before, files_after, "跨行场景：查询前后文件集合必须一致")
        self.assertEqual(bytes_before, bytes_after, "跨行场景：查询前后源文件字节必须一致")
        self.assertEqual(proc.returncode, 0, f"退出码应为 0，实际 {proc.returncode}")
        self.assertEqual(proc.stderr, b"", f"标准错误应为空，实际 {proc.stderr!r}")
        self.assertEqual(
            proc.stdout,
            b"[]\n",
            "CR 分隔的 tar/get 不得跨行匹配 target，应输出空 JSON 数组",
        )
        self.assertEqual(json.loads(proc.stdout.decode("utf-8")), [])

    # -- 4. 末行无结束符且以 CR 分隔时仍能命中（既有流程的直接核对） --------

    def test_final_line_without_terminator_still_hits_with_cr_newlines(self) -> None:
        # 两行仅以 CR 分隔，末行 target 不带任何结束符：仍须按第 2 行命中。
        self._write(self.root, "a.txt", b"header\rtarget")
        proc = run_args(self.root, "target")
        results = self.assert_clean_success(proc, "纯 CR 末行无结束符")
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 2, "snippet": "target"}],
            "纯 CR 换行下末行没有结束符仍应命中，且 CR 不得进入片段",
        )
        self.assertNotIn(b"\r", proc.stdout.rstrip(b"\n"))


if __name__ == "__main__":
    unittest.main()

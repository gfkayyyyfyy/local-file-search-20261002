"""文件大小与最后修改时间均未变化时仍读取当前内容的回归测试。

local_search 每次查询都重新扫描选定目录、重新读取并按 UTF-8 解码候选文件，
不缓存内容、不留下索引文件。本文件在既有“两次查询之间文件被改写”的回归
测试（tests/test_modified_files_between_queries.py）基础上补充一个更苛刻
的场景：同一普通 UTF-8 文本文件 a.txt 在两次查询之间被改写，但改写后的
UTF-8 字节总数与最后修改时间（mtime）都与初始基准完全相等——任何把
“大小和 mtime 相同”误当成“内容没有变化”而跳过重新读取的实现，都会在本
测试下暴露旧行号或旧片段。

固定资料（字符串中的 \\n 即换行符，按 UTF-8 编码成字节，三版等长）：

- 初始：``target old\\nother here\\n``，查询 ``target`` 命中第 1 行，
  片段 ``target old``；
- 改写一：``other here\\ntarget new\\n``，命中第 2 行，片段 ``target new``，
  不得残留第 1 行或旧片段；
- 改写二：``other here\\nplain text\\n``，不再含关键词，输出 ``[]``。

每次改写后先把 mtime 恢复至初始基准（访问时间不属于相等条件），并实际核对
文件大小与 mtime（按文件系统实际返回的精度）与基准相等，再执行查询，避免
只声称条件已经成立。三次查询都使用默认选项、相同目录与关键词参数，返回值
为 0，标准错误为空，标准输出为 UTF-8 JSON 数组加末尾换行。

所有改写只发生在查询之间，查询期间文件保持稳定；每次查询前后都核对选定
目录下资料文件的集合与每个文件的字节完全相同，确保查询本身既不写入资料
也不生成索引等额外文件。期望值全部以字面量直接写出，不调用任何被测检索
逻辑生成。

用例通过公开入口 ``local_search.main`` 在同一进程内驱动，以其返回值对应
退出码，并捕获 ``sys.stdout``/``sys.stderr`` 的 ``.buffer`` 核对 UTF-8
字节输出——与 ``python -m local_search`` 的输入输出约定一致。所有资料由
用例在 TemporaryDirectory 中独立准备并自动清理，仅使用标准库、离线运行，
不依赖等待、系统权限调整或仓库演示目录。

可独立执行：``python -m unittest tests.test_same_metadata_rewrite``，
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

# 三版固定内容：均为两行、UTF-8 字节总数相等（各 22 字节），但命中行与片段
# 不同，最后一版不含关键词。
INITIAL_BYTES = "target old\nother here\n".encode("utf-8")
REWRITTEN_BYTES = "other here\ntarget new\n".encode("utf-8")
NO_MATCH_BYTES = "other here\nplain text\n".encode("utf-8")


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


class SameMetadataRewriteTest(unittest.TestCase):
    """大小与 mtime 均不变的改写：每次查询仍须读取当前内容。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.a_path = self.root / "a.txt"
        self.a_path.write_bytes(INITIAL_BYTES)
        # 初始基准：文件系统实际返回的大小与 mtime（纳秒精度，访问时间不参与）。
        initial_stat = self.a_path.stat()
        self.base_size = initial_stat.st_size
        self.base_mtime_ns = initial_stat.st_mtime_ns

    def rewrite_keeping_metadata(self, content: bytes, label: str) -> None:
        """整体改写 a.txt，保持字节总数不变并把 mtime 恢复至初始基准。

        改写后实际核对大小与 mtime 与基准相等，再交给后续查询验证。
        """
        self.assertEqual(
            len(content),
            self.base_size,
            f"{label}: 测试资料本身必须与基准等长（前置条件）",
        )
        self.a_path.write_bytes(content)
        # 只恢复 mtime；atime 不属于相等条件，保持改写后的当前值即可。
        current_atime_ns = self.a_path.stat().st_atime_ns
        os.utime(self.a_path, ns=(current_atime_ns, self.base_mtime_ns))
        stat = self.a_path.stat()
        self.assertEqual(
            stat.st_size,
            self.base_size,
            f"{label}: 改写后文件大小必须与基准相等",
        )
        self.assertEqual(
            stat.st_mtime_ns,
            self.base_mtime_ns,
            f"{label}: 改写后最后修改时间必须与基准相等",
        )

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

    def test_same_size_and_mtime_still_reads_current_content(self) -> None:
        # 场景内参数保持不变：始终是 <目录> target（默认选项）。
        argv = [str(self.root), "target"]
        self.assertEqual(
            snapshot_tree(self.root),
            {"a.txt": INITIAL_BYTES},
            "用例初始只应有 a.txt 且内容为 target old\\nother here\\n",
        )

        # 首次查询：第 1 行命中，默认上下文足以覆盖全短行，片段即整行。
        code, out, err = self.run_query(argv, "首次查询（初始内容）")
        first = self.assert_clean_json(
            code,
            out,
            err,
            '[{"path": "a.txt", "line": 1, "snippet": "target old"}]\n',
            "首次查询",
        )
        self.assertEqual(
            first,
            [{"path": "a.txt", "line": 1, "snippet": "target old"}],
        )

        # 两次查询之间整体改写：字节总数与 mtime 均与基准相等，但命中移到
        # 第 2 行、片段变为 target new。
        self.rewrite_keeping_metadata(REWRITTEN_BYTES, "第一次改写")
        code, out, err = self.run_query(argv, "同大小同 mtime 改写后的查询")
        second = self.assert_clean_json(
            code,
            out,
            err,
            '[{"path": "a.txt", "line": 2, "snippet": "target new"}]\n',
            "同大小同 mtime 改写后的查询",
        )
        self.assertEqual(
            second,
            [{"path": "a.txt", "line": 2, "snippet": "target new"}],
            "默认查询每个文件只返回首个命中行：必须是当前内容的第 2 行",
        )
        self.assertNotIn("target old", out.decode("utf-8"), "不得残留旧片段 target old")
        self.assertNotIn('"line": 1', out.decode("utf-8"), "不得残留旧行号 1")

        # 再次改写为不含关键词的等长内容，mtime 仍恢复至基准。
        self.rewrite_keeping_metadata(NO_MATCH_BYTES, "第二次改写")
        code, out, err = self.run_query(argv, "改写为无命中后的查询")
        self.assert_clean_json(code, out, err, "[]\n", "无命中查询")


if __name__ == "__main__":
    unittest.main()

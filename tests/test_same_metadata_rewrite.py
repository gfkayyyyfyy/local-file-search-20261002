"""文件大小与最后修改时间均未变化时仍读取当前内容的回归测试。

既有“两次查询之间文件被改写”的测试覆盖的是内容变化通常伴随的大小或修改
时间变化；本文件补上更隐蔽的情形：同一个普通 UTF-8 文本文件 ``a.txt`` 在
两次查询之间被整体改写，但新内容与旧内容的 UTF-8 字节总数完全相同，并且
最后修改时间被显式恢复到改写前的基准——即“大小 + 最后修改时间”这对元数据
完全不变。现有实现每次查询都重新打开、重新读取候选文件，本测试防止后续
维护把相同的大小和修改时间误当成内容没有变化，从而复用旧内容或缓存/索引。

三版固定内容均为 22 个 UTF-8 字节，且两两字节不同（字符串中的 ``\\n`` 即
一个换行符）：

- 初始：``target old\\nother here\\n``
- 改写：``other here\\ntarget new\\n``（关键词从第 1 行移到第 2 行）
- 再改写：``other here\\nplain text\\n``（不再含关键词）

三次查询都使用相同目录与关键词参数 ``<目录> target`` 及全部默认选项，在同一
Python 进程内通过公开入口 ``local_search.main`` 连续调用：

1. 初始内容：JSON 数组只有一项，path 为 ``a.txt``、line 为 1、snippet 为
   ``target old``；
2. 改写并保持字节总数与最后修改时间不变后：应只有 ``a.txt`` 第 2 行、
   snippet 为 ``target new``，不能残留旧行号 1 或旧片段 ``target old``；
3. 再改写为无命中内容并同样保持大小与修改时间后：输出空数组 ``[]``。

每次改写后、发起查询前，都用文件系统实际返回的值核对 ``st.st_size`` 与
``st.st_mtime_ns`` 与初始基准相等——基准直接取自文件系统对初始文件的
stat，而不是测试自行指定的时间戳，因此按文件系统自身的时间精度比较，无需
sleep 或等待时间流逝。访问时间 ``st_atime`` 不属于相等条件：查询本身会
读取文件，atime 可能随之变化。所有改写只发生在两次调用之间，查询期间文件
保持稳定；每次调用前后都核对目录内文件集合恰为 ``{"a.txt"}`` 且文件字节
完全一致，大小与最后修改时间也一致，确保查询只读不写、不在资料目录留下
索引等任何额外文件。

三次查询都核对：返回值为 0、标准错误为空、标准输出为逐字节匹配的 UTF-8
JSON 数组加末尾换行。期望值全部以字面量直接写出，不调用任何检索内部函数
生成。资料在独立 TemporaryDirectory 中准备并自动清理，仅使用 Python 标准
库、离线执行，不调整系统权限，也不依赖仓库的演示目录。

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

# a.txt 的三版固定内容：UTF-8 字节总数相同（均为 22），字节两两不同。
# 初始第 1 行命中；改写后第 2 行命中；最后无命中。
INITIAL_A_BYTES = "target old\nother here\n".encode("utf-8")
REWRITTEN_A_BYTES = "other here\ntarget new\n".encode("utf-8")
NO_MATCH_A_BYTES = "other here\nplain text\n".encode("utf-8")

EXPECTED_BYTE_SIZE = 22


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
    """同大小、同最后修改时间的改写之间连续查询，每次都反映当前内容。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.a_path = self.root / "a.txt"
        self.a_path.write_bytes(INITIAL_A_BYTES)

        # 元数据基准直接取自文件系统对初始文件实际返回的值：大小为字节数，
        # 修改时间取纳秒精度的 st_mtime_ns，随后恢复时写回同一值即可按文件
        # 系统自身精度严格相等，不依赖 sleep 或时间粒度猜测。
        baseline_stat = self.a_path.stat()
        self.baseline_size = baseline_stat.st_size
        self.baseline_mtime_ns = baseline_stat.st_mtime_ns

    def run_query(self, label: str) -> tuple:
        """以始终不变的 argv（<目录> target，默认选项）执行一次查询。

        核对查询前后目录内文件集合恰为 {"a.txt"}、各文件字节完全一致，且
        a.txt 的大小与最后修改时间不变（查询期间文件稳定、查询不写资料、
        不生成索引等额外文件）。返回 (返回值, 标准输出字节, 标准错误字节)。
        """
        argv = [str(self.root), "target"]
        before = snapshot_tree(self.root)
        self.assertEqual(set(before), {"a.txt"}, f"{label}: 查询前目录内应只有 a.txt")
        before_stat = self.a_path.stat()

        code, out, err = run_main(argv)

        after = snapshot_tree(self.root)
        self.assertEqual(set(after), {"a.txt"}, f"{label}: 查询后目录内必须仍只有 a.txt（不得留下索引等文件）")
        self.assertEqual(
            after,
            before,
            f"{label}: 查询前后资料文件集合与各文件字节必须完全一致（查询不得写入资料）",
        )
        after_stat = self.a_path.stat()
        self.assertEqual(
            (after_stat.st_size, after_stat.st_mtime_ns),
            (before_stat.st_size, before_stat.st_mtime_ns),
            f"{label}: 查询不得改变文件大小或最后修改时间",
        )
        return code, out, err

    def rewrite_preserving_metadata(self, new_bytes: bytes, label: str) -> None:
        """在两次查询之间整体改写 a.txt，再把最后修改时间恢复到初始基准。

        恢复后、返回前实际核对（均以文件系统返回值为准）：文件大小与基准
        相等、最后修改时间与基准相等、字节确为新版内容。访问时间不做核对，
        写回时沿用其当前值（atime 不是本测试的相等条件）。
        """
        self.assertNotEqual(
            new_bytes,
            self.a_path.read_bytes(),
            f"{label}: 改写用的新字节必须与当前字节不同，否则场景不成立",
        )
        self.a_path.write_bytes(new_bytes)

        # 访问时间不属于相等条件：写回时沿用其当前值，只把修改时间恢复到基准。
        current_atime_ns = self.a_path.stat().st_atime_ns
        os.utime(self.a_path, ns=(current_atime_ns, self.baseline_mtime_ns))

        st = self.a_path.stat()
        self.assertEqual(
            st.st_size,
            self.baseline_size,
            f"{label}: 改写后文件大小必须与基准相等（同字节总数的改写）",
        )
        self.assertEqual(
            st.st_mtime_ns,
            self.baseline_mtime_ns,
            f"{label}: 改写后最后修改时间必须恢复至初始基准",
        )
        self.assertEqual(st.st_size, len(new_bytes))
        self.assertEqual(
            self.a_path.read_bytes(),
            new_bytes,
            f"{label}: 改写后磁盘字节必须确实是新版内容",
        )

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
            f"{label}: 标准输出应为完整 UTF-8 JSON 数组加末尾换行",
        )
        return json.loads(out.decode("utf-8"))

    def test_fixed_fixture_same_size_distinct_bytes(self) -> None:
        """固定资料前提：三版内容字节总数相同但字节两两不同。"""
        for label, data in (
            ("初始", INITIAL_A_BYTES),
            ("改写", REWRITTEN_A_BYTES),
            ("再改写", NO_MATCH_A_BYTES),
        ):
            self.assertEqual(
                len(data),
                EXPECTED_BYTE_SIZE,
                f"{label}内容应为 {EXPECTED_BYTE_SIZE} 个 UTF-8 字节，实际 {len(data)}",
            )
        self.assertNotEqual(INITIAL_A_BYTES, REWRITTEN_A_BYTES)
        self.assertNotEqual(INITIAL_A_BYTES, NO_MATCH_A_BYTES)
        self.assertNotEqual(REWRITTEN_A_BYTES, NO_MATCH_A_BYTES)

    def test_rewrite_with_unchanged_size_and_mtime_still_rereads(self) -> None:
        """同大小、同最后修改时间的两次改写后，默认查询必须读取当前内容。"""
        # 初始目录内只有普通文件 a.txt，基准大小即初始内容的字节数。
        self.assertEqual(
            snapshot_tree(self.root),
            {"a.txt": INITIAL_A_BYTES},
            "用例初始只应有 a.txt，内容为 target old\\nother here\\n",
        )
        self.assertTrue(self.a_path.is_file(), "a.txt 必须是普通文件")
        self.assertFalse(self.a_path.is_symlink(), "a.txt 不得是符号链接")
        self.assertEqual(
            self.baseline_size,
            EXPECTED_BYTE_SIZE,
            "初始基准大小应为 22 字节",
        )

        # 第一次查询（默认参数）：第 1 行命中，默认上下文 30 足以覆盖整行，
        # 片段即 target old。
        code, out, err = self.run_query("首次查询（初始内容）")
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

        # 两次查询之间整体改写：字节总数与最后修改时间恢复到基准后再查询。
        self.rewrite_preserving_metadata(REWRITTEN_A_BYTES, "改写为 target new")
        code, out, err = self.run_query("同大小同修改时间改写后的查询")
        text = out.decode("utf-8")
        second = self.assert_clean_json(
            code,
            out,
            err,
            '[{"path": "a.txt", "line": 2, "snippet": "target new"}]\n',
            "同大小同修改时间改写后的查询",
        )
        self.assertEqual(
            second,
            [{"path": "a.txt", "line": 2, "snippet": "target new"}],
            "元数据未变也必须重新读取：命中应随内容移到第 2 行、片段为 target new",
        )
        self.assertNotIn("target old", text, "输出不得残留旧片段 target old")
        self.assertNotIn('"line": 1', text, "输出不得残留旧行号 1")
        self.assertIn("target new", text)

        # 再次整体改写为不含关键词的内容，同样保持大小与最后修改时间。
        self.rewrite_preserving_metadata(NO_MATCH_A_BYTES, "改写为无命中内容")
        code, out, err = self.run_query("同大小同修改时间再改写后的查询")
        self.assert_clean_json(code, out, err, "[]\n", "无命中查询")
        self.assertNotIn(b"target", out, "无命中时标准输出不得出现关键词相关内容")


if __name__ == "__main__":
    unittest.main()

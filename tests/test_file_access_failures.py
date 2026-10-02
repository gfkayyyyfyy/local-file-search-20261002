"""文件系统访问失败的回归测试。

围绕公开入口 ``local_search.main``（返回值对应退出码）固定以下失败处理规则，
与 ``python -m local_search`` 的输入输出约定一致：

- 候选 ``.txt``/``.md`` 文件在打开或读取时发生 ``PermissionError`` 或其他
  ``OSError``：该文件被**整体跳过**，标准错误写入一条含相对路径、"无法读取"
  及失败原因的告警，其余文件正常检索，返回值为 0；全部候选文件都失败时结果
  为 ``[]``，每个文件各告警一次，返回值仍为 0；
- 被 ``--path-contains`` 排除的文件**不会被读取**，也不产生该文件的告警，
  入选文件的结果保持不变；
- 选定目录或其子目录在枚举时发生 ``OSError``：整个查询终止，返回值为 2，
  标准输出完全为空（不输出部分 JSON、不打印异常堆栈），标准错误包含选定
  目录、"无法完成目录遍历" 及失败原因；目录中另有正常命中文件、或附加路径
  筛选，都不改变这一结果。

失败不依赖真实权限配置（chmod 在 root 下失效、Windows 语义不同，均不稳定），
而是用 ``unittest.mock`` 在调用 ``main`` 期间对 ``local_search.search`` 模块内的
``open`` 与 ``os.scandir`` 注入确定性的 ``OSError``，因此在 Windows 与 Linux 上
无需管理员权限即可稳定复现。所有资料由测试在 TemporaryDirectory 中独立准备并
自动清理，仅使用 Python 标准库、离线运行。期望值全部以字面量直接写出，不调用
任何被测函数来生成期望结果。
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
sys.path.insert(0, str(PROJECT_ROOT))

from local_search import main  # noqa: E402

KEYWORD = "target"
# 注入失败时使用的可识别原因文本，断言标准错误必须原样包含它。
PERMISSION_REASON = "模拟的权限拒绝"
IO_REASON = "模拟的磁盘读取失败"
TRAVERSAL_REASON = "模拟的目录枚举失败"


class _BufferCapture:
    """仅暴露 ``.buffer`` 的标准流替身，匹配 main 只写 ``stream.buffer`` 的约定。"""

    def __init__(self) -> None:
        self.buffer = io.BytesIO()


def run_main(argv: list[str]) -> tuple[int, bytes, bytes]:
    """在进程内调用公开入口 main，返回 (返回值, 标准输出字节, 标准错误字节)。"""
    stdout_capture = _BufferCapture()
    stderr_capture = _BufferCapture()
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = stdout_capture, stderr_capture
    try:
        code = main(argv)
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
    return code, stdout_capture.buffer.getvalue(), stderr_capture.buffer.getvalue()


def _patch_open_failing_on(failing_paths: set[str], reason: BaseException):
    """让 local_search.search 内的 open 在打开指定绝对路径时抛出给定异常。

    其余路径委托给真正的 open；返回 (patch 上下文管理器, 已尝试打开的路径列表)。
    """
    import local_search.search as search_module

    real_open = open
    attempted: list[str] = []

    def fake_open(file, *args, **kwargs):
        normalized = os.path.normpath(os.fspath(file))
        attempted.append(normalized)
        if normalized in failing_paths:
            raise reason
        return real_open(file, *args, **kwargs)

    return mock.patch.object(search_module, "open", fake_open, create=True), attempted


def _patch_scandir_failing_on(failing_dirs: set[str]):
    """让 os.scandir 在枚举指定绝对目录时抛出 OSError，其余目录正常枚举。"""
    real_scandir = os.scandir

    def fake_scandir(path):
        if os.path.normpath(os.fspath(path)) in failing_dirs:
            raise OSError(TRAVERSAL_REASON)
        return real_scandir(path)

    return mock.patch("os.scandir", fake_scandir)


class FileReadFailureTest(unittest.TestCase):
    """单个/全部候选文件读取失败：跳过该文件并告警，查询继续，返回 0。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # 目录名刻意包含中文与空格。
        self.root = Path(self._tmp.name) / "资料 目录"
        (self.root / "notes").mkdir(parents=True)
        (self.root / "a.txt").write_bytes("alpha\ntarget note\n".encode("utf-8"))
        (self.root / "notes" / "b.md").write_bytes("target again\n".encode("utf-8"))
        self.b_md = os.path.normpath(str(self.root / "notes" / "b.md"))
        self.a_txt = os.path.normpath(str(self.root / "a.txt"))

    def _run_with_failures(self, failing_paths: set[str], reason: BaseException,
                           extra_argv: tuple[str, ...] = ()) -> tuple[int, bytes, bytes]:
        patcher, attempted = _patch_open_failing_on(failing_paths, reason)
        with patcher:
            code, stdout, stderr = run_main([str(self.root), KEYWORD, *extra_argv])
        self._last_attempted = attempted
        return code, stdout, stderr

    def _assert_warning(self, stderr: bytes, rel_path: str, reason_text: str, label: str) -> None:
        text = stderr.decode("utf-8")
        self.assertIn(rel_path, text, f"{label}: 告警应包含正斜杠相对路径 {rel_path!r}，实际 {text!r}")
        self.assertIn("无法读取", text, f"{label}: 告警应说明无法读取，实际 {text!r}")
        self.assertIn(reason_text, text, f"{label}: 告警应包含失败原因 {reason_text!r}，实际 {text!r}")

    # -- 1. notes/b.md 读取失败：仅 a.txt 命中，返回 0，告警说明原因 --------

    def test_permission_error_on_one_file_skips_it_and_keeps_other_hits(self) -> None:
        code, stdout, stderr = self._run_with_failures(
            {self.b_md}, PermissionError(PERMISSION_REASON)
        )

        self.assertEqual(code, 0, f"单文件失败时返回值应为 0，实际 {code}")
        results = json.loads(stdout.decode("utf-8"))
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 2, "snippet": "target note"}],
            "b.md 被整体跳过后应只剩 a.txt 第 2 行的命中",
        )
        self._assert_warning(stderr, "notes/b.md", PERMISSION_REASON, "PermissionError")
        self.assertNotIn("a.txt", stderr.decode("utf-8"), "正常文件不得出现在告警中")

    def test_generic_oserror_on_one_file_is_handled_the_same_way(self) -> None:
        code, stdout, stderr = self._run_with_failures(
            {self.b_md}, OSError(IO_REASON)
        )

        self.assertEqual(code, 0, f"普通 OSError 时返回值应为 0，实际 {code}")
        results = json.loads(stdout.decode("utf-8"))
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 2, "snippet": "target note"}],
            "普通 OSError 与 PermissionError 应按同一规则跳过该文件",
        )
        self._assert_warning(stderr, "notes/b.md", IO_REASON, "OSError")

    # -- 2. 全部候选文件读取失败：结果 []，分别告警，返回 0 ------------------

    def test_all_candidate_files_failing_returns_empty_array_with_warnings(self) -> None:
        code, stdout, stderr = self._run_with_failures(
            {self.a_txt, self.b_md}, PermissionError(PERMISSION_REASON)
        )

        self.assertEqual(code, 0, f"全部失败时返回值应为 0，实际 {code}")
        self.assertEqual(
            json.loads(stdout.decode("utf-8")),
            [],
            "全部候选文件读取失败时结果应为 []",
        )
        self.assertEqual(stdout.decode("utf-8").strip(), "[]")
        text = stderr.decode("utf-8")
        self._assert_warning(stderr, "a.txt", PERMISSION_REASON, "全部失败/a.txt")
        self._assert_warning(stderr, "notes/b.md", PERMISSION_REASON, "全部失败/b.md")
        warning_lines = [line for line in text.splitlines() if line.strip()]
        self.assertEqual(len(warning_lines), 2, "两个失败文件应各产生一条告警")

    # -- 3. 被 --path-contains 排除的失败文件：不读取、不告警 ----------------

    def test_excluded_failing_file_is_never_read_and_never_warned(self) -> None:
        # 片段 "a.txt" 只选中 a.txt；notes/b.md 被排除，即使注入失败也不得触发。
        code, stdout, stderr = self._run_with_failures(
            {self.b_md},
            PermissionError(PERMISSION_REASON),
            extra_argv=("--path-contains", "a.txt"),
        )

        self.assertEqual(code, 0, f"排除失败文件后返回值应为 0，实际 {code}")
        self.assertEqual(
            json.loads(stdout.decode("utf-8")),
            [{"path": "a.txt", "line": 2, "snippet": "target note"}],
            "入选文件 a.txt 的结果应保持不变",
        )
        self.assertEqual(stderr, b"", f"被排除的文件不得产生告警，实际 {stderr!r}")
        self.assertNotIn(
            self.b_md,
            self._last_attempted,
            "被路径筛选排除的文件不应被打开读取",
        )


class TraversalFailureTest(unittest.TestCase):
    """目录枚举失败：整个查询终止，返回 2，标准输出为空。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "资料 目录"
        # locked 子目录内也有一个命中文件：遍历失败时不得出现任何部分结果。
        (self.root / "locked").mkdir(parents=True)
        (self.root / "good.txt").write_bytes("target here\n".encode("utf-8"))
        (self.root / "locked" / "inner.txt").write_bytes("target inner\n".encode("utf-8"))
        self.root_str = str(self.root)
        self.locked = os.path.normpath(str(self.root / "locked"))

    def _assert_traversal_failure(self, code: int, stdout: bytes, stderr: bytes, label: str) -> None:
        self.assertEqual(code, 2, f"{label}: 返回值应为 2，实际 {code}")
        self.assertEqual(stdout, b"", f"{label}: 标准输出必须完全为空，实际 {stdout!r}")
        text = stderr.decode("utf-8")
        self.assertIn(self.root_str, text, f"{label}: 标准错误应包含选定目录，实际 {text!r}")
        self.assertIn("无法完成目录遍历", text, f"{label}: 标准错误应说明无法完成目录遍历，实际 {text!r}")
        self.assertIn(TRAVERSAL_REASON, text, f"{label}: 标准错误应包含失败原因，实际 {text!r}")
        self.assertNotIn("Traceback", text, f"{label}: 不得向用户打印异常堆栈")
        self.assertNotIn("[", stdout.decode("utf-8", errors="replace"), f"{label}: 不得输出部分 JSON")

    # -- 1. 子目录枚举失败：终止查询，即使另有正常命中文件 -------------------

    def test_subdirectory_traversal_failure_aborts_the_whole_query(self) -> None:
        with _patch_scandir_failing_on({self.locked}):
            code, stdout, stderr = run_main([self.root_str, KEYWORD])
        self._assert_traversal_failure(code, stdout, stderr, "子目录枚举失败")

    # -- 2. 选定目录本身枚举失败：同样终止查询 -------------------------------

    def test_root_directory_traversal_failure_aborts_the_whole_query(self) -> None:
        with _patch_scandir_failing_on({os.path.normpath(self.root_str)}):
            code, stdout, stderr = run_main([self.root_str, KEYWORD])
        self._assert_traversal_failure(code, stdout, stderr, "选定目录枚举失败")

    # -- 3. 路径筛选不能把遍历失败改成成功的空数组 ---------------------------

    def test_path_filter_does_not_turn_traversal_failure_into_empty_success(self) -> None:
        # 片段 "nothing/" 会排除所有文件；若先遍历后筛选，遍历失败仍应返回 2。
        with _patch_scandir_failing_on({self.locked}):
            code, stdout, stderr = run_main(
                [self.root_str, KEYWORD, "--path-contains", "nothing/"]
            )
        self._assert_traversal_failure(code, stdout, stderr, "筛选+遍历失败")


if __name__ == "__main__":
    unittest.main()

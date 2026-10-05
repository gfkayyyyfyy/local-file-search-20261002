"""文件系统访问失败的回归测试。

固定 local_search 在两类访问失败下的公开行为（与 README 约定一致）：

单个候选文件读取失败（PermissionError 或其他 OSError）——该文件被整体跳过：
- 其余文件仍正常检索，命中结果不变，退出码为 0；
- 标准错误逐文件告警，包含该文件的正斜杠相对路径、"无法读取"及失败原因；
- 全部候选文件都读取失败时输出 ``[]``，仍为每个文件分别告警，退出码 0；
- 被 ``--path-contains`` 排除的失败文件根本不会被打开，也不产生告警，
  正常入选文件的结果保持不变。

读取内容阶段失败（open 成功、read() 抛出 OSError）——与打开失败同等处理：
- 全部读取正常时两个候选文件各贡献一条命中，退出码 0，标准错误为空；
- 单个文件读取失败时该文件不贡献任何结果，退出码仍为 0，标准错误恰有
  一条告警（含正斜杠相对路径、"无法读取"与失败原因），不含异常堆栈；
  查询前后源文件路径与字节内容一致，不留下索引或导出文件；
- 全部候选文件都在读取时失败时输出 ``[]``，逐文件各一条告警（顺序不作
  约束），退出码 0；
- 被 ``--path-contains`` 排除的文件在读取前即被排除：既不打开也不触发
  失败，入选文件结果不变，标准错误为空。

目录枚举失败（选定目录或其子目录 scandir 抛出 OSError）——整个查询终止：
- 退出码为 2，标准输出完全为空（不输出部分 JSON，不打印异常堆栈）；
- 标准错误包含选定目录、"无法完成目录遍历"及失败原因；
- 即使目录中另有正常命中文件结果也一样；``--path-contains`` 路径筛选
  不能把遍历失败变成成功的空数组（遍历先于筛选）。

失败注入方式：不依赖 chmod、管理员权限或真实用户权限配置（这些在 Windows
与 Linux 上行为不一致，且 root 下 chmod 不生效），而是用 unittest.mock 在
进程内替换 ``local_search.search.open`` 与 ``os.scandir``，仅对目标路径抛出
预定的 OSError（或返回 read() 时抛错的文件对象替身），其余路径走真实实现。
因此在两个平台上都能稳定复现。

用例通过公开入口 ``local_search.main`` 驱动，以其返回值对应退出码，并捕获
``sys.stdout``/``sys.stderr`` 的 ``.buffer`` 核对 UTF-8 字节输出——与
``python -m local_search`` 的输入输出约定一致。所有资料由测试在
TemporaryDirectory 中独立准备并自动清理，仅使用标准库、离线运行；期望值
全部以字面量直接写出，不复用被测检索逻辑计算。
"""

import errno
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


class _CapturedStream:
    """sys.stdout/sys.stderr 的最小替身：被测代码只使用其 .buffer 写字节。"""

    def __init__(self) -> None:
        self.buffer = io.BytesIO()


def run_main(argv: list) -> tuple:
    """调用公开入口 local_search.main，返回 (退出码, 标准输出字节, 标准错误字节)。"""
    stdout = _CapturedStream()
    stderr = _CapturedStream()
    with mock.patch.object(sys, "stdout", stdout), mock.patch.object(sys, "stderr", stderr):
        code = local_search.main(argv)
    return code, stdout.buffer.getvalue(), stderr.buffer.getvalue()


def _raising_open(failures: dict, calls: set | None = None):
    """构造 open 替身：对 failures 中的规范化路径抛出预定异常，其余走真实 open。"""
    real_open = open

    def fake_open(file, *args, **kwargs):
        key = os.path.normpath(os.fspath(file))
        if calls is not None:
            calls.add(key)
        if key in failures:
            raise failures[key]
        return real_open(file, *args, **kwargs)

    return fake_open


def _raising_scandir(failures: dict):
    """构造 os.scandir 替身：对 failures 中的规范化路径抛出预定异常。"""
    real_scandir = os.scandir

    def fake_scandir(path):
        key = os.path.normpath(os.fspath(path))
        if key in failures:
            raise failures[key]
        return real_scandir(path)

    return fake_scandir


class _ReadFailingFile:
    """open 成功返回、但 read() 抛出预定异常的最小文件对象替身。

    用于把失败精确注入到“打开成功、读取内容”这一阶段：作为上下文管理器
    正常进入与退出，只有 read() 在首次调用时抛出构造时给定的 OSError。
    """

    def __init__(self, error: OSError, on_read=None) -> None:
        self._error = error
        self._on_read = on_read

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def read(self, *args, **kwargs):
        if self._on_read is not None:
            self._on_read()
        raise self._error


def _read_raising_open(failures: dict, opens: set | None = None, reads: set | None = None):
    """构造 open 替身：对 failures 中的规范化路径返回读取时抛错的文件对象，
    其余路径走真实 open。opens/reads 若给出，分别记录被打开与被读取的路径。"""
    real_open = open

    def fake_open(file, *args, **kwargs):
        key = os.path.normpath(os.fspath(file))
        if opens is not None:
            opens.add(key)
        if key in failures:
            def mark_read(key=key):
                if reads is not None:
                    reads.add(key)

            return _ReadFailingFile(failures[key], on_read=mark_read)
        return real_open(file, *args, **kwargs)

    return fake_open


class FileReadFailureTest(unittest.TestCase):
    """候选 .txt/.md 文件打开或读取失败时被整体跳过，其余文件不受影响。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "a.txt").write_bytes(b"alpha\ntarget note\n")
        (self.root / "notes").mkdir()
        (self.root / "notes" / "b.md").write_bytes(b"target again\n")

    def _path(self, rel: str) -> str:
        return os.path.normpath(str(self.root / rel))

    def _run_with_failures(self, failures: dict, argv: list, calls: set | None = None):
        with mock.patch(
            "local_search.search.open", _raising_open(failures, calls), create=True
        ):
            return run_main(argv)

    def _snapshot_files(self) -> dict:
        return {
            str(p.relative_to(self.root)).replace(os.sep, "/"): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    def test_permission_error_on_one_file_skips_it_and_keeps_others(self) -> None:
        # notes/b.md 打开时抛出 PermissionError；a.txt 不受影响。
        failures = {
            self._path("notes/b.md"): PermissionError(errno.EACCES, "Permission denied"),
        }
        before = self._snapshot_files()

        code, out, err = self._run_with_failures(failures, [str(self.root), "target"])

        self.assertEqual(code, 0, "单文件读取失败不应改变退出码")
        results = json.loads(out.decode("utf-8"))
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 2, "snippet": "target note"}],
            "失败文件被整体跳过，a.txt 的第 2 行命中保持原样",
        )
        stderr = err.decode("utf-8")
        self.assertIn("notes/b.md", stderr, "告警必须报告正斜杠相对路径")
        self.assertIn("无法读取", stderr, "告警必须说明无法读取")
        self.assertIn("Permission denied", stderr, "告警必须包含失败原因")
        self.assertEqual(before, self._snapshot_files(), "查询必须只读源文件且不留下索引")

    def test_other_oserror_on_one_file_is_also_skipped(self) -> None:
        # 非 PermissionError 的 OSError（如 EIO）按同样规则跳过。
        failures = {
            self._path("notes/b.md"): OSError(errno.EIO, "I/O error"),
        }

        code, out, err = self._run_with_failures(failures, [str(self.root), "target"])

        self.assertEqual(code, 0)
        results = json.loads(out.decode("utf-8"))
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 2, "snippet": "target note"}],
        )
        stderr = err.decode("utf-8")
        self.assertIn("notes/b.md", stderr)
        self.assertIn("无法读取", stderr)
        self.assertIn("I/O error", stderr, "告警必须包含 OSError 的失败原因")

    def test_all_candidate_files_fail_returns_empty_array_and_warns_each(self) -> None:
        # 两个候选文件分别抛出不同原因的 OSError。
        failures = {
            self._path("a.txt"): PermissionError(errno.EACCES, "Permission denied"),
            self._path("notes/b.md"): OSError(errno.EIO, "I/O error"),
        }

        code, out, err = self._run_with_failures(failures, [str(self.root), "target"])

        self.assertEqual(code, 0, "全部文件读取失败时退出码仍为 0")
        self.assertEqual(
            json.loads(out.decode("utf-8")), [], "全部失败时结果仍为 []"
        )
        stderr = err.decode("utf-8")
        warning_lines = [line for line in stderr.splitlines() if line.strip()]
        self.assertEqual(len(warning_lines), 2, "每个失败文件各产生一条告警")
        a_line = next(line for line in warning_lines if "a.txt" in line)
        b_line = next(line for line in warning_lines if "notes/b.md" in line)
        self.assertIn("无法读取", a_line)
        self.assertIn("Permission denied", a_line)
        self.assertIn("无法读取", b_line)
        self.assertIn("I/O error", b_line)

    def test_excluded_failed_file_is_never_read_and_produces_no_warning(self) -> None:
        # --path-contains a.txt 排除 notes/b.md：它不应被打开，也不应有告警。
        read_calls: set = set()
        failures = {
            self._path("notes/b.md"): PermissionError(errno.EACCES, "Permission denied"),
        }

        code, out, err = self._run_with_failures(
            failures,
            [str(self.root), "target", "--path-contains", "a.txt"],
            calls=read_calls,
        )

        self.assertEqual(code, 0)
        results = json.loads(out.decode("utf-8"))
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 2, "snippet": "target note"}],
            "入选文件的结果与不注入失败时保持一致",
        )
        self.assertEqual(err, b"", "被排除的失败文件不得产生任何告警")
        self.assertNotIn(
            self._path("notes/b.md"), read_calls, "被路径筛选排除的文件不应被打开"
        )


class ContentReadFailureTest(unittest.TestCase):
    """候选文件打开成功、读取内容时抛出 OSError：该文件被整体跳过。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "a.txt").write_bytes(b"alpha\ntarget note\n")
        (self.root / "notes").mkdir()
        (self.root / "notes" / "b.md").write_bytes(b"target again\n")

    def _path(self, rel: str) -> str:
        return os.path.normpath(str(self.root / rel))

    def _run_with_read_failures(
        self, failures: dict, argv: list, opens: set | None = None, reads: set | None = None
    ):
        with mock.patch(
            "local_search.search.open",
            _read_raising_open(failures, opens, reads),
            create=True,
        ):
            return run_main(argv)

    def _snapshot_files(self) -> dict:
        return {
            str(p.relative_to(self.root)).replace(os.sep, "/"): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    def test_all_files_read_normally_baseline(self) -> None:
        # 不注入任何失败：两个候选文件各贡献一条命中，标准错误为空。
        before = self._snapshot_files()

        code, out, err = run_main([str(self.root), "target"])

        self.assertEqual(code, 0)
        results = json.loads(out.decode("utf-8"))
        self.assertEqual(
            results,
            [
                {"path": "a.txt", "line": 2, "snippet": "target note"},
                {"path": "notes/b.md", "line": 1, "snippet": "target again"},
            ],
            "全部读取正常时按路径序返回两个文件的命中",
        )
        self.assertEqual(err, b"", "全部读取正常时标准错误必须为空")
        self.assertEqual(before, self._snapshot_files(), "查询必须只读源文件且不留下索引")

    def test_read_failure_on_one_file_skips_it_and_keeps_others(self) -> None:
        # notes/b.md 打开成功、read() 时抛出 OSError(EIO)；a.txt 不受影响。
        opens: set = set()
        reads: set = set()
        failures = {
            self._path("notes/b.md"): OSError(errno.EIO, "I/O error"),
        }
        before = self._snapshot_files()

        code, out, err = self._run_with_read_failures(
            failures, [str(self.root), "target"], opens=opens, reads=reads
        )

        self.assertEqual(code, 0, "单文件读取失败不应改变退出码")
        results = json.loads(out.decode("utf-8"))
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 2, "snippet": "target note"}],
            "失败文件不贡献任何结果，a.txt 的第 2 行命中保持原样",
        )
        stderr = err.decode("utf-8")
        warning_lines = [line for line in stderr.splitlines() if line.strip()]
        self.assertEqual(len(warning_lines), 1, "标准错误必须恰有一条告警")
        warning = warning_lines[0]
        self.assertIn("notes/b.md", warning, "告警必须报告正斜杠相对路径")
        self.assertIn("无法读取", warning, "告警必须说明无法读取")
        self.assertIn("I/O error", warning, "告警必须包含失败原因")
        self.assertNotIn("Traceback", stderr, "不得向用户打印异常堆栈")
        # 失败发生在内容读取阶段：文件已被打开，随后 read() 才抛出异常。
        self.assertIn(self._path("notes/b.md"), opens, "失败文件应已被成功打开")
        self.assertIn(self._path("notes/b.md"), reads, "失败应发生在读取内容时")
        self.assertEqual(
            before,
            self._snapshot_files(),
            "查询前后源文件路径与字节内容必须一致，不留下索引或导出文件",
        )

    def test_read_failure_on_all_files_returns_empty_array_and_warns_each(self) -> None:
        # 两个候选文件都在读取内容时抛出 OSError(EIO)。
        failures = {
            self._path("a.txt"): OSError(errno.EIO, "I/O error"),
            self._path("notes/b.md"): OSError(errno.EIO, "I/O error"),
        }

        code, out, err = self._run_with_read_failures(failures, [str(self.root), "target"])

        self.assertEqual(code, 0, "全部文件读取失败时退出码仍为 0")
        self.assertEqual(out, b"[]\n", "全部失败时标准输出是空 JSON 数组")
        stderr = err.decode("utf-8")
        warning_lines = [line for line in stderr.splitlines() if line.strip()]
        self.assertEqual(len(warning_lines), 2, "每个失败文件各产生一条告警")
        # 告警顺序不作约束，按路径分别核对。
        a_line = next(line for line in warning_lines if "a.txt" in line)
        b_line = next(line for line in warning_lines if "notes/b.md" in line)
        for line in (a_line, b_line):
            self.assertIn("无法读取", line)
            self.assertIn("I/O error", line)
        self.assertNotIn("Traceback", stderr, "不得向用户打印异常堆栈")

    def test_excluded_file_is_never_opened_and_produces_no_warning(self) -> None:
        # --path-contains a.txt 在读取前排除 notes/b.md：它不应被打开，也不应有告警。
        opens: set = set()
        failures = {
            self._path("notes/b.md"): OSError(errno.EIO, "I/O error"),
        }

        code, out, err = self._run_with_read_failures(
            failures,
            [str(self.root), "target", "--path-contains", "a.txt"],
            opens=opens,
        )

        self.assertEqual(code, 0)
        results = json.loads(out.decode("utf-8"))
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 2, "snippet": "target note"}],
            "入选文件的结果与不注入失败时保持一致",
        )
        self.assertEqual(err, b"", "被排除的失败文件不得产生任何告警")
        self.assertNotIn(
            self._path("notes/b.md"), opens, "被路径筛选排除的文件不应被打开"
        )


class TraversalFailureTest(unittest.TestCase):
    """选定目录或其子目录枚举失败时，整个查询以退出码 2 终止。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        # 根目录下另有正常命中文件：遍历失败时它也不得出现在输出中。
        (self.root / "a.txt").write_bytes(b"alpha\ntarget note\n")
        (self.root / "notes").mkdir()
        (self.root / "notes" / "b.md").write_bytes(b"target again\n")

    def _path(self, rel: str = "") -> str:
        return os.path.normpath(str(self.root / rel) if rel else str(self.root))

    def _run_with_scandir_failures(self, failures: dict, argv: list):
        with mock.patch.object(os, "scandir", _raising_scandir(failures)):
            return run_main(argv)

    def _assert_traversal_abort(self, code: int, out: bytes, err: bytes, reason: str) -> None:
        self.assertEqual(code, 2, "目录遍历失败必须以退出码 2 终止")
        self.assertEqual(out, b"", "遍历失败时标准输出必须完全为空，不含部分 JSON")
        stderr = err.decode("utf-8")
        self.assertIn(str(self.root), stderr, "标准错误必须包含选定目录")
        self.assertIn("无法完成目录遍历", stderr)
        self.assertIn(reason, stderr, "标准错误必须包含失败原因")
        self.assertNotIn("Traceback", stderr, "不得向用户打印异常堆栈")

    def test_root_directory_enumeration_failure_aborts(self) -> None:
        failures = {
            self._path(): PermissionError(errno.EACCES, "Permission denied"),
        }

        code, out, err = self._run_with_scandir_failures(
            failures, [str(self.root), "target"]
        )

        self._assert_traversal_abort(code, out, err, "Permission denied")

    def test_subdirectory_enumeration_failure_aborts_despite_hits(self) -> None:
        # 子目录 notes 枚举失败；即使 a.txt 有正常命中也不得输出部分结果。
        failures = {
            self._path("notes"): OSError(errno.EACCES, "Permission denied"),
        }

        code, out, err = self._run_with_scandir_failures(
            failures, [str(self.root), "target"]
        )

        self._assert_traversal_abort(code, out, err, "Permission denied")

    def test_path_filter_cannot_turn_traversal_failure_into_empty_success(self) -> None:
        # --path-contains a.txt 会排除 notes/ 下所有文件，但遍历先于筛选，
        # 失败仍以退出码 2 终止，而不是变成成功的 []。
        failures = {
            self._path("notes"): PermissionError(errno.EACCES, "Permission denied"),
        }

        code, out, err = self._run_with_scandir_failures(
            failures, [str(self.root), "target", "--path-contains", "a.txt"]
        )

        self._assert_traversal_abort(code, out, err, "Permission denied")


if __name__ == "__main__":
    unittest.main()

"""所选目录末级是符号链接时拒绝检索的回归测试。

固定入口边界行为：``python -m local_search <目录> <关键词>`` 与公开入口
``local_search.main`` 接收相同参数时，若所选路径**末级本身**是符号链接，
无论目标是目录、普通文件还是已失效，都在目录枚举与文件读取之前拒绝：

- 返回码 2，标准输出完全为空（即使指定 ``--format csv`` 也不写表头），
  标准错误只有一行错误说明，包含用户传入路径的原始写法与
  “所选目录不能是符号链接”，不出现 Traceback；
- 相对路径、绝对路径与末尾带目录分隔符的同一个链接同样被拒绝；
- 路径筛选、``--offset``、``--limit`` 都不能把这一错误变成成功空结果；
- 非法参数仍优先按原有规则报错（如 ``--limit 0`` 报取值超范围），
  不因目标是链接而改变报错原因。

对照行为（不得被本次改动破坏）：

- 普通目录 ``real`` 检索 ``needle`` 返回唯一命中
  ``{"path": "a.txt", "line": 2, "snippet": "needle local"}``，退出码 0；
- 遍历普通目录时遇到符号链接仍静默跳过（不跟随、不告警）；
- 普通缺失路径与文件路径仍按既有规则报“目录不存在”/“路径不是目录”。

环境适配：若平台不支持 ``os.symlink`` 或当前权限拒绝创建符号链接，仅跳过
依赖链接的用例并给出实际原因；普通目录对照用例始终执行。所有资料由测试在
TemporaryDirectory 中独立准备并自动清理，仅使用标准库、离线运行；查询前后
核对源文件字节不变、链接不变、整树无新增文件（无索引）。期望值全部以字面量
直接写出，不调用任何被测函数生成期望结果。
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import local_search

KEYWORD = "needle"

# 验收资料：real/a.txt 两行依次为 intro、needle local（字面量固定）。
A_TXT_BYTES = b"intro\nneedle local\n"
EXPECTED_HITS = [{"path": "a.txt", "line": 2, "snippet": "needle local"}]

SYMLINK_MESSAGE = "所选目录不能是符号链接"


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


def run_module(args: list, cwd: Path = PROJECT_ROOT) -> subprocess.CompletedProcess:
    """以命令行入口 python -m local_search <参数...> 执行检索。

    cwd 可指定其他工作目录（用于相对路径用例）；无论 cwd 为何，都把项目根
    放入 PYTHONPATH，保证子进程能导入 local_search。
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "local_search", *args],
        cwd=cwd,
        capture_output=True,
        env=env,
    )


class SymlinkTargetDirTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp_root = Path(self._tmp.name)

        # 验收目录：real/a.txt 两行依次为 intro、needle local。
        self.real = tmp_root / "real"
        self.real.mkdir()
        (self.real / "a.txt").write_bytes(A_TXT_BYTES)

        # 指向 real 的目录链接与指向不存在目标的失效链接，按需创建。
        self.alias = tmp_root / "alias"
        self.dangling = tmp_root / "dangling"

    # -- 辅助 ------------------------------------------------------------

    def _create_symlink(self, target, link: Path) -> None:
        """创建符号链接；环境不支持或权限拒绝时跳过当前用例并说明实际原因。

        只把“无法创建链接”转化为跳过；其余准备错误照常抛出，报告为失败。
        """
        if not hasattr(os, "symlink"):
            self.skipTest("当前平台不提供 os.symlink，无法创建符号链接")
        try:
            os.symlink(str(target), str(link))
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"当前环境无法创建符号链接（{type(exc).__name__}: {exc}）")

    def _make_alias(self) -> None:
        self._create_symlink(self.real, self.alias)

    def _snapshot_tree(self) -> dict:
        """临时目录树的完整快照：相对路径 -> ("file", 字节) 或 ("link", 目标)。"""
        snapshot = {}
        for dirpath, dirnames, filenames in os.walk(self._tmp.name):
            for name in dirnames + filenames:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, self._tmp.name).replace(os.sep, "/")
                if os.path.islink(full):
                    snapshot[rel] = ("link", os.readlink(full))
                elif os.path.isfile(full):
                    with open(full, "rb") as handle:
                        snapshot[rel] = ("file", handle.read())
                else:
                    snapshot[rel] = ("dir", None)
        return snapshot

    def assert_sources_unchanged(self, before: dict, label: str) -> None:
        """查询前后整树快照一致：源文件字节不变、链接不变、无新增索引文件。"""
        after = self._snapshot_tree()
        self.assertEqual(
            before,
            after,
            f"{label}: 查询后目录树不得变化（源文件字节不变、不新增索引文件）",
        )
        self.assertEqual((self.real / "a.txt").read_bytes(), A_TXT_BYTES)

    def assert_symlink_rejected(self, code: int, stdout: bytes, stderr: bytes,
                                passed_path: str, label: str) -> None:
        """固定拒绝形态：返回码 2、stdout 全空、stderr 单行含原始路径与说明。"""
        self.assertEqual(code, 2, f"{label}: 返回码应为 2，实际 {code}，stderr={stderr!r}")
        self.assertEqual(stdout, b"", f"{label}: 标准输出应完全为空，实际 {stdout!r}")
        text = stderr.decode("utf-8")
        self.assertNotIn("Traceback", text, f"{label}: 不得出现异常堆栈: {text!r}")
        lines = text.splitlines()
        self.assertEqual(len(lines), 1, f"{label}: 标准错误应只有一行，实际 {text!r}")
        self.assertIn(passed_path, lines[0], f"{label}: 错误说明应保留用户原始写法")
        self.assertIn(SYMLINK_MESSAGE, lines[0], f"{label}: 错误说明应含固定文字")

    # -- 1. 普通目录对照（不依赖符号链接，始终执行） -----------------------

    def test_plain_dir_baseline(self) -> None:
        before = self._snapshot_tree()
        proc = run_module([str(self.real), KEYWORD])

        self.assertEqual(proc.returncode, 0, f"退出码应为 0，stderr={proc.stderr!r}")
        self.assertEqual(proc.stderr, b"", f"标准错误应为空，实际 {proc.stderr!r}")
        results = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            results,
            EXPECTED_HITS,
            "普通目录应返回唯一命中 a.txt 第 2 行 needle local",
        )
        self.assert_sources_unchanged(before, "普通目录对照")

    # -- 2. 末级是符号链接：两种公开入口一致拒绝 ---------------------------

    def test_alias_rejected_via_both_entries(self) -> None:
        self._make_alias()
        before = self._snapshot_tree()

        argv = [str(self.alias), KEYWORD]
        proc = run_module(argv)
        self.assert_symlink_rejected(
            proc.returncode, proc.stdout, proc.stderr, str(self.alias), "命令行入口"
        )

        code, stdout, stderr = run_main(argv)
        self.assert_symlink_rejected(code, stdout, stderr, str(self.alias), "公开入口 main")
        # 两种入口接收相同参数时，返回码与输出完全一致。
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (code, stdout, stderr))

        self.assert_sources_unchanged(before, "链接拒绝")

    def test_rejection_happens_before_any_read(self) -> None:
        # 链接目标里放入命中关键词的文件与非法 UTF-8 文件：拒绝发生在目录
        # 枚举与文件读取之前，二者都不产生结果，也不产生逐文件告警。
        self._make_alias()
        (self.real / "bad.txt").write_bytes(b"\xff")
        before = self._snapshot_tree()

        proc = run_module([str(self.alias), KEYWORD])
        self.assert_symlink_rejected(
            proc.returncode, proc.stdout, proc.stderr, str(self.alias), "读取前拒绝"
        )
        self.assertNotIn("警告", proc.stderr.decode("utf-8"), "不得产生逐文件告警")
        self.assert_sources_unchanged(before, "读取前拒绝")

    def test_dangling_link_rejected(self) -> None:
        self._create_symlink(self.real / "no_such_dir", self.dangling)
        before = self._snapshot_tree()

        argv = [str(self.dangling), KEYWORD]
        proc = run_module(argv)
        self.assert_symlink_rejected(
            proc.returncode, proc.stdout, proc.stderr, str(self.dangling), "失效链接"
        )
        code, stdout, stderr = run_main(argv)
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (code, stdout, stderr))
        self.assert_sources_unchanged(before, "失效链接")

    def test_trailing_separator_rejected_with_original_spelling(self) -> None:
        self._make_alias()
        before = self._snapshot_tree()

        passed = str(self.alias) + os.sep
        argv = [passed, KEYWORD]
        proc = run_module(argv)
        self.assert_symlink_rejected(
            proc.returncode, proc.stdout, proc.stderr, passed, "末尾分隔符"
        )
        code, stdout, stderr = run_main(argv)
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (code, stdout, stderr))
        self.assert_sources_unchanged(before, "末尾分隔符")

    def test_relative_path_rejected_with_original_spelling(self) -> None:
        self._make_alias()
        before = self._snapshot_tree()

        # 以临时目录为工作目录传入相对路径 "alias"，错误说明保留该写法。
        proc = run_module(["alias", KEYWORD], cwd=Path(self._tmp.name))
        self.assert_symlink_rejected(
            proc.returncode, proc.stdout, proc.stderr, "alias", "相对路径"
        )
        self.assert_sources_unchanged(before, "相对路径")

    def test_csv_format_writes_no_header_on_rejection(self) -> None:
        self._make_alias()
        argv = [str(self.alias), KEYWORD, "--format", "csv"]
        proc = run_module(argv)
        self.assert_symlink_rejected(
            proc.returncode, proc.stdout, proc.stderr, str(self.alias), "CSV 拒绝"
        )
        self.assertNotIn(b"path,line,snippet", proc.stdout, "失败时不得写 CSV 表头")

    def test_filters_offset_limit_cannot_rescue_rejection(self) -> None:
        self._make_alias()
        argv = [
            str(self.alias), KEYWORD,
            "--path-contains", "a.txt", "--offset", "1000", "--limit", "1",
        ]
        proc = run_module(argv)
        self.assert_symlink_rejected(
            proc.returncode, proc.stdout, proc.stderr, str(self.alias), "筛选/偏移/上限"
        )

    def test_invalid_arguments_keep_original_error(self) -> None:
        self._make_alias()
        # --limit 0 非法：仍按原有规则报取值超范围，而非符号链接错误。
        code, stdout, stderr = run_main([str(self.alias), KEYWORD, "--limit", "0"])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, b"")
        text = stderr.decode("utf-8")
        self.assertIn("--limit", text)
        self.assertNotIn(SYMLINK_MESSAGE, text)

        # 空白关键词同样优先按原规则报错。
        code, stdout, stderr = run_main([str(self.alias), "   "])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, b"")
        text = stderr.decode("utf-8")
        self.assertIn("关键词为空或全为空白", text)
        self.assertNotIn(SYMLINK_MESSAGE, text)

    # -- 3. 既有行为不被破坏 ----------------------------------------------

    def test_inner_symlink_still_skipped_during_traversal(self) -> None:
        # 普通目录内的链接仍静默跳过：别名不产生重复命中，退出码 0，标准错误为空。
        self._create_symlink(self.real / "a.txt", self.real / "alias.txt")
        before = self._snapshot_tree()

        proc = run_module([str(self.real), KEYWORD])
        self.assertEqual(proc.returncode, 0, f"退出码应为 0，stderr={proc.stderr!r}")
        self.assertEqual(proc.stderr, b"", f"标准错误应为空，实际 {proc.stderr!r}")
        results = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(results, EXPECTED_HITS, "目录内链接应被静默跳过，结果不变")
        self.assert_sources_unchanged(before, "遍历跳过链接")

    def test_missing_path_and_file_path_keep_original_errors(self) -> None:
        missing = str(Path(self._tmp.name) / "missing")
        code, stdout, stderr = run_main([missing, KEYWORD])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, b"")
        self.assertIn("目录不存在", stderr.decode("utf-8"))

        code, stdout, stderr = run_main([str(self.real / "a.txt"), KEYWORD])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, b"")
        self.assertIn("路径不是目录", stderr.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()

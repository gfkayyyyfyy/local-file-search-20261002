"""所选目录末级为符号链接时拒绝查询的回归测试。

固定入口边界行为：遍历中遇到符号链接一律静默跳过（既有行为，见
tests/test_symlink_not_followed.py），而**直接作为检索目录传入**的符号链接
同样在参数校验之后、目录枚举与文件读取之前被拒绝：

- 退出码 2，标准输出完全为空（即使指定 ``--format csv`` 也不写表头），
  标准错误只有一行错误说明，包含用户传入的路径（保留原始写法）与
  “所选目录不能是符号链接”，不出现 Traceback；
- 无论链接指向目录、普通文件还是已失效，均按同一规则拒绝；相对路径、
  绝对路径与末尾带目录分隔符的写法一视同仁；
- 路径筛选、``--offset``、``--limit`` 不能把这一错误变成成功的空结果；
- 非法参数仍优先按原有规则报错（如 ``--limit 0`` 报取值超范围），
  不因目标是链接而改变报错原因；
- 普通目录检索不受影响：真实目录照常返回命中，遍历中遇到的符号链接仍
  静默跳过。

两种公开入口等价：``python -m local_search``（子进程）与公开入口
``local_search.main``（进程内调用，返回值对应退出码，捕获
``sys.stdout``/``sys.stderr`` 的 ``.buffer`` 字节）接受相同参数时返回码
与输出一致。

验收场景：真实目录 ``real`` 内 ``a.txt`` 两行依次为 ``intro`` 与
``needle local``，``alias`` 是指向 ``real`` 的符号链接；
``python -m local_search alias needle`` 按上述规则失败，
``python -m local_search real needle`` 返回唯一命中（path 为 ``a.txt``、
line 为 2、snippet 为 ``needle local``），退出码 0。

环境适配：若平台不支持 ``os.symlink`` 或当前权限拒绝创建符号链接，仅跳过
依赖链接的用例并在报告中给出实际原因；普通目录对照用例始终执行。所有资料
由测试在 TemporaryDirectory 中独立准备并自动清理，仅使用标准库、离线运行；
查询前后核对源文件字节不变、链接不变且整树无新增文件（无索引）。期望值全部
以字面量直接写出，不调用任何被测函数生成期望结果。
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

# 真实目录中 a.txt 的字节内容（字面量固定，供写入与查询后比对）。
A_TXT_BYTES = b"intro\nneedle local\n"

# 真实目录的唯一期望命中：a.txt 第 2 行。
EXPECTED_HITS = [{"path": "a.txt", "line": 2, "snippet": "needle local"}]

REJECTION_PHRASE = "所选目录不能是符号链接"


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


def run_module(argv: list, cwd: Path = PROJECT_ROOT) -> subprocess.CompletedProcess:
    """以命令行入口 python -m local_search 执行检索，返回已完成进程。

    cwd 默认是项目根目录；用例指定其他工作目录（如相对路径场景）时，
    通过 PYTHONPATH 保证 local_search 包仍可导入。
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "local_search", *argv],
        cwd=cwd,
        capture_output=True,
        env=env,
    )


class SelectedDirSymlinkRejectedTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp_root = Path(self._tmp.name)

        # 真实目录：a.txt 两行依次为 intro、needle local。
        self.real = tmp_root / "real"
        self.real.mkdir()
        (self.real / "a.txt").write_bytes(A_TXT_BYTES)

        # 链接目标用的普通文件（目录外），以及各类链接（在用例中按需创建）。
        self.outside_file = tmp_root / "outside.txt"
        self.outside_file.write_bytes(b"needle outside\n")
        self.alias = tmp_root / "alias"  # 指向 real 的目录链接
        self.file_link = tmp_root / "file_link"  # 指向普通文件的链接
        self.dangling = tmp_root / "dangling"  # 失效链接

    # -- 辅助 ------------------------------------------------------------

    def _create_symlink(self, target, link: Path) -> None:
        """创建符号链接；环境不支持或权限拒绝时跳过当前用例并说明实际原因。

        只把“无法创建链接”转化为跳过；其余准备错误照常抛出，报告为失败。
        target 可以是路径或字符串（失效链接的目标不存在，按字符串传入）。
        """
        if not hasattr(os, "symlink"):
            self.skipTest("当前平台不提供 os.symlink，无法创建符号链接")
        try:
            os.symlink(str(target), str(link))
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"当前环境无法创建符号链接（{type(exc).__name__}: {exc}）")

    def _snapshot_tree(self) -> dict:
        """临时目录树的完整快照：相对路径 -> ("file", 字节) / ("link", 目标) / ("dir", None)。"""
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

    def assert_rejection(self, code: int, stdout: bytes, stderr: bytes,
                         passed_path: str, label: str) -> None:
        """固定拒绝形态：退出码 2、标准输出为空、标准错误一行且含原始路径。"""
        self.assertEqual(
            code, 2,
            f"{label}: 退出码应为 2，实际 {code}，stderr={stderr!r}",
        )
        self.assertEqual(stdout, b"", f"{label}: 标准输出应完全为空，实际 {stdout!r}")
        text = stderr.decode("utf-8")
        self.assertNotIn("Traceback", text, f"{label}: 标准错误不得出现 Traceback")
        lines = text.splitlines()
        self.assertEqual(len(lines), 1, f"{label}: 标准错误应只有一行，实际 {text!r}")
        self.assertIn(
            REJECTION_PHRASE, lines[0],
            f"{label}: 错误说明应包含“{REJECTION_PHRASE}”，实际 {lines[0]!r}",
        )
        self.assertIn(
            passed_path, lines[0],
            f"{label}: 错误说明应保留用户传入的路径 {passed_path!r}，实际 {lines[0]!r}",
        )

    def assert_sources_unchanged(self, before: dict, label: str) -> None:
        """查询前后整树快照一致：字节不变、链接不变、没有新增索引文件。"""
        after = self._snapshot_tree()
        self.assertEqual(
            before, after,
            f"{label}: 查询后目录树不得变化（源文件字节不变、不新增索引文件）",
        )
        self.assertEqual((self.real / "a.txt").read_bytes(), A_TXT_BYTES)
        self.assertEqual(self.outside_file.read_bytes(), b"needle outside\n")

    # -- 1. 普通目录对照（不依赖符号链接，始终执行） -----------------------

    def test_real_directory_returns_unique_hit(self) -> None:
        before = self._snapshot_tree()
        proc = run_module([str(self.real), KEYWORD])

        self.assertEqual(proc.returncode, 0, f"退出码应为 0，stderr={proc.stderr!r}")
        self.assertEqual(proc.stderr, b"", f"标准错误应为空，实际 {proc.stderr!r}")
        results = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            results, EXPECTED_HITS,
            "真实目录应返回唯一命中：a.txt 第 2 行 needle local",
        )
        self.assert_sources_unchanged(before, "真实目录对照")

    # -- 2. 两种公开入口对同一链接目录一致拒绝 -----------------------------

    def test_alias_rejected_by_module_entry(self) -> None:
        self._create_symlink(self.real, self.alias)
        before = self._snapshot_tree()
        proc = run_module([str(self.alias), KEYWORD])

        self.assert_rejection(
            proc.returncode, proc.stdout, proc.stderr, str(self.alias),
            "命令行入口拒绝目录链接",
        )
        self.assert_sources_unchanged(before, "命令行入口拒绝目录链接")

    def test_alias_rejected_by_main_entry(self) -> None:
        self._create_symlink(self.real, self.alias)
        before = self._snapshot_tree()
        code, stdout, stderr = run_main([str(self.alias), KEYWORD])

        self.assert_rejection(code, stdout, stderr, str(self.alias), "公开入口拒绝目录链接")
        self.assert_sources_unchanged(before, "公开入口拒绝目录链接")

    def test_both_entries_agree(self) -> None:
        """两种入口接收相同参数时，返回码与标准输出、标准错误完全一致。"""
        self._create_symlink(self.real, self.alias)
        argv = [str(self.alias), KEYWORD]

        proc = run_module(argv)
        code, stdout, stderr = run_main(argv)

        self.assertEqual(proc.returncode, code, "两种入口的返回码应一致")
        self.assertEqual(proc.stdout, stdout, "两种入口的标准输出应一致")
        self.assertEqual(proc.stderr, stderr, "两种入口的标准错误应一致")

    # -- 3. 链接目标形态与路径写法不影响拒绝 --------------------------------

    def test_link_to_regular_file_rejected(self) -> None:
        self._create_symlink(self.outside_file, self.file_link)
        code, stdout, stderr = run_main([str(self.file_link), KEYWORD])

        self.assert_rejection(
            code, stdout, stderr, str(self.file_link), "指向普通文件的链接",
        )

    def test_dangling_link_rejected(self) -> None:
        self._create_symlink(self.real / "no_such_dir", self.dangling)
        code, stdout, stderr = run_main([str(self.dangling), KEYWORD])

        self.assert_rejection(code, stdout, stderr, str(self.dangling), "失效链接")

    def test_trailing_separator_rejected_with_original_spelling(self) -> None:
        self._create_symlink(self.real, self.alias)
        passed = str(self.alias) + os.sep
        code, stdout, stderr = run_main([passed, KEYWORD])

        self.assert_rejection(code, stdout, stderr, passed, "末尾带分隔符的链接")

    def test_relative_path_rejected(self) -> None:
        self._create_symlink(self.real, self.alias)
        # 以临时目录为工作目录，传入相对路径 "alias"。
        proc = run_module(["alias", KEYWORD], cwd=Path(self._tmp.name))

        self.assert_rejection(
            proc.returncode, proc.stdout, proc.stderr, "alias", "相对路径链接",
        )

    # -- 4. 输出形态与选项不影响拒绝 ---------------------------------------

    def test_csv_format_writes_no_header_on_rejection(self) -> None:
        self._create_symlink(self.real, self.alias)
        code, stdout, stderr = run_main([str(self.alias), KEYWORD, "--format", "csv"])

        self.assert_rejection(code, stdout, stderr, str(self.alias), "CSV 格式拒绝")
        self.assertNotIn(b"path,line,snippet", stdout, "失败时不得写出 CSV 表头")

    def test_filters_offset_limit_cannot_turn_rejection_into_empty_success(self) -> None:
        self._create_symlink(self.real, self.alias)
        argv = [
            str(self.alias), KEYWORD,
            "--path-contains", "a.txt", "--offset", "5", "--limit", "1",
        ]
        code, stdout, stderr = run_main(argv)

        self.assert_rejection(code, stdout, stderr, str(self.alias), "筛选/偏移/上限")

    def test_invalid_arguments_take_priority_over_symlink(self) -> None:
        self._create_symlink(self.real, self.alias)
        code, stdout, stderr = run_main([str(self.alias), KEYWORD, "--limit", "0"])

        self.assertEqual(code, 2, "非法参数仍返回 2")
        self.assertEqual(stdout, b"", "非法参数时标准输出为空")
        text = stderr.decode("utf-8")
        self.assertIn("--limit", text, "应优先报告 --limit 取值错误")
        self.assertNotIn(
            REJECTION_PHRASE, text,
            "非法参数优先于符号链接检查，不得改报链接错误",
        )

    # -- 5. 遍历中遇到链接仍静默跳过（既有行为不被入口拒绝破坏） -------------

    def test_inner_symlink_still_skipped_during_traversal(self) -> None:
        # 真实目录内放指向目录外文件的链接：检索不受影响，链接静默跳过。
        self._create_symlink(self.outside_file, self.real / "inner_link.txt")
        before = self._snapshot_tree()
        proc = run_module([str(self.real), KEYWORD])

        self.assertEqual(proc.returncode, 0, f"退出码应为 0，stderr={proc.stderr!r}")
        self.assertEqual(proc.stderr, b"", "遍历中的链接应静默跳过，标准错误为空")
        results = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            results, EXPECTED_HITS,
            "目录内链接不产生结果，真实文件命中不变",
        )
        self.assert_sources_unchanged(before, "遍历跳过目录内链接")


if __name__ == "__main__":
    unittest.main()

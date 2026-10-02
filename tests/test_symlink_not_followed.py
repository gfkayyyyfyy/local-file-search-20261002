"""扫描不跟随符号链接的端到端回归测试。

通过命令入口 ``python -m local_search <目录> <关键词>`` 固定既有行为：遍历
所选目录时**不跟随任何符号链接**（无论指向文件、目录、目录外资料还是已失效），
符号链接本身不产生结果、不引发读取告警，循环目录链接不妨碍查询正常结束。

场景 A（普通文件对照 + 各类符号链接）：

- 所选目录是普通目录，内含 ``real.txt``（两行：``intro``、``needle local``）与
  ``notes/readme.MD``（唯一一行 ``needle nested``）；
- 另放入：指向 ``real.txt`` 的 ``alias.txt``、指向目录外资料的目录链接、
  指回所选根目录的循环目录链接、指向不存在文件的 ``dangling.md``；
- 目录外资料含 ``extra.md``（``needle outside``）与仅一个 0xff 字节的
  ``bad.txt``（若被读取会产生 UTF-8 解码告警，借此反证它未被读取）；
- 检索 ``needle`` 应只按顺序返回 ``notes/readme.MD`` 第 1 行与 ``real.txt``
  第 2 行：别名不产生重复结果，外部文件不进入结果，失效链接与 0xff 文件
  都不引发告警，退出码 0，标准错误为空，每项仍只有 path、line、snippet。

场景 B（所选目录内只有符号链接）：

- 所选目录只含指向 ``extra.md`` 的链接、指向其父目录的目录链接和失效链接；
- 查询输出空数组 ``[]``，仍以 0 退出且标准错误为空。

环境适配：若平台不支持 ``os.symlink`` 或当前权限拒绝创建符号链接（如 Windows
未授予 SeCreateSymbolicLinkPrivilege），仅跳过符号链接用例并在测试报告中给出
实际原因；普通文件对照用例不依赖符号链接，始终执行。除链接创建外的准备错误
以及任何结果不符一律报告失败，不作为跳过处理。

所有资料由测试在 TemporaryDirectory 中独立准备并自动清理，仅使用 Python
标准库、离线运行；查询前后核对普通源文件与外部目标文件的字节不变，且整个
临时目录树中不出现任何新增文件（无索引）。期望值全部以字面量直接写出，
不调用任何被测函数来生成期望结果。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

KEYWORD = "needle"

# 普通源文件与目录外资料的字节内容（字面量固定，供写入与查询后比对）。
REAL_TXT_BYTES = b"intro\nneedle local\n"
README_MD_BYTES = b"needle nested\n"
EXTRA_MD_BYTES = b"needle outside\n"
BAD_TXT_BYTES = b"\xff"

# 场景 A 的期望结果：按路径字典序，别名、外部文件、失效链接均不出现。
EXPECTED_HITS = [
    {"path": "notes/readme.MD", "line": 1, "snippet": "needle nested"},
    {"path": "real.txt", "line": 2, "snippet": "needle local"},
]


def run_search(directory: Path, keyword: str = KEYWORD) -> subprocess.CompletedProcess:
    """以命令行入口 python -m local_search <目录> <关键词> 执行检索。"""
    return subprocess.run(
        [sys.executable, "-m", "local_search", str(directory), keyword],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )


class SymlinkNotFollowedTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp_root = Path(self._tmp.name)

        # 目录外资料：一个含关键词的合法文件 + 一个仅含 0xff 的非法 UTF-8 文件。
        self.outside = tmp_root / "outside"
        self.outside.mkdir()
        (self.outside / "extra.md").write_bytes(EXTRA_MD_BYTES)
        (self.outside / "bad.txt").write_bytes(BAD_TXT_BYTES)

        # 所选目录本身是普通目录，只放普通文件作为对照。
        self.root = tmp_root / "selected"
        self.root.mkdir()
        (self.root / "real.txt").write_bytes(REAL_TXT_BYTES)
        (self.root / "notes").mkdir()
        (self.root / "notes" / "readme.MD").write_bytes(README_MD_BYTES)

    # -- 辅助 ------------------------------------------------------------

    def _create_symlink(self, target: Path, link: Path) -> None:
        """创建符号链接；环境不支持或权限拒绝时跳过当前用例并说明实际原因。

        只把“无法创建链接”转化为跳过；其余准备错误照常抛出，报告为失败。
        """
        if not hasattr(os, "symlink"):
            self.skipTest("当前平台不提供 os.symlink，无法创建符号链接")
        try:
            os.symlink(str(target), str(link))
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"当前环境无法创建符号链接（{type(exc).__name__}: {exc}）")

    def _snapshot_tree(self) -> dict:
        """临时目录树的完整快照：相对路径 -> ("file", 字节) 或 ("link", 目标)。

        os.walk 默认不跟随符号链接，因此循环目录链接不会导致无限递归。
        """
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

    def assert_clean_success(self, proc: subprocess.CompletedProcess, label: str) -> list:
        """退出码 0、标准错误为空、标准输出为合法 UTF-8 JSON，返回解析结果。"""
        self.assertEqual(
            proc.returncode,
            0,
            f"{label}: 退出码应为 0，实际 {proc.returncode}，stderr={proc.stderr!r}",
        )
        self.assertEqual(proc.stderr, b"", f"{label}: 标准错误应为空，实际 {proc.stderr!r}")
        # 严格按 UTF-8 解码：任何非法字节都会在此抛出并报告失败。
        text = proc.stdout.decode("utf-8")
        return json.loads(text)

    def assert_result_shape(self, results: list, label: str) -> None:
        for item in results:
            self.assertEqual(
                set(item.keys()),
                {"path", "line", "snippet"},
                f"{label}: 每项结果只能含 path、line、snippet 三个键",
            )
            self.assertIsInstance(item["path"], str)
            self.assertIsInstance(item["line"], int)
            self.assertIsInstance(item["snippet"], str)

    def assert_sources_unchanged(self, before: dict, label: str) -> None:
        """查询前后整树快照一致：字节不变、链接不变、没有新增索引文件。"""
        after = self._snapshot_tree()
        self.assertEqual(
            before,
            after,
            f"{label}: 查询后目录树不得变化（源文件字节不变、不新增索引文件）",
        )
        # 普通源文件与外部目标文件的字节逐一显式核对。
        self.assertEqual((self.root / "real.txt").read_bytes(), REAL_TXT_BYTES)
        self.assertEqual((self.root / "notes" / "readme.MD").read_bytes(), README_MD_BYTES)
        self.assertEqual((self.outside / "extra.md").read_bytes(), EXTRA_MD_BYTES)
        self.assertEqual((self.outside / "bad.txt").read_bytes(), BAD_TXT_BYTES)

    # -- 1. 普通文件对照（不依赖符号链接，始终执行） -----------------------

    def test_plain_files_baseline(self) -> None:
        before = self._snapshot_tree()
        proc = run_search(self.root)

        results = self.assert_clean_success(proc, "普通文件对照")
        self.assertEqual(
            results,
            EXPECTED_HITS,
            "普通文件对照：应只返回 notes/readme.MD 第 1 行与 real.txt 第 2 行",
        )
        self.assert_result_shape(results, "普通文件对照")
        self.assert_sources_unchanged(before, "普通文件对照")

    # -- 2. 场景 A：各类符号链接一律不跟随 --------------------------------

    def _prepare_scenario_a_links(self) -> None:
        self._create_symlink(self.root / "real.txt", self.root / "alias.txt")
        self._create_symlink(self.outside, self.root / "outside_link")
        self._create_symlink(self.root, self.root / "loop_link")
        self._create_symlink(self.root / "no_such_file.md", self.root / "dangling.md")

    def test_symlinks_are_not_followed(self) -> None:
        self._prepare_scenario_a_links()
        before = self._snapshot_tree()
        proc = run_search(self.root)

        results = self.assert_clean_success(proc, "符号链接场景")
        # 与纯普通文件对照完全相同的期望：别名不重复、外部文件不进入、
        # 失效链接与 0xff 外部文件均不产生告警（标准错误已断言为空）。
        self.assertEqual(
            results,
            EXPECTED_HITS,
            "符号链接场景：结果应与无链接时完全一致，仅两个普通文件的命中",
        )
        self.assert_result_shape(results, "符号链接场景")
        paths = [item["path"] for item in results]
        self.assertNotIn("alias.txt", paths, "指向 real.txt 的别名不得产生重复结果")
        self.assertNotIn("dangling.md", paths, "失效链接不得产生结果")
        self.assert_sources_unchanged(before, "符号链接场景")

    def test_loop_link_does_not_block_query(self) -> None:
        # 只放循环目录链接：查询必须正常结束，结果与对照一致。
        self._create_symlink(self.root, self.root / "loop_link")
        proc = run_search(self.root)

        results = self.assert_clean_success(proc, "循环目录链接")
        self.assertEqual(results, EXPECTED_HITS, "循环链接不得改变结果或阻碍查询结束")

    def test_dangling_link_produces_no_read_warning(self) -> None:
        # 只放失效链接：.md 后缀的失效链接若被读取必然告警，标准错误必须为空。
        self._create_symlink(self.root / "no_such_file.md", self.root / "dangling.md")
        proc = run_search(self.root)

        results = self.assert_clean_success(proc, "失效链接")
        self.assertEqual(results, EXPECTED_HITS, "失效链接不得改变普通文件的命中")

    # -- 3. 场景 B：所选目录内只有符号链接，输出空数组 ---------------------

    def test_only_links_returns_empty_array(self) -> None:
        link_only = Path(self._tmp.name) / "link_only"
        link_only.mkdir()
        self._create_symlink(self.outside / "extra.md", link_only / "extra_link.md")
        self._create_symlink(self.outside, link_only / "parent_link")
        self._create_symlink(link_only / "missing.txt", link_only / "dangling.md")

        before = self._snapshot_tree()
        proc = run_search(link_only)

        self.assertEqual(proc.returncode, 0, f"退出码应为 0，实际 {proc.returncode}")
        self.assertEqual(proc.stderr, b"", f"标准错误应为空，实际 {proc.stderr!r}")
        text = proc.stdout.decode("utf-8")
        self.assertEqual(json.loads(text), [], "只有符号链接的目录应输出空数组")
        self.assertEqual(text.strip(), "[]")
        self.assert_sources_unchanged(before, "仅链接目录")


if __name__ == "__main__":
    unittest.main()

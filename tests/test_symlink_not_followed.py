"""扫描不跟随符号链接的端到端回归测试。

通过现有命令行入口 ``python -m local_search <目录> needle`` 固定既有行为：
扫描所选**普通目录**内部遇到的符号链接（无论指向文件、指向目录外、指回根
目录形成循环，还是指向不存在的目标）一律不跟随、不读取；根目录本身始终是
普通目录。本模块不涉及 Windows 联接点或快捷方式。

主场景（所选目录内同时存在普通文件与各类链接）：

- 普通文件对照：``real.txt`` 两行为 ``intro``、``needle local``；
  ``notes/readme.MD`` 唯一一行为 ``needle nested``；
- ``alias.txt`` 是指向 ``real.txt`` 的文件符号链接，不得产生重复结果；
- 目录符号链接指向所选目录之外的资料目录；外部 ``extra.md`` 含
  ``needle outside``、``bad.txt`` 仅有一个 0xff 字节——两者既不进入结果，
  也不得因坏文件产生任何读取/解码告警（链接根本不被跟随）；
- 目录符号链接指回所选根目录自身形成循环，查询必须正常结束而非无限递归；
- ``dangling.md`` 指向不存在的文件，不得引发读取告警；
- 结果按路径码点序恰为 ``notes/readme.MD`` 第 1 行、``real.txt`` 第 2 行，
  每项只含 ``path``、``line``、``snippet``；退出码 0、标准错误为空。

独立场景：所选目录只含三个符号链接——指向外部 ``extra.md`` 的文件链接、
指向其父目录的目录链接、失效链接——查询输出 ``[]``，退出码 0、无告警。

跳过策略：仅当环境不支持创建符号链接（``NotImplementedError``）或当前权限
拒绝创建（``PermissionError`` 等 ``OSError``）时，才跳过**链接相关**用例，
跳过原因取实际异常类型与信息写入测试报告；普通文件对照用例位于不带任何
链接准备的独立测试类，任何环境下都执行。写普通文件、建目录等其他准备
错误以及结果不符一律按失败报告，绝不转成跳过。

资料在 TemporaryDirectory 中准备并自动清理，仅使用 Python 标准库、离线
执行；另核对查询前后普通源文件与外部目标文件字节不变、无新增索引文件。
期望值全部以字面量直接写出，不调用被测函数生成。
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


def run_search(directory: Path, keyword: str = KEYWORD) -> subprocess.CompletedProcess:
    """通过命令行入口 python -m local_search 调用，返回完成的子进程。"""
    return subprocess.run(
        [sys.executable, "-m", "local_search", str(directory), keyword],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )


def _probe_symlink_support() -> str | None:
    """探测当前环境能否创建文件与目录符号链接；不能时返回实际原因，可用返回 None。"""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target_file = base / "target.txt"
            target_file.write_bytes(b"probe\n")
            file_link = base / "file-link"
            dir_link = base / "dir-link"
            os.symlink(target_file, file_link)
            os.symlink(base, dir_link, target_is_directory=True)
            if not file_link.is_symlink() or not dir_link.is_symlink():
                return "符号链接创建后未被文件系统识别为符号链接"
    except (OSError, NotImplementedError) as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def snapshot_tree(root: Path) -> tuple[dict[str, bytes], dict[str, str]]:
    """不跟随任何符号链接地快照目录树。

    返回 (普通文件相对路径 -> 字节内容, 符号链接相对路径 -> 链接目标)；
    目录符号链接不会被深入。查询前后两次快照必须完全一致，即源文件字节
    不变、链接不变、也没有新增任何文件（包括索引文件）。
    """
    files: dict[str, bytes] = {}
    links: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # 目录符号链接由 os.walk 放在 dirnames（且 followlinks=False 时不会深入），
        # 文件符号链接放在 filenames；两类都按“符号链接条目”记录其链接目标。
        for name in list(dirnames) + filenames:
            full = Path(dirpath) / name
            rel = str(full.relative_to(root)).replace(os.sep, "/")
            if full.is_symlink():
                links[rel] = os.readlink(full)
            elif full.is_file():
                files[rel] = full.read_bytes()
    return files, links


class RegularFileControlTest(unittest.TestCase):
    """普通文件对照：不准备任何符号链接，因此在任何环境下都必须执行。

    即使链接用例因环境原因全部跳过，本类仍固定两个普通文件的命中行号、
    片段与路径排序这一既有行为。
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # 根目录本身是普通目录。
        self.root = Path(self._tmp.name) / "selected"
        self.root.mkdir()
        (self.root / "real.txt").write_bytes(b"intro\nneedle local\n")
        (self.root / "notes").mkdir()
        (self.root / "notes" / "readme.MD").write_bytes(b"needle nested\n")

    def test_plain_files_match_without_any_links(self) -> None:
        proc = run_search(self.root)

        self.assertEqual(proc.returncode, 0, f"普通文件对照退出码应为 0，stderr={proc.stderr!r}")
        self.assertEqual(proc.stderr, b"", "普通文件对照标准错误应为空")
        results = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            results,
            [
                {"path": "notes/readme.MD", "line": 1, "snippet": "needle nested"},
                {"path": "real.txt", "line": 2, "snippet": "needle local"},
            ],
            "普通文件对照：notes/readme.MD 第 1 行在前，real.txt 第 2 行在后",
        )


class SymlinkNotFollowedTest(unittest.TestCase):
    """所选普通目录内部的各类符号链接都不跟随的回归测试。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)

        # 目录外资料：与所选根平级，处于所选目录之外。
        self.outside = self.tmp_root / "outside-materials"
        self.outside.mkdir()
        (self.outside / "extra.md").write_bytes(b"needle outside\n")
        # 仅有一个 0xff 字节；若链接被错误跟随，读取它会产生解码告警。
        self.bad_file = self.outside / "bad.txt"
        self.bad_file.write_bytes(b"\xff")

        # 主场景所选目录：普通目录，内含普通文件与各类符号链接。
        self.root = self.tmp_root / "selected"
        self.root.mkdir()
        (self.root / "real.txt").write_bytes(b"intro\nneedle local\n")
        (self.root / "notes").mkdir()
        (self.root / "notes" / "readme.MD").write_bytes(b"needle nested\n")

        # 独立场景所选目录：普通空目录，之后只放符号链接。
        self.links_only_root = self.tmp_root / "links-only"
        self.links_only_root.mkdir()

        # 探测放在普通文件/目录准备之后：准备普通资料失败必须报失败而非跳过；
        # 只有“环境无法创建符号链接/权限拒绝”才跳过链接用例。
        reason = _probe_symlink_support()
        if reason is not None:
            self.skipTest(f"当前环境不支持创建符号链接，跳过链接用例：{reason}")

    def _make_link(
        self, source: Path, link: Path, *, label: str, target_is_directory: bool = False
    ) -> None:
        """创建单个符号链接；仅链接创建被拒/不受支持时跳过，其他错误照常失败。"""
        try:
            os.symlink(source, link, target_is_directory=target_is_directory)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(
                f"{label}：当前权限拒绝或环境无法创建符号链接，跳过该用例："
                f"{type(exc).__name__}: {exc}"
            )
        if not link.is_symlink():
            # 创建“成功”却不是符号链接属于环境异常，按失败处理而非跳过。
            raise AssertionError(f"{label}：创建结果不是符号链接：{link}")

    def _add_main_scenario_links(self) -> None:
        # 指向所选目录内普通文件的文件链接：跟随即会产生重复命中。
        self._make_link(
            self.root / "real.txt", self.root / "alias.txt", label="文件链接 alias.txt"
        )
        # 指向目录外资料目录的目录链接：跟随即会读到 extra.md 与坏文件 bad.txt。
        self._make_link(
            self.outside,
            self.root / "external",
            label="指向目录外资料的目录链接 external",
            target_is_directory=True,
        )
        # 指回所选根目录自身的循环目录链接：跟随即会无限递归。
        self._make_link(
            self.root,
            self.root / "loop",
            label="指回根目录的循环目录链接 loop",
            target_is_directory=True,
        )
        # 指向不存在文件的失效链接：跟随即会在读取时出错告警。
        self._make_link(
            self.outside / "no-such-file.md",
            self.root / "dangling.md",
            label="指向不存在文件的失效链接 dangling.md",
        )

    # -- 1. 主场景：普通文件命中，各类链接全部无效 ----------------------------

    def test_search_ignores_all_symlink_kinds(self) -> None:
        self._add_main_scenario_links()

        before_selected = snapshot_tree(self.root)
        before_outside = snapshot_tree(self.outside)

        proc = run_search(self.root)

        # 循环链接存在时查询必须正常返回（而非挂起或崩溃）：退出码 0。
        self.assertEqual(
            proc.returncode,
            0,
            f"含循环链接时查询仍应正常结束并退出 0，实际 {proc.returncode}，"
            f"stderr={proc.stderr!r}",
        )
        # 失效链接不引发读取告警；目录外坏文件不得产生解码告警；标准错误整体为空。
        self.assertEqual(
            proc.stderr,
            b"",
            f"符号链接既不跟随也不读取，标准错误必须为空，实际 {proc.stderr!r}",
        )

        # 严格按 UTF-8 解码标准输出并解析 JSON。
        results = json.loads(proc.stdout.decode("utf-8"))
        expected = [
            {"path": "notes/readme.MD", "line": 1, "snippet": "needle nested"},
            {"path": "real.txt", "line": 2, "snippet": "needle local"},
        ]
        self.assertEqual(
            results,
            expected,
            "应只按顺序返回 notes/readme.MD 第 1 行与 real.txt 第 2 行",
        )
        for item in results:
            self.assertEqual(
                set(item.keys()),
                {"path", "line", "snippet"},
                f"每项结果只能含 path、line、snippet，实际 {sorted(item.keys())}",
            )
            self.assertIsInstance(item["path"], str)
            self.assertIsInstance(item["line"], int)
            self.assertIsInstance(item["snippet"], str)

        paths = [item["path"] for item in results]
        # 别名不产生重复结果，外部资料、循环目录与失效链接都不进入结果。
        self.assertNotIn("alias.txt", paths, "文件符号链接不得产生重复结果")
        for unexpected in paths:
            self.assertFalse(
                unexpected.startswith(("external/", "loop/")),
                f"目录符号链接下的路径不得进入结果：{unexpected}",
            )
        joined = json.dumps(results, ensure_ascii=False)
        self.assertNotIn("needle outside", joined, "目录外文件内容不得进入结果")
        self.assertNotIn("dangling", joined, "失效链接不得进入结果")
        self.assertNotIn(b"bad.txt", proc.stderr, "目录外坏文件不得产生解码告警")
        self.assertNotIn(b"dangling", proc.stderr, "失效链接不得产生读取告警")

        # 只读核对：所选目录与目录外资料的普通文件字节、链接目标均不变，
        # 也没有任何新增文件（包括索引文件）。
        self.assertEqual(
            before_selected,
            snapshot_tree(self.root),
            "查询前后所选目录的普通文件与符号链接集合必须完全一致（无新增索引）",
        )
        self.assertEqual(
            before_outside,
            snapshot_tree(self.outside),
            "查询前后目录外目标文件的字节内容必须保持不变",
        )
        selected_files, selected_links = snapshot_tree(self.root)
        self.assertEqual(
            set(selected_files),
            {"real.txt", "notes/readme.MD"},
            "所选目录内只能有两个普通源文件，查询不得新增任何文件",
        )
        self.assertEqual(
            set(selected_links),
            {"alias.txt", "external", "loop", "dangling.md"},
            "链接集合查询前后应保持不变",
        )
        # 外部目标的字节内容按字面再核对一次。
        self.assertEqual((self.outside / "extra.md").read_bytes(), b"needle outside\n")
        self.assertEqual(self.bad_file.read_bytes(), b"\xff")

    # -- 2. 独立场景：所选目录只有符号链接，结果为空、退出 0、无告警 ----------

    def test_directory_containing_only_links_returns_empty_clean(self) -> None:
        # 指向目录外 extra.md 的文件链接、指向其父目录的目录链接、失效链接。
        self._make_link(
            self.outside / "extra.md",
            self.links_only_root / "extra-link.md",
            label="仅链接场景的文件链接 extra-link.md",
        )
        self._make_link(
            self.outside,
            self.links_only_root / "parent-dir",
            label="仅链接场景指向父目录的目录链接 parent-dir",
            target_is_directory=True,
        )
        self._make_link(
            self.outside / "missing.md",
            self.links_only_root / "dangling.md",
            label="仅链接场景的失效链接 dangling.md",
        )

        before_selected = snapshot_tree(self.links_only_root)
        before_outside = snapshot_tree(self.outside)

        proc = run_search(self.links_only_root)

        self.assertEqual(
            proc.returncode,
            0,
            f"仅含链接的目录查询退出码应为 0，实际 {proc.returncode}，"
            f"stderr={proc.stderr!r}",
        )
        self.assertEqual(
            proc.stderr,
            b"",
            f"链接不被跟随：extra.md 不读取、bad.txt 不解码、失效链接不告警，"
            f"标准错误必须为空，实际 {proc.stderr!r}",
        )
        # 严格 UTF-8 解码后必须是空数组，标准输出去除行尾换行后恰为 []。
        results = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(results, [], "所选目录只有符号链接时必须输出空数组")
        self.assertEqual(
            proc.stdout.decode("utf-8").strip(),
            "[]",
            "标准输出应恰为空数组 JSON",
        )

        self.assertEqual(
            before_selected,
            snapshot_tree(self.links_only_root),
            "查询前后仅含链接的目录条目必须一致，不得新增索引文件",
        )
        self.assertEqual(
            before_outside,
            snapshot_tree(self.outside),
            "查询前后目录外目标文件的字节内容必须保持不变",
        )
        selected_files, selected_links = snapshot_tree(self.links_only_root)
        self.assertEqual(selected_files, {}, "所选目录内不应存在任何普通文件")
        self.assertEqual(
            set(selected_links),
            {"extra-link.md", "parent-dir", "dangling.md"},
            "三个符号链接应原样保留",
        )
        self.assertEqual((self.outside / "extra.md").read_bytes(), b"needle outside\n")
        self.assertEqual(self.bad_file.read_bytes(), b"\xff")


if __name__ == "__main__":
    unittest.main()

"""--file-type 单格式筛选的端到端回归测试。

固定 ``--file-type`` 的对外可观察行为：

- 取值仅接受小写字面值 ``txt`` 或 ``md``，分别选择扩展名为 ``.txt`` 或
  ``.md`` 的普通文件，扩展名比较仍忽略大小写（``b.MD`` 计入 md）；
- 未传该选项时继续同时检索两种格式；
- 格式条件与 --path-contains / --path-excludes 共同生效，只有满足全部
  条件的文件才参与内容匹配；
- 被格式条件排除的文件不读取、不告警；被选中的不可读或非法 UTF-8 文件
  仍按既有规则告警后跳过，其余文件继续检索，退出码为 0；
- 缺值、重复指定或非法取值均在扫描前以退出码 2、空标准输出、单行中文
  标准错误退出；紧随该选项的参数即使看似开关也作为其值校验；
- 结果仍是仅含 path、line、snippet 的 JSON 数组，排序、首个命中、
  --all-lines、大小写及片段规则保持不变。

资料由测试在 TemporaryDirectory 中独立准备并自动清理，仅使用 Python
标准库、离线运行。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FILE_TYPE_OPTION = "--file-type"


def run_cli(*argv: str, cwd: Path = PROJECT_ROOT) -> subprocess.CompletedProcess:
    """以任意命令行参数调用 python -m local_search，返回完成的子进程。"""
    return subprocess.run(
        [sys.executable, "-m", "local_search", *argv],
        cwd=cwd,
        capture_output=True,
    )


def decode(proc: subprocess.CompletedProcess) -> tuple[str, str]:
    return proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


class FileTypeAcceptanceTest(unittest.TestCase):
    """复现验收目录：a.txt、notes/b.MD、other.log 各一行。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "sample"
        (self.root / "notes").mkdir(parents=True)
        (self.root / "a.txt").write_text("target root\n", encoding="utf-8")
        (self.root / "notes" / "b.MD").write_text("Target note\n", encoding="utf-8")
        (self.root / "other.log").write_text("target log\n", encoding="utf-8")

    def test_txt_type_returns_only_txt_hit(self) -> None:
        proc = run_cli(
            str(self.root),
            "TARGET",
            "--ignore-case",
            FILE_TYPE_OPTION,
            "txt",
            "--context-chars",
            "0",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            [{"path": "a.txt", "line": 1, "snippet": "target"}],
        )

    def test_md_type_returns_only_md_hit_ignoring_extension_case(self) -> None:
        proc = run_cli(
            str(self.root),
            "TARGET",
            "--ignore-case",
            FILE_TYPE_OPTION,
            "md",
            "--context-chars",
            "0",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            [{"path": "notes/b.MD", "line": 1, "snippet": "Target"}],
        )

    def test_no_matching_type_hit_outputs_empty_array(self) -> None:
        # 只有 .log 含小写 target；限定 txt 后区分大小写的 target 命中
        # a.txt，因此这里用 md 且不用 --ignore-case：TARGET 不命中 Target。
        proc = run_cli(str(self.root), "TARGET", FILE_TYPE_OPTION, "md")
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(stdout, "[]\n")

    def test_without_file_type_both_formats_still_searched(self) -> None:
        proc = run_cli(
            str(self.root), "TARGET", "--ignore-case", "--context-chars", "0"
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        # other.log 从不参与；a.txt 与 notes/b.MD 均命中并按路径排序。
        self.assertEqual(
            json.loads(stdout),
            [
                {"path": "a.txt", "line": 1, "snippet": "target"},
                {"path": "notes/b.MD", "line": 1, "snippet": "Target"},
            ],
        )

    def test_option_order_interchangeable(self) -> None:
        tails = (
            ("--ignore-case", "--context-chars", "0", FILE_TYPE_OPTION, "txt"),
            (FILE_TYPE_OPTION, "txt", "--ignore-case", "--context-chars", "0"),
            ("--context-chars", "0", FILE_TYPE_OPTION, "txt", "--ignore-case"),
        )
        observed = []
        for tail in tails:
            proc = run_cli(str(self.root), "TARGET", *tail)
            stdout, stderr = decode(proc)
            self.assertEqual(proc.returncode, 0, stderr)
            self.assertEqual(stderr, "")
            observed.append(stdout)
        self.assertEqual(len(set(observed)), 1)
        self.assertEqual(
            json.loads(observed[0]),
            [{"path": "a.txt", "line": 1, "snippet": "target"}],
        )

    def test_file_type_combines_with_path_filters(self) -> None:
        # 格式限定 txt 且路径须含 notes/：a.txt 路径不符，notes/b.MD
        # 格式不符，结果为空且不读取任何通过全部条件之外的文件。
        proc = run_cli(
            str(self.root),
            "TARGET",
            "--ignore-case",
            FILE_TYPE_OPTION,
            "txt",
            "--path-contains",
            "notes/",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(stdout, "[]\n")

        # md 与路径排除 notes/ 同用：b.MD 被路径条件排除，结果为空。
        proc = run_cli(
            str(self.root),
            "TARGET",
            "--ignore-case",
            FILE_TYPE_OPTION,
            "md",
            "--path-excludes",
            "notes/",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(stdout, "[]\n")

    def test_excluded_by_type_is_not_read_or_warned(self) -> None:
        # 在 txt 格式下放入一个非法 UTF-8 的 .md 文件：因格式条件先排除，
        # 该文件不被读取，标准错误必须为空。
        bad_md = self.root / "broken.MD"
        bad_md.write_bytes(b"bad \xff\xff bytes\n")
        proc = run_cli(
            str(self.root),
            "TARGET",
            "--ignore-case",
            FILE_TYPE_OPTION,
            "txt",
            "--context-chars",
            "0",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            [{"path": "a.txt", "line": 1, "snippet": "target"}],
        )

    def test_selected_unreadable_utf8_file_still_warned_but_exit_zero(self) -> None:
        # 被 txt 选中的非法 UTF-8 文件仍按既有规则告警跳过，退出码 0。
        broken = self.root / "broken.txt"
        broken.write_bytes(b"target here \xff\xff\n")
        proc = run_cli(
            str(self.root),
            "TARGET",
            "--ignore-case",
            FILE_TYPE_OPTION,
            "txt",
            "--context-chars",
            "0",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("警告: 跳过文件 broken.txt", stderr)
        self.assertEqual(
            json.loads(stdout),
            [{"path": "a.txt", "line": 1, "snippet": "target"}],
        )

    def test_keyword_equal_to_file_type_token_is_literal(self) -> None:
        hit_dir = Path(self._tmp.name) / "literal"
        hit_dir.mkdir()
        (hit_dir / "f.txt").write_text(
            f"x {FILE_TYPE_OPTION} y\n", encoding="utf-8"
        )
        proc = run_cli(str(hit_dir), FILE_TYPE_OPTION, FILE_TYPE_OPTION, "txt")
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertEqual(
            json.loads(stdout),
            [{"path": "f.txt", "line": 1, "snippet": f"x {FILE_TYPE_OPTION} y"}],
        )

    def test_invalid_missing_and_duplicated_file_type_fail_with_exit_2(self) -> None:
        cases = (
            (FILE_TYPE_OPTION,),
            (FILE_TYPE_OPTION, "txt", FILE_TYPE_OPTION, "md"),
            (FILE_TYPE_OPTION, "TXT"),
            (FILE_TYPE_OPTION, "Md"),
            (FILE_TYPE_OPTION, "txt "),
            (FILE_TYPE_OPTION, " md"),
            (FILE_TYPE_OPTION, ""),
            (FILE_TYPE_OPTION, "log"),
            (FILE_TYPE_OPTION, "markdown"),
        )
        for tail in cases:
            with self.subTest(tail=tail):
                proc = run_cli(str(self.root), "target", *tail)
                stdout, stderr = decode(proc)
                self.assertEqual(proc.returncode, 2, stderr)
                self.assertEqual(stdout, "")
                self.assertIn(FILE_TYPE_OPTION, stderr)
                self.assertNotIn("Traceback", stderr)

    def test_nonexistent_directory_still_fails_even_with_valid_file_type(self) -> None:
        proc = run_cli(
            str(self.root) + "_不存在",
            "target",
            FILE_TYPE_OPTION,
            "txt",
        )
        stdout, stderr = decode(proc)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(stdout, "")
        self.assertIn("目录不存在", stderr)


if __name__ == "__main__":
    unittest.main()

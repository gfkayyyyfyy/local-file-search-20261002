"""CR 换行与三种行结束符混用场景的端到端回归测试。

现有检索入口按 ``content.split("\\n")`` 分行再以 ``rstrip("\\r\\n")`` 去掉行尾，
本模块固定这一既有检索流程在不同行结束符下的行号与片段结果，通过命令入口
``python -m local_search`` 验证公开输出：

- 四处分隔符全部为 CR 的版本：CR 必须与 LF、CRLF 一样算作一次换行，空行仍占
  一个行号，末行没有结束符仍能命中；
- 四处分隔符依次为 CRLF、CR、CRLF、LF 的混用版本：三种行结束符按出现顺序各
  算一次换行，CRLF 整体只算一次，行号不因 CR 与 LF 相邻而虚增；
- 全用 LF 的版本作为对照，三种版本的结果必须逐项完全一致；
- 查询 ``TARGET`` 并启用 ``--ignore-case``、``--context-chars 1`` 时，默认模式
  只返回第 2 行一项，加入 ``--all-lines`` 后再返回第 5 行一项，数组按行号排列，
  同一行的重复命中（第 5 行出现两次 target）只产生一项；
- 片段不得带入 CR 或 LF，也不能借用相邻行；😀 按一个 Unicode 码点计入上下文；
- 仅含 ``tar`` 与 ``get`` 两行（CR 分隔）的文件在查询 ``target`` 时输出空 JSON
  数组，不允许跨 CR 拼行匹配；
- 正常查询均退出码 0、标准错误为空；查询前后源文件字节与目录内文件集合保持
  一致，不新增索引或其他文件。

所有资料由测试为每个换行版本在独立的 TemporaryDirectory 中准备 UTF-8 的
a.txt 并自动清理，仅使用 Python 标准库、离线运行，不依赖仓库演示文件。期望值
全部以字面量直接写出，不调用任何被测函数来生成期望结果。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

IGNORE_CASE = "--ignore-case"
ALL_LINES = "--all-lines"
CONTEXT_CHARS = "--context-chars"

# 五个逻辑行：header、😀Target甲、空行、other、Target target；末行无结束符。
LOGICAL_LINES = ("header", "😀Target甲", "", "other", "Target target")

FIRST_HIT = {"path": "a.txt", "line": 2, "snippet": "😀Target甲"}
SECOND_HIT = {"path": "a.txt", "line": 5, "snippet": "Target "}


def build_file_bytes(separators: tuple[str, ...]) -> bytes:
    """按给定的四个行结束符拼接五个逻辑行，末行不附加任何结束符。"""
    text = "".join(line + sep for line, sep in zip(LOGICAL_LINES, separators))
    text += LOGICAL_LINES[-1]
    return text.encode("utf-8")


# 三个换行版本：全 CR、CRLF/CR/CRLF/LF 混用、全 LF 对照。
EOL_VARIANTS = (
    ("全 CR", ("\r", "\r", "\r", "\r")),
    ("混用 CRLF/CR/CRLF/LF", ("\r\n", "\r", "\r\n", "\n")),
    ("全 LF（对照）", ("\n", "\n", "\n", "\n")),
)


def run_args(directory: Path, *argv: str) -> subprocess.CompletedProcess:
    """以任意命令行参数调用 python -m local_search，返回完成的子进程。"""
    return subprocess.run(
        [sys.executable, "-m", "local_search", str(directory), *argv],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )


class LineEndingVariantTest(unittest.TestCase):
    """每个换行版本在独立的临时目录中准备同一份逻辑内容。"""

    def _variant_dir(self, separators: tuple[str, ...]) -> tuple:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        root.joinpath("a.txt").write_bytes(build_file_bytes(separators))
        return tmp, root

    def _snapshot(self, root: Path) -> tuple:
        """返回 (相对路径集合, {相对路径: 文件字节})。"""
        files = {}
        for path in sorted(root.rglob("*")):
            if path.is_file():
                rel = str(path.relative_to(root)).replace(os.sep, "/")
                files[rel] = path.read_bytes()
        return set(files), files

    def assert_success_clean(self, proc: subprocess.CompletedProcess, label: str) -> list:
        """场景 label：退出码必须为 0、标准错误必须为空，返回解析后的 JSON。"""
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

    def assert_snippet_clean(self, item: dict, label: str) -> None:
        self.assertEqual(
            set(item.keys()),
            {"path", "line", "snippet"},
            f"{label}: 每项结果只能含 path、line、snippet 三个键",
        )
        self.assertNotIn("\r", item["snippet"], f"{label}: 片段不得带入 CR")
        self.assertNotIn("\n", item["snippet"], f"{label}: 片段不得带入 LF")

    # -- 1. 默认模式：三个换行版本均只返回第 2 行一项 ------------------------

    def test_default_mode_first_hit_only_for_all_variants(self) -> None:
        expected = [FIRST_HIT]
        for label, separators in EOL_VARIANTS:
            with self.subTest(换行版本=label):
                _tmp, root = self._variant_dir(separators)
                before = self._snapshot(root)

                proc = run_args(root, "TARGET", IGNORE_CASE, CONTEXT_CHARS, "1")
                results = self.assert_success_clean(proc, label)

                self.assertEqual(
                    results,
                    expected,
                    f"{label}: 默认模式只应返回第 2 行一项，实际 {results!r}",
                )
                self.assertEqual(
                    proc.stdout,
                    (json.dumps(expected, ensure_ascii=False) + "\n").encode("utf-8"),
                    f"{label}: 标准输出应与字面 JSON（含结尾换行）逐字节一致",
                )
                for item in results:
                    self.assert_snippet_clean(item, label)
                self.assertEqual(
                    self._snapshot(root),
                    before,
                    f"{label}: 查询后目录文件集合与源文件字节必须保持一致",
                )

    # -- 2. --all-lines：第 2 行与第 5 行各一项，按行号排列 ------------------

    def test_all_lines_returns_line_2_and_line_5_for_all_variants(self) -> None:
        expected = [FIRST_HIT, SECOND_HIT]
        for label, separators in EOL_VARIANTS:
            with self.subTest(换行版本=label):
                _tmp, root = self._variant_dir(separators)
                before = self._snapshot(root)

                proc = run_args(
                    root,
                    "TARGET",
                    IGNORE_CASE,
                    CONTEXT_CHARS,
                    "1",
                    ALL_LINES,
                )
                results = self.assert_success_clean(proc, label)

                self.assertEqual(
                    results,
                    expected,
                    f"{label}: --all-lines 应按行号返回第 2、5 行两项，实际 {results!r}",
                )
                self.assertEqual(
                    proc.stdout,
                    (json.dumps(expected, ensure_ascii=False) + "\n").encode("utf-8"),
                    f"{label}: 标准输出应与字面 JSON（含结尾换行）逐字节一致",
                )
                lines = [item["line"] for item in results]
                self.assertEqual(lines, sorted(lines), f"{label}: 结果数组必须按行号排列")
                for item in results:
                    self.assert_snippet_clean(item, label)
                # 第 5 行的 "Target target" 有两处大小写不敏感命中，只允许一项。
                self.assertEqual(
                    [item for item in results if item["line"] == 5],
                    [SECOND_HIT],
                    f"{label}: 第 5 行重复命中只应产生一项",
                )
                self.assertEqual(
                    self._snapshot(root),
                    before,
                    f"{label}: 查询后目录文件集合与源文件字节必须保持一致",
                )

    # -- 3. 行号语义的针对性固定：空行占号、CRLF 一次、末行无结束符 ----------

    def test_blank_line_counts_and_final_unterminated_line_matches(self) -> None:
        # 对三个版本逐行核对：第 3 行是空行、第 5 行（末行，无结束符）可命中，
        # 且第 1、3、4 行均不命中 TARGET。
        for label, separators in EOL_VARIANTS:
            with self.subTest(换行版本=label):
                _tmp, root = self._variant_dir(separators)

                # 查询一个只出现在末行的词，确认末行无结束符时行号仍为 5。
                tail = self.assert_success_clean(
                    run_args(root, "target", CONTEXT_CHARS, "0"), f"{label}：末行"
                )
                self.assertEqual(
                    tail,
                    [{"path": "a.txt", "line": 5, "snippet": "target"}],
                    f"{label}: 末行无结束符仍应在第 5 行命中最左侧小写 target",
                )

                # 查询空行不可能包含的词：只能命中第 2 行与第 5 行，
                # 间接固定空行占据第 3 行、other 为第 4 行。
                hits = self.assert_success_clean(
                    run_args(
                        root, "TARGET", IGNORE_CASE, CONTEXT_CHARS, "0", ALL_LINES
                    ),
                    label,
                )
                self.assertEqual(
                    [item["line"] for item in hits],
                    [2, 5],
                    f"{label}: 空行必须占一个行号，CRLF 只算一次换行",
                )
                self.assertEqual(
                    [item["snippet"] for item in hits],
                    ["Target", "Target"],
                    f"{label}: 上下文为 0 时片段只保留完整命中关键词",
                )

    # -- 4. 上下文码点语义：😀 按一个码点，片段不借邻行 ----------------------

    def test_emoji_counts_as_one_code_point(self) -> None:
        for label, separators in EOL_VARIANTS:
            with self.subTest(换行版本=label):
                _tmp, root = self._variant_dir(separators)
                results = self.assert_success_clean(
                    run_args(root, "TARGET", IGNORE_CASE, CONTEXT_CHARS, "1"), label
                )
                self.assertEqual(
                    results,
                    [FIRST_HIT],
                    f"{label}: 😀 应按一个 Unicode 码点计入上下文，片段为整行 😀Target甲",
                )
                self.assertEqual(
                    len(results[0]["snippet"]),
                    8,
                    f"{label}: 😀 按一个码点计入上下文，片段共 😀(1)+Target(6)+甲(1)=8 个码点",
                )

    # -- 5. 不跨行匹配：tar CR get 查询 target 必须为空 ----------------------

    def test_no_match_across_cr_separator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 仅含两行 tar、get，以单个 CR 分隔，末行无结束符。
            root.joinpath("a.txt").write_bytes(b"tar\rget")
            before = self._snapshot(root)

            proc = run_args(root, "target", CONTEXT_CHARS, "1")

            self.assertEqual(
                proc.returncode, 0, f"不跨行：退出码应为 0，stderr={proc.stderr!r}"
            )
            self.assertEqual(proc.stderr, b"", "不跨行：标准错误应为空")
            self.assertEqual(
                proc.stdout,
                b"[]\n",
                "target 跨越 CR 分处两行时不得拼行匹配，输出应为空 JSON 数组",
            )
            self.assertEqual(json.loads(proc.stdout.decode("utf-8")), [])
            self.assertEqual(
                self._snapshot(root),
                before,
                "不跨行：查询后目录文件集合与源文件字节必须保持一致",
            )


if __name__ == "__main__":
    unittest.main()

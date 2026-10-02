"""``--ignore-case`` 开关的端到端回归测试。

通过命令入口 ``python -m local_search <目录> <关键词> --ignore-case`` 固定从命令行
输入到 JSON 结果输出的行为：

- 不指定开关时完全保留既有区分大小写行为：大写 ``TARGET`` 不命中 ``Target``；
- 指定开关后仅把 ASCII 的 A-Z 与 a-z 视为同一字符，其他字符仍精确比较：
  ``É`` 不匹配 ``é``、``ß`` 不匹配 ``ss``；
- 关键词仍是连续的**字面子串**：首尾空格保留、不拆词、不解释正则/通配符、不跨行；
  第二个位置参数即使恰好写作 ``--ignore-case`` 也仍是关键词；
- 每个文件仍只返回按行号、行内位置确定的首个命中——较早的大小写不同命中不会
  让位于较晚的完全同大小写命中；``snippet`` 始终取自源文本并保留原始大小写，
  上下文仍为同一行前后各至多 30 个 Unicode 码点，无高亮、换行或省略号；
- 开关可与 ``--path-contains`` 在位置参数之后以任意顺序同用；路径筛选仍区分
  大小写；片段取值即使写作 ``--ignore-case`` 也仍是字面值；被排除文件不读取、
  不告警；
- 重复开关、无法识别的多余参数（含把开关写在关键词之前）、关键词为空或全为空白、
  目录不存在或不是目录时退出码 2、标准输出为空、原因写入标准错误；
- 含非法 UTF-8 字节的候选文件仍被跳过并向标准错误告警，其余结果正常输出且退出 0；
  无命中输出 ``[]``；每项结果只含 ``path``、``line``、``snippet``。

所有资料由测试在 TemporaryDirectory 中独立准备并自动清理，仅使用 Python 标准库、
离线运行。期望值全部以字面量直接写出，不调用任何被测函数来生成期望结果。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

IGNORE_CASE = "--ignore-case"
PATH_OPTION = "--path-contains"


def run_args(directory: Path, *argv: str) -> subprocess.CompletedProcess:
    """以任意命令行参数调用 python -m local_search，返回完成的子进程。"""
    return subprocess.run(
        [sys.executable, "-m", "local_search", str(directory), *argv],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )


class IgnoreCaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _write(self, rel: str, data: bytes) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def _write_text(self, rel: str, text: str) -> None:
        self._write(rel, text.encode("utf-8"))

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

    def assert_item_shape(self, item: dict, label: str) -> None:
        self.assertEqual(
            set(item.keys()),
            {"path", "line", "snippet"},
            f"{label}: 每项结果只能含 path、line、snippet 三个键",
        )
        self.assertIsInstance(item["path"], str)
        self.assertIsInstance(item["line"], int)
        self.assertIsInstance(item["snippet"], str)
        self.assertNotIn("\n", item["snippet"], f"{label}: 片段不得包含换行符")
        self.assertNotIn("\r", item["snippet"], f"{label}: 片段不得包含回车符")

    def assert_argument_error(self, proc: subprocess.CompletedProcess, reason: str, label: str) -> None:
        """输入边界错误：退出码 2、标准输出为空、标准错误说明对应原因。"""
        self.assertEqual(proc.returncode, 2, f"{label}: 退出码应为 2，实际 {proc.returncode}")
        self.assertEqual(proc.stdout, b"", f"{label}: 出错时标准输出必须为空")
        stderr = proc.stderr.decode("utf-8")
        self.assertIn(reason, stderr, f"{label}: 标准错误应说明原因 {reason!r}，实际 {stderr!r}")

    def _snapshot_files(self) -> dict[str, bytes]:
        return {
            str(p.relative_to(self.root)).replace(os.sep, "/"): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    # -- 1. 未指定开关：保留区分大小写的既有行为 --------------------------

    def test_without_flag_matching_remains_case_sensitive(self) -> None:
        self._write_text("a.txt", "header\nTarget note\n")
        self._write_text("notes/b.md", "target again\n")

        miss = self.assert_success_clean(run_args(self.root, "TARGET"), "大写无开关")
        self.assertEqual(miss, [], "不指定开关时 TARGET 不得命中 Target/target")
        hit = self.assert_success_clean(run_args(self.root, "target"), "小写无开关")
        self.assertEqual(
            hit,
            [{"path": "notes/b.md", "line": 1, "snippet": "target again"}],
            "不指定开关时小写 target 只命中真正小写的 notes/b.md",
        )

    # -- 2. 指定开关：ASCII 大小写不同的命中都返回，片段保留原始大小写 -----

    def test_flag_matches_ascii_case_variants_with_original_case_snippet(self) -> None:
        self._write_text("a.txt", "header\nTarget note\n")
        self._write_text("notes/b.md", "target again\n")

        results = self.assert_success_clean(
            run_args(self.root, "TARGET", IGNORE_CASE), "TARGET + 开关"
        )
        self.assertEqual(
            results,
            [
                {"path": "a.txt", "line": 2, "snippet": "Target note"},
                {"path": "notes/b.md", "line": 1, "snippet": "target again"},
            ],
            "开关场景：应依次返回 a.txt 第 2 行 Target note 与 notes/b.md 第 1 行 target again",
        )
        for item in results:
            self.assert_item_shape(item, "开关场景")
        # 片段必须是源文本的原始大小写，而不是关键词或折叠后的大小写。
        self.assertEqual(results[0]["snippet"], "Target note")
        self.assertEqual(results[1]["snippet"], "target again")

    def test_no_hit_with_flag_outputs_empty_array_clean(self) -> None:
        self._write_text("a.txt", "alpha\nbeta\n")
        proc = run_args(self.root, "TARGET", IGNORE_CASE)
        results = self.assert_success_clean(proc, "开关无命中")
        self.assertEqual(results, [])
        self.assertEqual(proc.stdout.decode("utf-8").strip(), "[]")

    # -- 3. 首个命中按行号、行内位置决定：较早的异大小写命中优先 -----------

    def test_earlier_different_case_hit_beats_later_exact_case_hit(self) -> None:
        # 同一行：第 0 列起是异大小写的 tArget，35 个 m 之后才是完全同大小写的
        # TARGET。折叠后两者都匹配关键词 TARGET，但首个命中在 tArget。
        self._write_text("a.txt", "tArget" + "m" * 35 + "TARGET\n")

        results = self.assert_success_clean(
            run_args(self.root, "TARGET", IGNORE_CASE), "行内首命中"
        )
        self.assertEqual(
            results,
            [{"path": "a.txt", "line": 1, "snippet": "tArget" + "m" * 30}],
            "较晚的完全同大小写 TARGET 不得取代较早的 tArget；片段保留 tArget 原样",
        )

    def test_earlier_line_different_case_beats_later_line_exact_case(self) -> None:
        # 第 1 行异大小写 targetish，第 2 行完全同大小写 TARGET：应取第 1 行。
        self._write_text("b.txt", "targetish\nTARGET\n")

        results = self.assert_success_clean(
            run_args(self.root, "TARGET", IGNORE_CASE), "跨行首命中"
        )
        self.assertEqual(
            results,
            [{"path": "b.txt", "line": 1, "snippet": "targetish"}],
            "第 1 行较早的异大小写命中必须优先于第 2 行的完全同大小写命中",
        )

    # -- 4. 仅折叠 ASCII：É/é 不折叠，ß/ss 不折叠 --------------------------

    def test_non_ascii_letters_are_not_folded(self) -> None:
        # 同一行先出现 cafÉ（大写 U+00C9），后出现 café（小写 U+00E9）。
        self._write_text("c.txt", "cafÉ ... café\n")

        # 关键词 CAFÉ：C/A/F 按 ASCII 折叠，É 必须精确等于大写 É → 命中首个 cafÉ。
        upper = self.assert_success_clean(
            run_args(self.root, "CAFÉ", IGNORE_CASE), "CAFÉ + 开关"
        )
        self.assertEqual(
            upper,
            [{"path": "c.txt", "line": 1, "snippet": "cafÉ ... café"}],
            "É 只能精确匹配 É，不得匹配 é；片段保留源文本",
        )
        # 关键词 café（小写 é）：必须落到后一个 café，而不是开头的 cafÉ。
        lower = self.assert_success_clean(
            run_args(self.root, "café", IGNORE_CASE), "café + 开关"
        )
        self.assertEqual(
            lower,
            [{"path": "c.txt", "line": 1, "snippet": "cafÉ ... café"}],
            "é 只能精确匹配 é；命中位置应为行内后一个 café",
        )
        self.assertEqual(lower[0]["snippet"].find("café"), len("cafÉ ... "))

    def test_sharp_s_is_not_expanded_to_ss(self) -> None:
        self._write_text("de.txt", "die straße\n")
        self._write_text("ascii.txt", "die strasse\n")

        # ß 不匹配 ss：STRASSE 在忽略 ASCII 大小写时只命中纯 ASCII 的 strasse。
        results = self.assert_success_clean(
            run_args(self.root, "STRASSE", IGNORE_CASE), "STRASSE + 开关"
        )
        self.assertEqual(
            results,
            [{"path": "ascii.txt", "line": 1, "snippet": "die strasse"}],
            "ß 不得等价于 ss：straße 不得被 STRASSE 命中",
        )
        # 反向同样不成立：小写关键词 ss 也不得命中带 ß 的文件。
        ss_only = self.assert_success_clean(
            run_args(self.root, "ss", IGNORE_CASE), "ss + 开关"
        )
        self.assertEqual([item["path"] for item in ss_only], ["ascii.txt"])
        # 含 ß 的关键词与含 ß 的源文仍按精确码点比较照常命中（开头的 S 折叠为 s）。
        sharp = self.assert_success_clean(
            run_args(self.root, "Straße", IGNORE_CASE), "Straße + 开关"
        )
        self.assertEqual(
            sharp,
            [{"path": "de.txt", "line": 1, "snippet": "die straße"}],
            "ß 与 ß 自身仍精确相等",
        )

    # -- 5. 关键词仍是字面子串：首尾空格、元字符、跨行 --------------------

    def test_spaces_in_keyword_remain_literal(self) -> None:
        self._write_text("with.txt", "a target b\n")
        self._write_text("nospace.txt", "a TARGETb\n")

        results = self.assert_success_clean(
            run_args(self.root, " target ", IGNORE_CASE), "带首尾空格关键词"
        )
        self.assertEqual(
            results,
            [{"path": "with.txt", "line": 1, "snippet": "a target b"}],
            "关键词首尾空格必须按字面保留：仅 'a target b' 含 ' target '",
        )

    def test_regex_and_glob_metacharacters_remain_literal(self) -> None:
        self._write_text("lit.txt", "a.b*c? section\n")

        self.assertEqual(
            self.assert_success_clean(run_args(self.root, "A.B*C?", IGNORE_CASE), "元字符命中"),
            [{"path": "lit.txt", "line": 1, "snippet": "a.b*c? section"}],
        )
        # 正则语义下 "a.b" 会命中 "a.b" 也会命中 "axb"；字面语义下 "axb" 不命中。
        self._write_text("nometa.txt", "axb section\n")
        self.assertEqual(
            self.assert_success_clean(run_args(self.root, "A.B", IGNORE_CASE), "元字符不解释"),
            [{"path": "lit.txt", "line": 1, "snippet": "a.b*c? section"}],
        )
        # 通配符 * 不做展开：没有任何行真正包含星号组合 "x*y"。
        self._write_text("star.txt", "xxxy\n")
        self.assertEqual(
            self.assert_success_clean(run_args(self.root, "X*Y", IGNORE_CASE), "星号字面"),
            [],
        )

    def test_keyword_does_not_match_across_lines(self) -> None:
        self._write("split.txt", "TAR\nget\n".encode("utf-8"))
        results = self.assert_success_clean(
            run_args(self.root, "TARGET", IGNORE_CASE), "跨行不命中"
        )
        self.assertEqual(results, [], "开关不得改变单行匹配边界：跨行关键词必须返回 []")

    # -- 6. 第二个位置参数恰好是 --ignore-case：它仍是关键词 ---------------

    def test_second_positional_equal_to_flag_name_is_the_keyword(self) -> None:
        self._write_text("cfg.txt", "flag --ignore-case was set here\n")

        # 只有两个参数：目录 + 字面关键词 "--ignore-case"，开关并未启用。
        exact = self.assert_success_clean(
            run_args(self.root, IGNORE_CASE), "关键词恰为开关名（区分大小写）"
        )
        self.assertEqual(
            exact,
            [{"path": "cfg.txt", "line": 1, "snippet": "flag --ignore-case was set here"}],
            "第二个位置参数即使写作 --ignore-case 也必须按字面关键词处理",
        )

        # 再来一个真正的开关：位置参数仍是关键词，第三个同名 token 才是开关，
        # 因此大写 --IGNORE-CASE 文本也能命中，片段保留原始大小写。
        self._write_text("upper.txt", "value --IGNORE-CASE end\n")
        with_flag = self.assert_success_clean(
            run_args(self.root, IGNORE_CASE, IGNORE_CASE), "关键词+同名开关"
        )
        paths = {item["path"]: item for item in with_flag}
        self.assertEqual(
            paths["cfg.txt"]["snippet"],
            "flag --ignore-case was set here",
        )
        self.assertEqual(
            paths["upper.txt"]["snippet"],
            "value --IGNORE-CASE end",
            "位置参数与开关同名时：前者是关键词，后者才启用大小写折叠",
        )

    # -- 7. 与 --path-contains 同用：顺序不限、路径筛选仍区分大小写 --------

    def test_flag_and_path_filter_combine_in_either_order(self) -> None:
        self._write_text("a.txt", "Target root\n")
        self._write_text("notes/b.MD", "alpha\ntarget note\n")
        self._write_text("Notes/c.txt", "TARGET upper\n")

        expected_lower = [
            {"path": "notes/b.MD", "line": 2, "snippet": "target note"},
        ]
        for argv in (
            ("TARGET", IGNORE_CASE, PATH_OPTION, "notes/"),
            ("TARGET", PATH_OPTION, "notes/", IGNORE_CASE),
        ):
            with self.subTest(argv=argv):
                results = self.assert_success_clean(run_args(self.root, *argv), "两种选项顺序")
                self.assertEqual(results, expected_lower)
                self.assert_item_shape(results[0], "组合筛选")

        # 路径筛选仍区分大小写：片段 Notes/ 只选大写目录，不受开关影响。
        upper = self.assert_success_clean(
            run_args(self.root, "TARGET", IGNORE_CASE, PATH_OPTION, "Notes/"),
            "大写路径片段",
        )
        self.assertEqual(
            upper,
            [{"path": "Notes/c.txt", "line": 1, "snippet": "TARGET upper"}],
        )
        neither = self.assert_success_clean(
            run_args(self.root, "TARGET", IGNORE_CASE, PATH_OPTION, "NOTES/"),
            "全大写路径片段",
        )
        self.assertEqual(neither, [])

    def test_path_filter_value_equal_to_flag_name_is_a_literal_fragment(self) -> None:
        # --path-contains 紧随的值即使是 --ignore-case 也按字面片段消费：
        # 相对路径中包含该子串的文件才会被选中（这里文件名本身含该子串）。
        self._write_text("--ignore-case.md", "target in oddly named file\n")
        self._write_text("plain.md", "target plain\n")

        # 注意：此时 --ignore-case 已被当作片段取值消费，开关并未启用；
        # 用小写关键词即可证明文件确实被片段选中（plain.md 被排除）。
        results = self.assert_success_clean(
            run_args(self.root, "target", PATH_OPTION, IGNORE_CASE), "片段恰为开关名"
        )
        self.assertEqual(
            results,
            [{"path": "--ignore-case.md", "line": 1, "snippet": "target in oddly named file"}],
            "--path-contains 的取值 --ignore-case 必须是字面片段而非开关",
        )
        # 反证：该片段选不中普通文件名时输出 []，退出 0。
        self.assertEqual(
            self.assert_success_clean(
                run_args(self.root, "target", PATH_OPTION, "plain"), "片段选普通文件"
            ),
            [{"path": "plain.md", "line": 1, "snippet": "target plain"}],
        )

    def test_excluded_broken_file_is_not_read_or_warned(self) -> None:
        self._write("notes/broken.txt", b"TARGET broken\n\xff\n")
        self._write_text("notes/ok.txt", "target ok\n")
        proc = run_args(self.root, "TARGET", IGNORE_CASE, PATH_OPTION, "notes/ok")
        results = self.assert_success_clean(proc, "排除坏文件")
        self.assertEqual(
            results,
            [{"path": "notes/ok.txt", "line": 1, "snippet": "target ok"}],
        )
        self.assertEqual(proc.stderr, b"", "被路径筛选排除的文件不得产生告警")

    def test_selected_broken_file_warns_but_other_results_still_print(self) -> None:
        self._write("broken.txt", b"TARGET broken\n\xff\n")
        self._write_text("ok.txt", "target ok\n")
        proc = run_args(self.root, "TARGET", IGNORE_CASE)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(
            json.loads(proc.stdout.decode("utf-8")),
            [{"path": "ok.txt", "line": 1, "snippet": "target ok"}],
        )
        stderr = proc.stderr.decode("utf-8")
        self.assertIn("broken.txt", stderr)
        self.assertIn("无法按 UTF-8 解码", stderr)

    # -- 8. 片段截取在开关启用时仍按码点、保留原始大小写 -------------------

    def test_snippet_context_still_30_code_points_with_original_case(self) -> None:
        # 31 个“中” + 异大小写 TaRgEt + 31 个 😀；前后各超出 1 个码点。
        content = "中" * 31 + "TaRgEt" + "😀" * 31
        self._write_text("u.md", content + "\n")

        results = self.assert_success_clean(
            run_args(self.root, "target", IGNORE_CASE), "码点片段"
        )
        self.assertEqual(
            results,
            [{"path": "u.md", "line": 1, "snippet": "中" * 30 + "TaRgEt" + "😀" * 30}],
            "片段按码点截到前后各 30 个，且完整命中保留 TaRgEt 原始大小写",
        )
        self.assertEqual(len(results[0]["snippet"]), 66)

    # -- 9. 输入边界：重复开关、多余参数、空白关键词、目录错误 → 退出 2 ----

    def test_duplicate_flag_is_argument_error(self) -> None:
        proc = run_args(self.root, "target", IGNORE_CASE, IGNORE_CASE)
        self.assert_argument_error(proc, "只能指定一次", "重复开关")
        self.assertIn(IGNORE_CASE, proc.stderr.decode("utf-8"))

    def test_flag_before_keyword_is_an_unrecognized_argument(self) -> None:
        # 选项只允许出现在两个位置参数之后：此处 TARGET 落在尾随位置，必须报错。
        proc = run_args(self.root, IGNORE_CASE, "TARGET")
        self.assert_argument_error(proc, "无法识别的参数", "开关在关键词之前")
        self.assertIn("TARGET", proc.stderr.decode("utf-8"))

    def test_extra_arguments_are_argument_errors(self) -> None:
        cases = [
            (("target", IGNORE_CASE, "trailing"), "trailing"),
            (("target", "--bogus"), "--bogus"),
            (("target", IGNORE_CASE, PATH_OPTION, "notes/", "extra"), "extra"),
        ]
        for argv, token in cases:
            with self.subTest(argv=argv):
                proc = run_args(self.root, *argv)
                self.assert_argument_error(proc, "无法识别的参数", f"多余参数 {token!r}")
                self.assertIn(token, proc.stderr.decode("utf-8"))

    def test_blank_keyword_with_flag_is_argument_error(self) -> None:
        for keyword in ("", "   ", "\t", " \t "):
            with self.subTest(keyword=keyword):
                proc = run_args(self.root, keyword, IGNORE_CASE)
                self.assert_argument_error(proc, "关键词为空或全为空白", f"空白关键词 {keyword!r}")

    def test_directory_errors_with_flag_exit_two_with_empty_stdout(self) -> None:
        missing = Path(self._tmp.name) / "不存在"
        proc = run_args(missing, "target", IGNORE_CASE)
        self.assert_argument_error(proc, "目录不存在", "目录不存在")

        file_path = Path(self._tmp.name) / "a.txt"
        file_path.write_text("target\n", encoding="utf-8")
        proc = run_args(file_path, "target", IGNORE_CASE)
        self.assert_argument_error(proc, "路径不是目录", "路径不是目录")

    # -- 10. 查询只读源文件且不留下索引 -----------------------------------

    def test_flagged_query_is_read_only_and_leaves_no_index(self) -> None:
        self._write_text("keep.txt", "before\nTARGET in middle\nafter\n")
        before = self._snapshot_files()
        proc = run_args(self.root, "target", IGNORE_CASE)
        after = self._snapshot_files()
        self.assertEqual(before, after, "查询后源文件字节不得变化，且不得新增任何文件/索引")
        results = self.assert_success_clean(proc, "只读/无索引")
        self.assertEqual(
            results,
            [{"path": "keep.txt", "line": 2, "snippet": "TARGET in middle"}],
        )


if __name__ == "__main__":
    unittest.main()

"""local_search 全文件 UTF-8 校验的回归测试。

验收场景：
1. broken.txt 首行命中 target，但文件末尾（16384 个 ASCII 字符之后）追加
   单字节 0xff —— 整个文件被跳过并告警，只返回 good.md 的命中。
2. 把末尾的 0xff 换成 ASCII 字符 x —— 文件合法，按路径顺序返回两个文件，
   broken.txt 仍返回第 1 行命中，且无告警。

仅依赖标准库；通过子进程调用 ``python -m local_search`` 验证端到端行为。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

FILLER = b"x" * 16384


def run_search(directory: Path, keyword: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "local_search", str(directory), keyword],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )


class WholeFileUtf8Test(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "good.md").write_bytes(b"target good\n")
        # 首行即命中，非法字节远在命中之后、文件末尾。
        (self.root / "broken.txt").write_bytes(
            b"target early\n" + FILLER + b"\xff"
        )

    def test_invalid_byte_after_hit_skips_whole_file(self) -> None:
        proc = run_search(self.root, "target")

        self.assertEqual(proc.returncode, 0)
        stderr = proc.stderr.decode("utf-8")
        self.assertIn("broken.txt", stderr)
        self.assertIn("无法按 UTF-8 解码", stderr)

        results = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            results,
            [{"path": "good.md", "line": 1, "snippet": "target good"}],
        )

    def test_valid_file_with_same_content_is_returned(self) -> None:
        # 把末尾的 0xff 换成合法的 ASCII 字符，文件即可完整解码。
        (self.root / "broken.txt").write_bytes(
            b"target early\n" + FILLER + b"x"
        )

        proc = run_search(self.root, "target")

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr, b"")

        results = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            results,
            [
                {"path": "broken.txt", "line": 1, "snippet": "target early"},
                {"path": "good.md", "line": 1, "snippet": "target good"},
            ],
        )

    def test_truncated_multibyte_char_at_eof_skips_file(self) -> None:
        # 末尾截断的多字节字符（0xc3 是某两字节序列的首字节）同样视为解码失败。
        (self.root / "broken.txt").write_bytes(
            b"target early\n" + FILLER + b"\xc3"
        )

        proc = run_search(self.root, "target")

        self.assertEqual(proc.returncode, 0)
        stderr = proc.stderr.decode("utf-8")
        self.assertIn("broken.txt", stderr)
        self.assertIn("无法按 UTF-8 解码", stderr)
        results = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            results,
            [{"path": "good.md", "line": 1, "snippet": "target good"}],
        )


if __name__ == "__main__":
    unittest.main()

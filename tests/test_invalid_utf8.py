"""回归测试：非 UTF-8 文件无论非法字节出现在何处都必须整体跳过。

仅依赖标准库；通过子进程运行 ``python -m local_search`` 验收真实行为。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def run_search(directory: Path, keyword: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "local_search", str(directory), keyword],
        cwd=PROJECT_ROOT,
        capture_output=True,
    )


class InvalidUtf8SkipTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "good.md").write_bytes(b"target good\n")

    def _write_broken(self, tail: bytes) -> None:
        # 首行命中，换行后 16384 个 ASCII 'x'，再追加指定尾部字节。
        payload = b"target early\n" + b"x" * 16384 + tail
        (self.root / "broken.txt").write_bytes(payload)

    def test_invalid_byte_after_match_skips_whole_file(self) -> None:
        """命中之后、文件末尾的非法字节 0xff 使整个文件被跳过并告警。"""
        self._write_broken(b"\xff")

        proc = run_search(self.root, "target")

        self.assertEqual(proc.returncode, 0)
        results = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            results,
            [{"path": "good.md", "line": 1, "snippet": "target good"}],
        )
        stderr = proc.stderr.decode("utf-8")
        self.assertIn("broken.txt", stderr)
        self.assertIn("UTF-8", stderr)

    def test_valid_tail_keeps_both_results(self) -> None:
        """末尾非法字节替换为 'x' 后，两个文件按路径顺序返回且无告警。"""
        self._write_broken(b"x")

        proc = run_search(self.root, "target")

        self.assertEqual(proc.returncode, 0)
        results = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            results,
            [
                {"path": "broken.txt", "line": 1, "snippet": "target early"},
                {"path": "good.md", "line": 1, "snippet": "target good"},
            ],
        )
        self.assertEqual(proc.stderr, b"")

    def test_truncated_multibyte_char_at_eof_skips_file(self) -> None:
        """文件末尾截断的多字节字符同样视为解码失败。"""
        self._write_broken("汉".encode("utf-8")[:2])  # 截断的三字节序列

        proc = run_search(self.root, "target")

        self.assertEqual(proc.returncode, 0)
        results = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            results,
            [{"path": "good.md", "line": 1, "snippet": "target good"}],
        )
        self.assertIn("broken.txt", proc.stderr.decode("utf-8"))

    def test_invalid_byte_before_match_skips_file(self) -> None:
        """非法字节出现在命中之前时同样跳过整个文件。"""
        (self.root / "broken.txt").write_bytes(b"\xfftarget early\n")

        proc = run_search(self.root, "target")

        self.assertEqual(proc.returncode, 0)
        results = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(
            results,
            [{"path": "good.md", "line": 1, "snippet": "target good"}],
        )
        self.assertIn("broken.txt", proc.stderr.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()

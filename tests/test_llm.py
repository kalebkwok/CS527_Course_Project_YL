"""§12 — llm: DryRunClient logging, retry behaviour, provider usage accounting."""
from __future__ import annotations

import socket
import unittest
from unittest import mock

from demandtest import db
from demandtest.llm import DryRunClient, LLMClient, LLMError

from tests._util import make_task_row, memory_conn


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class LlmTest(unittest.TestCase):
    def _conn(self):
        conn = memory_conn()
        self.addCleanup(conn.close)
        return conn

    def test_dry_run_writes_one_row_per_call_with_increasing_seq(self):
        conn = self._conn()
        repo_id = db.add_repo(conn, "mini", "/tmp/mini")
        task_id = make_task_row(conn, repo_id)
        run_id = db.start_run(conn, task_id, "demandtest", "dry", {"dry_run": True})
        client = DryRunClient(conn=conn, run_id=run_id)
        client.chat([{"role": "user", "content": "packet one"}], "generate")
        client.chat([{"role": "user", "content": "packet two"}], "refine")
        rows = conn.execute("SELECT seq, stage, prompt_tokens, completion_tokens FROM llm_calls ORDER BY id").fetchall()
        self.assertEqual([r["seq"] for r in rows], [1, 2])
        self.assertEqual([r["stage"] for r in rows], ["generate-dry", "refine-dry"])
        self.assertTrue(all(r["prompt_tokens"] > 0 and r["completion_tokens"] > 0 for r in rows))

    def test_dry_run_returns_parsable_java(self):
        from demandtest.generate import extract_java
        client = DryRunClient()
        reply = client.chat([{"role": "user", "content": "x"}], "generate")
        self.assertIn("class ", extract_java(reply))

    def test_retries_exactly_max_retries_then_raises(self):
        sleeps: list[float] = []
        client = LLMClient(model="m", base_url=f"http://127.0.0.1:{_closed_port()}", timeout_s=1,
                           max_retries=3, backoff=sleeps.append)
        with self.assertRaises(LLMError):
            client.chat([{"role": "user", "content": "hi"}], "generate")
        self.assertEqual(len(sleeps), 3)

    def test_provider_usage_is_logged(self):
        conn = self._conn()
        repo_id = db.add_repo(conn, "mini", "/tmp/mini")
        task_id = make_task_row(conn, repo_id)
        run_id = db.start_run(conn, task_id, "demandtest", "m", {"x": 1})
        response = {"choices": [{"message": {"content": "hello"}}],
                    "usage": {"prompt_tokens": 42, "completion_tokens": 7}}
        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = __import__("json").dumps(response).encode()
            client = LLMClient(model="m", conn=conn, run_id=run_id, base_url="http://localhost:9", api_key="k")
            reply = client.chat([{"role": "user", "content": "hi"}], "generate")
        self.assertEqual(reply, "hello")
        row = conn.execute("SELECT * FROM llm_calls").fetchone()
        self.assertEqual((row["prompt_tokens"], row["completion_tokens"]), (42, 7))
        self.assertEqual(row["stage"], "generate")
        self.assertEqual(len(row["prompt_sha"]), 16)


if __name__ == "__main__":
    unittest.main()

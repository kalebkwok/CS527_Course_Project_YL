"""Accounting LLM client (SPEC §5 llm.py).

- OpenAI-compatible /chat/completions via stdlib urllib; network lives only here.
- Retries transport errors (URLError/OSError/timeout, HTTP >= 500) exactly
  max_retries times with exponential backoff, then raises LLMError.
- On success inserts one llm_calls row with the provider's usage object
  (prompt_tokens/completion_tokens, -1 if absent), latency, seq, and
  sha256[:16] of prompt & reply. Estimates are never logged (§6 rule 1).
- DryRunClient: canned answers per stage; logs stage+"-dry" with estimated
  tokens (the only place estimates appear, and never in llm_calls accounting
  for real systems).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from typing import Optional

from . import db
from .packet import est_tokens


class LLMError(RuntimeError):
    pass


def _sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


class LLMClient:
    def __init__(self, model: str, conn=None, run_id: Optional[int] = None, base_url: Optional[str] = None,
                 api_key: Optional[str] = None, timeout_s: int = 180, max_retries: int = 3,
                 backoff=None):
        self.model = model
        self.conn = conn
        self.run_id = run_id
        self.base_url = base_url if base_url is not None else os.environ.get("DEMANDTEST_BASE_URL", "")
        self.api_key = api_key if api_key is not None else os.environ.get("DEMANDTEST_API_KEY", "")
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self._backoff = backoff if backoff is not None else (lambda attempt: min(30.0, 0.5 * (2 ** attempt)))

    def chat(self, messages, stage: str, max_tokens: int = 1024, temperature: float = 0.0, **extra) -> str:
        payload = {"model": self.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        payload.update(extra)
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        prompt_sha = _sha16(json.dumps(messages, sort_keys=True))
        url = self.base_url.rstrip("/") + "/chat/completions"
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            started = time.monotonic()
            try:
                req = urllib.request.Request(url, data=body, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                latency_ms = int((time.monotonic() - started) * 1000)
                reply = data["choices"][0]["message"]["content"]
                usage = data.get("usage") or {}
                self._log(stage, usage.get("prompt_tokens", -1), usage.get("completion_tokens", -1),
                          latency_ms, prompt_sha, _sha16(reply))
                return reply
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode("utf-8", errors="replace")[:500]
                except Exception:
                    pass
                if e.code >= 500 and attempt < self.max_retries:
                    last_error = e
                    self._backoff(attempt)
                    continue
                raise LLMError(f"HTTP {e.code}: {detail}") from e
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last_error = e
                if attempt < self.max_retries:
                    self._backoff(attempt)
                    continue
                raise LLMError(f"transport error after {self.max_retries} retries: {e}") from e
        raise LLMError(str(last_error))

    def _log(self, stage: str, prompt_tokens: int, completion_tokens: int, latency_ms: int,
             prompt_sha: str, response_sha: str) -> None:
        if self.conn is None or self.run_id is None:
            return
        db.log_llm_call(self.conn, self.run_id, stage, self.model, prompt_tokens, completion_tokens,
                        latency_ms, prompt_sha, response_sha)


class DryRunClient(LLMClient):
    """Canned answers per stage; logs stage+"-dry" with estimated tokens (§12 llm test)."""

    DEFAULT_ANSWER = (
        "```java\n"
        "package com.mini;\n"
        "\n"
        "import org.junit.jupiter.api.Test;\n"
        "\n"
        "class FooTestGeneratedTest {\n"
        "    @Test\n"
        "    void validatesIntention() {\n"
        "        org.junit.jupiter.api.Assertions.assertEquals(2, 1 + 1);\n"
        "    }\n"
        "}\n"
        "```\n"
    )

    def __init__(self, model: str = "dry-run", conn=None, run_id: Optional[int] = None,
                 answers: Optional[dict] = None, **kw):
        super().__init__(model=model, conn=conn, run_id=run_id, **kw)
        self.answers = dict(answers or {})

    def chat(self, messages, stage: str, max_tokens: int = 1024, temperature: float = 0.0, **extra) -> str:
        prompt_text = "\n".join(str(m.get("content", "")) for m in messages)
        answer = self.answers.get(stage) or self.DEFAULT_ANSWER
        self._log(stage + "-dry", est_tokens(prompt_text), est_tokens(answer), 0,
                  _sha16(prompt_text), _sha16(answer))
        return answer

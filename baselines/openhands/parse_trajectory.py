"""OpenHands trajectory parser (SPEC §10.1).

Maps a recorded headless OpenHands event log into the ledger with the same
accounting rules as DemandTest (§6):

- one `llm_calls` row per LLM completion, tokens from the event's usage metadata
  (never estimates: -1 when the trajectory does not carry usage);
- one `file_access` row per distinct file-reading tool action; a path counts when
  a tool reads, views, greps into, lists with content, or opens it
  (rule 3). Directory listings without content do not count;
- `wall_ms` from the first to the last event;
- the final test file is handed to our S4 step 5 by the caller so that
  `compiled`/`passed` use the same verdict rules as every other system.

The event schema assumed here is documented in `README.md`; it MUST be
re-checked against the pinned OpenHands version before the numbers are reported.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from demandtest import db

READ_ACTIONS = {"read", "view", "cat", "sed", "head", "tail", "less", "open"}
GREP_ACTIONS = {"grep", "search", "find_in_file"}
LIST_ACTIONS = {"list", "ls"}
WRITE_ACTIONS = {"write", "create", "edit"}
USAGE_KEYS = ("llm_metrics", "usage", "metrics")


def load_events(path: str) -> list[dict]:
    """Accepts a JSON array or JSONL of events."""
    text = Path(path).read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("["):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _parse_ts(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


def _usage(event: dict) -> Optional[dict]:
    for key in USAGE_KEYS:
        value = event.get(key)
        if isinstance(value, dict) and ("prompt_tokens" in value or "completion_tokens" in value):
            return value
    return None


def _args(event: dict) -> dict:
    args = event.get("args")
    return args if isinstance(args, dict) else {}


def _observation(event: dict) -> dict:
    obs = event.get("observation")
    return obs if isinstance(obs, dict) else {}


def event_paths(event: dict) -> list[str]:
    """Paths this event counts as inspected (§6 rule 3); [] for anything else."""
    action = str(event.get("action", "")).lower()
    args = _args(event)
    obs = _observation(event)
    out: list[str] = []
    if action in READ_ACTIONS:
        path = args.get("path") or args.get("file") or obs.get("path")
        if path:
            out.append(str(path))
    elif action in GREP_ACTIONS:
        path = args.get("path") or args.get("file")
        if path and Path(str(path)).suffix:  # a concrete file, not just a search root
            out.append(str(path))
        for key in ("matched_files", "files"):
            for item in event.get(key, []) or obs.get(key, []) or []:
                out.append(str(item))
    elif action in LIST_ACTIONS:
        content = obs.get("content") or event.get("content") or ""
        if content.strip():  # directory listings without content do not count
            path = args.get("path") or event.get("path")
            if path:
                out.append(str(path))
    return out


def is_completion(event: dict) -> bool:
    return _usage(event) is not None


def extract_test(events: Iterable[dict]) -> tuple[Optional[str], str]:
    """Last written/edited test file → (path, source) for our S4 verdict step."""
    test_path: Optional[str] = None
    source = ""
    for event in events:
        action = str(event.get("action", "")).lower()
        if action not in WRITE_ACTIONS:
            continue
        args = _args(event)
        path = args.get("path") or args.get("file") or event.get("path")
        content = args.get("content") or args.get("file_text") or _observation(event).get("content")
        if path and str(path).endswith(".java") and content:
            test_path, source = str(path), str(content)
    return test_path, source


def ingest(conn, run_id: int, events: list[dict], model: str) -> dict:
    """Write llm_calls/file_access rows; return wall_ms, counts and the final test."""
    timestamps = [t for t in (_parse_ts(e.get("timestamp") or e.get("created_at")) for e in events) if t is not None]
    wall_ms = int((max(timestamps) - min(timestamps)) * 1000) if len(timestamps) >= 2 else 0

    n_calls = 0
    for event in events:
        usage = _usage(event)
        if usage is None:
            continue
        n_calls += 1
        db.log_llm_call(
            conn, run_id, stage=str(event.get("action") or "message"), model=model,
            prompt_tokens=int(usage.get("prompt_tokens", -1)),
            completion_tokens=int(usage.get("completion_tokens", -1)),
            latency_ms=int(usage.get("latency_ms", 0)),
            prompt_sha=str(event.get("prompt_sha") or event.get("id") or "")[:16],
            response_sha=str(event.get("response_sha") or event.get("id") or "")[:16],
        )

    files: list[str] = []
    for event in events:
        for path in event_paths(event):
            if path not in files:
                files.append(path)
            db.log_file_access(conn, run_id, path, stage=str(event.get("action") or "read"))

    test_path, source = extract_test(events)
    return {"wall_ms": wall_ms, "n_llm_calls": n_calls, "files": files, "test_path": test_path, "test_source": source}


def run(conn, run_id: int, trajectory_path: str, model: str) -> dict:
    return ingest(conn, run_id, load_events(trajectory_path), model)

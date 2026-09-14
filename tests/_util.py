"""Shared offline fixtures for the §12 acceptance tests (no LLM, no Java toolchain)."""
from __future__ import annotations

import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MINI_INDEX = FIXTURES / "mini_index.json"
MINI_REPO = FIXTURES / "mini_repo"
MINI_TASKS = FIXTURES / "mini_tasks.jsonl"
MAVEN_FAILURE_LOG = FIXTURES / "maven_failure.log"

INTENTION = {
    "objective": "process stores the value associated with the given Bar and reports the resulting size",
    "preconditions": "bar is a validated Bar; n is the delta to apply",
    "expected_results": "process throws IOException when the delta is negative, otherwise it returns the updated size",
}
REF_TEST_ID = "com.mini.FooTest#testProcessRoundTrips"


def load_mini_index():
    from demandtest import index as index_mod
    return index_mod.load(str(MINI_INDEX))


def mini_task():
    from demandtest.demand import Task
    return Task(
        repo="mini", focal_class="com.mini.Foo", focal_method="process", focal_sig="process(Bar,int)",
        focal_file="src/main/java/com/mini/Foo.java", intention=dict(INTENTION), ref_test_id=REF_TEST_ID, id=1,
    )


def focal_source() -> str:
    return (MINI_REPO / "src/main/java/com/mini/Foo.java").read_text(encoding="utf-8")


def memory_conn():
    from demandtest import db
    conn = db.connect(":memory:")
    db.init_schema(conn)
    return conn


def make_task_row(conn, repo_id: int = 1, **overrides) -> int:
    """Insert one task row directly; returns its id."""
    payload = {
        "focal_class": "com.mini.Foo", "focal_method": "process", "focal_sig": "process(Bar,int)",
        "focal_file": "src/main/java/com/mini/Foo.java", "intention": INTENTION, "ref_test_id": REF_TEST_ID,
    }
    payload.update(overrides)
    with conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO tasks(repo_id, focal_class, focal_method, focal_sig, focal_file, "
            "intention_json, ref_test_id) VALUES(?,?,?,?,?,?,?)",
            (repo_id, payload["focal_class"], payload["focal_method"], payload["focal_sig"], payload["focal_file"],
             json.dumps(payload["intention"]), payload["ref_test_id"]),
        )
    if cur.lastrowid:
        return int(cur.lastrowid)
    return int(conn.execute("SELECT id FROM tasks WHERE ref_test_id=?", (payload["ref_test_id"],)).fetchone()[0])

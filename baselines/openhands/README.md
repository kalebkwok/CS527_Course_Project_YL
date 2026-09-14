# OpenHands baseline (SPEC §10.1)

`parse_trajectory.py` maps a recorded headless OpenHands event log into our
ledger so that the baseline is measured with the same accounting rules as
DemandTest: one `llm_calls` row per completion (provider usage, never
estimates), one `file_access` row per distinct file-reading action, `wall_ms`
from the first to the last event, and the final test file handed to our S4
verdict step.

## Event schema assumed here (NOT yet verified against a pinned version)

SPEC §10.1 requires this to be checked against the pinned OpenHands version
before any number is reported. The parser currently assumes one JSON object per
event (JSON array or JSONL):

```jsonc
{
  "id": 7,
  "timestamp": "2026-09-14T12:00:03Z",     // ISO-8601 or epoch seconds
  "action": "read",                         // read|view|cat|sed|head|tail|less|open
  "args": {"path": "src/main/java/com/mini/Foo.java"},
  "observation": {"content": "..."},        // optional
  "llm_metrics": {"prompt_tokens": 1200, "completion_tokens": 90, "latency_ms": 800}
}
```

Rules implemented:

| event | ledger effect |
|---|---|
| any event carrying `llm_metrics` / `usage` / `metrics` | one `llm_calls` row, `seq` increasing per run |
| `read`/`view`/`cat`/`sed`/`head`/`tail`/`less`/`open` | `file_access` for `args.path`/`args.file` |
| `grep`/`search` | the searched file when it is a concrete file, plus `matched_files`/`files` entries |
| `list`/`ls` | `file_access` only when the listing carries content |
| `write`/`create`/`edit` on a `.java` file | candidate final test for the S4 compile/run verdict |

## Not implemented yet

- The runner that starts OpenHands headless (`max_iterations = 30`, no
  internet, per-task checkout with the reference test removed), three runs per
  task, and the report of mean/spread.
- The compile/run step that consumes `ingest(...)["test_source"]` — reuse
  `demandtest.execute.compile_and_run` on the per-task checkout.

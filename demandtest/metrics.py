"""Reporting over the ledger (SPEC §5 metrics.py, §11 metrics)."""
from __future__ import annotations

SUMMARY_SQL = """
SELECT r.system AS system, r.model AS model, COUNT(*) AS n,
       AVG(res.compiled) AS compile_rate, AVG(res.passed) AS pass_rate,
       AVG(res.mutation_score) AS mutation, AVG(res.alignment_score) AS alignment,
       AVG(res.prompt_tokens + res.completion_tokens) AS tokens_per_task,
       AVG(res.n_llm_calls) AS calls_per_task,
       AVG(res.wall_ms) / 1000.0 AS wall_s,
       AVG(res.inspected_files) AS files_per_task
FROM results res JOIN runs r ON r.id = res.run_id
GROUP BY r.system, r.model
ORDER BY r.system, r.model
"""

PARETO_SQL = """
SELECT r.config_hash AS config_hash, r.model AS model, COUNT(*) AS n,
       AVG(res.passed) AS pass_rate,
       AVG(res.prompt_tokens + res.completion_tokens) AS tokens_per_task,
       AVG(res.inspected_files) AS files_per_task
FROM results res JOIN runs r ON r.id = res.run_id
WHERE r.system = 'demandtest'
GROUP BY r.config_hash, r.model
ORDER BY tokens_per_task
"""

COLUMNS = ["system", "model", "n", "compile_rate", "pass_rate", "mutation", "alignment",
           "tokens_per_task", "calls_per_task", "wall_s", "files_per_task"]


def summarize(conn) -> list[dict]:
    return [dict(row) for row in conn.execute(SUMMARY_SQL)]


def pareto(conn) -> list[dict]:
    return [dict(row) for row in conn.execute(PARETO_SQL)]


def _cell(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def format_table(rows: list[dict], columns: list[str] | None = None) -> str:
    if not rows:
        return "_no rows_"
    cols = columns or [c for c in COLUMNS if c in rows[0]] or list(rows[0].keys())
    out = ["| " + " | ".join(cols) + " |",
           "|" + "|".join("---" for _ in cols) + "|"]
    for row in rows:
        out.append("| " + " | ".join(_cell(row.get(c)) for c in cols) + " |")
    return "\n".join(out)

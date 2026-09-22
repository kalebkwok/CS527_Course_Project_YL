"""Reporting over the ledger (SPEC §5 metrics.py, §11 metrics).

0.2.2 (after TestTailor, Zhou et al. FSE 2026): `target_hit_rate` and `aligned_pass_rate`
separate "passes" from "passes and checks the intention"; `call_success` is usable tests
per LLM call; `budget_curve` is cumulative passes against cumulative tokens in task order.
"""
from __future__ import annotations

SUMMARY_SQL = """
SELECT r.system AS system, r.model AS model, COUNT(*) AS n,
       AVG(res.compiled) AS compile_rate, AVG(res.passed) AS pass_rate,
       AVG(res.target_hit) AS target_hit_rate,
       AVG(CASE WHEN res.passed = 1 AND res.target_hit = 1 THEN 1.0 ELSE 0.0 END) AS aligned_pass_rate,
       AVG(res.mutation_score) AS mutation, AVG(res.alignment_score) AS alignment,
       AVG(res.prompt_tokens + res.completion_tokens) AS tokens_per_task,
       AVG(res.n_llm_calls) AS calls_per_task,
       CAST(SUM(res.passed) AS REAL) / NULLIF(SUM(res.n_llm_calls), 0) AS call_success,
       AVG(res.wall_ms) / 1000.0 AS wall_s,
       AVG(res.inspected_files) AS files_per_task
FROM results res JOIN runs r ON r.id = res.run_id
GROUP BY r.system, r.model
ORDER BY r.system, r.model
"""

PARETO_SQL = """
SELECT r.config_hash AS config_hash, r.model AS model, COUNT(*) AS n,
       AVG(res.passed) AS pass_rate,
       AVG(CASE WHEN res.passed = 1 AND res.target_hit = 1 THEN 1.0 ELSE 0.0 END) AS aligned_pass_rate,
       AVG(res.prompt_tokens + res.completion_tokens) AS tokens_per_task,
       AVG(res.inspected_files) AS files_per_task
FROM results res JOIN runs r ON r.id = res.run_id
WHERE r.system = 'demandtest'
GROUP BY r.config_hash, r.model
ORDER BY tokens_per_task
"""

CURVE_SQL = """
SELECT r.system AS system, r.model AS model, res.run_id AS run_id,
       COALESCE(res.passed, 0) AS passed,
       COALESCE(res.prompt_tokens, 0) + COALESCE(res.completion_tokens, 0) AS tokens
FROM results res JOIN runs r ON r.id = res.run_id
WHERE r.status = 'done'
ORDER BY r.system, r.model, res.run_id
"""

STATUS_SQL = """
SELECT r.system AS system, r.model AS model, COALESCE(res.packet_status, '-') AS packet_status, COUNT(*) AS n,
       AVG(res.compiled) AS compile_rate, AVG(res.passed) AS pass_rate,
       AVG(CASE WHEN res.passed = 1 AND res.target_hit = 1 THEN 1.0 ELSE 0.0 END) AS aligned_pass_rate,
       AVG(res.prompt_tokens + res.completion_tokens) AS tokens_per_task
FROM results res JOIN runs r ON r.id = res.run_id
GROUP BY r.system, r.model, packet_status
ORDER BY r.system, r.model, packet_status
"""

STATUS_COLUMNS = ["system", "model", "packet_status", "n", "compile_rate", "pass_rate", "aligned_pass_rate",
                  "tokens_per_task"]
COLUMNS = ["system", "model", "n", "compile_rate", "pass_rate", "target_hit_rate", "aligned_pass_rate",
           "mutation", "alignment", "tokens_per_task", "calls_per_task", "call_success", "wall_s",
           "files_per_task"]
CURVE_COLUMNS = ["system", "model", "runs", "cum_tokens", "cum_passed", "pass_rate"]


def summarize(conn) -> list[dict]:
    return [dict(row) for row in conn.execute(SUMMARY_SQL)]


def pareto(conn) -> list[dict]:
    return [dict(row) for row in conn.execute(PARETO_SQL)]


def by_status(conn) -> list[dict]:
    """Outcomes stratified by the §2.4 final packet status (sufficient / sufficient-with-gaps / budget-limited / fallback)."""
    return [dict(row) for row in conn.execute(STATUS_SQL)]


def budget_curve(conn, points: int = 10) -> list[dict]:
    """Per system×model: cumulative tokens and passes in task order, sampled at ≤ `points` evenly spaced runs."""
    groups: dict[tuple, list] = {}
    for row in conn.execute(CURVE_SQL):
        groups.setdefault((row["system"], row["model"]), []).append(row)
    out: list[dict] = []
    for (system, model), rows in groups.items():
        n = len(rows)
        cum_tokens = cum_passed = 0
        series = []
        for row in rows:
            cum_tokens += int(row["tokens"])
            cum_passed += int(row["passed"])
            series.append((cum_tokens, cum_passed))
        if n <= points:
            picks = list(range(n))
        else:
            picks = sorted({round((k + 1) * n / points) - 1 for k in range(points)})
        for idx in picks:
            tokens, passed = series[idx]
            out.append({"system": system, "model": model, "runs": idx + 1, "cum_tokens": tokens,
                        "cum_passed": passed, "pass_rate": passed / (idx + 1)})
    return out


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

"""S3 — one-call generation (SPEC §2.7, §8.1). Parsing failure is terminal: no retry."""
from __future__ import annotations

import re


class ParseError(ValueError):
    pass


SYSTEM_PROMPT = """You write one JUnit test class for a Java project.
Rules:
- Validate exactly the stated OBJECTIVE / EXPECTED RESULTS with meaningful assertions; do not chase coverage.
- Obtain every value the way the HOW TO OBTAIN VALUES section shows; do not invent constructors, factories or APIs.
- Use the project's test framework, assertion library and mocking library as given in PROJECT FACTS.
- Output a single ```java fenced block containing one complete, compilable test class with imports and package. Nothing else."""


def extract_java(text: str) -> str:
    m = re.search(r"```java[ \t]*\r?\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip("\n")
    m = re.search(r"```[ \t]*\r?\n(.*?)```", text, re.DOTALL)
    if m and re.search(r"\b(class|interface|enum)\s+\w+", m.group(1)):
        return m.group(1).strip("\n")
    raise ParseError("no ```java fenced block in reply")


def generate(client, packet, max_tokens: int = 1200) -> str:
    """Exactly one chat completion, temperature 0 (§2.7)."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": packet.text},
    ]
    return client.chat(messages, stage="generate", max_tokens=max_tokens, temperature=0.0)

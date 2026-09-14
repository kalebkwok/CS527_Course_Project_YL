"""S5 — semantic repair: at most one LLM call, no new files opened (SPEC §2.9, §8.2)."""
from __future__ import annotations

from .generate import extract_java
from .packet import SECTION_KEYS

S5_PROMPT = """The test below was generated for this validation intention but failed. Fix it with the smallest change.
Do not add new dependencies or invent APIs; use only what appears in the packet excerpt. Return one ```java block with the full class.

INTENTION AND KNOWN RECIPES:
{excerpt}

CURRENT TEST:
```java
{test}
```

FAILURE:
{diagnostics}
"""


def refine_once(client, index, task, src: str, verdict, packet, max_tokens: int = 1200) -> str:
    excerpt = "\n\n".join(
        packet.sections[k] for k in (SECTION_KEYS[0], SECTION_KEYS[2], SECTION_KEYS[3])
        if packet.sections.get(k)
    )
    prompt = S5_PROMPT.format(excerpt=excerpt, test=src, diagnostics=verdict.diagnostics or "(no diagnostics)")
    reply = client.chat([{"role": "user", "content": prompt}], stage="refine", max_tokens=max_tokens, temperature=0.0)
    return extract_java(reply)

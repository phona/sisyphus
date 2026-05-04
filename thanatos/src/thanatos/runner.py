"""Product knowledge recall — atomic-MCP era.

Atomic-MCP era: thanatos no longer parses spec scenarios. The only runner
function that survives is :func:`recall`, which surfaces product knowledge
fragments (``.thanatos/anchors.md`` / ``flows.md`` / ``pitfalls.md`` / etc.)
to whichever agent calls it.

Why ``recall`` is mandatory:
- Without it, every agent invocation re-discovers product context from raw
  source — verdicts drift across REQs, design intent decays.
- accept-agent / analyze-agent / challenger MUST call ``recall`` at least once
  per session to anchor on the same product baseline.
"""

from __future__ import annotations

import re
from pathlib import Path


def recall(
    skill_path: str, intent: str, *, limit: int = 10, tags: list[str] | None = None
) -> list[dict]:
    """Look up product knowledge fragments matching an intent.

    Recursively searches all ``.md`` files under the same directory as
    ``skill.yaml`` (the *skill_dir*) and returns snippets ranked by keyword
    overlap with *intent*. Optional *tags* filters to files whose YAML
    frontmatter ``tags`` list intersects the requested set.
    """
    skill_dir = Path(skill_path).parent
    if not skill_dir.is_dir():
        return []

    intent_words = {w.lower() for w in intent.split() if len(w) > 2}
    if not intent_words:
        return []

    filter_tags = {t.lower() for t in (tags or [])}
    hits: list[tuple[float, float, dict]] = []

    for md_path in skill_dir.rglob("*.md"):
        text = md_path.read_text(encoding="utf-8")
        frontmatter_tags = _extract_frontmatter_tags(text)

        if filter_tags and not filter_tags.intersection(frontmatter_tags):
            continue

        body = _strip_frontmatter(text)
        chunks = _split_into_chunks(body)
        for chunk in chunks:
            if not chunk.strip():
                continue
            score = _score_chunk(chunk, intent_words)
            if score > 0:
                mtime = md_path.stat().st_mtime
                hits.append(
                    (
                        score,
                        mtime,
                        {
                            "kind": md_path.name,
                            "snippet": chunk.strip()[:800],
                            "freshness": mtime,
                        },
                    )
                )

    hits.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [h[2] for h in hits[:limit]]


def _split_into_chunks(text: str) -> list[str]:
    heading_split = re.split(r"\n(?=#+\s+)", text)
    chunks: list[str] = []
    for part in heading_split:
        sub = [s.strip() for s in part.split("\n\n") if s.strip()]
        chunks.extend(sub)
    return chunks


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _extract_frontmatter_tags(text: str) -> set[str]:
    import yaml  # type: ignore[import-untyped]

    m = _FRONTMATTER_RE.match(text)
    if not m:
        return set()
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return set()
    if not isinstance(fm, dict):
        return set()
    raw = fm.get("tags")
    if isinstance(raw, list):
        return {str(t).lower() for t in raw}
    if isinstance(raw, str):
        return {raw.lower()}
    return set()


def _strip_frontmatter(text: str) -> str:
    m = _FRONTMATTER_RE.match(text)
    if m:
        return text[m.end() :]
    return text


def _score_chunk(chunk: str, intent_words: set[str]) -> float:
    chunk_words = {w.lower() for w in chunk.split() if len(w) > 2}
    if not chunk_words:
        return 0.0
    overlap = intent_words & chunk_words
    return len(overlap) / len(intent_words)

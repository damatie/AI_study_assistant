"""Utilities for extracting, validating, and enforcing a single stepsjson block.

The LLM is instructed to end markdown with a fenced block like:

```stepsjson
{ "version":1,"title":"Flow","steps":[{"id":"A","text":"Do X","next":["B"]},{"id":"B","text":"Done"}] }
```

We enforce:
* Max 25 steps
* id: 1-3 chars, alphanumeric start, no whitespace
* text <= 60 chars, stripped of trailing period
* next contains only existing ids (invalid references dropped)
* duplicates removed preserving first occurrence
* Always ensure a single final fenced block appended if missing
"""
from __future__ import annotations

from typing import List, Dict, Tuple, Any
import json
import re
import logging

logger = logging.getLogger(__name__)

FENCE_RE = re.compile(r"```stepsjson\s*\n(?P<json>{[\s\S]*?})\s*```", re.IGNORECASE)
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,5}$")  # allow a bit longer (up to 6 chars) to reduce filtering

# Generic fenced code blocks (not stepsjson)
GENERIC_FENCE_RE = re.compile(r"```(?P<lang>[A-Za-z0-9_+-]*)\s*\n(?P<body>[\s\S]*?)\n```", re.MULTILINE)

def extract_stepsjson(markdown: str) -> Tuple[Dict[str, Any] | None, str | None]:
    """Return (parsed_obj, raw_json_str) for the FIRST stepsjson fence. If none, (None, None)."""
    m = FENCE_RE.search(markdown)
    if not m:
        return None, None
    raw = m.group("json").strip()
    try:
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            return None, raw
        return obj, raw
    except Exception:
        logger.debug("Failed to parse stepsjson block")
        return None, raw

def _sanitize_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cleaned: List[Dict[str, Any]] = []
    seen_ids = set()
    for raw in steps:
        if not isinstance(raw, dict):
            continue
        sid = str(raw.get("id", "")).strip()
        if not ID_RE.match(sid) or sid in seen_ids:
            continue
        text = str(raw.get("text", "")).strip()
        if text.endswith('.'):
            text = text[:-1].rstrip()
        if len(text) > 60:
            text = text[:57].rstrip() + '…'
        next_ids = []
        nxt = raw.get("next", [])
        if isinstance(nxt, list):
            for n in nxt:
                n = str(n).strip()
                if ID_RE.match(n):
                    next_ids.append(n)
        cleaned.append({"id": sid, "text": text, **({"next": next_ids} if next_ids else {})})
        seen_ids.add(sid)
        if len(cleaned) >= 25:
            break
    # second pass: filter next arrays to existing ids
    existing = {s["id"] for s in cleaned}
    for s in cleaned:
        if "next" in s:
            s["next"] = [n for n in s["next"] if n in existing and n != s["id"]]
            if not s["next"]:
                s.pop("next", None)
    return cleaned

def _salvage_steps(raw_steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """If the primary sanitize pass yields zero steps but raw had data, try to salvage.

    Strategy:
    * Take up to first 25 raw step dicts preserving order.
    * Generate compact IDs (A, B, ... Z, A1, B1, ...).
    * Truncate text and strip trailing periods.
    * Re-map any 'next' references by position if possible or ignore invalid.
    """
    if not raw_steps:
        return []
    # basic id sequence generator
    gen_ids: List[str] = []
    base_letters = [chr(c) for c in range(ord('A'), ord('Z') + 1)]
    # allow up to 25 anyway so base letters enough
    gen_ids = base_letters
    salvaged: List[Dict[str, Any]] = []
    limit = min(25, len(raw_steps))
    for idx in range(limit):
        raw = raw_steps[idx]
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or raw.get("label") or raw.get("name") or "Step").strip()
        if text.endswith('.'):
            text = text[:-1].rstrip()
        if len(text) > 60:
            text = text[:57].rstrip() + '…'
        sid = gen_ids[idx] if idx < len(gen_ids) else f"S{idx+1}"
        salvaged.append({"id": sid, "text": text})
    # attempt to reconstruct next lists by original order if any raw next indexes exist
    id_by_original_index = {i: salvaged[i]["id"] for i in range(len(salvaged))}
    for i, raw in enumerate(raw_steps[:limit]):
        if not isinstance(raw, dict):
            continue
        nxt = raw.get("next")
        mapped: List[str] = []
        if isinstance(nxt, list):
            for ref in nxt:
                # try numeric index
                if isinstance(ref, int) and ref in id_by_original_index and ref != i:
                    mapped.append(id_by_original_index[ref])
                else:
                    # attempt to match by position of first occurrence of a raw id string if present
                    if isinstance(ref, str):
                        # find step with that original id position
                        for pos, candidate in enumerate(raw_steps[:limit]):
                            if isinstance(candidate, dict) and str(candidate.get("id")) == ref and pos != i:
                                mapped.append(id_by_original_index[pos])
                                break
        if mapped:
            salvaged[i]["next"] = sorted(set(mapped))
    logger.info("stepsjson salvage applied: generated %d steps from raw content", len(salvaged))
    return salvaged

def validate_or_build(obj: Dict[str, Any] | None) -> Dict[str, Any]:
    if not obj or not isinstance(obj, dict):
        return {"version": 1, "title": "No Process", "steps": []}
    steps_raw = obj.get("steps", [])
    if not isinstance(steps_raw, list):
        steps_raw = []
    sanitized = _sanitize_steps(steps_raw)
    if not sanitized and steps_raw:
        # attempt salvage if model produced steps but all were filtered (e.g., long IDs)
        sanitized = _salvage_steps(steps_raw)
    title = obj.get("title")
    if not isinstance(title, str) or not title.strip():
        title = "Process"
    else:
        title = title.strip()
        if len(title) > 60:
            title = title[:57].rstrip() + '…'
    version = obj.get("version")
    if not isinstance(version, (int, float)):
        version = 1
    return {"version": int(version), "title": title, "steps": sanitized}

def sanitize_all_blocks(markdown: str) -> str:
    """Validate every stepsjson fenced block in-place.

    - Each block replaced with sanitized JSON (ids truncated, etc.).
    - Empty/invalid blocks become {"version":1,"title":"Process","steps":[]}.
    - Does NOT inject new blocks or remove extras; preserves author/LLM placement.
    """
    def _repl(match: re.Match) -> str:
        raw_json = match.group("json")
        try:
            obj = json.loads(raw_json)
        except Exception:
            obj = None
        validated = validate_or_build(obj)
        return "```stepsjson\n" + json.dumps(validated, ensure_ascii=False) + "\n```"

    return FENCE_RE.sub(_repl, markdown)

VERB_HINTS = [
    "load",
    "remove",
    "handle",
    "identify",
    "tokenize",
    "extract",
    "encode",
    "prepare",
    "authenticate",
    "monitor",
    "preprocess",
    "feature",
    "evaluate",
    "deploy",
    "retain",
    "log",
    "wait",
    "aggregate",
    "apply",
    "fine-tune",
    "react",
    "produce",
    "generate",
    "release",
    "classify",
    "adjust",
    "optimize",
]

def filter_trivial_blocks(markdown: str) -> str:
    """Remove sanitized stepsjson blocks that appear non-procedural / trivial.

    Heuristics (block removed if ANY fail):
      * <3 steps
      * No edges (no step has a non-empty next array)
      * Insufficient action verbs ( must have >=2 OR >=40% of steps containing an action verb )
    """

    def _repl(match: re.Match) -> str:
        raw_json = match.group("json")
        try:
            obj = json.loads(raw_json)
        except Exception:
            return ""  # drop unparsable
        validated = validate_or_build(obj)
        steps = validated.get("steps", []) or []
        if len(steps) < 3:
            return ""
        if not any("next" in s and s["next"] for s in steps):
            return ""  # no sequencing info
        verb_hits = 0
        for s in steps:
            txt = s.get("text", "").lower()
            if any(v in txt for v in VERB_HINTS) or any(word.endswith("ing") for word in txt.split()):
                verb_hits += 1
        if verb_hits < 2 or verb_hits < len(steps) * 0.4:
            return ""  # likely just a static list
        # keep block (already sanitized earlier if pipeline orders it that way)
        return "```stepsjson\n" + json.dumps(validated, ensure_ascii=False) + "\n```"

    return FENCE_RE.sub(_repl, markdown)


def _looks_like_code(text: str) -> bool:
    """Heuristically determine if a fenced block body is actual code.

    We treat as code if we see common programming tokens, braces, assignments,
    imports, function/class definitions, or JSON/XML structures.
    """
    t = text.strip()
    if not t:
        return False
    # Fast signals for code-like blocks
    code_signals = [
        r"\bclass\b", r"\bdef\b", r"\bfunction\b", r"\bvar\b", r"\blet\b", r"\bconst\b",
        r"#include\b", r"using\s+namespace", r"=>", r"::", r"->", r"==", r"!=", r"<=", r">=",
        r"\breturn\b", r"\bif\s*\(", r"\bfor\s*\(", r"\bwhile\s*\(", r"try:\s*$", r"catch\b",
        r"<[^>]+>",  # XML/HTML tags
    ]
    if any(re.search(p, t) for p in code_signals):
        return True
    # JSON-like or dict-like
    if (t.startswith("{") and t.endswith("}")) or (t.startswith("[") and t.endswith("]")):
        return True
    # Many braces/semicolons are typical of code
    if t.count(";") >= 2 or t.count("{") + t.count("}") >= 2:
        return True
    return False


def unwrap_non_code_fences(markdown: str) -> str:
    """Convert fenced blocks that are plain text/URLs into normal paragraphs.

    Rules:
    - Ignore ```stepsjson fences (handled elsewhere).
    - If lang is empty or a prose-ish marker (text, txt, md, markdown), and body doesn't look like code,
      unwrap the fence.
    - If the body is a single bare URL, convert to an inline link [URL](URL).
    """

    def _repl(match: re.Match) -> str:
        lang = (match.group("lang") or "").strip().lower()
        body = (match.group("body") or "").strip("\n")
        if lang == "stepsjson":  # safety; our other regex shouldn't catch this
            return match.group(0)
        if lang in {"", "text", "txt", "md", "markdown"} and not _looks_like_code(body):
            # Single URL line -> inline link
            url_match = re.fullmatch(r"https?://\S+", body.strip())
            if url_match:
                url = url_match.group(0)
                return f"[{url}]({url})"
            # Otherwise, just unwrap as plain text (preserve internal newlines)
            return body
        return match.group(0)

    # Apply only to non-steps fences by first replacing generic fences; stepsjson uses a different pattern
    return GENERIC_FENCE_RE.sub(_repl, markdown)


_ARTIFACT_HEADER_RE = re.compile(
    r"^\s*\|?\s*source\s*\|\s*definition\s*#?\s*\|?\s*$",
    re.IGNORECASE,
)
_SNAKE_TITLE_RE = re.compile(r"^[A-Za-z0-9]+(?:_[A-Za-z0-9]+){5,}\s*$")


def strip_scanned_table_artifacts(markdown: str) -> str:
    """Remove ugly scan artifacts like a table header line "| Source | Definition #"
    and an immediate long snake_case title row.

    Safety rules:
    - Never alter content inside fenced code blocks (including stepsjson).
    - Only strip exact header pattern variants and, if present, the very next line
      when it is a long snake_case title (≥6 underscore-delimited tokens).
    - Do not remove regular prose or headings.
    """
    lines = markdown.splitlines()

    out: List[str] = []
    i = 0
    in_fence = False
    current_fence_lang = None

    while i < len(lines):
        line = lines[i]
        fence_open = re.match(r"^```\s*([A-Za-z0-9_+-]*)\s*$", line)
        if fence_open:
            lang = (fence_open.group(1) or "").strip().lower()
            if not in_fence:
                in_fence = True
                current_fence_lang = lang
            else:
                # closing fence
                in_fence = False
                current_fence_lang = None
            out.append(line)
            i += 1
            continue

        if not in_fence:
            if _ARTIFACT_HEADER_RE.match(line):
                # Skip header line
                i += 1
                # Optionally skip immediate snake_case long title line(s)
                if i < len(lines) and _SNAKE_TITLE_RE.match(lines[i]):
                    i += 1
                # Also skip an empty spacer line just after
                if i < len(lines) and not lines[i].strip():
                    i += 1
                continue

        # default: keep line
        out.append(line)
        i += 1

    return "\n".join(out)

__all__ = [
    "extract_stepsjson",
    "validate_or_build",
    "sanitize_all_blocks",
    "filter_trivial_blocks",
    "unwrap_non_code_fences",
    "strip_scanned_table_artifacts",
]

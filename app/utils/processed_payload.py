"""
Utilities for storing both light overview and detailed notes inside a single
`processed_content` Text column as a compact JSON envelope.

Backward compatibility:
- If `processed_content` contains plain markdown (historical behavior), we
  treat it as the detailed notes while overview remains None.
- If it contains JSON but not our expected shape, we stringify it into the
  detailed field to avoid data loss.

Envelope shape (stringified as JSON):
{
  "v": 1,
  "overview": str | None,
  "detailed": str | None,
  "suggested_questions": list[str] | None
}
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Union


Envelope = Dict[str, Any]


def _empty() -> Envelope:
	return {"v": 1, "overview": None, "detailed": None, "suggested_questions": None}


def parse(raw: Union[Optional[str], Dict[str, Any]]) -> Envelope:
	"""Parse the raw processed_content value into an envelope.

	- None or empty -> empty envelope
	- Valid envelope JSON -> normalized envelope
	- Other JSON -> stringified as detailed
	- Plain text -> detailed markdown
	"""
	if not raw:
		return _empty()
	# If already a dict (e.g., JSON column returns python object)
	if isinstance(raw, dict):
		obj = raw
		if ("overview" in obj) or ("detailed" in obj) or ("suggested_questions" in obj):
			return {
				"v": obj.get("v", 1), 
				"overview": obj.get("overview"), 
				"detailed": obj.get("detailed"),
				"suggested_questions": obj.get("suggested_questions")
			}
		# Unknown dict shape -> stringify into detailed
		return {"v": 1, "overview": None, "detailed": json.dumps(obj, ensure_ascii=False), "suggested_questions": None}
	try:
		obj = json.loads(raw)  # type: ignore[arg-type]
		if isinstance(obj, dict) and ("overview" in obj or "detailed" in obj or "suggested_questions" in obj):
			return {
				"v": obj.get("v", 1), 
				"overview": obj.get("overview"), 
				"detailed": obj.get("detailed"),
				"suggested_questions": obj.get("suggested_questions")
			}
		# JSON but not our schema: keep it as string inside detailed
		return {"v": 1, "overview": None, "detailed": json.dumps(obj, ensure_ascii=False), "suggested_questions": None}
	except Exception:
		# Not JSON: assume it's detailed markdown
		return {"v": 1, "overview": None, "detailed": raw, "suggested_questions": None}


def dump(env: Envelope) -> str:
	"""Serialize the envelope to JSON string."""
	return json.dumps(env, ensure_ascii=False)


def get_overview(raw: Optional[str]) -> Optional[str]:
	"""Return the overview markdown from the envelope (or None)."""
	return parse(raw).get("overview")


def get_detailed(raw: Optional[str]) -> Optional[str]:
	"""Return the detailed markdown from the envelope (or None)."""
	return parse(raw).get("detailed")


def get_suggestions(raw: Union[Optional[str], Dict[str, Any]]) -> Optional[list[str]]:
	"""Return the suggested questions list from the envelope (or None)."""
	return parse(raw).get("suggested_questions")


# JSON column friendly variants (return the dict envelope directly)
def set_overview_env(raw: Union[Optional[str], Dict[str, Any]], md: str) -> Envelope:
	env = parse(raw)
	env["overview"] = md
	return env


def set_detailed_env(raw: Union[Optional[str], Dict[str, Any]], md: str) -> Envelope:
	env = parse(raw)
	env["detailed"] = md
	return env


def set_suggestions_env(raw: Union[Optional[str], Dict[str, Any]], questions: list[str]) -> Envelope:
	env = parse(raw)
	env["suggested_questions"] = questions
	return env


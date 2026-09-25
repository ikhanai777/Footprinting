"""Turn raw Algolia hits into flat rows and write them as CSV / JSON / JSONL."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from typing import Any, Dict, IO, Iterable, List, Optional

SKIP = {"_highlightResult", "_snippetResult", "_rankingInfo"}


def _localize(value: Any, lang: str) -> Any:
    # {"en": ..., "ar": ...} -> the chosen language
    if isinstance(value, dict) and ("en" in value or "ar" in value):
        return _localize(value.get(lang, value.get("en")), lang)
    # {"label": ..., "value": ...} (spec sheets) -> the value
    if isinstance(value, dict) and set(value) >= {"label", "value"}:
        return _localize(value["value"], lang)
    return value


def _scalar(value: Any) -> Any:
    if isinstance(value, list):
        if all(not isinstance(v, (dict, list)) for v in value):
            return " | ".join("" if v is None else str(v) for v in value)
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return value


def flatten(hit: Dict[str, Any], lang: str = "en", max_depth: int = 2) -> Dict[str, Any]:
    """Flatten nested hit fields into dotted keys, picking one language."""
    row: Dict[str, Any] = {}

    def walk(prefix: str, value: Any, depth: int) -> None:
        value = _localize(value, lang)
        if isinstance(value, dict) and value and depth < max_depth:
            for k, v in value.items():
                walk(f"{prefix}.{k}", v, depth + 1)
        else:
            row[prefix] = _scalar(value)

    for key, value in hit.items():
        if key not in SKIP:
            walk(key, value, 0)
    # Derived columns that every section can rely on.
    if isinstance(hit.get("added"), (int, float)):
        row["added_date"] = datetime.fromtimestamp(hit["added"], timezone.utc).strftime("%Y-%m-%d %H:%M")
    url = _localize(hit.get("absolute_url"), lang) or hit.get("permalink") or hit.get("short_url")
    if url:
        row["url"] = url
    return row


def select(row: Dict[str, Any], fields: Optional[List[str]]) -> Dict[str, Any]:
    return row if not fields else {f: row.get(f) for f in fields}


def write(hits: Iterable[Dict[str, Any]], out: IO[str], fmt: str, *, lang: str = "en",
          fields: Optional[List[str]] = None) -> int:
    """Write hits to `out`; returns the number written. CSV buffers rows to collect all columns."""
    n = 0
    if fmt == "jsonl":
        for hit in hits:
            out.write(json.dumps(hit if not fields else select(flatten(hit, lang), fields), ensure_ascii=False) + "\n")
            n += 1
        return n
    if fmt == "json":
        rows = [hit if not fields else select(flatten(hit, lang), fields) for hit in hits]
        json.dump(rows, out, ensure_ascii=False, indent=1)
        out.write("\n")
        return len(rows)
    if fmt == "csv":
        rows = [select(flatten(hit, lang), fields) for hit in hits]
        columns: List[str] = list(fields or [])
        if not fields:
            seen = set()
            for r in rows:
                for k in r:
                    if k not in seen:
                        seen.add(k)
                        columns.append(k)
        w = csv.DictWriter(out, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
        return len(rows)
    raise ValueError(f"unknown format: {fmt}")

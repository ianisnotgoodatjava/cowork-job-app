"""Remembers every verdict you have overruled, so it can never be re-derived.

The scanners infer status from email wording. When they get it wrong you say
so, and the row is fixed — but the next scan reads the same mail, draws the
same wrong conclusion, and overwrites the fix. That happened repeatedly:
Wolfe's rejection email closed four different rows across four runs, and the
same coffee-chat notes re-flagged bp and Liberty Mutual as interviews after
they had already been corrected.

The fix is not a better classifier. It is memory. A correction recorded here
is a hard override: scanners must consult `locked()` before writing, and skip
the row if the column is locked.

Rows are keyed by *content*, not row number. Row numbers shift every time the
sheet is sorted or a duplicate is removed, so a number-keyed lock would drift
onto an unrelated application within a day.

The same records double as labelled training data — see `training_examples()`.
"""

import datetime
import json
import os

from company_match import _core, normalize

STORE = os.path.join("logs", "corrections.json")

_cache: dict | None = None


def row_key(company: str, position: str) -> str:
    """Stable identity for an application, independent of its row number."""
    c = _core(company or "").replace(" ", "") or normalize(company or "").replace(" ", "")
    p = " ".join(normalize(position or "").split())
    return f"{c}|{p}"


def _load() -> dict:
    # Scan loops call locked()/is_locked() once per row, which otherwise
    # re-reads and re-parses this file from disk every time. Cached for the
    # life of the process; _save() keeps it in sync with what's written.
    global _cache
    if _cache is None:
        try:
            with open(STORE) as f:
                _cache = json.load(f)
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _save(data: dict) -> None:
    global _cache
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    with open(STORE, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    _cache = data


def record(company: str, position: str, column: str, value: str,
           reason: str = "", source: str = "you") -> None:
    """Locks `column` for this application at `value`."""
    data = _load()
    key = row_key(company, position)
    entry = data.setdefault(key, {"company": company, "position": position,
                                  "locks": {}})
    entry["company"], entry["position"] = company, position
    entry["locks"][column] = {
        "value": value,
        "reason": reason,
        "source": source,
        "date": datetime.date.today().isoformat(),
    }
    _save(data)


def unlock(company: str, position: str, column: str | None = None) -> bool:
    """Drops a lock — for when the lock itself was recorded against the wrong row."""
    data = _load()
    key = row_key(company, position)
    entry = data.get(key)
    if not entry:
        return False
    if column is None:
        data.pop(key)
    else:
        entry.get("locks", {}).pop(column, None)
        if not entry.get("locks"):
            data.pop(key)
    _save(data)
    return True


def locked(row: dict, column: str) -> str | None:
    """The value you fixed this column to, or None if it was never corrected."""
    entry = _load().get(row_key(row.get("Company", ""), row.get("Position", "")))
    if not entry:
        return None
    lock = entry.get("locks", {}).get(column)
    return lock["value"] if lock else None


def is_locked(row: dict, column: str) -> bool:
    return locked(row, column) is not None


def all_locks() -> dict:
    return _load()


def training_examples(column: str = "Status") -> list[tuple[str, str, str]]:
    """(company, position, corrected value) — labels straight from your fixes.

    These are worth more than the weak labels the models train on today, since
    they come from someone who actually knows the outcome. Small, though: a
    few dozen rows, so treat them as a high-confidence supplement rather than
    a training set on their own.
    """
    out = []
    for entry in _load().values():
        lock = entry.get("locks", {}).get(column)
        if lock:
            out.append((entry["company"], entry["position"], lock["value"]))
    return out

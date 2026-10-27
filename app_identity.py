"""One answer to "is this the same application?".

Five places had their own version of this, and they disagreed. The one that
mattered: `corrections` and `auto_draft` keyed on the position verbatim, while
`job_finder` stripped the ATS reference first. So

    U.S. Bank | Investment Portfolio Analyst
    U.S. Bank | Investment Portfolio Analyst Job ID# 2025

were the same application to one module and two to the others. That is the
exact failure `corrections` exists to prevent: lock a status while the title
still carries the requisition, let `fix_positions` clean the title, and the
lock silently stops matching — so the next scan re-applies the status the user
had already corrected.

`legacy_key` reproduces the old spelling, because there are locks and drafted
markers on disk that were written with it. Lookups try the current key first
and fall back, so nothing saved before this existed is lost.
"""

from company_match import _core, normalize


def key(company: str, position: str) -> str:
    """Identity that survives re-sorting, renumbering and ATS reference noise."""
    from dedupe import strip_req          # local: dedupe imports company_match
    c = (_core(company or "").replace(" ", "")
         or normalize(company or "").replace(" ", ""))
    p = " ".join(normalize(strip_req(position or "")).split())
    return f"{c}|{p}"


def legacy_key(company: str, position: str) -> str:
    """The pre-consolidation spelling, for reading state written under it."""
    c = (_core(company or "").replace(" ", "")
         or normalize(company or "").replace(" ", ""))
    p = " ".join(normalize(position or "").split())
    return f"{c}|{p}"


def row_key(row: dict) -> str:
    return key(row.get("Company", ""), row.get("Position", ""))


def lookup(store: dict, company: str, position: str):
    """store[key] if present, else the legacy key, else None."""
    k = key(company, position)
    if k in store:
        return store[k]
    return store.get(legacy_key(company, position))


def keys_for(company: str, position: str) -> tuple[str, str]:
    """Both spellings, for membership tests over a set of saved keys."""
    return key(company, position), legacy_key(company, position)

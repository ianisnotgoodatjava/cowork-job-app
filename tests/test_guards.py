"""Tests for the mistakes this project has actually made.

The existing suite covers reasoning. This one covers two classes of failure
that reached main anyway:

  * A refactor left `done` and `batch_outreach` undefined inside
    auto_draft.run. Both are NameErrors on a path that only runs with a
    Claude credential set, so nothing here imported it, no test touched it,
    and the suite stayed green. pyflakes finds that class in a tenth of a
    second; not running it is the only reason it shipped.

  * Classifiers that look obviously right and are not. Every case below is
    one that was observed going wrong on real data, not an invented example.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import company_formats
import people_search
import recent_contacts

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestNoUndefinedNames:
    """pyflakes over the project, as a test.

    This is the cheap half of correctness. An undefined name is a crash the
    moment that branch runs, and the branch that shipped broken was the one
    behind an API credential -- the least likely to be exercised by hand.
    """

    def test_pyflakes_is_clean(self):
        modules = [f for f in os.listdir(ROOT) if f.endswith(".py")]
        result = subprocess.run(
            [sys.executable, "-m", "pyflakes", *modules],
            cwd=ROOT, capture_output=True, text=True)
        assert result.returncode == 0, (
            "pyflakes found problems:\n" + result.stdout + result.stderr)


class TestSeniorityBand:
    """Which titles are worth writing to.

    The target is a VP or Director: a Managing Director rarely answers an
    unsolicited note and an Analyst cannot speak for the desk.
    """

    def test_vice_president_is_not_senior(self):
        # "president" matched inside "Vice President", so a VP was binned with
        # a global co-head -- the exact people this ranking exists to surface.
        assert people_search.band("Vice President - FIG & Sponsors Risk "
                                  "Solutions") == people_search.BAND_TARGET
        assert people_search.band("Senior Vice President of Institutional "
                                  "Sales Trading") == people_search.BAND_TARGET

    def test_actual_president_is_senior(self):
        assert people_search.band(
            "President, Rockefeller Global Investment Management"
        ) == people_search.BAND_SENIOR

    def test_managing_director_and_heads_are_senior(self):
        for title in ("Managing Director, Global co-Head of Financial "
                      "Institutions Group",
                      "Head of Alternative Investments",
                      "Chief Investment Officer"):
            assert people_search.band(title) == people_search.BAND_SENIOR

    def test_director_is_target_even_with_a_second_title(self):
        assert people_search.band(
            "Director of Public Equity Research, Portfolio Manager"
        ) == people_search.BAND_TARGET

    def test_analysts_and_associates_are_too_junior(self):
        assert people_search.band("Corporate Banking Analyst") == \
            people_search.BAND_JUNIOR
        assert people_search.band("Client Associate") == \
            people_search.BAND_JUNIOR


class TestSameEmployer:
    """Whether a mail domain belongs to a company.

    A wrong domain is worse than no domain: every address built on it
    bounces, and bounces are charged against the sender for later mail.
    """

    def test_shared_prefix_is_not_a_match(self):
        # AlphaSights matched alphacapital.online -- two firms that start
        # alike and are otherwise unrelated.
        assert not company_formats._same_employer("alphacapital", "alphasights")

    def test_exact_and_contained_names_match(self):
        assert company_formats._same_employer("nomura", "nomura")
        assert company_formats._same_employer("bancofcal", "bancofcalifornia")

    def test_short_labels_are_refused(self):
        # "us" in us.mufg.jp must not claim every company starting with those
        # two letters.
        assert not company_formats._same_employer("us", "usbank")


class TestRepeatContacts:
    """A second introduction inside a year reads as generated, not meant."""

    def test_first_touch_to_a_recent_contact_is_a_duplicate(self):
        recent = {"a@b.com": recent_contacts.datetime.date.today()}
        assert recent_contacts.is_duplicate(
            "a@b.com", "Recent NYU Graduate Inquiring about X at Y", recent)

    def test_follow_ups_are_not_duplicates(self):
        recent = {"a@b.com": recent_contacts.datetime.date.today()}
        for subject in ("Re: Recent NYU Graduate Inquiring about X",
                        "Following up — Research Analyst"):
            assert recent_contacts.is_duplicate("a@b.com", subject, recent) is None

    def test_own_addresses_are_never_duplicates(self):
        # 16 drafts sit addressed to these. A sweep that ignored the
        # distinction would delete them.
        for address in recent_contacts.OWN_ADDRESSES:
            recent = {address: recent_contacts.datetime.date.today()}
            assert recent_contacts.is_duplicate(
                address, "Recent NYU Graduate Inquiring about X", recent) is None

    def test_an_unknown_address_is_not_a_duplicate(self):
        assert recent_contacts.is_duplicate(
            "new@firm.com", "Recent NYU Graduate Inquiring about X", {}) is None


class TestSheetCache:
    """The row cache, and the invalidation that makes it safe.

    38 call sites ask for every row and a pipeline run walks most of them, so
    the cache is worth roughly fourteen seconds a run. The risk it introduces
    is the only thing worth testing: a read that predates a write would have
    one step decide against rows another step had already rewritten.
    """

    def _fake_sheet(self, monkeypatch, rows):
        import sheets
        calls = {"n": 0}

        def fake_service():
            class Values:
                def get(self, **kw):
                    calls["n"] += 1
                    return self

                def execute(self):
                    return {"values": rows}

            class Spreadsheets:
                def values(self):
                    return Values()

            class Svc:
                def spreadsheets(self):
                    return Spreadsheets()

            return Svc()

        monkeypatch.setattr(sheets, "_service", fake_service)
        sheets._invalidate()
        return calls

    def test_second_read_does_not_hit_the_api(self, monkeypatch):
        import sheets
        calls = self._fake_sheet(monkeypatch, [["Acme", "Analyst"]])
        sheets.list_rows()
        sheets.list_rows()
        sheets.list_rows()
        assert calls["n"] == 1

    def test_refresh_forces_a_reread(self, monkeypatch):
        import sheets
        calls = self._fake_sheet(monkeypatch, [["Acme", "Analyst"]])
        sheets.list_rows()
        sheets.list_rows(refresh=True)
        assert calls["n"] == 2

    def test_invalidate_forces_a_reread(self, monkeypatch):
        import sheets
        calls = self._fake_sheet(monkeypatch, [["Acme", "Analyst"]])
        sheets.list_rows()
        sheets._invalidate()
        sheets.list_rows()
        assert calls["n"] == 2

    def test_callers_cannot_mutate_each_others_rows(self, monkeypatch):
        # Several callers annotate the dicts they are handed. Sharing the
        # cached objects would let one step's bookkeeping appear in another
        # step's input.
        import sheets
        self._fake_sheet(monkeypatch, [["Acme", "Analyst"]])
        first = sheets.list_rows()
        first[0]["scratch"] = "x"
        assert "scratch" not in sheets.list_rows()[0]

    def test_every_writer_invalidates(self):
        # The cache is only safe while this holds, and a new writer added
        # without the call is exactly the change that would break it quietly.
        import ast
        import inspect

        import sheets
        tree = ast.parse(inspect.getsource(sheets))
        writers = {"append_job_row", "update_row", "update_rows",
                   "delete_rows", "sort_by_applied_date", "ensure_headers"}
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in writers:
                assert "_invalidate" in ast.dump(node), (
                    f"sheets.{node.name} writes without invalidating the cache")

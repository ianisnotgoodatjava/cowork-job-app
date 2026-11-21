"""Who counts as a Taiwan connection, and who the sweep checks.

The failure worth guarding against is not missing someone. It is reporting
someone whose "connection" is a guess -- a surname, Mandarin, a market they
cover -- and then writing "as a fellow Taiwanese" to a stranger who is not.
So most of these tests are refusals, and each refusal names its reason.

No model, sheet or network: the research call is the only paid part, and
everything that decides something sits on either side of it.
"""

import datetime
import os
import sys
from urllib.parse import unquote_plus

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import taiwan_signals as signals
from people import taiwan_connections as tc


def _person(evidence, **extra):
    return {"name": "Jane Doe", "title": "Analyst", "evidence": evidence,
            "source": "https://example.com/profile", **extra}


class TestWhatCounts:
    @pytest.mark.parametrize("evidence, kind", [
        ("B.B.A. Finance, National Taiwan University", signals.SCHOOL),
        ("National Chengchi University, M.S. Finance", signals.SCHOOL),
        ("National Tsing Hua University, B.S.", signals.SCHOOL),
        ("Taipei American School, class of 2012", signals.SCHOOL),
        ("Languages: Mandarin (native), Taiwanese (native)", signals.LANGUAGE),
        ("Member, Taiwanese American Professionals - New York",
         signals.ASSOCIATION),
        ("President, Columbia Taiwanese Student Association",
         signals.ASSOCIATION),
        ("Taiwanese-American; grew up in New Jersey", signals.IDENTITY),
        ("Previously an associate at Fubon Securities", signals.EMPLOYER),
        ("Former analyst at Bank of Taiwan", signals.EMPLOYER),
        ("Based in Taipei", signals.PLACE),
    ])
    def test_stated_connections_pass(self, evidence, kind):
        assert signals.check(_person(evidence)) == ""
        assert signals.kind(evidence) == kind

    def test_a_language_list_is_not_read_as_a_group(self):
        """Both contain the word "Taiwanese"; only one is a club."""
        assert signals.kind("Languages: English, Taiwanese") == signals.LANGUAGE

    def test_coverage_next_to_a_real_school_keeps_the_school(self):
        evidence = "Covers Taiwan tech; BBA, National Taiwan University"
        assert signals.check(_person(evidence)) == ""
        assert signals.kind(evidence) == signals.SCHOOL


class TestWhatIsRefused:
    @pytest.mark.parametrize("evidence, reason", [
        ("Surname Chen is common in Taiwan", "inferred"),
        ("Likely Taiwanese given the name", "inferred"),
        ("Name suggests a Taiwanese background", "inferred"),
        ("Speaks Mandarin", "names no Taiwanese"),
        ("Covers Taiwan and Korea semiconductors", "market they cover"),
        ("M.S., Tsinghua University", "names no Taiwanese"),
        ("Sun Yat-sen University, Guangzhou", "names no Taiwanese"),
        ("NTU Singapore", "names no Taiwanese"),
    ])
    def test_guesses_and_namesakes_are_refused_with_a_reason(self, evidence,
                                                             reason):
        assert reason in signals.check(_person(evidence))

    def test_no_source_no_report(self):
        assert "source" in signals.check(
            _person("National Taiwan University", source=""))

    def test_one_word_name_is_not_a_person(self):
        assert signals.check({**_person("National Taiwan University"),
                              "name": "Jane"}) == "no full name"


RESPONSE = """Some preamble the model wrote.

=== PERSON ===
NAME: Alex Example
TITLE: Associate, Investor Relations
TEAM: Investor Relations
LOCATION: New York
PROFILE_URL: UNKNOWN
TAIWAN_EVIDENCE: B.A., National Chengchi University
TAIWAN_SOURCE: https://example.com/a
RELEVANCE: elsewhere at the firm
WHY_RELEVANT: different division

=== PERSON ===
NAME: Sam Example
TITLE: Vice President, Structured Finance Credit
TEAM: Structured Finance
LOCATION: New York
PROFILE_URL: https://example.com/sam
TAIWAN_EVIDENCE: Based in Taipei for six years
TAIWAN_SOURCE: https://example.com/sam
RELEVANCE: same team
WHY_RELEVANT: rates the deals this role analyzes

=== PERSON ===
NAME: Guess Example
TITLE: Analyst, Structured Finance
TEAM: Structured Finance
TAIWAN_EVIDENCE: surname is common in Taiwan
TAIWAN_SOURCE: https://example.com/g
RELEVANCE: same team
"""


class TestReadingTheResearch:
    def test_blocks_parse_and_unknown_is_empty(self):
        people = signals.parse(RESPONSE)
        assert [p["name"] for p in people] == [
            "Alex Example", "Sam Example", "Guess Example"]
        assert people[0]["profile_url"] == ""
        assert people[0]["relevance"] == signals.ELSEWHERE
        assert people[1]["relevance"] == signals.SAME_TEAM

    def test_screen_ranks_nearest_the_role_and_refuses_the_guess(self):
        kept, refused = tc.screen(signals.parse(RESPONSE),
                                  role="Structured Finance Credit Analyst")
        # A city on the desk outranks a school in another division.
        assert [p["name"] for p in kept] == ["Sam Example", "Alex Example"]
        assert kept[0]["kind"] == signals.PLACE
        assert [(p["name"], p["refused"]) for p in refused] == [
            ("Guess Example", "connection inferred, not stated")]

    def test_the_same_person_twice_is_reported_once(self):
        people = signals.parse(RESPONSE)[:2] * 2
        kept, refused = tc.screen(people, role="")
        assert len(kept) == 2
        assert {p["refused"] for p in refused} == {"listed twice"}

    def test_none_block_parses_to_nobody(self):
        assert signals.parse("=== NONE ===\nlooked at LinkedIn") == []


class TestSearchLinks:
    def test_role_words_drop_seniority_and_place(self):
        assert signals.role_terms(
            "Senior Credit Analyst - Structured Finance, New York") == [
            "credit", "structured", "finance"]

    def test_linkedin_search_quotes_the_company_and_names_the_desk(self):
        (label, url), (wide_label, wide) = signals.linkedin_urls(
            "Moody's", "Credit Analyst")
        assert label == "near the role"
        assert url.startswith("https://www.linkedin.com/search/results/people/")
        keywords = unquote_plus(url.split("keywords=")[1])
        assert keywords.startswith('"Moody\'s" AND (')
        assert '"National Taiwan University"' in keywords
        assert keywords.endswith("AND (credit)")
        assert "credit" not in unquote_plus(wide.split("keywords=")[1])

    def test_no_role_gives_only_the_wide_search(self):
        assert [l for l, _ in signals.linkedin_urls("Moody's")] == [
            "anywhere at the firm"]

    def test_web_query_stays_under_googles_word_limit(self):
        query = signals.xray_query("Sumitomo Mitsui Banking Corporation",
                                   "Senior Credit Risk Analyst, Leveraged "
                                   "Finance")
        assert query.startswith('site:linkedin.com/in "Sumitomo')
        assert len(query.split()) <= 32


TODAY = datetime.date(2026, 11, 20)


def _row(company, position="Analyst", applied="2026-11-18", status="applied"):
    return {"Company": company, "Position": position,
            "Applied Draes": applied, "Status": status}


class TestWhichApplicationsTheSweepChecks:
    def test_recent_open_applications_one_per_firm(self):
        rows = [_row("Fitch Ratings", "Credit Analyst"),
                _row("Fitch Ratings", "Associate Director"),
                _row("Jefferies", applied="2026-11-19"),
                _row("Old Co", applied="2026-09-01"),
                _row("Said No", status="rejected"),
                _row("Onyx Staffing")]
        picked = tc.pending(rows, days_back=14, store={}, today=TODAY)
        assert [r["Company"] for r in picked] == ["Jefferies", "Fitch Ratings"]

    def test_an_application_already_checked_is_not_paid_for_twice(self):
        from core import app_identity
        store = {app_identity.key("Jefferies", "Analyst"): {
            "checked": "2026-01-01", "company_key": "unused"}}
        assert tc.pending([_row("Jefferies")], store=store, today=TODAY) == []

    def test_a_firm_checked_this_month_for_another_role_waits(self):
        from core import company_id
        store = {"x": {"checked": "2026-11-10",
                       "company_key": company_id.key("Jefferies")}}
        assert tc.pending([_row("Jefferies", "Other Role")], store=store,
                          today=TODAY) == []

    def test_but_not_forever(self):
        from core import company_id
        store = {"x": {"checked": "2026-08-01",
                       "company_key": company_id.key("Jefferies")}}
        assert len(tc.pending([_row("Jefferies", "Other Role")], store=store,
                              today=TODAY)) == 1

    def test_limit(self):
        rows = [_row(f"Firm {c}") for c in "ABCDEFG"]
        assert len(tc.pending(rows, limit=3, store={}, today=TODAY)) == 3

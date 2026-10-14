"""Tests for the parts that decide something.

Scope is deliberate: the pure logic, with no Gmail client, Google Sheet or
Claude credential involved. Those paths are exercised by running the tool;
what is worth pinning down here is the reasoning -- how an address is
trusted, what counts as a follow-up, how a posting becomes a draft.

Several of these encode a bug that was actually shipped. The address
confidence tests are the clearest: the obvious implementation reads the word
"inferred" in the confidence note and gets the answer exactly backwards.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import date_utils
import followup_model
import stage_drafts


class TestAddressConfidence:
    """How much an email address can be trusted, read off the research note.

    Three tiers, strongest first: mail already delivered to that domain
    pattern, an address the firm publishes, and a pattern guess. A bounce
    burns the contact, so the distinction has to survive the prose.
    """

    def test_delivery_history_is_the_strongest_signal(self):
        contact = {
            "verdict": "matches the domain's pattern (first.last); 12 delivered",
            "confidence": "the address is INFERRED from Fitch's pattern",
        }
        assert stage_drafts.address_confidence(contact) == "12 delivered"

    def test_delivery_history_outranks_published_wording(self):
        """Evidence beats assertion: the note says published, the domain says
        delivered, and delivered is the one backed by something that happened."""
        contact = {
            "verdict": "pattern matches; 4 delivered",
            "confidence": "Published on the firm's staff page; not inferred.",
        }
        assert stage_drafts.address_confidence(contact) == "4 delivered"

    def test_not_inferred_means_published(self):
        """'not inferred' contains 'inferred'. A substring test inverts this."""
        contact = {
            "verdict": "UNKNOWN - no history for this domain",
            "confidence": ("Published on Douglas Elliman's own executive contact "
                           "page, listed with a direct line; not inferred."),
        }
        assert stage_drafts.address_confidence(contact) == "published"

    def test_published_name_with_inferred_address_is_inferred(self):
        """The mirror image: 'published' here describes the name, not the
        address, and the address is the thing being judged."""
        contact = {
            "verdict": "UNKNOWN - no history for this domain",
            "confidence": ("Name and title are published (quoted in Asset "
                           "Securitization Report); the address is INFERRED "
                           "from the firstname.lastname pattern."),
        }
        assert stage_drafts.address_confidence(contact) == "inferred"

    def test_plain_guess_is_inferred(self):
        contact = {
            "verdict": "UNKNOWN - no history for this domain",
            "confidence": "Inferred from the standard first-initial pattern; "
                          "not published, so treat as unverified.",
        }
        assert stage_drafts.address_confidence(contact) == "inferred"

    def test_missing_fields_do_not_raise(self):
        assert stage_drafts.address_confidence({}) == "inferred"


class TestParseResearchFile:
    """Reading back what auto_draft saved, so a contact can be staged without
    paying to research it a second time."""

    FILE = """Company:    Example Corp  (sheet row 42)
Role:       Analyst
Job link:   https://example.com/jobs/1

Sources:
https://example.com/team


CONTACT 1 OF 1
To:         jane.doe@example.com
Person:     Jane Doe — Senior Analyst
Confidence: Published on the firm's team page; not inferred.
Address:    UNKNOWN — no history for this domain
Subject:    Recent NYU Graduate Inquiring about Credit at Example Corp

----------------------------------------------------------------------
Dear Jane,

A short body.

Best regards,
Ian
----------------------------------------------------------------------
"""

    def _parsed(self, tmp_path):
        path = tmp_path / "042_example-corp.txt"
        path.write_text(self.FILE, encoding="utf-8")
        return stage_drafts.parse(str(path))

    def test_reads_company_and_row(self, tmp_path):
        parsed = self._parsed(tmp_path)
        assert parsed["company"] == "Example Corp"
        assert parsed["row_number"] == 42

    def test_reads_the_contact(self, tmp_path):
        contact = self._parsed(tmp_path)["contacts"][0]
        assert contact["addr"] == "jane.doe@example.com"
        assert contact["name"] == "Jane Doe"
        assert contact["title"] == "Senior Analyst"

    def test_body_excludes_the_rule_lines(self, tmp_path):
        body = self._parsed(tmp_path)["contacts"][0]["body"]
        assert body.startswith("Dear Jane,")
        assert "-----" not in body

    def test_a_file_with_no_contact_is_not_a_result(self, tmp_path):
        path = tmp_path / "skipped.txt"
        path.write_text("Company:    Nobody Inc  (sheet row 7)\nRole: Analyst\n")
        assert stage_drafts.parse(str(path)) is None


class TestLocateRow:
    """The row number in a saved file is a position, not an identity.

    Sorting the sheet renumbers every row beneath an insert, so research
    saved in September and staged in October once wrote one company's
    contact onto another's row.
    """

    @staticmethod
    def _slug(name):
        return (name or "").lower().replace(" ", "").replace(",", "")

    def test_stored_number_is_used_when_it_still_matches(self):
        row = {"row_number": 42, "Company": "Example Corp", "Contact": ""}
        found = stage_drafts._locate(
            {"row_number": 42, "company": "Example Corp"},
            {42: row}, {self._slug("Example Corp"): [row]}, self._slug)
        assert found is row

    def test_company_wins_when_the_row_moved(self):
        moved = {"row_number": 99, "Company": "Example Corp", "Contact": ""}
        other = {"row_number": 42, "Company": "Unrelated Inc", "Contact": ""}
        found = stage_drafts._locate(
            {"row_number": 42, "company": "Example Corp"},
            {42: other, 99: moved},
            {self._slug("Example Corp"): [moved],
             self._slug("Unrelated Inc"): [other]}, self._slug)
        assert found is moved, "must not write onto whoever holds row 42 now"

    def test_several_rows_for_one_company_fill_one_at_a_time(self):
        taken = {"row_number": 1, "Company": "Fitch", "Contact": "Someone <a@b.c>"}
        empty = {"row_number": 2, "Company": "Fitch", "Contact": ""}
        found = stage_drafts._locate(
            {"row_number": 500, "company": "Fitch"},
            {}, {self._slug("Fitch"): [taken, empty]}, self._slug)
        assert found is empty

    def test_missing_company_returns_nothing(self):
        assert stage_drafts._locate(
            {"row_number": 1, "company": "Gone Ltd"}, {}, {}, self._slug) is None


class TestParseDate:
    """The sheet's date column was typed by hand over a year, in whatever
    format was convenient that day."""

    def test_full_date(self):
        assert date_utils.parse_date("6/27/2025").isoformat() == "2025-06-27"

    def test_iso(self):
        assert date_utils.parse_date("2025-06-27").isoformat() == "2025-06-27"

    def test_bare_month_day_infers_a_year(self):
        """A lot of rows are just '6/27'."""
        parsed = date_utils.parse_date("6/27")
        assert parsed is not None and (parsed.month, parsed.day) == (6, 27)

    def test_bare_date_never_lands_in_the_future(self):
        """December typed in January means last December, not next."""
        import datetime
        today = datetime.date.today()
        for value in (f"{today.month}/{today.day}", "12/31", "1/1"):
            assert date_utils.parse_date(value) <= today, value

    def test_leap_day_without_a_year(self):
        """2/29 can only be resolved if this year or last was a leap year.
        None is the honest answer otherwise -- better than silently moving it
        to the 28th."""
        import datetime
        today = datetime.date.today()
        leap = any((y % 4 == 0 and y % 100 != 0) or y % 400 == 0
                   for y in (today.year, today.year - 1))
        assert (date_utils.parse_date("2/29") is not None) == leap

    def test_blank_and_junk_are_none(self):
        for value in ("", "   ", None, "not a date", "13/45/2025"):
            assert date_utils.parse_date(value) is None, value


class TestStripQuote:
    """A reply carries the message it answers. Training or measuring on that
    text would be learning the other person's words, not his."""

    def test_removes_the_quoted_tail(self):
        body = ("Hello Dan,\n\nShort note.\n\n"
                "On Wed, Aug 26, 2026 at 9:31 AM Dan R <dan@x.com> wrote:\n"
                "> their original message")
        out = followup_model._strip_quote(body)
        assert "their original message" not in out
        assert out.startswith("Hello Dan,")

    def test_leaves_an_unquoted_body_alone(self):
        body = "Hello Dan,\n\nJust following up on my note."
        assert followup_model._strip_quote(body) == body

    def test_handles_empty(self):
        assert followup_model._strip_quote("") == ""
        assert followup_model._strip_quote(None) == ""

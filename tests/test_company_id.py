"""Tests for company identity.

Every case here is a pair that appeared on the real sheet, and half of them
are pairs the previous code got wrong. The expensive direction is a false
merge: two companies treated as one means an email addressed to the wrong
firm, or a draft deleted because the other one closed.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import company_id


class TestKey:
    def test_short_names_are_not_truncated(self):
        # _core("S&P Global") was "sp" -- two characters, matching hundreds of
        # firms, and the reason prefix lookups went astray.
        assert company_id.key("S&P Global") == "s&pglobal"
        assert len(company_id.key("S&P Global")) > 3

    def test_case_and_punctuation_do_not_change_identity(self):
        assert company_id.key("nomura") == company_id.key("Nomura")
        assert company_id.key("Intercontinental Exchange, Inc") == \
            company_id.key("Intercontinental Exchange, Inc.")
        assert company_id.key("WellsFargo") == company_id.key("Wells Fargo")

    def test_legal_suffixes_are_dropped(self):
        assert company_id.key("Stagwell Global LLC") == \
            company_id.key("Stagwell Global, LLC")

    def test_empty_input_gives_no_identity(self):
        assert company_id.key("") == ""
        assert company_id.key("   ") == ""
        assert company_id.key(",,,") == ""


class TestSameEmployer:
    def test_ats_tenant_names_resolve_to_the_employer(self):
        # "Moody's WorkWise" is Moody's Workday instance. Logged as a separate
        # company it produced a duplicate application row.
        assert company_id.same("Moody's WorkWise", "Moody's")
        assert company_id.same("msigusa", "MSIG USA")

    def test_divisional_names_that_are_the_same_firm(self):
        for a, b in [("Scale", "Scale Microgrids"),
                     ("Palmetto", "Palmetto Clean Technology"),
                     ("Fanatics", "Fanatics Betting & Gaming"),
                     ("Tomo", "Tomo Mortgage"),
                     ("The Mather Group, LLC", "The Mather Group")]:
            assert company_id.same(a, b), f"{a} should equal {b}"

    def test_names_that_merely_start_alike_are_different(self):
        # Each of these shares a leading word and is a different employer.
        # The old prefix rule merged them, which sent an email meant for
        # AlphaSights to alphacapital.online.
        for a, b in [("AlphaSights", "AlphaCapital"),
                     ("Bank of America", "Bank of New York Mellon"),
                     ("Russell Investments", "Russell Tobin"),
                     ("Sumitomo Mitsui Banking Corporation",
                      "Sumitomo Mitsui Trust"),
                     ("New Fortress Energy", "New Street Research"),
                     ("Matthews Asia", "Matthews Real Estate"),
                     ("First Citizens Bank", "First Horizon"),
                     ("AXA GROUP", "AXA XL")]:
            assert not company_id.same(a, b), f"{a} must not equal {b}"

    def test_a_two_letter_company_matches_itself(self):
        # A length floor inside same() meant BP could never equal BP.
        assert company_id.same("BP", "bp")
        assert not company_id.same("BP", "BPCE")

    def test_nothing_matches_an_empty_name(self):
        assert not company_id.same("", "")
        assert not company_id.same("Nomura", "")


class TestReliability:
    def test_three_letter_keys_are_flagged_as_weak(self):
        # Real companies, but matching on three characters alone is poor
        # evidence -- the caller needs to know that before using it to pick a
        # mail domain.
        for name in ("AIG", "BNY", "BMO", "FRB"):
            assert not company_id.is_reliable(name)

    def test_longer_names_are_reliable(self):
        for name in ("Nomura", "S&P Global", "Rockefeller Capital Management"):
            assert company_id.is_reliable(name)


class TestProposals:
    def test_proposals_are_suggestions_not_merges(self):
        names = ["Russell Investments", "Russell Tobin", "Russell"]
        pairs = company_id.propose_aliases(names)
        # It may suggest them, but it must not have merged them.
        assert not company_id.same("Russell Investments", "Russell Tobin")
        assert all(isinstance(p, tuple) and len(p) == 2 for p in pairs)


class TestAliasTableIntegrity:
    def test_every_alias_key_is_reachable(self):
        # A wrong alias key fails silently -- it simply never fires. One did:
        # "fanaticsbettinggaming" could not match, because folding keeps "&"
        # and the real key is "fanaticsbetting&gaming".
        assert company_id._check_aliases() == []

    def test_aliases_resolve_to_a_real_identity(self):
        for variant, canonical in company_id.ALIASES.items():
            assert company_id.key(canonical), f"{canonical!r} has no key"


class TestStatusCarryingVariants:
    """Spellings that carry different statuses on the sheet.

    These are the costly ones. When two spellings of one employer key apart,
    the closed one speaks for the whole company and draft_cleanup deletes the
    drafts belonging to the live role. "Flagstar Bank" was interview-rejected
    while "Flagstar" was interviewing, and a dry run queued both Flagstar
    drafts for deletion.
    """

    def test_variants_that_must_merge(self):
        for a, b in [("Flagstar Bank", "Flagstar"),
                     ("OTC FLOW U.S.", "OTC Flow"),
                     ("Virtus Investment Partners", "Virtus")]:
            assert company_id.same(a, b), f"{a} must equal {b}"

    def test_variants_deliberately_kept_apart(self):
        # A rejection by the asset manager must not close the employer Ian
        # works for, and "Horizon" alone is too generic to claim.
        assert not company_id.same("AXA XL Investment Management", "AXA XL")

"""The parser's first offline test.

Every case below is a confirmation email that was once read wrongly and put a
bad row on the sheet, or was read as nothing and cost an application that had
to be logged by hand. They are written as messages rather than as regex
assertions, because what matters is what the mailbox produces, not which of
the forty patterns happens to catch it.

The real corpus — a frozen pull of every confirmation in the mailbox — lives
in tests/fixtures/confirmations.json and is deliberately not committed: it is
two years of one person's job search, and this repository is public.
test_corpus_if_present picks it up when it is there and skips when it is not,
so the measured numbers are reproducible on the machine that has it without
the history leaving it.
"""

import json
import os
import unittest

from core import confirmation_parser as cp

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
CORPUS = os.path.join(FIXTURES, "confirmations.json")


def msg(subject, sender, body=""):
    return {"subject": subject, "from": sender, "body": body,
            "date": "Wed, 19 Nov 2026 09:00:00 -0500"}


class TestNotApplications(unittest.TestCase):
    """Mail that matched the search and records no application.

    These are the parser working. Counting them as failures is what made the
    old figure — 27% of the mailbox "unparsed" — look like an unfixable mess
    rather than a short list of real bugs.
    """

    def test_job_alert_is_noise(self):
        out = cp.parse(msg("New job openings at Group One Trading",
                           "LinkedIn <jobs-noreply@linkedin.com>"))
        self.assertFalse(out)
        self.assertEqual(out.reason, "noise")

    def test_account_signup_is_not_an_application(self):
        # Phillips 66's "Welcome! Thank you for Creating an Account" closes
        # with "thank you for your interest in Phillips 66" and went onto the
        # sheet as an application under the ATS's own sender name, "ATS_P66".
        out = cp.parse(msg("Welcome! Thank you for Creating an Account",
                           "ATS_P66 <noreply@p66.com>"))
        self.assertFalse(out)
        self.assertEqual(out.reason, "not-an-application")

    def test_talent_community_is_not_an_application(self):
        # Caught by the noise gate rather than the signup gate — the two
        # overlap on this wording, and which one fires only changes the
        # label. What must not happen is a row.
        out = cp.parse(msg("Thanks for joining our Talent Community",
                           "Acme Careers <careers@acme.com>"))
        self.assertIn(out.reason, ("noise", "not-an-application"))

    def test_every_reason_is_documented(self):
        """A Skipped whose reason has no entry in REASONS crashes the scan.

        scan() prints REASONS[reason] for each bucket, so a new reason added
        without its explanation is a KeyError in the nightly run.
        """
        import re
        source = open(cp.__file__).read()
        used = set(re.findall(r'Skipped\(\s*"([a-z-]+)"', source))
        self.assertTrue(used)
        self.assertEqual(used - set(cp.REASONS), set())


class TestCompanyFromSubject(unittest.TestCase):
    def test_linkedin_easy_apply(self):
        # LinkedIn's own receipt names the company and never the role.
        out = cp.parse(msg("Ian, your application was sent to Adore Me",
                           "LinkedIn <jobs-noreply@linkedin.com>"))
        self.assertEqual(out.company, "Adore Me")

    def test_role_at_company(self):
        out = cp.parse(msg("Your application to Credit Analyst at Barings",
                           "Barings <noreply@barings.com>"))
        self.assertEqual((out.company, out.role), ("Barings", "Credit Analyst"))

    def test_role_is_never_stored_as_the_company(self):
        # "Analyst, Fixed Income Pricing" is a role. A role in the Company
        # column is worse than no row, because you have to find it to fix it.
        # The generic rules therefore refuse "interest in X" outright; only a
        # parser that already knows the employer may read it (see TestWorkday).
        out = cp.parse(msg("Thank you for your interest in Analyst, Fixed Income Pricing",
                           "Fixed Income Pricing <careers@fipricing.com>"))
        self.assertNotIn("Analyst", out.company)

    def test_requisition_id_is_not_a_company(self):
        # A recruiter's own gmail tells you nothing, so there is no domain to
        # fall back to and the subject is all there is.
        out = cp.parse(msg("Application for 10073106-WD Credit Analyst",
                           "Jane Doe <jane@gmail.com>"))
        self.assertFalse(out)
        self.assertEqual(out.reason, "no-company")

    def test_workday_requisition_code_comes_off_the_role(self):
        # "10073106-WD Credit Analyst": the digits were stripped and the
        # orphaned ATS code left behind, so two MUFG credit roles sit on the
        # sheet today as "WD Credit Analyst ..." and "WD Credit Portfolio
        # Management Analyst ...". role_extract's own requisition rule looks
        # for the id and the code together, so once the id had gone it could
        # no longer see the code.
        out = cp.parse(msg("Application for 10073106-WD Credit Analyst",
                           "Barings Careers <careers@barings.com>"))
        self.assertEqual(out.role, "Credit Analyst")

    def test_trailing_requisition_noise_is_trimmed(self):
        out = cp.parse(msg("Your application to iCapital - Application Received",
                           "noreply@mailer.example.net"))
        self.assertEqual(out.company, "iCapital")


class TestCompanyFromSender(unittest.TestCase):
    """Most confirmations say "Thank you for applying!" and nothing else.

    Throwing the display name away is what left ~350 emails unscanned.
    """

    def test_careers_suffix_is_stripped(self):
        out = cp.parse(msg("Thank you for applying!",
                           "Ramirez Asset Management Careers <careers@ramirezco.com>"))
        self.assertEqual(out.company, "Ramirez Asset Management")

    def test_hr_system_glued_to_the_name(self):
        # "MUFGUBHR" loses the "HR" and leaves "MUFGUB" — UB is Union Bank,
        # part of the old MUFG Union Bank name, not platform noise.
        out = cp.parse(msg("Thank you for your application",
                           "MUFGUBHR <mufgubhr@myworkday.com>"))
        self.assertEqual(out.company, "MUFG")

    def test_two_suffixes_need_two_passes(self):
        # "Nomura HR SuccessFactors" loses SuccessFactors first; only then is
        # "HR" at the end for the rule to see. One pass left "Nomura HR".
        out = cp.parse(msg("New Application Received",
                           "Nomura HR SuccessFactors <no-reply@successfactors.com>"))
        self.assertEqual(out.company, "Nomura")

    def test_icims_platform_tail(self):
        # iCIMS sends as "Rockefeller Capital Management  @ icims". Stripping
        # "icims" alone left a dangling "@", which the final guard rejected,
        # and the whole application was dropped on that one character.
        out = cp.parse(msg("Thank you for your application",
                           "Rockefeller Capital Management  @ icims <x@talent.icims.com>"))
        self.assertEqual(out.company, "Rockefeller Capital Management")

    def test_recruiter_name_is_not_a_company(self):
        # Writing the recruiter's name into the Company column produced rows
        # called "Jane Roe" and "Doe, John".
        out = cp.parse(msg("Thank you for applying",
                           "Doe, John <jdoe@gmail.com>"))
        self.assertFalse(out)

    def test_initials_domain_corroborates_a_two_word_name(self):
        # "Goldman Sachs" and "Jane Roe" look identical to the person test;
        # the domain settles it.
        out = cp.parse(msg("Thank you for applying",
                           "Goldman Sachs <careers@gs.com>"))
        self.assertEqual(out.company, "Goldman Sachs")

    def test_bracketed_employer(self):
        out = cp.parse(msg("Thank you for applying",
                           "Jane Doe [DRIEHAUS CAPITAL MANAGEMENT] <j@driehaus.com>"))
        self.assertIn("DRIEHAUS", out.company.upper())

    def test_vendor_display_name_is_not_the_employer(self):
        # saashr.com is the UKG Ready host and reached the sheet as the
        # employer "saashr" for a SIFMA application.
        out = cp.parse(msg("Thank you for your application",
                           "Workday <noreply@saashr.com>"))
        self.assertFalse(out)

    def test_leftover_word_is_not_a_company(self):
        # A sender called "bp Global" left "Global" once the org-token logic
        # ran, and "Global" reached the sheet as a company.
        out = cp.parse(msg("Thank you for applying", "Global <x@mailer.example.net>"))
        self.assertFalse(out)
        self.assertEqual(out.reason, "company-blocklisted")


class TestWorkday(unittest.TestCase):
    """Workday was the worst source in the mailbox — 158 of the 247 unreadable
    confirmations came from it alone — because it leaves the subject to each
    employer and writes the role in the body."""

    def test_workday_at_company_display_name(self):
        # The generic rules refuse "interest in X" — X is a role as often as a
        # company. Here the display name already names the employer, so X can
        # only be the role.
        out = cp.parse(msg(
            "Thank you for your interest in Rating Analyst – Structured Finance, Colorado",
            '"Workday at S&P Global" <spgi@myworkday.com>'))
        self.assertEqual(out.company, "S&P Global")
        self.assertIn("Rating Analyst", out.role)
        # Reached through the tenant table rather than the display name,
        # because the sheet's own spelling of a company beats every other
        # source. Either route is correct; what matters is that a sender-aware
        # rule handled it at all.
        self.assertTrue(out.parser.startswith("ats:"), out.parser)

    def test_status_change_subject(self):
        out = cp.parse(msg(
            "Status change for your CLO Portfolio Analyst - ONSITE Carmel, IN "
            "application at CNO Financial Group",
            "No_Replies_CNO_Workday_Notification CNOinc <cnoinc@myworkday.com>"))
        self.assertEqual(out.company, "CNO Financial Group")
        self.assertIn("CLO Portfolio Analyst", out.role)

    def test_company_repeated_in_subject_is_not_a_role(self):
        # "interest in MUFG Bank, Ltd." names the employer again. Reading it
        # as the role put "MUFG Bank Ltd" in the Position column.
        out = cp.parse(msg(
            "Thank you for your interest in MUFG Bank, Ltd. : "
            "10078709-WD Corporate Banking Analyst – Financial Institutions Group",
            "MUFGUBHR <mufgubhr@myworkday.com>"))
        self.assertEqual(out.company, "MUFG")
        self.assertNotIn("MUFG", out.role)
        self.assertIn("Corporate Banking Analyst", out.role)

    def test_falls_through_when_the_sender_names_nothing(self):
        # A Workday sender with no readable employer must still reach the
        # generic rules rather than being refused outright.
        out = cp.parse(msg("Your application to Trading Analyst at Jane Street",
                           "Workday <noreply@myworkday.com>"))
        self.assertEqual(out.company, "Jane Street")


class TestVendorSenders(unittest.TestCase):
    """What the frozen corpus taught, pinned.

    Each of these was a real confirmation that named its employer plainly
    somewhere in the message and still produced no row, or produced a wrong
    one. Together they were 226 of the 1437 emails in the mailbox.
    """

    def test_workday_tenant_is_the_employer(self):
        # The local part of a myworkday.com address is the employer's Workday
        # tenant. It was the only thing identifying 158 unread confirmations,
        # and the old rules threw it away twice over: the display name was
        # rejected for containing "@", and the domain for being a vendor's.
        out = cp.parse(msg("Thank You for Your Application!",
                           "pipersandler@myworkday.com <pipersandler@myworkday.com>",
                           "Thank you for applying to Piper Sandler."))
        self.assertEqual(out.company, "Piper Sandler")

    def test_display_name_that_is_only_the_address_says_nothing(self):
        # Taking "conning@myworkday.com" apart for words gave the company
        # "Conning Com", and "no-reply@us.greenhouse-mail.io" gave "Us - Io"
        # on 28 emails.
        out = cp.parse(msg("Thank you for your application",
                           "conning@myworkday.com <conning@myworkday.com>",
                           "Thank you for applying to Conning."))
        self.assertEqual(out.company, "Conning")

    def test_hr_suffix_comes_off_the_display_name(self):
        # The pattern read `hris?`, which matches "hri" and "hris" but never
        # "HR". Ten JLL confirmations were logged under the company "JLL HR".
        out = cp.parse(msg("Thank You for Your Application!",
                           "JLL HR <jll@myworkday.com>"))
        self.assertEqual(out.company, "JLL")

    def test_underscores_and_glued_words(self):
        # "Deutsche_Bank_Workday-do-not-reply" named Deutsche Bank in its
        # display name through nine unread confirmations.
        out = cp.parse(msg("Thank You for Your Application!",
                           "Deutsche_Bank_Workday-do-not-reply <db@myworkday.com>"))
        self.assertEqual(out.company, "Deutsche Bank")

    def test_run_together_name_is_split_but_a_spaced_one_is_not(self):
        # "WellsFargoHR" has to be split to be read; "CoBank Recruiting" must
        # not be, and splitting it produced the company "Co Bank".
        self.assertEqual(cp._split_camel("WellsFargoHR"), "Wells Fargo HR")
        self.assertEqual(cp._split_camel("CoBank Recruiting"), "CoBank Recruiting")
        self.assertEqual(cp._split_camel("MUFGUBHR"), "MUFGUBHR")

    def test_explicit_subject_beats_a_noisy_display_name(self):
        # "No_Replies_CNO_Workday_Notification CNOinc" survives every guard
        # and becomes a 34-character company. The same message ends
        # "...application at CNO Financial Group".
        out = cp.parse(msg(
            "Status change for your CLO Portfolio Analyst application "
            "at CNO Financial Group",
            "No_Replies_CNO_Workday_Notification CNOinc <cnoinc@myworkday.com>"))
        self.assertEqual(out.company, "CNO Financial Group")

    def test_vendor_account_number_is_not_a_company(self):
        # "u1821112.wl.sendgrid.net" reached the sheet as the company
        # "U1821112".
        self.assertEqual(
            cp._employer_from_subdomain(
                cp._sender("X <noreply@u1821112.wl.sendgrid.net>")), "")

    def test_opaque_vendor_plumbing_yields_no_employer(self):
        # Oracle Recruiting gives each pod a four-letter code and sends as
        # "exvv.fa.sender"; Paylocity appends a random token to "recruiting".
        # Both became companies: "Exvvfasender", "Recruiting27Koy7Oan1L". A
        # miss is better than a row nobody can act on.
        for local in ("exvv.fa.sender", "recruiting+27koy7oan1l"):
            self.assertEqual(
                cp._employer_slug(cp._sender(f"X <{local}@mail.paylocity.com>")),
                "", local)

    def test_oracle_recruiting_names_nobody(self):
        # End to end: the display name is the address, the local part is a
        # pod code, the domain is Oracle's. Six confirmations, no employer
        # anywhere in the headers.
        out = cp.parse(msg(
            "Job Application Update",
            "exvv.fa.sender@workflow.mail.us2.cloud.oracle.com "
            "<exvv.fa.sender@workflow.mail.us2.cloud.oracle.com>"))
        self.assertFalse(out)
        self.assertEqual(out.reason, "no-company")

    def test_a_leading_digit_can_be_part_of_a_name(self):
        # "9fin" and "1823 Partners" are firms. Refusing anything starting
        # with a digit meant both applications were logged by hand.
        out = cp.parse(msg("Confirmation of your application to 9fin",
                           "9fin <careers@9fin.com>"))
        self.assertEqual(out.company, "9fin")

    def test_a_rejected_subject_company_falls_back_to_the_sender(self):
        # "Thank you for applying to the 2026 Public Fixed Income Analyst
        # Program" captures a requisition-shaped company, and the parse ended
        # there — even though the sender was metlifecareers.com all along.
        out = cp.parse(msg(
            "Thank you for applying to the 2026 Public Fixed Income Analyst Program",
            "MetLife Careers <noreply@metlifecareers.com>"))
        self.assertTrue(out, out)
        self.assertIn("metlife", out.company.lower())

    def test_the_role_is_never_the_employer(self):
        # Once the employer is known, "your interest in X" is read as the
        # role. On four messages X was the employer written out in full, and
        # it overwrote a correct job title: "Analyst, Structured Notes"
        # became "Mariner Wealth Advisors".
        out = cp.parse(msg(
            "Thank you for your interest in Mariner Wealth Advisors",
            "Mariner Hiring Team <no-reply@ashbyhq.com>",
            "Thank you for applying for the Analyst, Structured Notes role."))
        self.assertIn("Analyst", out.role)
        self.assertNotIn("Mariner", out.role)

    def test_job_alert_feed_named_only_in_the_display_name(self):
        # "Group One Trading - Jobs Alerts". The subject of a digest is
        # whatever that week's marketing line was, so the subject rules
        # cannot see it.
        out = cp.parse(msg(
            "Ever felt like there's a secret handshake to landing jobs",
            "Group One Trading - Jobs Alerts <x@u1821112.wl.sendgrid.net>"))
        self.assertFalse(out)
        self.assertEqual(out.reason, "not-an-application")

    def test_tenant_table_entries_are_reachable(self):
        """A table entry keyed on something _employer_slug never produces is
        dead weight that reads as coverage."""
        for slug in cp.TENANTS:
            self.assertEqual(
                slug, cp._employer_slug(cp._sender(f"X <{slug}@myworkday.com>")),
                f"TENANTS key {slug!r} cannot be produced by _employer_slug")


class TestCorpus(unittest.TestCase):
    """Runs the frozen mailbox pull, when this machine has it."""

    def test_corpus_if_present(self):
        if not os.path.exists(CORPUS):
            self.skipTest(f"no corpus at {CORPUS}")
        messages = json.load(open(CORPUS))
        report = cp.measure(messages)
        # Not an accuracy assertion — the corpus grows and companies change
        # their ATS. This pins the shape of the result so measure() cannot
        # quietly stop counting, which is how a regression hides.
        self.assertEqual(report["read"] + report["missed"] + report["excluded"],
                         report["total"])
        self.assertTrue(report["read"] > 0)
        self.assertEqual(set(report["by_reason"]) - set(cp.REASONS), set())


if __name__ == "__main__":
    unittest.main()

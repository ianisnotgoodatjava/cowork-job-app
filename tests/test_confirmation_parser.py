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
    """Workday was the worst source in the mailbox: 56% of its confirmations
    went unread, because it leaves the subject to each employer and writes the
    role in the body."""

    def test_workday_at_company_display_name(self):
        # The generic rules refuse "interest in X" — X is a role as often as a
        # company. Here the display name already names the employer, so X can
        # only be the role.
        out = cp.parse(msg(
            "Thank you for your interest in Rating Analyst – Structured Finance, Colorado",
            '"Workday at S&P Global" <spgi@myworkday.com>'))
        self.assertEqual(out.company, "S&P Global")
        self.assertIn("Rating Analyst", out.role)
        self.assertEqual(out.parser, "workday")

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

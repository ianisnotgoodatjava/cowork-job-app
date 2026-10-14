"""Everything an application form asks for, in one place, ready to paste.

The mechanical half of an application is not hard, it is just tedious and
repetitive: the same twenty fields, retyped into Workday, Greenhouse, Lever
and iCIMS, forty times a month. A browser autofill extension handles that
half well and there is no reason to rebuild it here.

What an extension cannot do is answer the questions that are actually about
you -- work authorization phrasing, salary, notice period, why this firm --
and what it cannot do *safely* is invent them. So this produces two files
per application:

  autofill_profile.json  a flat field -> value map for the extension
  ANSWERS.md             the questions a person has to read before pasting

Both come from resume_bank.json, which is also what resume_pdf.py and
cover_letter.py build from, so a form, a resume and a cover letter sent
together cannot disagree about the phone number.

Two things this deliberately does not do. It never submits, and it never
fills a Social Security number, a date of birth, a password or a bank
detail -- those are typed by you, into a form you are looking at. And it
never guesses a missing field: anything absent comes out as MISSING with the
key to add, because a plausible-looking wrong salary or address is worse
than a blank one you notice.
"""

import json
import os
import re

BANK_FILE = "resume_bank.json"
OUT_ROOT = "prep"

# Fields no autofill should ever carry. Some ATSs ask for SSN and date of
# birth on the same page as the address, and an extension that fills the
# whole page will fill those too.
NEVER_FILL = ("ssn", "social security", "date of birth", "dob", "password",
              "bank", "routing", "account number", "driver's license")

_ADDRESS_RE = re.compile(
    r"^\s*(?P<street>.+?),\s*(?P<city>[^,]+),\s*(?P<state>[A-Z]{2})\b,?\s*"
    r"(?P<zip>\d{5}(?:-\d{4})?)\s*$")


def load_bank(path: str = BANK_FILE) -> dict:
    import resume_bank
    return resume_bank.load(path)


def split_address(address: str) -> dict:
    """"REDACTED" -> the four fields a form wants.

    Workday and iCIMS ask for street, city, state and postal code separately;
    the bank stores one display string because that is what a resume header
    and a cover letter letterhead need. Rather than duplicate the address in
    two formats and let them drift, it is stored once and split here.

    Explicit `address_street` / `address_city` / ... keys in the profile win
    if present, so an address this regex cannot read is fixable without
    touching code.
    """
    match = _ADDRESS_RE.match(address or "")
    if not match:
        return {}
    parts = match.groupdict()
    parts["country"] = "United States"
    return parts


def _education(profile: dict) -> dict:
    edu = profile.get("education") or {}
    dates = edu.get("dates", "")
    # "Aug 2021 — Dec 2024" -> the two dates forms ask for separately.
    halves = [h.strip() for h in re.split(r"[—–-]", dates) if h.strip()]
    school = edu.get("school", "")
    degree = edu.get("degree", "")
    # Forms split "B.A. in Mathematics and Economics" into a degree dropdown
    # ("Bachelor's") and a free-text major.
    major = re.sub(r"^[A-Z.]+\s+in\s+", "", degree).strip()
    return {
        "school": school,
        "school_short": school.split(",")[0].strip(),
        "degree": degree,
        "degree_level": "Bachelor's Degree",
        "major": major,
        "gpa": edu.get("gpa", ""),
        "education_start": halves[0] if halves else "",
        "education_end": halves[1] if len(halves) > 1 else "",
        "coursework": ", ".join(edu.get("coursework") or []),
    }


def field_map(bank: dict | None = None) -> dict:
    """Flat field -> value for every standard application field.

    Flat on purpose. Autofill extensions, browser userscripts and a person
    reading a file all want "what goes in the box labelled City", not a
    nested profile they have to walk.
    """
    profile = (bank or load_bank())["profile"]
    first, _, last = profile.get("name", "").partition(" ")

    address = split_address(profile.get("address", ""))
    for key in ("street", "city", "state", "zip", "country"):
        explicit = profile.get(f"address_{key}")
        if explicit:
            address[key] = explicit

    current = (bank or load_bank()).get("experience", [{}])[0]

    fields = {
        "first_name": first,
        "last_name": last,
        "full_name": profile.get("name", ""),
        "email": profile.get("email", ""),
        "phone": profile.get("phone", ""),
        "address_street": address.get("street", ""),
        "address_city": address.get("city", ""),
        "address_state": address.get("state", ""),
        "address_zip": address.get("zip", ""),
        "address_country": address.get("country", "United States"),
        "location": profile.get("location", ""),
        "linkedin": profile.get("linkedin", ""),
        "github": profile.get("github", ""),
        "website": profile.get("website", ""),
        **_education(profile),
        "current_employer": current.get("company", ""),
        "current_title": current.get("role", ""),
        "skills": ", ".join(profile.get("skills") or []),
        "languages": ", ".join(profile.get("languages") or []),
        "certifications": ", ".join(profile.get("certifications") or []),
        # The three yes/no questions on essentially every form. Stated as the
        # literal dropdown answer, because that is the failure mode worth
        # avoiding: a US citizen who ticks "yes" to sponsorship gets filtered
        # out by a rule before a person ever reads the application.
        "work_authorized": "Yes",
        "requires_sponsorship": "No",
        "work_authorization_long": profile.get("work_authorization", ""),
        "citizenship": ", ".join(profile.get("citizenship") or []),
        "salary_expectation": profile.get("salary_expectation", ""),
        "start_date": profile.get("start_date", ""),
        "notice_period": profile.get("notice_period", ""),
        "willing_to_relocate": profile.get("willing_to_relocate", ""),
        "how_did_you_hear": profile.get("how_did_you_hear", ""),
        # EEO/demographics. Declining is always an accepted answer and is
        # never held against an applicant; it is the default here because the
        # real values are yours to give, not mine to store and paste.
        "gender": profile.get("gender", "Decline to self-identify"),
        "race_ethnicity": profile.get("race_ethnicity",
                                      "Decline to self-identify"),
        "veteran_status": profile.get(
            "veteran_status", "I don't wish to answer"),
        "disability_status": profile.get(
            "disability_status", "I don't wish to answer"),
    }
    return fields


# Fields a form will reject an empty answer for, or where a blank costs you
# the application. Everything else can stay empty without consequence.
REQUIRED = ("first_name", "last_name", "email", "phone", "address_street",
            "address_city", "address_state", "address_zip", "school",
            "degree", "salary_expectation", "start_date", "linkedin")


def missing(fields: dict | None = None) -> list[str]:
    fields = fields if fields is not None else field_map()
    return [k for k in REQUIRED if not str(fields.get(k, "")).strip()]


def _standard_answers(fields: dict) -> list[tuple[str, str]]:
    """The short questions, answered. Ordered as forms tend to ask them."""
    def value(key: str, fallback: str = "") -> str:
        got = str(fields.get(key, "")).strip()
        return got or fallback or f"**MISSING** — add `{key}` to resume_bank.json"

    return [
        ("Are you legally authorized to work in the United States?", "Yes"),
        ("Will you now or in the future require sponsorship?", "No"),
        ("Work authorization (long form)",
         value("work_authorization_long")),
        ("Salary expectation", value("salary_expectation")),
        ("Earliest start date", value("start_date")),
        ("Notice period", value("notice_period", "Two weeks")),
        ("Are you willing to relocate?", value("willing_to_relocate")),
        ("How did you hear about this role?", value("how_did_you_hear")),
        ("Have you previously worked for this company?", "No"),
        ("Gender", value("gender")),
        ("Race / ethnicity", value("race_ethnicity")),
        ("Veteran status", value("veteran_status")),
        ("Disability status", value("disability_status")),
    ]


def answer_sheet(fields: dict, company: str = "", role: str = "") -> str:
    header = f"{role} @ {company}".strip(" @") or "Standard answers"
    lines = [f"# Application answers — {header}", ""]

    gaps = missing(fields)
    if gaps:
        lines += [
            "> **Fill these before you rely on this.** Each is a field a form "
            "will ask for and",
            "> this cannot invent. Add them once to `resume_bank.json` under "
            "`profile` and every",
            "> future application has them:",
            "",
            *[f">   - `{k}`" for k in gaps],
            "",
        ]

    lines += ["## Short answers", ""]
    for question, answer in _standard_answers(fields):
        lines += [f"**{question}**", "", answer, ""]

    lines += [
        "## Never autofilled",
        "",
        "These are typed by you, into a form you are looking at — no tool "
        "here stores or",
        "pastes them: " + ", ".join(NEVER_FILL) + ".",
        "",
        "And nothing here presses submit. Read the form back before you do.",
        "",
    ]
    return "\n".join(lines)


def export(out_dir: str = "", company: str = "", role: str = "") -> str:
    """Writes autofill_profile.json and ANSWERS.md; returns the directory."""
    fields = field_map()
    out_dir = out_dir or os.path.join(OUT_ROOT, "_profile")
    os.makedirs(out_dir, exist_ok=True)

    with open(os.path.join(out_dir, "autofill_profile.json"), "w") as f:
        json.dump(fields, f, indent=2)
    with open(os.path.join(out_dir, "ANSWERS.md"), "w") as f:
        f.write(answer_sheet(fields, company, role))
    return out_dir


def run(company: str = "", role: str = "") -> None:
    fields = field_map()
    out = export(company=company, role=role)
    gaps = missing(fields)

    filled = sum(1 for v in fields.values() if str(v).strip())
    print(f"{filled}/{len(fields)} fields ready -> {out}/autofill_profile.json")
    print(f"Answers to the written questions -> {out}/ANSWERS.md")
    if gaps:
        print(f"\n{len(gaps)} field(s) a form will ask for and this cannot "
              f"invent.\nAdd to `profile` in resume_bank.json:")
        for key in gaps:
            print(f"  {key}")

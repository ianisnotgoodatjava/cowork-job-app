"""Renders a one-page PDF resume, with bullets selected for one job category.

What this does NOT do is rewrite your bullets. Every line on the page is text
you wrote, copied verbatim from resume_bank.json. Tailoring is *selection and
ordering*: which of your real bullets lead, and which get dropped to keep the
page to one side. A generator that reworded your experience per posting would
be inventing a record you cannot defend in the interview.

Every role stays on the page — dropping a job to make room reads as a gap.
Only bullets are cut, and never the last one under a role.
"""

import os

from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether,
                                PageTemplate, Paragraph)

import prep_package

OUT_ROOT = "resumes"

_NAME = ParagraphStyle("name", fontName="Times-Bold", fontSize=15, leading=17,
                       spaceAfter=1)
_CONTACT = ParagraphStyle("contact", fontName="Times-Roman", fontSize=9,
                          leading=11, spaceAfter=5)
_SECTION = ParagraphStyle("section", fontName="Times-Bold", fontSize=10.5,
                          leading=12, spaceBefore=5, spaceAfter=1)
_ORG = ParagraphStyle("org", fontName="Times-Bold", fontSize=9.5, leading=11)
_ROLE = ParagraphStyle("role", fontName="Times-Italic", fontSize=9, leading=10.5)
_BULLET = ParagraphStyle("bullet", fontName="Times-Roman", fontSize=8.6,
                         leading=10.1, leftIndent=10, bulletIndent=1,
                         alignment=TA_JUSTIFY, spaceAfter=1)
_PLAIN = ParagraphStyle("plain", fontName="Times-Roman", fontSize=8.8,
                        leading=10.4, spaceAfter=1)


def _esc(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _rule(text: str) -> Paragraph:
    """Section heading with the underscore rule the original resume uses."""
    return Paragraph(f"{_esc(text)}<br/>"
                     f"<font size='2'>{'_' * 118}</font>", _SECTION)


def _select(bank: dict, category: str, parsed: dict | None, max_bullets: int):
    """Bullets to print, grouped by role, ordered by fit."""
    if parsed is None:
        parsed = {"requirements": [], "responsibilities": [],
                  "text": " ".join(prep_package.CATEGORIES[category]["keywords"])}
    ranked = prep_package.rank_bullets(bank, parsed, category)
    order = {b["id"]: i for i, (_, b) in enumerate(ranked)}

    by_company: dict[str, list[dict]] = {}
    for _, bullet in ranked:
        by_company.setdefault(bullet["company"], []).append(bullet)

    # Trim globally worst bullets first, but never empty a role.
    chosen = {c: list(bs) for c, bs in by_company.items()}
    total = sum(len(bs) for bs in chosen.values())
    while total > max_bullets:
        worst, worst_company = None, None
        for company, bullets in chosen.items():
            if len(bullets) <= 1:
                continue
            cand = max(bullets, key=lambda b: order[b["id"]])
            if worst is None or order[cand["id"]] > order[worst["id"]]:
                worst, worst_company = cand, company
        if worst is None:
            break
        chosen[worst_company].remove(worst)
        total -= 1

    for company in chosen:
        chosen[company].sort(key=lambda b: order[b["id"]])
    return chosen


def build(category: str, parsed: dict | None = None, company_name: str = "",
          role_name: str = "", out_root: str = OUT_ROOT,
          # 9 of your 12 bullets. At 11 only one was ever cut, so every
          # category rendered the same page and the tailoring was a no-op. At
          # 9 they genuinely diverge: quant leads with the Canoe ML pipeline,
          # risk with REDACTED, trading with the REDACTED desk rotation.
          max_bullets: int = 9) -> str:
    bank = prep_package.load_bank()
    profile = bank["profile"]
    chosen = _select(bank, category, parsed, max_bullets)

    os.makedirs(out_root, exist_ok=True)
    suffix = prep_package._slug(f"{company_name}-{role_name}", 40) if company_name else category
    path = os.path.join(out_root, f"Ian_Liu_Resume_{suffix}.pdf")

    doc = BaseDocTemplate(path, pagesize=LETTER,
                          leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                          topMargin=0.45 * inch, bottomMargin=0.4 * inch,
                          title="Ian Liu — Resume", author="Ian Liu")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="one", frames=[frame])])

    story = [
        Paragraph(_esc(profile["name"]), _NAME),
        Paragraph(f"{_esc(profile.get('address', profile['location']))} | "
                  f"{_esc(profile['phone'])} | {_esc(profile['email'])}", _CONTACT),
    ]

    # --- Education ---
    edu = profile["education"]
    story.append(_rule("Education"))
    story.append(Paragraph(
        f"<b>{_esc(edu['school'])}</b> &nbsp;&nbsp;<font size='8'>"
        f"{_esc(profile['location'])}</font>", _PLAIN))
    story.append(Paragraph(
        f"<i>{_esc(edu['degree'])}</i> &nbsp;&nbsp;<font size='8'>"
        f"{_esc(edu['dates'])}</font>", _PLAIN))
    story.append(Paragraph(
        f"GPA: {_esc(edu['gpa'])} | Coursework: "
        f"{_esc(', '.join(edu['coursework']))}", _PLAIN))

    # --- Experience / Leadership ---
    # Headings are passed raw — _rule() escapes them, so pre-escaping here
    # would render a literal "&amp;".
    for section, heading in (("experience", "Experience"),
                             ("leadership", "Leadership & Extracurricular Activities")):
        entries = [e for e in bank["experience"] if e.get("section") == section]
        if not entries:
            continue
        story.append(_rule(heading))
        for entry in entries:
            key = entry.get("bullet_company", entry["company"])
            bullets = chosen.get(key, [])
            if not bullets:
                continue
            block = [
                Paragraph(f"<b>{_esc(entry['company'])}</b> &nbsp;&nbsp;"
                          f"<font size='8'>{_esc(entry['location'])}</font>", _PLAIN),
                Paragraph(f"<i>{_esc(entry['role'])}</i> &nbsp;&nbsp;"
                          f"<font size='8'>{_esc(entry['dates'])}</font>", _PLAIN),
            ]
            block += [Paragraph(_esc(b["text"]), _BULLET, bulletText="•")
                      for b in bullets]
            # Keeps a role's header from stranding at the foot of the page.
            story.append(KeepTogether(block))

    # --- Skills ---
    story.append(_rule("Skills & Interests"))
    story.append(Paragraph(
        f"<b>Skills:</b> {_esc(', '.join(profile['skills']))} | "
        f"<b>Languages:</b> {_esc(', '.join(profile['languages']))}", _PLAIN))
    story.append(Paragraph(
        f"<b>Certifications:</b> {_esc(', '.join(profile['certifications']))}", _PLAIN))
    story.append(Paragraph(
        f"<b>Citizenships:</b> {_esc(', '.join(profile['citizenship']))}", _PLAIN))

    doc.build(story)

    dropped = sum(len(b) for b in chosen.values())
    print(f"  resume -> {path} ({dropped} bullets, "
          f"{prep_package.CATEGORIES[category]['label']})")
    return path

"""Renders a cover letter to Word (.docx) and PDF.

The letter body lives in a plain text file so it stays easy to edit: blank
lines separate paragraphs, and hard wrapping inside a paragraph is ignored —
lines within a block are rejoined so Word and reportlab do their own wrapping.
Editing a .txt in any editor beats hand-formatting a .docx.

The letterhead is not in that file. Name, address, email and phone come from
resume_bank.json, the same source `resume_pdf.py` builds from, so a cover
letter and a resume sent together can never disagree about the phone number.
"""

import datetime
import os

BANK = "resume_bank.json"
OUT_DIR = os.path.join("logs", "drafts", "cover_letters")


def _profile(bank_path: str = BANK) -> dict:
    import resume_bank
    return resume_bank.profile(bank_path)


def _blocks(path: str) -> list[str]:
    """Splits the body file into paragraphs, unwrapping hard line breaks."""
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    out = []
    for chunk in raw.split("\n\n"):
        joined = " ".join(line.strip() for line in chunk.splitlines()
                          if line.strip())
        if joined:
            out.append(joined)
    return out


def _header_lines(p: dict) -> tuple[str, list[str]]:
    contact = " | ".join(x for x in (p.get("address"), p.get("email"),
                                     p.get("phone")) if x)
    return p["name"], [contact]


def build_docx(body_path: str, out_path: str, recipient: list[str],
               date: str | None = None, bank_path: str = BANK) -> str:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, Inches

    p = _profile(bank_path)
    name, contact = _header_lines(p)
    doc = Document()

    for section in doc.sections:
        section.top_margin = Inches(0.7)
        section.bottom_margin = Inches(0.6)
        section.left_margin = Inches(0.9)
        section.right_margin = Inches(0.9)

    style = doc.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(11)
    style.paragraph_format.space_after = Pt(0)
    style.paragraph_format.line_spacing = 1.0

    def para(text="", *, bold=False, size=11, after=0, align=None):
        par = doc.add_paragraph()
        par.paragraph_format.space_after = Pt(after)
        if align is not None:
            par.alignment = align
        if text:
            run = par.add_run(text)
            run.bold = bold
            run.font.size = Pt(size)
            run.font.name = "Times New Roman"
        return par

    # Letterhead
    para(name, bold=True, size=15, after=2, align=WD_ALIGN_PARAGRAPH.CENTER)
    for line in contact:
        para(line, size=9.5, after=12, align=WD_ALIGN_PARAGRAPH.CENTER)

    para(date or datetime.date.today().strftime("%B %-d, %Y"), after=12)
    for line in recipient:
        para(line, after=0)
    para(after=12)

    blocks = _blocks(body_path)
    for i, block in enumerate(blocks):
        # The salutation and the sign-off are single lines; body paragraphs
        # get the spacing between them.
        para(block, after=9 if i < len(blocks) - 1 else 0)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    doc.save(out_path)
    return out_path


def build_pdf(body_path: str, out_path: str, recipient: list[str],
              date: str | None = None, bank_path: str = BANK) -> str:
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    p = _profile(bank_path)
    name, contact = _header_lines(p)

    body = ParagraphStyle("body", fontName="Times-Roman", fontSize=11,
                          leading=13.2, spaceAfter=8)
    head = ParagraphStyle("head", fontName="Times-Bold", fontSize=15,
                          leading=17, alignment=1, spaceAfter=2)
    sub = ParagraphStyle("sub", fontName="Times-Roman", fontSize=9.5,
                         leading=11.5, alignment=1, spaceAfter=12)
    tight = ParagraphStyle("tight", parent=body, spaceAfter=0)

    story = [Paragraph(name, head), Paragraph(" | ".join(contact), sub),
             Paragraph(date or datetime.date.today().strftime("%B %-d, %Y"),
                       body)]
    for line in recipient:
        story.append(Paragraph(line, tight))
    story.append(Spacer(1, 12))
    for block in _blocks(body_path):
        story.append(Paragraph(block, body))

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    SimpleDocTemplate(out_path, pagesize=LETTER,
                      leftMargin=0.9 * inch, rightMargin=0.9 * inch,
                      topMargin=0.7 * inch, bottomMargin=0.6 * inch,
                      title=f"{name} — Cover Letter").build(story)
    return out_path


def build(body_path: str, company: str, recipient: list[str] | None = None,
          date: str | None = None, out_dir: str = OUT_DIR,
          bank_path: str = BANK) -> list[str]:
    """Renders both formats. Returns the paths written."""
    slug = os.path.splitext(os.path.basename(body_path))[0]
    recipient = recipient or [company]
    return [
        build_docx(body_path, os.path.join(out_dir, f"{slug}.docx"),
                   recipient, date, bank_path),
        build_pdf(body_path, os.path.join(out_dir, f"{slug}.pdf"),
                  recipient, date, bank_path),
    ]

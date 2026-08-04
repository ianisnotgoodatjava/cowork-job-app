"""Recreates the outreach drafts whose bodies were emptied, from saved source
text — unwrapped into real paragraphs and with the 'I recently applied' opener
removed."""
import base64, re
from email.message import EmailMessage
from draft_cleanup import _svc

D = "/private/tmp/claude-501/-Users-ianliu-cowork--job-app-/65b274e7-9466-4e50-bb39-03bcf9a3897a/scratchpad"
M = [("255_cme.txt", "lawton.warzala@cmegroup.com", "Clearing & Post-Trade at CME Group"),
     ("258_scale.txt", "daniel.forero@scalemicrogridsolutions.com", "Capital Markets at Scale Microgrids"),
     ("265_unfi.txt", "alatterell@unfi.com", "Finance at UNFI"),
     ("269_ubs.txt", "hsiaowei.chung@ubs.com", "APAC Equity Research at UBS"),
     ("270_hartree.txt", "yalmoayed@hartreepartners.com", "Risk at Hartree Partners"),
     ("272_jsb.txt", "EN@jsbpartners.com", "Trading Operations at JSB Partners"),
     ("274_banc.txt", "john.bryson@bancofcal.com", "Venture Banking at Banc of California"),
     ("275_charlie.txt", "hiral.patel@charliehealth.com", "Commercial Strategy at Charlie Health"),
     ("277_fgs.txt", "mariah.zwicker@fgsglobal.com", "Research & Insights at FGS Global"),
     ("250_codypeak.txt", "kschultz@codypeakadv.com", "Investment Banking at Cody Peak Advisors"),
     ("287_amex.txt", "faye.li@americanexpress.com", "Credit and Fraud Risk at American Express"),
     ("fitch_kohl_outreach.txt", "harry.kohl@fitchratings.com", "ABS at Fitch Ratings")]
_A = re.compile(r"I recently applied to (?:the|an|a)\s+.{0,140}?,\s*and\s+(I['’a-z ]*)", re.I)


def unwrap(t):
    return "\n\n".join(
        re.sub(r"\s{2,}", " ", " ".join(l.strip() for l in p.split("\n") if l.strip()))
        for p in re.split(r"\n\s*\n", t.strip()))


def strip_applied(t):
    def r(m):
        tail = m.group(1).strip()
        return tail[0].upper() + tail[1:] if tail else "I"
    return _A.sub(r, t)


def run():
    svc = _svc()
    resume = open("/Users/you/Ian_Liu_Resume.pdf", "rb").read()
    n = 0
    for fn, to, topic in M:
        txt = None
        for base in (D + "/drafts/", D + "/"):
            try:
                txt = open(base + fn).read()
                break
            except FileNotFoundError:
                pass
        if not txt:
            print("MISSING", fn)
            continue
        m = EmailMessage()
        m["To"] = to
        m["Subject"] = f"Recent NYU Graduate Inquiring about {topic}"
        m.set_content(strip_applied(unwrap(txt)))
        m.add_attachment(resume, maintype="application", subtype="pdf",
                         filename="Ian_Liu_Resume.pdf")
        svc.users().drafts().create(
            userId="me",
            body={"message": {"raw": base64.urlsafe_b64encode(m.as_bytes()).decode()}},
        ).execute()
        n += 1
        print("restored", to)
    print(f"restored {n} draft(s)")

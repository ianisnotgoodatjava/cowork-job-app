"""Rewrites existing outreach drafts: unwraps hard-wrapped lines and removes
the "I recently applied to X" opener.

The drafts were written with hard line breaks and trailing spaces, which mail
clients re-wrap into ragged text with visible gaps. Paragraphs are stored as
single lines instead, letting the client wrap them.
"""
import base64, re
from email.message import EmailMessage
from draft_cleanup import _svc
from gmail_client import _extract_mimetype

_APPLIED = re.compile(
    r"I recently applied to (?:the|an|a)\s+.{0,140}?,\s*and\s+(I['’a-z ]*)",
    re.I)


def unwrap(text: str) -> str:
    paras = re.split(r"\n\s*\n", text.strip())
    out = []
    for p in paras:
        joined = " ".join(line.strip() for line in p.split("\n") if line.strip())
        out.append(re.sub(r"\s{2,}", " ", joined))
    return "\n\n".join(out)


def strip_applied(text: str) -> str:
    def repl(m):
        tail = m.group(1).strip()
        return tail[0].upper() + tail[1:] if tail else "I"
    return _APPLIED.sub(repl, text)


def run(dry_run=True, limit=20):
    svc = _svc()
    drafts = svc.users().drafts().list(userId="me", maxResults=100).execute().get("drafts", [])
    fixed = 0
    for d in drafts[:limit]:
        full = svc.users().drafts().get(userId="me", id=d["id"], format="full").execute()
        msg = full["message"]
        hdr = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
        subj = hdr.get("Subject", "")
        if "Inquiring about" not in subj or not hdr.get("To"):
            continue
        old = _extract_mimetype(msg["payload"], "text/plain")
        if not old:
            continue
        new = strip_applied(unwrap(old))
        if new == old:
            continue
        fixed += 1
        print(f"--- {hdr.get('To')} | {subj[:52]}")
        if dry_run:
            print(new[:420], "\n")
            continue
        m = EmailMessage()
        m["To"] = hdr.get("To", "")
        m["Subject"] = subj
        m.set_content(new)
        # Re-attach the resume; drafts were created with it.
        with open("/Users/you/Ian_Liu_Resume.pdf", "rb") as f:
            m.add_attachment(f.read(), maintype="application", subtype="pdf",
                             filename="Ian_Liu_Resume.pdf")
        svc.users().drafts().update(
            userId="me", id=d["id"],
            body={"message": {"raw": base64.urlsafe_b64encode(m.as_bytes()).decode()}},
        ).execute()
        print("  updated\n")
    print(f"{'would fix' if dry_run else 'fixed'} {fixed} draft(s)")

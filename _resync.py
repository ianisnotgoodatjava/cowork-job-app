from dotenv import load_dotenv
load_dotenv()
import base64, glob, os, re, time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from googleapiclient.discovery import build
from google_auth import get_credentials

RESUME = os.path.expanduser("~/Ian_Liu_Resume.pdf")

want = {}
for path in glob.glob("logs/drafts/ready/*.txt"):
    t = open(path, encoding="utf-8").read()
    to = re.search(r"^To:\s*(\S+)", t, re.M)
    su = re.search(r"^Subject:\s*(.+)$", t, re.M)
    bd = re.search(r"^-{40,}\n(.*?)\n-{40,}\s*$", t, re.S | re.M)
    if to and su and bd:
        want[to.group(1).strip().lower()] = (su.group(1).strip(), bd.group(1).strip())


def svc():
    return build("gmail", "v1", credentials=get_credentials())


def retry(fn, attempts=4):
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:
            if i == attempts - 1:
                raise
            print(f"    retry after {type(exc).__name__}")
            time.sleep(2 * (i + 1))


s = svc()
listed = retry(lambda: s.users().drafts().list(userId="me", maxResults=300).execute()).get("drafts", [])
fixed, skipped = 0, 0
for d in listed:
    try:
        meta = retry(lambda: s.users().drafts().get(userId="me", id=d["id"], format="metadata").execute())
    except Exception:
        skipped += 1
        continue
    h = {x["name"]: x["value"] for x in meta["message"]["payload"]["headers"]}
    to_raw = h.get("To", "").strip()
    addr = to_raw.split("<")[-1].strip("> ").lower()
    if addr not in want:
        continue
    subject, body = want[addr]
    msg = MIMEMultipart()
    msg["To"] = to_raw
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    if os.path.exists(RESUME):
        part = MIMEBase("application", "pdf")
        with open(RESUME, "rb") as f:
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment",
                        filename=os.path.basename(RESUME))
        msg.attach(part)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    try:
        retry(lambda: s.users().drafts().update(
            userId="me", id=d["id"], body={"message": {"raw": raw}}).execute())
        fixed += 1
    except Exception as exc:
        print(f"    failed {addr}: {type(exc).__name__}")
        skipped += 1
print(f"{fixed} Gmail draft(s) rewritten; {skipped} skipped")

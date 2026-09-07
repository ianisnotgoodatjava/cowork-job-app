"""Builds a one-click bookmarklet that fills an application form.

The mechanical half of an application is twenty fields retyped into
Greenhouse, Lever, Workday and iCIMS, sixty times in three weeks.
apply_autofill already assembles those values from resume_bank; this turns
them into something that can actually touch a page.

A bookmarklet rather than an extension, for three reasons. Nothing has to be
installed or trusted -- it is a bookmark you can read before you click it.
It only ever acts on the page already open in front of you, so there is no
crawler and no session being driven. And it cannot run on its own: it fires
when clicked, on a form being looked at.

It never submits. It fills, reports what it touched, and stops -- the
submit button stays a human decision, which is also what keeps a wrong
salary or a mistyped address from going out unnoticed.

It also refuses a set of fields outright. Some ATSs put SSN and date of
birth on the same page as the address, and a filler that walks the whole
form would fill those too.

Not built for LinkedIn Easy Apply. LinkedIn's User Agreement prohibits
automated interaction with the service, and the penalty falls on the
account rather than the tool -- which would cost the job search far more
than the typing saves.
"""

import json

import apply_autofill

# Label fragments -> profile key. Matched against every label, placeholder,
# name and id on the field, lowercased.
MATCHERS = [
    (("first name", "given name", "firstname"), "first_name"),
    (("last name", "family name", "surname", "lastname"), "last_name"),
    (("full name", "your name"), "full_name"),
    (("email",), "email"),
    (("phone", "mobile", "telephone"), "phone"),
    (("street", "address line 1", "address1"), "address_street"),
    (("city", "town"), "address_city"),
    (("state", "province", "region"), "address_state"),
    (("zip", "postal"), "address_zip"),
    (("country",), "address_country"),
    (("linkedin",), "linkedin"),
    (("github", "portfolio", "website"), "website"),
    (("school", "university", "college", "institution"), "school"),
    (("degree",), "degree"),
    (("discipline", "major", "field of study"), "major"),
    (("graduation", "grad date", "end date"), "grad_date"),
    (("gpa",), "gpa"),
    (("current company", "employer"), "current_company"),
    (("current title", "job title", "position title"), "current_title"),
    (("salary", "compensation", "expected pay"), "salary_expectation"),
    (("start date", "available", "notice"), "start_date"),
    (("authorized", "authorisation", "work auth", "legally"), "work_auth"),
    (("sponsor", "visa"), "sponsorship"),
    (("pronoun",), "pronouns"),
]


# Demographic questions are the applicant's to answer, and a filler that
# walks the page will happily put a city into an ethnicity field -- which is
# exactly what the first version did on KBRA's Greenhouse form.
EEO_FIELDS = ("gender", "ethnicity", "race", "hispanic", "latino", "veteran",
              "disability", "sexual orientation", "pronoun")

# Consent is given, not filled in. On KBRA's form the privacy-acknowledgement
# field was matched by "state" -- as a substring of "Privacy Statement" -- and
# answered "NY". Agreeing to a privacy policy on someone's behalf is not a
# typing shortcut, so these are refused outright as well.
CONSENT_FIELDS = ("acknowledge", "consent", "privacy", "terms", "i agree",
                  "certify", "authorize the", "policy")


def _js_profile() -> dict:
    """{profile key: value} for keys that actually have a value."""
    fields = apply_autofill.field_map()
    out = {}
    for keys, target in MATCHERS:
        value = fields.get(target)
        if value and str(value).strip() and str(value) != "MISSING":
            out[target] = str(value).strip()
    return out


def build() -> str:
    """The bookmarklet source, as a javascript: URL."""
    profile = _js_profile()
    matchers = [[list(keys), target] for keys, target in MATCHERS
                if target in profile]
    js = """
(function(){
 var P=%(profile)s, M=%(matchers)s, NEVER=%(never)s;
 function label(el){
  var s=[el.name,el.id,el.placeholder,el.getAttribute('aria-label')||''];
  if(el.id){var l=document.querySelector('label[for="'+CSS.escape(el.id)+'"]');
            if(l)s.push(l.innerText);}
  var p=el.closest('label'); if(p)s.push(p.innerText);
  var w=el.closest('div,fieldset');
  if(w&&w.querySelectorAll('input,textarea,select').length===1){
   var t=w.querySelector('label,legend'); if(t)s.push(t.innerText);}
  return s.join(' ').toLowerCase().replace(/\\s+/g,' ');
 }
 function set(el,v){
  var proto=el.tagName==='TEXTAREA'?HTMLTextAreaElement:HTMLInputElement;
  var d=Object.getOwnPropertyDescriptor(proto.prototype,'value');
  d.set.call(el,v);
  el.dispatchEvent(new Event('input',{bubbles:true}));
  el.dispatchEvent(new Event('change',{bubbles:true}));
 }
 var n=0, skipped=0;
 document.querySelectorAll('input,textarea').forEach(function(el){
  if(el.type==='hidden'||el.type==='file'||el.type==='password')return;
  if(el.type==='checkbox'||el.type==='radio')return;
  if(el.value&&el.value.trim())return;
  var L=label(el);
  if(!L)return;
  for(var i=0;i<NEVER.length;i++){if(L.indexOf(NEVER[i])>-1){skipped++;return;}}
  for(var j=0;j<M.length;j++){
   var keys=M[j][0], key=M[j][1];
   for(var k=0;k<keys.length;k++){
    if(new RegExp('\\\\b'+keys[k]+'\\\\b').test(L)&&P[key]){set(el,P[key]);n++;return;}
   }
  }
 });
 alert('Filled '+n+' field(s).'+(skipped?' Skipped '+skipped+' sensitive.':'')
   +'\\nNothing was submitted - review the form, then submit it yourself.');
})();
""" % {"profile": json.dumps(profile),
       "matchers": json.dumps(matchers),
       "never": json.dumps(list(apply_autofill.NEVER_FILL) + list(EEO_FIELDS)
                                + list(CONSENT_FIELDS))}
    return "javascript:" + "".join(line.strip() for line in js.splitlines())


def run(out: str = "prep/autofill_bookmarklet.txt") -> None:
    import os

    profile = _js_profile()
    missing = [t for _keys, t in MATCHERS if t not in profile]
    link = build()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        f.write(link + "\n")

    print(f"{len(profile)} field(s) it can fill:")
    for key, value in sorted(profile.items()):
        print(f"  {key:20s} {value[:44]}")
    if missing:
        print("\nNot filled — no value in resume_bank.json:")
        print("  " + ", ".join(missing))
    print(f"\nBookmarklet written to {out} ({len(link)} chars).")
    print("Add it as a bookmark, open a Greenhouse or Lever form, click it.")
    print("It fills and stops. You review and submit.")

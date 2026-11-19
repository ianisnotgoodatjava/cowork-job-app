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
    (("github", "portfolio", "personal website"), "website"),
    (("school", "university", "college", "institution"), "school"),
    (("degree",), "degree"),
    (("discipline", "major", "field of study"), "major"),
    (("graduation", "grad date", "end date"), "education_end"),
    (("gpa",), "gpa"),
    (("current employer", "current company", "employer"), "current_employer"),
    (("current title", "job title", "position title"), "current_title"),
    (("salary", "compensation", "expected pay"), "salary_expectation"),
    (("start date", "available", "notice"), "start_date"),
    # Voluntary self-identification, filled only because it was given
    # explicitly. Gender, veteran and disability stay refused -- those have
    # not been stated, and "decline" is a choice to make rather than a
    # default to paste.
    (("race", "ethnicity"), "race_ethnicity"),
]


# Demographic questions are the applicant's to answer, and a filler that
# walks the page will happily put a city into an ethnicity field -- which is
# exactly what the first version did on KBRA's Greenhouse form.
EEO_FIELDS = ("pronoun",)

# Consent is given, not filled in. On KBRA's form the privacy-acknowledgement
# field was matched by "state" -- as a substring of "Privacy Statement" -- and
# answered "NY". Agreeing to a privacy policy on someone's behalf is not a
# typing shortcut, so these are refused outright as well.
CONSENT_FIELDS = ("acknowledge", "consent", "privacy", "terms", "i agree",
                  "certify", "authorize the", "policy")

# Work authorization and sponsorship are answered by RULES below, not by
# keyword length, because length is the wrong tiebreaker. KBRA's sponsorship
# question reads "Do you require immigration sponsorship to attain or
# maintain your employment eligibility... sponsorship by your employer" -- it
# contains "employer", "sponsorship" and "work authorization" at once.
# First-match answered it "REDACTED"; longest-match answered it "Yes", the one
# answer that has a US citizen filtered out automatically.
#
# Precedence by meaning fixes it: anything mentioning sponsorship or a visa
# is the sponsorship question and answers No, and only what is left over can
# be the authorization question and answer Yes.
DECISIVE_FIELDS = ()


# Answers that are decided by meaning, or computed when the page opens.
# Checked in order, before the keyword matchers, and the order is the point:
# sponsorship is tested before authorization because its question contains
# both phrases.
#
# Three of these cannot be baked in. A start date written today is wrong
# tomorrow; the salary depends on where the job is; and the disability
# question is Yes only when its own list of conditions names one of yours,
# which differs by employer. All three are evaluated against the live page.
#
# The answers themselves -- salary, self-identification, the conditions --
# are the applicant's, so they come from the gitignored resume_bank.json
# rather than this file. A question with no answer in the profile gets no
# rule and is left for you.
RULES_HEAD_JS = """
 var _d=new Date(Date.now()+14*864e5);
 while(_d.getDay()!==1)_d.setDate(_d.getDate()+1);
 var START=_d.toLocaleDateString('en-US',{month:'long',day:'numeric',year:'numeric'});
 var PAGE=(document.body.innerText||'').toLowerCase();
 var MAJOR=['new york','new york city','nyc','san francisco','boston','chicago',
            'los angeles','washington, d.c.','washington dc','seattle','london',
            'hong kong','singapore','dublin','toronto','jersey city','stamford'];
"""


def rules_js(profile: dict | None = None) -> str:
    """The RULES block, with this applicant's answers filled in."""
    if profile is None:
        import resume_bank
        profile = resume_bank.profile()
    js = RULES_HEAD_JS
    rules = [
        ("/sponsor|visa/", "'No'"),
        ("/authoriz|authoris|legally/", "'Yes'"),
        ("/relocat|willing to work|willing to commute|able to commute|"
         "willing to be based|willing to travel/", "'Yes'"),
        ("/referr|refer by|referred by/", "'No'"),
        ("/start date|how soon|availab|notice period/", "START"),
    ]
    major, other = profile.get("salary_major_city"), profile.get("salary_other")
    if major and other:
        js += (" var SALARY=MAJOR.some(function(c){return PAGE.indexOf(c)>-1;})"
               f"?{json.dumps(str(major))}:{json.dumps(str(other))};\n")
        rules.append(("/salary|compensation|expected pay|base pay/", "SALARY"))
    conditions = profile.get("disability_conditions")
    if conditions is not None:
        js += (f" var DIS={json.dumps([c.lower() for c in conditions])}"
               ".some(function(c){return PAGE.indexOf(c)>-1;});\n")
        rules.append(("/disabilit/", "DIS?'Yes':'No'"))
    citizenship = profile.get("citizenship")
    if isinstance(citizenship, list):
        citizenship = citizenship[0] if citizenship else ""
    for pattern, key, value in (
            ("/veteran/", "veteran_status", None),
            ("/hispanic|latino/", "hispanic_ethnicity", None),
            ("/gender/", "gender", None),
            ("/sexual orientation/", "sexual_orientation", None),
            ("/citizenship/", "citizenship", citizenship)):
        value = value if value is not None else profile.get(key)
        if value and str(value).strip():
            rules.append((pattern, json.dumps(str(value).strip())))
    js += " var RULES=[\n" + "".join(f"  [{p}, {v}],\n" for p, v in rules) + " ];\n"
    return js


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
  /* Workday names every field with data-automation-id and routinely
     supplies no name and no aria-label -- the signals every other ATS
     gives. Ids read like legalNameSection_firstName, so camelCase and
     letter-digit boundaries are split before matching, or "firstName"
     never meets a "first name" matcher and addressLine1 misses
     "address line 1" by one space.
     Block comments, not line comments: build() emits this as a one-line
     javascript: URL, where // comments out the entire rest of the script. */
  var auto=(el.getAttribute('data-automation-id')||'')
             .replace(/([a-z0-9])([A-Z])/g,'$1 $2')
             .replace(/([a-zA-Z])(\\d)/g,'$1 $2')
             .replace(/[_-]+/g,' ');
  var s=[el.name,el.id,el.placeholder,el.getAttribute('aria-label')||'',auto];
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
 %(rules)s
 var n=0, skipped=0;
 document.querySelectorAll('input,textarea').forEach(function(el){
  if(el.type==='hidden'||el.type==='file'||el.type==='password')return;
  if(el.type==='checkbox'||el.type==='radio')return;
  if(el.value&&el.value.trim())return;
  var L=label(el);
  if(!L)return;
  for(var i=0;i<NEVER.length;i++){if(L.indexOf(NEVER[i])>-1){skipped++;return;}}
  var hit=null;
  for(var r=0;r<RULES.length;r++){ if(RULES[r][0].test(L)){hit=RULES[r][1];break;} }
  if(hit!==null){ set(el,hit); n++; return; }
  var best=null,bestLen=0;
  for(var j=0;j<M.length;j++){
   var keys=M[j][0], key=M[j][1];
   for(var k=0;k<keys.length;k++){
    if(P[key]&&new RegExp('\\\\b'+keys[k]+'\\\\b').test(L)&&keys[k].length>bestLen){best=key;bestLen=keys[k].length;}
   }
  }
  if(best){set(el,P[best]);n++;}
 });
 /* The diversity, veteran and disability questions are never text boxes.
    They are dropdowns and radio groups, and the loop above reads neither --
    which is why they were still being answered by hand on every
    application. The answers themselves were already decided by RULES. */
 function answerFor(L){
  for(var i=0;i<NEVER.length;i++){if(L.indexOf(NEVER[i])>-1)return null;}
  for(var r=0;r<RULES.length;r++){if(RULES[r][0].test(L))return RULES[r][1];}
  /* Race and ethnicity are answered from the profile, not the rules, so a
     handler that consults only RULES leaves the one dropdown the rest of
     this block exists for. Longest match, as in the text loop. */
  var best=null,bestLen=0;
  for(var j=0;j<M.length;j++){
   var keys=M[j][0], key=M[j][1];
   for(var k=0;k<keys.length;k++){
    if(P[key]&&L.indexOf(keys[k])>-1&&keys[k].length>bestLen){
     best=P[key];bestLen=keys[k].length;}
   }
  }
  return best;
 }
 /* Option text rarely equals the answer exactly: "No" has to find
    "No, I do not have a disability", and "Male" must not match "Female".
    Prefix and word-boundary only, never a bare substring. */
 function optionMatches(text,want){
  text=(text||'').trim().toLowerCase(); want=(want||'').trim().toLowerCase();
  if(!text||!want)return false;
  if(text===want)return true;
  /* Word-start containment, done without a RegExp on purpose. "Male" must
     not match "Female", so a bare indexOf is wrong; and building a RegExp
     here means escaping the answer text through Python, the minifier and
     the javascript: URL, where one lost backslash is a syntax error in the
     whole script rather than a bad match. Padding with spaces gets the same
     boundary with none of that. */
  return (' '+text).indexOf(' '+want)>-1;
 }
 document.querySelectorAll('select').forEach(function(sel){
  if(sel.value&&sel.value.trim())return;
  var L=label(sel); if(!L)return;
  var want=answerFor(L); if(want===null){skipped++;return;}
  var opts=sel.options, picked=-1;
  for(var i=0;i<opts.length;i++){
   if(optionMatches(opts[i].text,want)){picked=i;break;}
  }
  if(picked<0){skipped++;return;}
  sel.selectedIndex=picked;
  sel.dispatchEvent(new Event('input',{bubbles:true}));
  sel.dispatchEvent(new Event('change',{bubbles:true}));
  n++;
 });
 /* Radios are grouped by name; the question lives on the fieldset, not on
    the individual button, so the group is matched once and then the member
    whose own label carries the answer is clicked. */
 var groups={};
 document.querySelectorAll('input[type=radio]').forEach(function(el){
  if(!el.name)return; (groups[el.name]=groups[el.name]||[]).push(el);
 });
 Object.keys(groups).forEach(function(name){
  var els=groups[name];
  if(els.some(function(e){return e.checked;}))return;
  var box=els[0].closest('fieldset,div[role=radiogroup],div');
  var q=((box?box.innerText:'')+' '+label(els[0])).toLowerCase().replace(/\\s+/g,' ');
  var want=answerFor(q); if(want===null){skipped++;return;}
  for(var i=0;i<els.length;i++){
   if(optionMatches(label(els[i]),want)){
    els[i].click();
    els[i].dispatchEvent(new Event('change',{bubbles:true}));
    n++; return;
   }
  }
  skipped++;
 });
 alert('Filled '+n+' field(s). Left '+skipped+' for you.'
   +'\\n\\nAnswer work authorization (Yes) and sponsorship (No) yourself -'
   +'\\nkeyword matching cannot tell those two questions apart, and a wrong'
   +'\\nanswer there filters the application out automatically.'
   +'\\n\\nNothing was submitted.');
})();
""" % {"profile": json.dumps(profile),
       "matchers": json.dumps(matchers),
       "rules": rules_js(),
       "never": json.dumps(list(apply_autofill.NEVER_FILL) + list(EEO_FIELDS)
                                + list(CONSENT_FIELDS)
                                + list(DECISIVE_FIELDS))}
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

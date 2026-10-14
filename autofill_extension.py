"""Builds an unpacked Chrome extension that fills forms on page load.

Same profile, same matchers and the same refusals as the bookmarklet -- the
only difference is what triggers it. The bookmarklet needs a click; this
runs when a Greenhouse, Lever, Ashby or Workable application opens.

Two things are harder when nobody clicks a button.

The form is not there on load. Greenhouse renders in React, so the fields
appear after the first paint; a script that runs once at DOMContentLoaded
finds an empty page. It re-runs on a MutationObserver, capped, and stops
once a pass fills nothing new.

And there is no longer a person watching at the moment it acts, which is
the argument for keeping the refusals identical rather than relaxing them.
Work authorization, sponsorship, citizenship, consent and demographics stay
untouched -- and unattended, a wrong sponsorship answer is worse, not
better, because nobody is looking when it goes in.

It never submits, and it never touches a field that already has a value, so
anything corrected by hand stays corrected.

Load it once: chrome://extensions -> Developer mode -> Load unpacked.
Re-run this after changing resume_bank.json to refresh the baked-in values.
"""

import json
import os

import autofill_bookmarklet as bm

OUT_DIR = os.path.join("prep", "autofill_extension")

SITES = [
    "https://*.greenhouse.io/*",
    "https://job-boards.greenhouse.io/*",
    "https://boards.greenhouse.io/*",
    "https://jobs.lever.co/*",
    "https://*.ashbyhq.com/*",
    "https://*.workable.com/*",
    "https://*.myworkdayjobs.com/*",
]

_MANIFEST = {
    "manifest_version": 3,
    "name": "Application autofill (Ian Liu)",
    "version": "1.0",
    "description": "Fills your own details into application forms. "
                   "Never submits, never answers sponsorship or demographics.",
    "content_scripts": [{
        "matches": SITES,
        "js": ["content.js"],
        "run_at": "document_idle",
        "all_frames": True,
    }],
}

_CONTENT = """
const P = %(profile)s;
const M = %(matchers)s;
const NEVER = %(never)s;
%(rules)s

function label(el){
 const s=[el.name,el.id,el.placeholder,el.getAttribute('aria-label')||''];
 if(el.id){const l=document.querySelector('label[for="'+CSS.escape(el.id)+'"]');
           if(l)s.push(l.innerText);}
 const p=el.closest('label'); if(p)s.push(p.innerText);
 const w=el.closest('div,fieldset');
 if(w&&w.querySelectorAll('input,textarea,select').length===1){
  const t=w.querySelector('label,legend'); if(t)s.push(t.innerText);}
 return s.join(' ').toLowerCase().replace(/\\s+/g,' ');
}

function setValue(el,v){
 const proto = el.tagName==='TEXTAREA'?HTMLTextAreaElement:HTMLInputElement;
 Object.getOwnPropertyDescriptor(proto.prototype,'value').set.call(el,v);
 el.dispatchEvent(new Event('input',{bubbles:true}));
 el.dispatchEvent(new Event('change',{bubbles:true}));
}

function fill(){
 let n=0;
 document.querySelectorAll('input,textarea').forEach(el=>{
  if(['hidden','file','password','checkbox','radio','submit','button']
      .includes(el.type)) return;
  if(el.value && el.value.trim()) return;       // never overwrite
  const L=label(el);
  if(!L) return;
  if(NEVER.some(k=>L.includes(k))) return;      // refused outright
  let hit=null;
  for(const [re,val] of RULES){ if(re.test(L)){ hit=val; break; } }
  if(hit!==null){ setValue(el,hit); n++; return; }
  let best=null,bestLen=0;
  for(const [keys,key] of M){
   for(const k of keys){
    if(P[key] && new RegExp('\\\\b'+k+'\\\\b').test(L) && k.length>bestLen){
     best=key; bestLen=k.length;}
   }
  }
  if(best){ setValue(el,P[best]); n++; }
 });
 return n;
}

function banner(total){
 if(document.getElementById('jobtracker-autofill-note')) return;
 const d=document.createElement('div');
 d.id='jobtracker-autofill-note';
 d.textContent='Autofilled '+total+' field(s). Answer work authorization, '
   +'sponsorship and availability yourself. Nothing was submitted.';
 d.style.cssText='position:fixed;bottom:16px;right:16px;z-index:2147483647;'
  +'max-width:320px;background:#111;color:#fff;font:13px/1.45 system-ui,'
  +'sans-serif;padding:11px 13px;border-radius:8px;opacity:.94;'
  +'box-shadow:0 2px 12px rgba(0,0,0,.3)';
 d.onclick=()=>d.remove();
 document.body.appendChild(d);
 setTimeout(()=>d.remove(), 12000);
}

// React mounts the form after first paint, so one pass at load finds
// nothing. Re-run as the DOM changes, and stop once a pass adds nothing.
let total=0, passes=0;
function attempt(){
 passes++;
 const n=fill();
 total+=n;
 if(total && n===0) { observer.disconnect(); banner(total); }
 if(passes>40) observer.disconnect();
}
const observer=new MutationObserver(()=>{ clearTimeout(window.__afT);
 window.__afT=setTimeout(attempt, 250); });
observer.observe(document.documentElement,{childList:true,subtree:true});
attempt();
setTimeout(()=>{ observer.disconnect(); if(total) banner(total); }, 15000);
"""


def build(out_dir: str = OUT_DIR) -> str:
    profile = bm._js_profile()
    matchers = [[list(keys), target] for keys, target in bm.MATCHERS
                if target in profile]
    never = (list(bm.apply_autofill.NEVER_FILL) + list(bm.EEO_FIELDS)
             + list(bm.CONSENT_FIELDS) + list(bm.DECISIVE_FIELDS))

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(_MANIFEST, f, indent=2)
    with open(os.path.join(out_dir, "content.js"), "w") as f:
        f.write(_CONTENT % {"profile": json.dumps(profile, indent=1),
                            "matchers": json.dumps(matchers),
                            "rules": bm.RULES_JS,
                            "never": json.dumps(never)})
    return out_dir


def run(out_dir: str = OUT_DIR) -> None:
    path = build(out_dir)
    profile = bm._js_profile()
    print(f"Extension written to {path}/")
    print(f"  manifest.json   {len(SITES)} site patterns")
    print(f"  content.js      {len(profile)} field(s) baked in\n")
    print("Fills automatically on: Greenhouse, Lever, Ashby, Workable, Workday.")
    print("Always left for you: work authorization, sponsorship, citizenship,")
    print("consent boxes, gender, veteran and disability status.\n")
    print("Install once:")
    print("  1. Chrome -> chrome://extensions")
    print("  2. Turn on Developer mode (top right)")
    print("  3. Load unpacked -> choose")
    print(f"     {os.path.abspath(path)}")

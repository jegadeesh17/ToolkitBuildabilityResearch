"""Generate verification/spot_check.html: a guided human check of the reference labeller's verdicts.

One sample app per category (seeded). For each, the page shows the reference verdict, its one-line reason and the
verbatim quote with a link to the vendor page; the person answers Correct / Wrong (+ the right verdict) / Can't tell.
The page autosaves in the browser and exports spot_check.json.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import config  # noqa: E402
from agent.store import load_apps, load_split  # noqa: E402

SPOT_SEED = 20260925


def pick(sample_ids: list[int], categories: dict[int, str], seed: int = SPOT_SEED) -> list[int]:
    rng = random.Random(seed)
    by_cat: dict[str, list[int]] = defaultdict(list)
    for i in sorted(sample_ids):
        by_cat[categories[i]].append(i)
    return sorted(rng.choice(ids) for _, ids in sorted(by_cat.items()))


def build(items: list[dict], labeller: str, seed: int) -> str:
    cfg = json.dumps({"items": items, "labeller": labeller, "seed": seed}, ensure_ascii=False).replace("</", "<\\/")
    return _PAGE.substitute(cfg=cfg)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(config.VERIFICATION_DIR / "spot_check.html"))
    args = ap.parse_args(argv)
    ref_path = config.VERIFICATION_DIR / "reference_evidence.json"
    if not ref_path.exists():
        print("verification/reference_evidence.json not found: run scripts/import_reference_labels.py first")
        return 2
    ref = json.loads(ref_path.read_text(encoding="utf-8"))
    apps = {a.id: a for a in load_apps()}
    chosen = pick(load_split("sample"), {i: a.category for i, a in apps.items()})
    items = []
    for i in chosen:
        r = ref["apps"].get(str(i), {})
        ev = (r.get("evidence") or {}).get("verdict") or {}
        items.append({"id": i, "app": apps[i].app, "category": apps[i].category, "verdict": r.get("verdict", "unknown"),
                      "reason": r.get("verdict_reason", ""), "url": ev.get("url", ""), "quote": ev.get("quote", "")})
    out = Path(args.out)
    out.write_text(build(items, ref.get("labeller", "agent"), SPOT_SEED), encoding="utf-8")
    print(f"wrote {out} ({len(items)} apps, one per category, seed {SPOT_SEED})")
    return 0


_PAGE = Template("""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Verdict spot check</title>
<style>
body{margin:0;background:#0b0b0c;color:#f2f2f2;font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:860px;margin:0 auto;padding:28px 20px 60px}
h1{font-weight:500;font-size:24px;margin:0 0 8px}p.lede{color:rgba(255,255,255,.75);margin:0 0 14px}
.defs{border:1px solid rgba(255,255,255,.14);border-radius:4px;padding:12px 16px;margin:0 0 22px;color:rgba(255,255,255,.8);font-size:15px}
.defs b{color:#fff}
.card{border:1px solid rgba(255,255,255,.14);border-radius:4px;padding:16px 18px;margin:0 0 14px}
.card.done{border-color:rgba(52,211,153,.6)}
.card h2{font-size:18px;font-weight:600;margin:0}.meta{color:rgba(255,255,255,.65);font-size:14px;margin:2px 0 10px}
.v{display:inline-block;border:1px solid #60a5fa;color:#60a5fa;border-radius:2px;padding:1px 8px;font-size:14px}
blockquote{margin:10px 0;padding:8px 12px;border-left:3px solid rgba(255,255,255,.25);color:#fff}
a{color:#60a5fa;word-break:break-all}
.btns{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
button{background:transparent;color:#f2f2f2;border:1px solid rgba(255,255,255,.3);border-radius:3px;padding:7px 12px;font:inherit;cursor:pointer}
button[aria-pressed=true]{background:#fff;color:#000;border-color:#fff}
select{background:#111;color:#fff;border:1px solid rgba(255,255,255,.3);padding:6px;margin-left:8px}
.bar{position:sticky;top:0;background:#0b0b0c;padding:10px 0;border-bottom:1px solid rgba(255,255,255,.12);margin-bottom:16px;display:flex;gap:14px;align-items:center}
#export{background:#fff;color:#000;border:0;font-weight:600}#export[disabled]{opacity:.35;cursor:not-allowed}
</style></head><body><main>
<h1>Verdict spot check</h1>
<p class="lede">An AI agent researched these apps on its own. For each one, open the link, read the quoted sentence, and say whether the agent's verdict is right. About 1–2 minutes per app. Your answers save automatically in this browser.</p>
<div class="defs"><b>Buildable now</b>: the app has a public API and a developer can get working credentials alone, free or on a trial.<br>
<b>Gated</b>: there is a public API, but you need a paid plan, an approval, a partnership or a sales call first.<br>
<b>Blocked</b>: no public API, read-only, or the terms forbid automation.<br>
<b>Can't tell</b>: pick this if the page doesn't let you decide in two minutes. That's a valid answer.</div>
<div class="bar"><span id="progress">0 of 0 answered</span><button id="export" disabled>Export spot_check.json</button></div>
<div id="cards"></div>
</main>
<script type="application/json" id="cfg">$cfg</script>
<script>
(function(){
const CFG = JSON.parse(document.getElementById('cfg').textContent), KEY = 'tbr-spot-v1';
const LABEL = {buildable_now:'Buildable now', buildable_gated:'Gated', blocked:'Blocked', unknown:'Unknown'};
let st = {}; try { st = JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch (e) {}
function save(){ try { localStorage.setItem(KEY, JSON.stringify(st)); } catch (e) {} }
function el(t, a, ...k){ const e = document.createElement(t); Object.entries(a||{}).forEach(([x,y]) => x==='text' ? e.textContent=y : e.setAttribute(x,y)); k.forEach(c => c && e.appendChild(c)); return e; }
function done(id){ const s = st[id]; return s && (s.judgement === 'correct' || s.judgement === 'cant_tell' || (s.judgement === 'wrong' && s.corrected)); }
function render(){
  const root = document.getElementById('cards'); root.innerHTML = '';
  CFG.items.forEach(it => {
    const s = st[it.id] || {}; const c = el('section', {class: 'card' + (done(it.id) ? ' done' : '')});
    c.appendChild(el('h2', {text: it.app})); c.appendChild(el('div', {class: 'meta', text: it.category}));
    c.appendChild(el('div', {}, document.createTextNode('Agent verdict: '), el('span', {class: 'v', text: LABEL[it.verdict] || it.verdict})));
    if (it.reason) c.appendChild(el('p', {text: 'Why: ' + it.reason}));
    if (it.quote) c.appendChild(el('blockquote', {text: it.quote}));
    if (it.url) c.appendChild(el('div', {}, el('a', {href: it.url, target: '_blank', rel: 'noopener', text: 'Open the source page'})));
    const b = el('div', {class: 'btns'});
    [['correct','Correct'],['wrong','Wrong'],['cant_tell',"Can't tell"]].forEach(([v,t]) => {
      const btn = el('button', {type: 'button', 'aria-pressed': String(s.judgement === v), text: t});
      btn.onclick = () => { st[it.id] = Object.assign({}, st[it.id], {judgement: v}); save(); render(); };
      b.appendChild(btn);
    });
    if (s.judgement === 'wrong') {
      const sel = el('select', {}); sel.appendChild(el('option', {value: '', text: 'Right verdict…'}));
      ['buildable_now','buildable_gated','blocked'].filter(v => v !== it.verdict).forEach(v => sel.appendChild(el('option', {value: v, text: LABEL[v]})));
      sel.value = s.corrected || ''; sel.onchange = () => { st[it.id].corrected = sel.value; save(); render(); };
      b.appendChild(sel);
    }
    c.appendChild(b); root.appendChild(c);
  });
  const n = CFG.items.filter(it => done(it.id)).length;
  document.getElementById('progress').textContent = n + ' of ' + CFG.items.length + ' answered';
  document.getElementById('export').disabled = n !== CFG.items.length;
}
document.getElementById('export').onclick = () => {
  const checks = CFG.items.map(it => ({id: it.id, app: it.app, category: it.category, reference_verdict: it.verdict,
    judgement: st[it.id].judgement, corrected_verdict: st[it.id].corrected || null}));
  const doc = {created_at: new Date().toISOString(), checker: 'human', seed: CFG.seed, labeller: CFG.labeller, checks: checks};
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([JSON.stringify(doc, null, 1)], {type: 'application/json'}));
  a.download = 'spot_check.json'; document.body.appendChild(a); a.click(); a.remove();
};
render();
})();
</script></body></html>
""")

if __name__ == "__main__":
    sys.exit(main())

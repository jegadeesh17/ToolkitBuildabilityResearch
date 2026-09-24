"""Generate verification/label.html: an offline form for blind-labelling the 20 sample apps.

The page embeds only seed data (id, name, category, hint) and the schema's enums. It never
contains agent output, so the human labels without anchoring on what the agent said.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.schema import FIELD_DEFINITIONS, FIELD_ENUMS, SCORED_FIELDS, SET_FIELDS  # noqa: E402


def schema_spec() -> dict:
    fields = []
    for name in SCORED_FIELDS:
        options = [str(v) for v in FIELD_ENUMS[name]]
        if "unknown" not in options:
            options.append("unknown")
        fields.append({"name": name, "multi": name in SET_FIELDS, "options": options,
                       "definition": FIELD_DEFINITIONS[name]})
    return {"fields": fields}


def _home(hint: str) -> str | None:
    token = (hint or "").split()[0] if hint and hint.split() else ""
    return f"https://{token}" if "." in token else None


def build_label_html(apps: list[dict], sample_ids: list[int], spec: dict, seed: int | None = None) -> str:
    wanted = set(sample_ids)
    chosen = [{"id": a["id"], "app": a["app"], "category": a["category"], "hint": a["hint"],
               "home": _home(a["hint"])} for a in apps if a["id"] in wanted]
    missing = wanted - {a["id"] for a in chosen}
    if missing:
        raise ValueError(f"sample ids not in apps.json: {sorted(missing)}")
    cfg = {"sample_ids": sorted(wanted), "apps": sorted(chosen, key=lambda a: a["id"]),
           "fields": spec["fields"], "seed": seed,
           "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    config_json = json.dumps(cfg, ensure_ascii=False).replace("</", "<\\/")
    return _PAGE.substitute(config_json=config_json, css=_CSS, js=_JS)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apps", default=str(ROOT / "data" / "apps.json"))
    ap.add_argument("--sample", default=str(ROOT / "data" / "sample.json"))
    ap.add_argument("--out", default=str(ROOT / "verification" / "label.html"))
    args = ap.parse_args(argv)
    apps = json.loads(Path(args.apps).read_text(encoding="utf-8"))
    sample = json.loads(Path(args.sample).read_text(encoding="utf-8"))
    html = build_label_html(apps, sample["ids"], schema_spec(), seed=sample.get("seed"))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({len(sample['ids'])} apps)")
    return 0


_PAGE = Template("""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Blind labelling - 20-app sample</title>
<style>$css</style>
</head>
<body>
<header>
  <h1>Blind labelling &middot; 20-app sample</h1>
  <p class="lede">Label each app from its <strong>real documentation only</strong>. Do not look at any agent
  output until you have exported. Pick <code>unknown</code> when the docs don't settle a field.
  Your work autosaves in this browser.</p>
  <div class="bar">
    <span id="progress" class="mono">0/20 complete</span>
    <label class="btn secondary">Import JSON<input id="import" type="file" accept="application/json" hidden></label>
    <button id="export" class="btn" disabled>Export ground_truth.json</button>
  </div>
  <p class="mono small">After exporting, move the file to <code>verification/ground_truth.json</code>.</p>
</header>
<main id="cards"></main>
<script type="application/json" id="label-config">$config_json</script>
<script>$js</script>
</body>
</html>
""")

_CSS = """
:root{--bg:#0b0b0c;--card:#131316;--line:rgba(255,255,255,.12);--text:#f2f2f2;--muted:rgba(255,255,255,.72);
--ok:#34d399;--warn:#fbbf24;--accent:#60a5fa}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{position:sticky;top:0;z-index:2;background:var(--bg);border-bottom:1px solid var(--line);padding:16px 20px}
h1{margin:0 0 4px;font-weight:500;font-size:20px}.lede{margin:0 0 10px;color:var(--muted);max-width:900px}
.bar{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.mono{font-family:ui-monospace,Consolas,monospace;letter-spacing:.02em}.small{font-size:12px;color:var(--muted);margin:8px 0 0}
.btn{background:#fff;color:#000;border:0;border-radius:2px;padding:8px 14px;font:600 13px ui-monospace,Consolas,monospace;
cursor:pointer}.btn[disabled]{opacity:.35;cursor:not-allowed}.btn.secondary{background:transparent;color:var(--text);
border:1px solid var(--line)}
main{padding:20px;display:grid;gap:16px;max-width:1100px}
.card{background:var(--card);border:1px solid var(--line);border-radius:4px;padding:16px}
.card.done{border-color:rgba(52,211,153,.55)}
.card h2{margin:0;font-size:17px;font-weight:600}.meta{color:var(--muted);font-size:13px;margin:2px 0 8px}
.links a{color:var(--accent);margin-right:14px;font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px 18px;margin-top:12px}
.field label.name{display:block;font:600 12px ui-monospace,Consolas,monospace;text-transform:uppercase;letter-spacing:.06em}
.def{color:var(--muted);font-size:12px;margin:2px 0 6px}
select,input[type=url],textarea{width:100%;background:#0e0e10;color:var(--text);border:1px solid var(--line);
border-radius:2px;padding:6px 8px;font:inherit}
.checks{display:flex;flex-wrap:wrap;gap:4px 12px}.checks label{font-size:13px;white-space:nowrap}
.warn{color:var(--warn);font-size:13px;margin-top:10px}.status{float:right;font-size:12px}
.status.ok{color:var(--ok)}.status.todo{color:var(--muted)}
"""

_JS = r"""
(function(){
const CFG = JSON.parse(document.getElementById('label-config').textContent);
const KEY = 'tbr-labels-v1';
const FIELDS = CFG.fields;
let state = {};
try { state = JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch (e) { state = {}; }

function blank(){ const s = {source_url:'', extra_urls:'', notes:''};
  FIELDS.forEach(f => s[f.name] = f.multi ? [] : ''); return s; }
CFG.apps.forEach(a => { if (!state[a.id]) state[a.id] = blank(); });

function save(){ try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {} }
function isUrl(u){ return /^https?:\/\/\S+\.\S+/.test((u||'').trim()); }
function fieldDone(f, v){ return f.multi ? Array.isArray(v) && v.length > 0 : !!v; }
function appDone(id){ const s = state[id]; return FIELDS.every(f => fieldDone(f, s[f.name])) && isUrl(s.source_url); }

function warnings(s){
  const w = [], at = s.api_type, v = s.verdict, b = s.blocker, am = s.access_model;
  const gated = ['paid_plan_required','admin_or_approval','partner_or_sales'];
  if (Array.isArray(at) && at.length === 1 && at[0] === 'none_public' && v && v !== 'blocked')
    w.push('Rule 1: api_type none_public normally means verdict = blocked.');
  if (v === 'buildable_now' && ((b && b !== 'none') || (am && !['self_serve_free','self_serve_trial'].includes(am))))
    w.push('Rule 2: buildable_now normally needs blocker = none and self-serve access.');
  if (gated.includes(am) && v && !['buildable_gated','blocked'].includes(v))
    w.push('Rule 3: gated access normally means verdict = buildable_gated or blocked.');
  if (b === 'none' && v && !['buildable_now','unknown'].includes(v))
    w.push('Rule 4: blocker none normally means verdict = buildable_now.');
  return w;
}

function el(tag, attrs, ...kids){ const e = document.createElement(tag);
  Object.entries(attrs||{}).forEach(([k,v]) => { if (k === 'text') e.textContent = v; else e.setAttribute(k, v); });
  kids.forEach(k => k && e.appendChild(k)); return e; }

function search(q){ return 'https://duckduckgo.com/?q=' + encodeURIComponent(q); }

function render(){
  const root = document.getElementById('cards'); root.innerHTML = '';
  CFG.apps.forEach(a => {
    const s = state[a.id];
    const card = el('section', {class:'card', id:'app-'+a.id});
    const status = el('span', {class:'status mono'});
    card.appendChild(status);
    card.appendChild(el('h2', {text: a.app}));
    card.appendChild(el('div', {class:'meta', text: a.category + ' · hint: ' + a.hint + ' · id ' + a.id}));
    const links = el('div', {class:'links'});
    if (a.home) links.appendChild(el('a', {href:a.home, target:'_blank', rel:'noopener', text:'hint site'}));
    links.appendChild(el('a', {href:search(a.app+' API documentation'), target:'_blank', rel:'noopener', text:'search: API docs'}));
    links.appendChild(el('a', {href:search(a.app+' API pricing access'), target:'_blank', rel:'noopener', text:'search: API access/pricing'}));
    links.appendChild(el('a', {href:search(a.app+' MCP server'), target:'_blank', rel:'noopener', text:'search: MCP server'}));
    card.appendChild(links);
    const grid = el('div', {class:'grid'});
    FIELDS.forEach(f => {
      const box = el('div', {class:'field'});
      box.appendChild(el('label', {class:'name', text: f.name}));
      box.appendChild(el('div', {class:'def', text: f.definition}));
      if (f.multi) {
        const checks = el('div', {class:'checks'});
        f.options.forEach(opt => {
          const cb = el('input', {type:'checkbox', 'data-app':a.id, 'data-field':f.name, value:opt});
          cb.checked = s[f.name].includes(opt);
          if (opt !== 'unknown' && s[f.name].includes('unknown')) cb.disabled = true;
          cb.addEventListener('change', () => {
            let cur = state[a.id][f.name];
            if (opt === 'unknown') cur = cb.checked ? ['unknown'] : [];
            else cur = cb.checked ? cur.filter(x => x !== 'unknown').concat([opt]) : cur.filter(x => x !== opt);
            state[a.id][f.name] = f.options.filter(o => cur.includes(o));
            save(); render();
          });
          checks.appendChild(el('label', {}, cb, document.createTextNode(' ' + opt)));
        });
        box.appendChild(checks);
      } else {
        const sel = el('select', {'data-app':a.id, 'data-field':f.name});
        sel.appendChild(el('option', {value:'', text:'— choose —'}));
        f.options.forEach(opt => sel.appendChild(el('option', {value:opt, text:opt})));
        sel.value = s[f.name];
        sel.addEventListener('change', () => { state[a.id][f.name] = sel.value; save(); refresh(); });
        box.appendChild(sel);
      }
      grid.appendChild(box);
    });
    const src = el('div', {class:'field'});
    src.appendChild(el('label', {class:'name', text:'source_url (required)'}));
    const srcIn = el('input', {type:'url', placeholder:'https://…', 'data-app':a.id});
    srcIn.value = s.source_url;
    srcIn.addEventListener('input', () => { state[a.id].source_url = srcIn.value; save(); refresh(); });
    src.appendChild(srcIn); grid.appendChild(src);
    const extra = el('div', {class:'field'});
    extra.appendChild(el('label', {class:'name', text:'extra urls (one per line, optional)'}));
    const exIn = el('textarea', {rows:'2'}); exIn.value = s.extra_urls;
    exIn.addEventListener('input', () => { state[a.id].extra_urls = exIn.value; save(); });
    extra.appendChild(exIn); grid.appendChild(extra);
    const notes = el('div', {class:'field'});
    notes.appendChild(el('label', {class:'name', text:'notes (optional)'}));
    const nIn = el('textarea', {rows:'2'}); nIn.value = s.notes;
    nIn.addEventListener('input', () => { state[a.id].notes = nIn.value; save(); });
    notes.appendChild(nIn); grid.appendChild(notes);
    card.appendChild(grid);
    card.appendChild(el('div', {class:'warn', id:'warn-'+a.id}));
    root.appendChild(card);
  });
  refresh();
}

function refresh(){
  let done = 0;
  CFG.apps.forEach(a => {
    const ok = appDone(a.id); if (ok) done++;
    const card = document.getElementById('app-'+a.id);
    card.classList.toggle('done', ok);
    const st = card.querySelector('.status');
    st.textContent = ok ? '✓ complete' : '… incomplete'; st.className = 'status mono ' + (ok ? 'ok' : 'todo');
    document.getElementById('warn-'+a.id).textContent = warnings(state[a.id]).map(w => '⚠ ' + w).join('  ');
  });
  document.getElementById('progress').textContent = done + '/' + CFG.apps.length + ' complete';
  document.getElementById('export').disabled = done !== CFG.apps.length;
}

function exportJson(){
  const labels = CFG.apps.map(a => {
    const s = state[a.id], out = {id: a.id, app: a.app};
    FIELDS.forEach(f => {
      const v = s[f.name];
      out[f.name] = f.multi ? (v.length === 1 && v[0] === 'unknown' ? 'unknown' : v) : v;
    });
    out.source_url = s.source_url.trim();
    out.extra_urls = (s.extra_urls || '').split(/\s+/).map(x => x.trim()).filter(isUrl);
    out.notes = s.notes || '';
    return out;
  });
  const doc = {created_at: new Date().toISOString(), labeller: 'human', blind: true, labels: labels};
  const blob = new Blob([JSON.stringify(doc, null, 1)], {type:'application/json'});
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob); link.download = 'ground_truth.json';
  document.body.appendChild(link); link.click(); link.remove();
}

function importJson(file){
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const doc = JSON.parse(reader.result);
      (doc.labels || []).forEach(l => {
        if (!state[l.id]) return;
        const s = blank();
        FIELDS.forEach(f => {
          const v = l[f.name];
          s[f.name] = f.multi ? (v === 'unknown' ? ['unknown'] : (Array.isArray(v) ? v : [])) : (v || '');
        });
        s.source_url = l.source_url || ''; s.extra_urls = (l.extra_urls || []).join('\n'); s.notes = l.notes || '';
        state[l.id] = s;
      });
      save(); render();
    } catch (e) { alert('Could not read that file: ' + e.message); }
  };
  reader.readAsText(file);
}

window.__fillAllForTest = function(){
  CFG.apps.forEach(a => {
    const s = state[a.id];
    FIELDS.forEach(f => { s[f.name] = f.multi ? [f.options[0]] : f.options[0]; });
    s.source_url = 'https://example.com/';
  });
  save(); render();
};

document.getElementById('export').addEventListener('click', exportJson);
document.getElementById('import').addEventListener('change', e => e.target.files[0] && importJson(e.target.files[0]));
render();
})();
"""

if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
build_index.py -- turn the puush osu! archive jsonl files into one offline HTML browser.

    python3 build_index.py /path/to/jsonl/folder  [-o osu_puush_index.html]

Reads replays_osr.jsonl, replays_zip.jsonl, beatmaps_osz.jsonl, beatmaps_zip.jsonl,
skins_osk.jsonl, skins_zip.jsonl. Missing files are skipped.

Player and beatmap names are dictionary-encoded (5,502 and 8,292 distinct values across
24k rows), which cuts the embedded payload from ~1.25 MB to ~0.41 MB for those columns.
"""

import json
import os
import sys

MODES = ["osu", "taiko", "fruits", "mania"]


def load(folder, name):
    path = os.path.join(folder, name)
    if not os.path.exists(path):
        print(f"  skip {name} (not found)")
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    print(f"  {name}: {len(out)} rows")
    return out


def accuracy(r):
    """osu! accuracy per mode, 0-100. Returns None if the counts don't add up."""
    n300, n100, n50 = r.get("n300") or 0, r.get("n100") or 0, r.get("n50") or 0
    miss, geki, katu = r.get("miss") or 0, r.get("geki") or 0, r.get("katu") or 0
    m = r.get("mode")
    try:
        if m == "taiko":
            tot = n300 + n100 + miss
            return 100.0 * (n300 + 0.5 * n100) / tot if tot else None
        if m == "fruits":
            tot = n300 + n100 + n50 + katu + miss
            return 100.0 * (n300 + n100 + n50) / tot if tot else None
        if m == "mania":
            tot = n300 + geki + katu + n100 + n50 + miss
            hit = 300 * (n300 + geki) + 200 * katu + 100 * n100 + 50 * n50
            return 100.0 * hit / (300 * tot) if tot else None
        tot = n300 + n100 + n50 + miss
        hit = 300 * n300 + 100 * n100 + 50 * n50
        return 100.0 * hit / (300 * tot) if tot else None
    except (TypeError, ZeroDivisionError):
        return None


def flags(r):
    """Data-quality flags. Bit 1 = impossible date, 2 = impossible score, 4 = pre-2007 client."""
    f = 0
    d = r.get("replay_date")
    if d and not ("2007-01-01" <= d[:10] <= "2026-12-31"):
        f |= 1
    s = r.get("score") or 0
    if s > 400_000_000:
        f |= 2
    v = r.get("game_version") or 0
    if 0 < v < 20070000:
        f |= 4
    return f


def build_replays(rows):
    players, beatmaps = {}, {}

    def idx(d, v):
        v = v or ""
        if v not in d:
            d[v] = len(d)
        return d[v]

    out = []
    for r in rows:
        acc = accuracy(r)
        d = r.get("replay_date")
        out.append([
            idx(players, r.get("player")),
            idx(beatmaps, r.get("beatmap")),
            MODES.index(r["mode"]) if r.get("mode") in MODES else 0,
            r.get("mods") or "NM",
            r.get("score") or 0,
            r.get("max_combo") or 0,
            round(acc, 2) if acc is not None else -1,
            r.get("miss") or 0,
            (d[:10] if d else ""),
            r.get("game_version") or 0,
            r.get("puush_id") or "",
            (r.get("wayback_url") or "").split("/web/")[-1].split("id_/")[0],
            int(r.get("size_bytes") or 0),
            len(r.get("copies") or []),
            r.get("beatmap_hash") or "",
            flags(r),
        ])
    inv = lambda d: [k for k, _ in sorted(d.items(), key=lambda kv: kv[1])]
    return {"players": inv(players), "beatmaps": inv(beatmaps), "rows": out}


TYPE_MAP = {"skin": "skin", "beatmap": "beatmap", "replays": "replay-archive"}


def route(r, fallback):
    """Decide what an archive really is from its contents, not which file it came from.

    Hard evidence in `contents` wins. Otherwise fall back to the maintainer's `type`
    field, then to the file the row came from. Their type field is right the vast
    majority of the time -- this only catches the ~200 rows where it isn't.
    """
    contents = [c.lower() for c in (r.get("contents") or [])]
    ev = (r.get("evidence") or "").lower()
    fn = (r.get("filename") or "").lower()
    blob = " ".join(contents) + " " + ev

    has_osr = any(c.endswith(".osr") for c in contents) or ".osr" in ev
    has_osk = fn.endswith(".osk") or any(c.endswith(".osk") for c in contents)
    has_map = (fn.endswith(".osz") or any(c.endswith((".osu", ".osz")) for c in contents)
               or ".osu" in ev)
    is_skin = has_osk or "skin.ini" in blob

    if has_osr and not is_skin and not has_map:
        return "replay-archive"
    if is_skin:
        return "skin"
    if has_map and "skin.ini" not in blob:
        return "beatmap"
    return TYPE_MAP.get(r.get("type"), fallback)


def build_files(rows, fallback):
    """Archive rows -- flat, no dictionary (filenames are near-unique)."""
    out = []
    for r in rows:
        contents = r.get("contents") or []
        first = r.get("zip_first_entry") or (contents[0] if contents else "")
        out.append([
            r.get("filename") or "",
            int(r.get("size_bytes") or 0),
            r.get("archived") or "",
            r.get("puush_id") or "",
            (r.get("wayback_url") or "").split("/web/")[-1].split("id_/")[0],
            first,
            r.get("entries") or (1 if first else 0),
            r.get("evidence") or "",
            route(r, fallback),
        ])
    return out


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>osu! puush archive index</title>
<style>
:root{
  --bg:#14111c; --panel:#1d1928; --panel2:#241f31; --line:#302943;
  --text:#ede8f5; --dim:#8f86a8; --dimmer:#6b6382;
  --osu:#ff66ab; --taiko:#ff7a47; --fruits:#66dd88; --mania:#7a9cff;
  --warn:#ffcc55;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
}
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{background:var(--bg);color:var(--text);font-family:var(--sans);
  font-size:14px;line-height:1.5;display:flex;flex-direction:column;
  -webkit-font-smoothing:antialiased}

header{padding:26px 24px 0;max-width:1500px;width:100%;margin:0 auto}
h1{font-size:19px;font-weight:600;letter-spacing:-.01em;margin:0 0 3px}
h1 b{color:var(--osu);font-weight:600}
.sub{color:var(--dim);font-size:12.5px;margin:0 0 20px;max-width:70ch}
.sub a{color:var(--dim)}

.searchwrap{position:relative;margin-bottom:14px}
#q{width:100%;background:var(--panel);border:1px solid var(--line);color:var(--text);
  border-radius:9px;padding:15px 16px 15px 44px;font-size:16px;font-family:var(--sans);
  outline:none;transition:border-color .12s}
#q:focus{border-color:var(--osu)}
#q::placeholder{color:var(--dimmer)}
.mag{position:absolute;left:16px;top:50%;transform:translateY(-50%);
  width:15px;height:15px;border:1.8px solid var(--dimmer);border-radius:50%}
.mag::after{content:"";position:absolute;right:-6px;bottom:-4px;width:7px;height:1.8px;
  background:var(--dimmer);transform:rotate(45deg)}

nav{display:flex;gap:4px;border-bottom:1px solid var(--line);margin-bottom:14px}
nav button{background:none;border:none;border-bottom:2px solid transparent;color:var(--dim);
  font:inherit;font-size:13.5px;padding:9px 14px;cursor:pointer;margin-bottom:-1px}
nav button:hover{color:var(--text)}
nav button[aria-selected=true]{color:var(--text);border-bottom-color:var(--osu)}
nav button span{color:var(--dimmer);font-family:var(--mono);font-size:11.5px;margin-left:7px}

.controls{display:flex;flex-wrap:wrap;gap:7px;align-items:center;margin-bottom:12px}
.chip{background:var(--panel);border:1px solid var(--line);color:var(--dim);
  border-radius:999px;padding:5px 13px;font:inherit;font-size:12.5px;cursor:pointer}
.chip:hover{color:var(--text)}
.chip[aria-pressed=true]{color:#14111c;font-weight:600}
.chip[data-m="0"][aria-pressed=true]{background:var(--osu);border-color:var(--osu)}
.chip[data-m="1"][aria-pressed=true]{background:var(--taiko);border-color:var(--taiko)}
.chip[data-m="2"][aria-pressed=true]{background:var(--fruits);border-color:var(--fruits)}
.chip[data-m="3"][aria-pressed=true]{background:var(--mania);border-color:var(--mania)}
.chip.flag[aria-pressed=true]{background:var(--warn);border-color:var(--warn)}
select{background:var(--panel);border:1px solid var(--line);color:var(--text);
  border-radius:7px;padding:6px 9px;font:inherit;font-size:12.5px}
.count{margin-left:auto;color:var(--dim);font-family:var(--mono);font-size:12px}
.count b{color:var(--text);font-weight:600}

main{flex:1;min-height:0;max-width:1500px;width:100%;margin:0 auto;padding:0 24px 24px;
  display:flex;flex-direction:column}
.thead{display:flex;border-bottom:1px solid var(--line);color:var(--dimmer);
  font-size:11.5px;padding:0 4px 7px;user-select:none}
.thead div{cursor:pointer;white-space:nowrap;overflow:hidden}
.thead div:hover{color:var(--text)}
.thead div.on{color:var(--osu)}
#scroll{flex:1;overflow-y:auto;position:relative;contain:strict}
#pad{position:relative}
.row{display:flex;align-items:center;padding:0 4px;border-bottom:1px solid #241f31;
  cursor:pointer;position:absolute;left:0;right:0;height:34px}
.row:hover{background:var(--panel)}
.row.open{background:var(--panel2)}
.row>div{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding-right:12px}
.num{font-family:var(--mono);font-size:12px;text-align:right}
.mono{font-family:var(--mono);font-size:12px;color:var(--dim)}
.dim{color:var(--dim)}
.dot{display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:7px;
  vertical-align:1px}
.m0{background:var(--osu)} .m1{background:var(--taiko)}
.m2{background:var(--fruits)} .m3{background:var(--mania)}
.mods{font-family:var(--mono);font-size:11.5px;color:var(--osu)}
.mods.nm{color:var(--dimmer)}
.bang{color:var(--warn);font-family:var(--mono);font-size:11px}

.detail{position:absolute;left:0;right:0;background:var(--panel2);
  border-bottom:1px solid var(--line);padding:14px 18px;font-size:12.5px}
.detail dl{display:grid;grid-template-columns:max-content 1fr;gap:5px 16px;margin:0 0 11px}
.detail dt{color:var(--dimmer)}
.detail dd{margin:0;font-family:var(--mono);word-break:break-all}
.detail .acts{display:flex;gap:8px;flex-wrap:wrap}
.detail a,.detail button{background:var(--osu);color:#14111c;border:none;border-radius:6px;
  padding:6px 13px;font:inherit;font-size:12.5px;font-weight:600;text-decoration:none;cursor:pointer}
.detail button.ghost{background:none;border:1px solid var(--line);color:var(--dim);font-weight:400}
.notice{color:var(--warn);margin-bottom:10px}
.empty{padding:48px 4px;color:var(--dim)}
.empty b{color:var(--text);display:block;margin-bottom:5px;font-weight:600}
@media (max-width:820px){
  .hide-s{display:none!important}
  header,main{padding-left:14px;padding-right:14px}
}
</style>
</head>
<body>
<header>
  <h1>osu! puush archive index <b>·</b> <span id="grand" class="mono"></span> captures</h1>
  <p class="sub">Replays, beatmaps and skins uploaded to puu.sh and preserved by the Wayback
     Machine. Search a player, a beatmap, or a filename. Every row links to the raw capture.</p>
  <div class="searchwrap"><i class="mag"></i>
    <input id="q" placeholder="Cookiezi, Freedom Dive, Blue Zenith&hellip;" autocomplete="off" spellcheck="false"></div>
  <nav id="tabs"></nav>
  <div class="controls" id="ctl"></div>
</header>
<main>
  <div class="thead" id="thead"></div>
  <div id="scroll"><div id="pad"></div></div>
</main>
<script id="payload" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('payload').textContent);
const MODES=['osu','taiko','catch','mania'];
const RH=34;
const $=s=>document.querySelector(s);

document.getElementById('grand').textContent=
  (D.replays.rows.length+D.beatmaps.length+D.skins.length).toLocaleString();

/* ---- flatten replays into objects once, lazily per tab ---- */
const R=D.replays.rows.map(r=>({
  player:D.replays.players[r[0]], beatmap:D.replays.beatmaps[r[1]], mode:r[2],
  mods:r[3], score:r[4], combo:r[5], acc:r[6], miss:r[7], date:r[8], gver:r[9],
  id:r[10], ts:r[11], size:r[12], copies:r[13], hash:r[14], flag:r[15]
}));
const mkFiles=a=>a.map(r=>({
  filename:r[0], size:r[1], archived:r[2], id:r[3], ts:r[4],
  first:r[5], entries:r[6], evidence:r[7], kind:r[8], dup:r[9]
}));
const B=mkFiles(D.beatmaps), S=mkFiles(D.skins), A=mkFiles(D.archives);

/* search keys, built once */
R.forEach(r=>r._k=(r.player+' '+r.beatmap+' '+r.id).toLowerCase());
[B,S,A].forEach(a=>a.forEach(r=>r._k=(r.filename+' '+r.first+' '+r.id).toLowerCase()));

const TABS=[
  {k:'replays', label:'Replays', data:R, n:R.length},
  {k:'beatmaps',label:'Beatmaps',data:B, n:B.length},
  {k:'skins',   label:'Skins',   data:S, n:S.length},
  {k:'archives',label:'Replay archives', data:A, n:A.length},
];
const COLS={
  replays:[
    ['player','Player','flex:0 0 150px'],
    ['beatmap','Beatmap','flex:1 1 340px'],
    ['mods','Mods','flex:0 0 84px'],
    ['acc','Accuracy','flex:0 0 78px',1],
    ['score','Score','flex:0 0 104px',1,'hide-s'],
    ['combo','Combo','flex:0 0 72px',1,'hide-s'],
    ['date','Played','flex:0 0 92px',0,'hide-s'],
  ],
  beatmaps:[
    ['filename','Filename','flex:1 1 480px'],
    ['first','First entry','flex:1 1 300px',0,'hide-s'],
    ['size','Size','flex:0 0 84px',1],
    ['entries','Files','flex:0 0 60px',1,'hide-s'],
    ['archived','Archived','flex:0 0 92px',0,'hide-s'],
  ],
};
COLS.skins=COLS.beatmaps; COLS.archives=COLS.beatmaps;

let tab='replays', modes=[true,true,true,true], flagOnly=false, hideDup=false,
    sortKey='player', sortDir=1, view=[], open=-1;

/* ---- chrome ---- */
$('#tabs').innerHTML=TABS.map(t=>
  `<button data-k="${t.k}" aria-selected="${t.k===tab}">${t.label}<span>${t.n.toLocaleString()}</span></button>`).join('');
$('#tabs').onclick=e=>{const b=e.target.closest('button'); if(!b)return;
  tab=b.dataset.k; open=-1;
  sortKey = tab==='replays' ? 'player' : 'filename'; sortDir=1;
  chrome(); refresh();};

function chrome(){
  [...$('#tabs').children].forEach(b=>b.setAttribute('aria-selected',b.dataset.k===tab));
  $('#ctl').innerHTML = tab==='replays'
    ? MODES.map((m,i)=>`<button class="chip" data-m="${i}" aria-pressed="${modes[i]}">${m}</button>`).join('')
      + `<button class="chip flag" id="fl" aria-pressed="${flagOnly}">flagged only</button>`
      + `<span class="count" id="cnt"></span>`
    : `<button class="chip flag" id="dp" aria-pressed="${hideDup}">hide re-captures</button>`
      + `<span class="count" id="cnt"></span>`;
  $('#ctl').onclick=e=>{const c=e.target.closest('.chip'); if(!c)return;
    if(c.id==='fl'){flagOnly=!flagOnly;}
    else if(c.id==='dp'){hideDup=!hideDup;}
    else {modes[+c.dataset.m]=!modes[+c.dataset.m];}
    chrome(); refresh();};
  $('#thead').innerHTML=COLS[tab].map(c=>
    `<div style="${c[2]}" class="${c[4]||''} ${c[0]===sortKey?'on':''}" data-k="${c[0]}">${c[1]}${
      c[0]===sortKey?(sortDir>0?' \u2191':' \u2193'):''}</div>`).join('');
  $('#thead').onclick=e=>{const d=e.target.closest('[data-k]'); if(!d)return;
    if(sortKey===d.dataset.k) sortDir=-sortDir; else {sortKey=d.dataset.k; sortDir=1;}
    chrome(); refresh();};
}

/* ---- filter + sort ---- */
function refresh(){
  const q=$('#q').value.trim().toLowerCase();
  const terms=q?q.split(/\s+/):[];
  let a=TABS.find(t=>t.k===tab).data;
  if(tab==='replays'){
    if(!modes.every(Boolean)) a=a.filter(r=>modes[r.mode]);
    if(flagOnly) a=a.filter(r=>r.flag);
  } else if(hideDup){
    a=a.filter(r=>!r.dup);
  }
  if(terms.length) a=a.filter(r=>terms.every(t=>r._k.includes(t)));
  const k=sortKey, dir=sortDir;
  a=a.slice().sort((x,y)=>{
    let p=x[k], n=y[k];
    if(typeof p==='string'||typeof n==='string'){
      p=(p||'').toLowerCase(); n=(n||'').toLowerCase();
      return p<n?-dir:p>n?dir:0;
    }
    return ((p||0)-(n||0))*dir;
  });
  view=a; open=-1;
  $('#cnt').innerHTML=`<b>${a.length.toLocaleString()}</b> of ${TABS.find(t=>t.k===tab).n.toLocaleString()}`;
  $('#scroll').scrollTop=0; draw();
}

/* ---- virtual list ---- */
const sc=$('#scroll'), pad=$('#pad');
sc.addEventListener('scroll',()=>draw());
addEventListener('resize',()=>draw());

const fmtSize=n=>n>=1048576?(n/1048576).toFixed(1)+' MB':n>=1024?Math.round(n/1024)+' KB':n+' B';
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const wb=r=>`https://web.archive.org/web/${r.ts}id_/http://puu.sh/${r.id}`;

function draw(){
  const openH=open>=0?230:0;
  pad.style.height=(view.length*RH+openH)+'px';
  if(!view.length){
    pad.innerHTML=`<div class="empty"><b>No matches</b>Try a shorter query \u2014 search covers player, beatmap and filename.</div>`;
    return;
  }
  const top=sc.scrollTop, h=sc.clientHeight;
  let a=Math.max(0,Math.floor(top/RH)-8), b=Math.min(view.length,Math.ceil((top+h)/RH)+8);
  let html='';
  for(let i=a;i<b;i++){
    const off=i*RH+(open>=0&&i>open?openH:0);
    html+=`<div class="row${i===open?' open':''}" data-i="${i}" style="top:${off}px">${cells(view[i])}</div>`;
    if(i===open) html+=`<div class="detail" style="top:${off+RH}px">${detail(view[i])}</div>`;
  }
  pad.innerHTML=html;
}

function cells(r){
  if(tab==='replays'){
    return `<div style="flex:0 0 150px"><i class="dot m${r.mode}"></i>${
        r.player?esc(r.player):'<span class="dim">unknown</span>'}</div>
      <div style="flex:1 1 340px">${esc(r.beatmap)||'<span class="dim">unresolved</span>'}</div>
      <div style="flex:0 0 84px" class="mods ${r.mods==='NM'?'nm':''}">${esc(r.mods)}</div>
      <div style="flex:0 0 78px" class="num">${r.acc<0?'&mdash;':r.acc.toFixed(2)+'%'}${
        r.flag?' <span class="bang">!</span>':''}</div>
      <div style="flex:0 0 104px" class="num hide-s">${r.score.toLocaleString()}</div>
      <div style="flex:0 0 72px" class="num hide-s">${r.combo.toLocaleString()}x</div>
      <div style="flex:0 0 92px" class="mono hide-s">${esc(r.date)||'&mdash;'}</div>`;
  }
  return `<div style="flex:1 1 480px">${esc(r.filename)}</div>
    <div style="flex:1 1 300px" class="dim hide-s">${esc(r.first)}</div>
    <div style="flex:0 0 84px" class="num">${fmtSize(r.size)}</div>
    <div style="flex:0 0 60px" class="num hide-s">${r.entries||''}</div>
    <div style="flex:0 0 92px" class="mono hide-s">${esc(r.archived)}</div>`;
}

function detail(r){
  const u=wb(r);
  let warn='';
  if(tab==='replays'&&r.flag){
    const w=[];
    if(r.flag&1) w.push('the played-on date failed to parse and is not real');
    if(r.flag&2) w.push('the score exceeds any achievable value');
    if(r.flag&4) w.push(`client build ${r.gver} predates date-based versioning, so this is a very early replay`);
    warn=`<div class="notice">Heads up: ${w.join('; ')}.</div>`;
  }
  const dl = tab==='replays'
    ? `<dt>Beatmap hash</dt><dd>${esc(r.hash)}</dd>
       <dt>Client build</dt><dd>${r.gver||'unknown'}</dd>
       <dt>Misses</dt><dd>${r.miss}</dd>
       <dt>Mode</dt><dd>${MODES[r.mode]}</dd>
       <dt>Size</dt><dd>${fmtSize(r.size)}</dd>
       ${r.copies?`<dt>Re-uploads</dt><dd>${r.copies} other capture${r.copies>1?'s':''} of the same replay</dd>`:''}
       <dt>puush ID</dt><dd>${esc(r.id)}</dd>`
    : `<dt>Classified as</dt><dd>${esc(r.kind)}${r.dup?' \u00b7 re-capture of an identical earlier upload':''}</dd>
       <dt>Archive contains</dt><dd>${esc(r.evidence)||esc(r.first)}</dd>
       <dt>Entries</dt><dd>${r.entries||'unknown'}</dd>
       <dt>Size</dt><dd>${fmtSize(r.size)}</dd>
       <dt>puush ID</dt><dd>${esc(r.id)}</dd>`;
  return `${warn}<dl>${dl}<dt>Capture</dt><dd>${esc(r.ts)}</dd></dl>
    <div class="acts">
      <a href="${u}" target="_blank" rel="noopener">Download capture</a>
      <button class="ghost" data-copy="${esc(u)}">Copy link</button>
      <a class="ghost" href="https://web.archive.org/web/*/http://puu.sh/${esc(r.id)}"
         target="_blank" rel="noopener" style="text-decoration:none">All captures of this ID</a>
    </div>`;
}

pad.addEventListener('click',e=>{
  const cp=e.target.closest('[data-copy]');
  if(cp){navigator.clipboard.writeText(cp.dataset.copy).then(()=>{
    const t=cp.textContent; cp.textContent='Copied'; setTimeout(()=>cp.textContent=t,1200);});
    return;}
  const row=e.target.closest('.row'); if(!row)return;
  const i=+row.dataset.i; open = open===i ? -1 : i; draw();
});

let t; $('#q').addEventListener('input',()=>{clearTimeout(t);t=setTimeout(refresh,110);});
addEventListener('keydown',e=>{
  if(e.key==='/'&&document.activeElement!==$('#q')){e.preventDefault();$('#q').focus();}
  if(e.key==='Escape'){$('#q').value='';$('#q').blur();refresh();}
});

chrome(); refresh();
</script>
</body>
</html>"""


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else "."
    out = "osu_puush_index.html"
    if "-o" in sys.argv:
        out = sys.argv[sys.argv.index("-o") + 1]

    print("reading:")
    replays = load(folder, "replays_osr.jsonl")
    bm = load(folder, "beatmaps_osz.jsonl") + load(folder, "beatmaps_zip.jsonl")
    sk = load(folder, "skins_osk.jsonl") + load(folder, "skins_zip.jsonl")
    rz = load(folder, "replays_zip.jsonl")

    everything = (build_files(bm, "beatmap") + build_files(sk, "skin")
                  + build_files(rz, "replay-archive"))

    # Mark re-captures of identical content: same filename + same byte count.
    # Earliest capture keeps dup=0, the rest get dup=1 so the UI can collapse them.
    seen = {}
    for row in sorted(everything, key=lambda x: x[2]):
        key = (row[0], row[1])
        row.append(1 if key in seen else 0)
        seen[key] = True

    buckets = {"beatmap": [], "skin": [], "replay-archive": []}
    for row in everything:
        buckets.setdefault(row[8], buckets["skin"]).append(row)

    data = {
        "replays": build_replays(replays),
        "beatmaps": buckets["beatmap"],
        "skins": buckets["skin"],
        "archives": buckets["replay-archive"],
    }
    print("\nrouted by contents:")
    for k, v in buckets.items():
        print(f"  {k:16s} {len(v)}")
    print(f"  duplicate captures marked: {sum(r[9] for r in everything)}")
    blob = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    blob = blob.replace("</script", "<\\/script")

    html = HTML.replace("__DATA__", blob)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"\nwrote {out}  ({os.path.getsize(out)/1e6:.1f} MB)")
    print(f"  replays {len(data['replays']['rows'])}, "
          f"beatmaps {len(data['beatmaps'])}, skins+archives {len(data['skins'])}")
    flagged = sum(1 for r in data["replays"]["rows"] if r[15])
    print(f"  {flagged} replays carry a data-quality flag")


if __name__ == "__main__":
    main()

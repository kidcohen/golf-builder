# Regenerate the course-library index.html in the target dir (all *.html sims).
import os, html, sys
d = sys.argv[1] if len(sys.argv) > 1 else "."
fs = sorted(f for f in os.listdir(d) if f.endswith(".html") and f != "index.html")
cards = "\n".join(
    f'<a class="card" href="./{f}"><span class="n">{html.escape(f[:-5].replace("_"," "))}</span>'
    f'<span class="p">Play &rarr;</span></a>' for f in fs)
page = f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Course Library</title>
<style>
:root{{--bg:#0f1512;--pan:#161d19;--ln:#26302a;--ink:#eef2ee;--dim:#9db0a4;--ac:#6fae5f}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 system-ui,sans-serif;padding:40px 20px}}
h1{{font-size:34px;margin:0 0 4px}}.sub{{color:var(--dim);margin-bottom:28px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px;max-width:1140px;margin:0 auto}}
.card{{display:flex;flex-direction:column;gap:8px;background:var(--pan);border:1px solid var(--ln);
border-radius:12px;padding:16px 18px;text-decoration:none;color:inherit;transition:.15s}}
.card:hover{{border-color:var(--ac);transform:translateY(-2px)}}
.n{{font-size:18px;font-weight:600}}.p{{color:var(--ac);font-size:13px;font-weight:600;opacity:.6}}
.card:hover .p{{opacity:1}}.wrap{{max-width:1140px;margin:0 auto}}
</style></head><body><div class=wrap><h1>Course Library</h1>
<div class=sub>{len(fs)} playable courses &middot; streamed from the GPU to any device</div></div>
<div class=grid>
{cards}
</div></body></html>"""
open(os.path.join(d, "index.html"), "w", encoding="utf-8").write(page)
print(f"index.html: {len(fs)} courses")

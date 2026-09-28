"""Cloud builder: runs on GitHub Actions (no local app needed). Drives the whole
remaining target list end-to-end, resumable, with a wall-clock budget so each
scheduled run commits progress and the next resumes. Repo-relative paths only.

  locate (multi-form) -> crawl (audit-gated, reconstructs holes from greens+tees)
  -> auto-classify landscape -> terrain/dunes -> build HTML sim -> log.
"""
import sys, os, json, re, time
import numpy as np
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "web_sim"))
os.chdir(ROOT)
import golf_crawl as C, export_terrain as ET, golf_top as T, redune
import landscape_spec as L
from golf_relief import Ground
import build_course as B

TER = os.path.join(ROOT, "terrain"); OUT = os.path.join(ROOT, "courses")
STATE = os.path.join(ROOT, "state.json"); LOG = os.path.join(ROOT, "build_log.txt")
os.makedirs(TER, exist_ok=True); os.makedirs(OUT, exist_ok=True)
BUDGET = int(os.environ.get("TIME_BUDGET", "18000"))   # seconds per run (default 5h)
START = time.time()

def log(m):
    line = f"[{time.strftime('%m-%d %H:%M')}] {m}"; print(line, flush=True)
    open(LOG, "a", encoding="utf-8").write(line + "\n")

def safe(s): return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")
def norm(s): return re.sub(r"[^a-z0-9]", "", re.sub(r"\(.*?\)", "", s.lower()))
def pfl(y): return 3 if y < 245 else (5 if y >= 470 else 4)

def audit(holes):
    yd = sum(h["length_yards"] for h in holes); pars = [pfl(h["length_yards"]) for h in holes]
    p3 = pars.count(3); p5 = pars.count(5)
    return (len(holes) == 18 and 5600 <= yd <= 7900 and 2 <= p3 <= 6 and p5 <= 6), round(yd), p3, p5

def crawl_dedup(box, cn, keep="short"):
    holes = None
    for a in range(5):
        try: holes = C.crawl(*box, course_name=cn); break
        except Exception: time.sleep(6 + a * 6)
    if not holes: return None
    holes = [h for h in holes if h.get("number") and (h.get("length_yards") or 0) > 60 and h.get("tee_latlon")]
    seen = {}
    for h in holes:
        n = h["number"]
        if n not in seen or (keep == "short") == (h["length_yards"] < seen[n]["length_yards"]): seen[n] = h
    return [seen[n] for n in sorted(seen)]

def build_terrain(label, cn, box):
    s, w, n, e = box; dl = (n - s) / 2; do = (e - w) / 2; cy = (s + n) / 2; cx = (w + e) / 2
    best = None
    for pad in (0.15, 0.05, -0.05):
        for keep in ("short", "long"):
            b = [cy - dl*(1+pad), cx - do*(1+pad+0.1), cy + dl*(1+pad), cx + do*(1+pad+0.1)]
            hs = crawl_dedup(b, cn, keep)
            if not hs or len(hs) < 18: continue
            hs = hs[:18]; ok = audit(hs)[0]
            if ok: best = hs; break
            if best is None: best = hs
        if best and audit(best)[0]: break
    if not best: return False, False
    ok = audit(best)[0]
    ground = Ground(cache=ET.RCACHE)
    folder = os.path.join(TER, safe(label)); os.makedirs(folder, exist_ok=True)
    listing = []
    for h in best:
        n = h["number"]
        hole = {"number": n, "par": pfl(h["length_yards"]), "length_yards": h["length_yards"],
                "tee_latlon": h["tee_latlon"], "bearing_deg": h["bearing_deg"],
                "centreline": h["centreline"], "hazards": h["hazards"]}
        crop, meta = ET.one_hole(ground, hole, zoom=19, step_yd=2.0, corridor=85.0)
        stem = f"hole_{n:02d}"; crop.save(os.path.join(folder, stem + "_ortho.jpg"), quality=88)
        json.dump(meta, open(os.path.join(folder, stem + ".json"), "w"))
        listing.append({"hole": n, "stem": stem, "par": meta["par"], "length": meta["length_yards"]})
    json.dump({"course": label, "source": "openstreetmap", "holes": listing,
               "weather": ET.fetch_weather(*best[0]["tee_latlon"])},
              open(os.path.join(folder, "_course.json"), "w"), indent=1)
    return True, ok

# ---- landscape identity ----
def classify(name, loc):
    t = (name + " " + (loc or "")).lower()
    if any(k in t for k in ["melbourne","sandbelt","kingston heath","victoria golf"]): return "sandbelt"
    if any(k in t for k in ["desert","arizona","nevada","dubai","scottsdale","las vegas","new mexico"]): return "desert"
    if any(k in t for k in ["heath","sunningdale","swinley","wentworth","berkshire","woking","walton","surrey"]): return "heath"
    if any(k in t for k in ["links","dunes","bay","beach","strand","sand hills","seaside"]): return "links"
    if any(k in t for k in ["ireland","scotland","wales","northern ireland","norway","iceland","netherlands"]) and ("royal" in t or "golf" in t): return "links"
    return "parkland"

CAT = {
 "links":   (L.LINKS_GOLD, L.OCEAN,  L.NO_TREE, L.bd("flat",30),      True),
 "heath":   (L.HEATH,      L.INLAND, L.HEATH_TR, L.bd("forest",140),  False),
 "sandbelt":(L.SANDBELT,   L.INLAND, L.EUCALYPT, L.bd("forest",70),   False),
 "desert":  (L.DESERT,     L.INLAND, [0.1,0.05,0.05,0,0], L.bd("mountains",190), False),
 "parkland":(L.PARK_LUSH,  L.LAKE,   L.PARK_TR, L.bd("forest",130),   False),
}
def apply_landscape(folder, cat):
    pal, water, tp, bdrop, links = CAT[cat]
    cj = os.path.join(TER, folder, "_course.json"); c = json.load(open(cj))
    palette = dict(pal); palette["deep"] = water["deep"]; palette["shallow"] = water["shallow"]
    palette.setdefault("fairway", pal["grassFair"]); palette.setdefault("rough", pal["grassRough"])
    c["palette"] = palette; c["tree_profile"] = tp; c["backdrop"] = bdrop; c["links"] = links
    json.dump(c, open(cj, "w"), indent=1); return links

def relief_median(folder):
    rng = []
    for n in range(1, 19):
        p = os.path.join(TER, folder, f"hole_{n:02d}.json")
        if os.path.exists(p):
            d = json.load(open(p)); hb = d.get("heights_base")
            rng.append(float(np.array(hb).max() - np.array(hb).min()) if hb else d["height_max"] - d["height_min"])
    return float(np.median(rng)) if rng else 0.0

def forms(name):
    base = re.sub(r"\(.*?\)", "", name).strip(); out = [base]
    if not re.search(r"golf|links|club|course", base, re.I):
        out += [base + " Golf Club", base + " Golf Links", base + " Golf Course"]
    return out
def locate_multi(name, gap=6):
    for q in forms(name):
        try:
            b = T.locate(q)
            if b: return b
        except Exception: pass
        time.sleep(gap)
    return None

def built_ids():
    have = {}
    for a in ["augustanational","standrewsold","turnberry","royaltroon","royalportrush","royalcountydown",
              "cypresspoint","pebblebeach","royalstgeorges","carnoustie","royaldornoch","spyglasshill","gullane"]:
        have[a] = 1
    if os.path.isdir(TER):
        for d in os.listdir(TER):
            p = os.path.join(TER, d)
            if os.path.isdir(p) and len([f for f in os.listdir(p) if f.endswith("_ortho.jpg")]) >= 18:
                have[norm(d)] = 1
    return have

def run():
    targets = json.load(open("courses_top_world.json"))["courses"]
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    have = built_ids()
    todo = [c for c in targets if norm(c["name"]) not in have and state.get(c["name"], {}).get("status") not in ("BUILT",)]
    log(f"CLOUD BUILD START: {len(targets)} targets, {len(have)} built, {len(todo)} to attempt, budget {BUDGET}s")
    ok = fail = 0
    for c in todo:
        if time.time() - START > BUDGET:
            log(f"TIME BUDGET HIT — stopping cleanly at +{ok} built this run (resumes next run)"); break
        name, loc = c["name"], c.get("location", ""); label = re.sub(r"\(.*?\)", "", name).strip()
        folder = safe(label)
        try:
            box = locate_multi(name)
            if not box:
                state[name] = {"status": "LOCATE_FAIL"}; fail += 1; log(f"LOCATE_FAIL  {name}")
            else:
                got, passed = build_terrain(label, None, list(box))
                if not got:
                    state[name] = {"status": "CRAWL_FAIL"}; fail += 1; log(f"CRAWL_FAIL   {name}")
                else:
                    cat = classify(name, loc); links = apply_landscape(folder, cat)
                    if links and relief_median(folder) < 8.0:
                        for n in range(1, 19):
                            p = os.path.join(TER, folder, f"hole_{n:02d}.json")
                            if os.path.exists(p): redune.process(p, 6.0)
                    B.build_course(os.path.join(TER, folder), label, os.path.join(OUT, folder + ".html"))
                    state[name] = {"status": "BUILT", "cat": cat, "audit": "pass" if passed else "best_effort"}
                    ok += 1; log(f"BUILT        {name}  [{cat}]  ({'PASS' if passed else 'best-effort'})")
        except Exception as e:
            state[name] = {"status": "ERROR", "err": repr(e)[:120]}; fail += 1; log(f"ERROR        {name}: {repr(e)[:90]}")
        json.dump(state, open(STATE, "w"), indent=1); have = built_ids()
        time.sleep(5)
    total = sum(1 for d in os.listdir(TER) if os.path.isdir(os.path.join(TER, d)) and len([f for f in os.listdir(os.path.join(TER, d)) if f.endswith("_ortho.jpg")]) >= 18)
    log(f"RUN DONE: +{ok} built, {fail} failed this run. Total terrain courses now: {total}")

if __name__ == "__main__":
    run()

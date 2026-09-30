"""Assemble a whole course into one self-contained WebGL page: every exported
hole (heightfield + orthophoto) inlined, navigable, no server, no external
fetch. Opens on any device.
"""
import base64, json, os, urllib.request
try:
    from fairway_trace import trace_fairway
except Exception:
    try:
        from web_sim.fairway_trace import trace_fairway
    except Exception:
        trace_fairway = None

LIBS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "libs")
CDN = {
    "three.min.js": "https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js",
    "OrbitControls.js": "https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js",
}

def lib(name):
    path = os.path.join(LIBS, name)
    if not os.path.exists(path):
        os.makedirs(LIBS, exist_ok=True)
        req = urllib.request.Request(CDN[name], headers={"User-Agent": "Mozilla/5.0"})
        open(path, "wb").write(urllib.request.urlopen(req, timeout=60).read())
    return open(path, encoding="utf-8").read()

_GROUND = None
def _ground_holes(course_title):
    """Per-hole map data (tees, fairway_width) from courses_ground.json for a course,
    keyed by hole number. Coordinates match the exported hole geometry exactly."""
    global _GROUND
    if _GROUND is None:
        try:
            gp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "courses_ground.json")
            _GROUND = json.load(open(gp))["courses"]
        except Exception:
            _GROUND = []
    key = (course_title or "").strip().lower()
    for c in _GROUND:
        nm = c.get("name", "").strip().lower()
        if nm == key or nm.replace(" ", "_") == key.replace(" ", "_"):
            return {h["number"]: h for h in c.get("holes", [])}
    return {}

def _card_tee_along(gh):
    """The played (card) tee's along position from the map data: the fairway begins
    just past it. Prefer a named tee, else the tee whose yardage matches the hole."""
    tees = gh.get("tees") or []
    if not tees:
        return None
    ln = gh.get("length_yards")
    cand = [t for t in tees if t.get("name")] or list(tees)
    if ln:
        cand = sorted(cand, key=lambda t: abs((t.get("plays_yards") or 0) - ln))
    return cand[0].get("along")

TREE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trees")
def grass_texture():
    """Real seamless turf (CC0, ambientCG Grass004) as an embedded data URL."""
    p = os.path.join(TREE_DIR, "grass_color.jpg")
    return "data:image/jpeg;base64," + base64.b64encode(open(p, "rb").read()).decode()

def grass_detail():
    """Fine tileable grass-blade grain (generated) as an embedded data URL - the near-camera blade detail."""
    p = os.path.join(TREE_DIR, "grass_detail.jpg")
    return "data:image/jpeg;base64," + base64.b64encode(open(p, "rb").read()).decode()

def tuft_texture():
    """Wispy native-grass tuft with wildflowers (generated cutout) as an embedded data URL."""
    p = os.path.join(TREE_DIR, "grass_tuft.png")
    return "data:image/png;base64," + base64.b64encode(open(p, "rb").read()).decode()

def grass_sample():
    """The user's own reference grass, cropped from the approved picture and made tileable - used directly as the turf texture."""
    p = os.path.join(TREE_DIR, "grass_sample.jpg")
    return "data:image/jpeg;base64," + base64.b64encode(open(p, "rb").read()).decode()

def _tex_data(fname):
    p = os.path.join(TREE_DIR, fname)
    return "data:image/jpeg;base64," + base64.b64encode(open(p, "rb").read()).decode()

def sand_texture():
    """Real fine bunker sand (CC0, Poly Haven aerial_sand)."""
    return _tex_data("sand_color.jpg")

def rock_texture():
    """Real rock face (CC0, Poly Haven cliff_side) for the coastal boulders."""
    return _tex_data("rock_color.jpg")

def rock_images(kind="granite"):
    """Real-rock billboard cutouts (boulder masses cut from a CC0 rock photo), like the
    tree cutouts. `kind` picks the rock proper to the place: 'granite' (grey sea-cliff
    rock, Pebble/Monterey and most coasts) or 'sand' (warm sandstone, desert country)."""
    from PIL import Image
    out = []
    for i in (1, 2, 3):
        p = os.path.join(TREE_DIR, "rock_%s_%d.png" % (kind, i))
        w, h = Image.open(p).size
        b64 = base64.b64encode(open(p, "rb").read()).decode()
        out.append({"d": "data:image/png;base64," + b64, "a": round(w / h, 4)})
    return out

def _rock_kind(title, modelled_on=""):
    """Which rock a course's exposed stone should be - it differs by place."""
    t = (title + " " + (modelled_on or "")).lower()
    sandstone = ("desert", "canyon", "sedona", "scottsdale", "arizona", "mesa",
                 "red rock", "waste", "dune")
    if any(k in t for k in sandstone):
        return "sand"
    return "granite"   # grey granite: coastal cliffs and most exposed course rock
_TREE_CACHE = None
def tree_images():
    """The four real-tree billboard cutouts as embedded data URLs, in species order
    [broadleaf, spreading oak, conifer, autumn], each with its width/height aspect."""
    global _TREE_CACHE
    if _TREE_CACHE is None:
        from PIL import Image
        order = ["broadleaf", "spreading", "pine", "autumn", "palm"]
        out = []
        for name in order:
            p = os.path.join(TREE_DIR, name + ".png")
            w, h = Image.open(p).size
            b64 = base64.b64encode(open(p, "rb").read()).decode()
            out.append({"d": "data:image/png;base64," + b64, "a": round(w / h, 4)})
        _TREE_CACHE = out
    return _TREE_CACHE


def _tree_profile(title):
    """Species mix [broadleaf, broadleaf2, conifer, autumn] proper to the course.

    Pine country stands dark and evergreen; links carry sparse wind-shaped
    broadleaf and essentially no conifers; parkland is a broadleaf mix with the
    odd stand of pine. It shapes what species the billboards draw from - the
    trees themselves still come from each hole's real tree clumps.
    """
    t = title.lower()
    # weights over species [broadleaf, spreading oak/cypress, conifer, autumn, palm]
    coastal = ("pebble", "cypress", "spyglass", "monterey", "kiawah", "coast",
               "ocean", "seaside", "harbour town", "hawaii", "mauna", "cabo",
               "stillwater")
    pine = ("pine", "pinehurst", "augusta", "azalea", "sand", "heath", "carolina",
            "whistling", "foothill")
    links = ("links", "st andrews", "carnoustie", "troon", "birkdale", "turnberry",
             "royal", "postage", "gullane", "muirfield", "lahinch", "ballybunion",
             "portrush", "portmarnock", "dornoch", "hoylake", "lytham", "sandwich",
             "barry burn", "quarry", "honours", "shinnecock", "ailsa", "dunluce",
             "struie", "dune", "auld", "furrow", "slieve")
    if any(k in t for k in coastal):
        return [0.05, 0.35, 0.35, 0.0, 0.25]   # Monterey cypress + pine + palm
    if any(k in t for k in pine):
        return [0.12, 0.12, 0.68, 0.04, 0.04]
    if any(k in t for k in links):
        return [0.58, 0.34, 0.0, 0.06, 0.02]
    return [0.38, 0.38, 0.12, 0.08, 0.04]


def _backdrop(title, modelled_on=""):
    """The distant landscape naturally around a course - only what's really there.

    Most courses (coastal, links, parkland, sandhills) sit in low, forested country,
    so the horizon gets low forested hills and NO snow. Only courses actually set in
    mountains get tall, snow-capped peaks."""
    t = (title + " " + (modelled_on or "")).lower()
    mountainous = ("alp", "banff", "whistler", "rockies", "aspen", "vail", "tahoe",
                   "jackson hole", "highland", "sierra", "cascade", "andes", "dolomit")
    if any(k in t for k in mountainous):
        return {"type": "mountains", "height": 700, "snow": True}
    links_or_flat = ("links", "sand", "st andrews", "carnoustie", "birkdale",
                     "troon", "sandwich", "postage", "dune", "prairie")
    if any(k in t for k in links_or_flat):
        return {"type": "flat", "height": 60, "snow": False}   # links: open, a faint rise of dunes only
    # parkland / coastal (Pebble, Augusta, etc.): ringed by forest, not hills - a treeline backdrop
    return {"type": "forest", "height": 0, "snow": False}


def _palette(title):
    """Colour palette proper to the course's place: the deep-blue Pacific and rich
    green of Pebble, the grey-green seas and muted golden fescue of a links, or the
    lush saturated green of parkland."""
    t = title.lower()
    coastal = ("pebble", "stillwater", "cypress", "monterey", "spyglass", "kiawah",
               "hawaii", "mauna", "cabo", "harbour")
    links = ("links", "st andrews", "auld", "carnoustie", "barry", "troon", "postage",
             "birkdale", "dune", "sandwich", "royal", "turnberry", "ailsa", "dornoch",
             "struie", "portrush", "dunluce", "county down", "slieve", "gullane",
             "whistling", "strand")
    lush = ("augusta", "azalea", "pine valley", "pine hollow")
    # colours below are measured from real course photos (grass/ocean/rock)
    if any(k in t for k in coastal):   # Pebble/Monterey: vivid mid-blue Pacific, yellow-green turf, DARK charcoal granite
        return {"deep": 0x0d4f86, "shallow": 0x3f8fbe, "fairway": [0.44, 0.57, 0.14], "rough": [0.32, 0.42, 0.15], "rock": [0.28, 0.32, 0.33]}
    if any(k in t for k in links):     # grey-green sea, muted golden fescue, mid-grey stone
        return {"deep": 0x1a4a52, "shallow": 0x3f7a7a, "fairway": [0.40, 0.48, 0.18], "rough": [0.34, 0.36, 0.18], "rock": [0.52, 0.52, 0.48]}
    if any(k in t for k in lush):      # Augusta/Pine Valley: rich saturated green
        return {"deep": 0x145a44, "shallow": 0x2a8a68, "fairway": [0.26, 0.56, 0.13], "rough": [0.16, 0.36, 0.09], "rock": [0.48, 0.46, 0.42]}
    return {"deep": 0x104a68, "shallow": 0x2f7a9e, "fairway": [0.36, 0.52, 0.16], "rough": [0.22, 0.36, 0.12], "rock": [0.50, 0.50, 0.47]}


def _tall_grass(title):
    """How much TALL native/fescue grass the real course actually has. Manicured
    parkland (Augusta) has almost none; links are full of it. Short grass tufts are
    scattered everywhere for turf detail regardless - this only gates the TALL ones."""
    t = title.lower()
    manicured = ("augusta", "azalea", "pine valley", "pine hollow", "oakmont",
                 "bethpage", "shinnecock", "sebonack", "foothill")
    links = ("links", "st andrews", "auld", "carnoustie", "barry", "troon", "postage",
             "birkdale", "dune", "sandwich", "royal", "turnberry", "ailsa", "dornoch",
             "struie", "portrush", "dunluce", "county down", "slieve", "gullane",
             "whistling", "strand", "furrow", "quarry", "honours")
    if any(k in t for k in manicured): return 0.12
    if any(k in t for k in links): return 0.90
    return 0.40


def _tall_grass_color(title):
    """Tint for the tall native grass, matching the real course: golden fescue on
    links, natural green elsewhere. Multiplies the (green) tuft texture."""
    t = title.lower()
    links = ("links", "st andrews", "auld", "carnoustie", "barry", "troon", "postage",
             "birkdale", "dune", "sandwich", "royal", "turnberry", "ailsa", "dornoch",
             "struie", "portrush", "dunluce", "county down", "slieve", "gullane",
             "whistling", "strand", "furrow", "quarry", "honours")
    if any(k in t for k in links): return [1.75, 1.28, 0.62]   # golden-brown fescue
    return [1.0, 1.06, 0.86]                                    # natural green native grass


def _small_jpeg(path, maxpx=1400, quality=78):
    """Base64 of the ortho, downscaled so its longest side <= maxpx and recompressed.
    The ground texture doesn't need full survey resolution; this roughly halves the
    inlined page size (faster load) with no visible loss at play distance."""
    import io
    from PIL import Image
    im = Image.open(path).convert("RGB")
    w, h = im.size
    m = max(w, h)
    if m > maxpx:
        im = im.resize((round(w * maxpx / m), round(h * maxpx / m)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality, optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def build_course(course_dir, course_title, out_html, only=None):
    course_meta = json.load(open(f"{course_dir}/_course.json"))
    listing = course_meta["holes"]
    weather = course_meta.get("weather",
                              {"wind_speed_mph": 7, "wind_dir_deg": 270, "rain": 0, "wetness": 0})
    ground = _ground_holes(course_meta.get("course", course_title))
    # Per-hole fairway overrides read off the real hole maps: a JSON in the course
    # dir, {"<hole>": {"start": yards, "width": half_yards}}. Highest priority.
    fw_over = {}
    _fwp = f"{course_dir}/fw_overrides.json"
    if os.path.exists(_fwp):
        try:
            fw_over = {int(k): v for k, v in json.load(open(_fwp)).items()}
        except Exception:
            fw_over = {}
    holes = []
    for h in listing:
        if only is not None and h["hole"] not in only: continue
        meta = json.load(open(f"{course_dir}/{h['stem']}.json"))
        ortho_path = f"{course_dir}/{h['stem']}_ortho.jpg"
        ortho = _small_jpeg(ortho_path)   # downscale/recompress the inlined ortho: much smaller page = faster load
        # Trace THIS hole's real fairway body from its orthophoto (asymmetric
        # half-width along the centreline). A hole may override with meta["fw"].
        fw = meta.get("fw")
        if fw is None and trace_fairway is not None:
            try:
                fw = trace_fairway(ortho_path, meta)
            except Exception:
                fw = None
        # Map data (hole maps): the fairway starts just past the played (card) tee,
        # and the map's own fairway width where it recorded one. A hole may override
        # with meta["fw_start"] / meta["fw_width"] (e.g. read off a real hole map).
        gh = ground.get(h["hole"], {})
        ov = fw_over.get(h["hole"], {})
        cta = ov.get("start", meta.get("fw_start"))
        if cta is None:
            cta = _card_tee_along(gh)
            if cta is not None:
                cta = max(0.0, round(cta + 3.0, 1))    # fairway begins just past the tee box
        fw_width = ov.get("width", meta.get("fw_width"))
        if fw_width is None:
            fww = gh.get("fairway_width_yards")
            fw_width = round(fww / 2.0, 1) if fww else None   # map's own half-width
        # the real land around the hole (coast, hills), if it was exported
        surround = None
        spath = f"{course_dir}/{h['stem']}_surround.json"
        sjpg = f"{course_dir}/{h['stem']}_surround.jpg"
        if os.path.exists(spath) and os.path.exists(sjpg):
            sm = json.load(open(spath))
            sb64 = base64.b64encode(open(sjpg, "rb").read()).decode()
            surround = {"span": sm["span"], "grid": sm["grid"], "heights": sm["heights"],
                        "ortho": "data:image/jpeg;base64," + sb64}
        holes.append({
            "name": f"{course_title} \u2014 hole {h['hole']}",
            "sub": f"par {h['par']} \u00b7 {h['length']:.0f} yards",
            "grid": meta["grid"], "extent": meta["extent"], "heights": meta["heights"],
            "centreline": meta.get("centreline"), "trees": meta.get("trees"),
            "water": meta.get("water"), "bunkers": meta.get("bunkers"), "fw": fw,
            "fwStart": cta, "fwWidth": fw_width,
            "bearing": meta.get("bearing_deg", 0.0),
            "water_level": meta.get("water_level"), "water_frac": meta.get("water_frac", 0.0),
            "ortho": "data:image/jpeg;base64," + ortho, "surround": surround,
        })
    # a course may state its own species mix (fictional names won't keyword-match);
    # otherwise fall back to guessing from the title
    profile = course_meta.get("tree_profile") or _tree_profile(course_title)
    backdrop = course_meta.get("backdrop") or _backdrop(course_title, course_meta.get("after"))
    html = (PAGE.replace("/*THREE*/", lib("three.min.js"))
                .replace("/*ORBIT*/", lib("OrbitControls.js"))
                .replace("__TITLE__", course_title)
                .replace("__WEATHER__", json.dumps(weather))
                .replace("__TREEPROFILE__", json.dumps(profile))
                .replace("__BACKDROP__", json.dumps(backdrop))
                .replace("__PALETTE__", json.dumps(course_meta.get("palette") or _palette(course_title)))
                .replace("__TREEIMAGES__", json.dumps(tree_images()))
                .replace("__GRASSTEX__", json.dumps(grass_texture()))
                .replace("__GRASSDETAIL__", json.dumps(grass_detail()))
                .replace("__TUFTTEX__", json.dumps(tuft_texture()))
                .replace("__GRASSSAMPLE__", json.dumps(grass_sample()))
                .replace("__TALLGRASS__", json.dumps(_tall_grass(course_title)))
                .replace("__TALLGRASSCOL__", json.dumps(_tall_grass_color(course_title)))
                .replace("__SANDTEX__", json.dumps(sand_texture()))
                .replace("__ROCKIMAGES__", json.dumps(rock_images(_rock_kind(course_title, course_meta.get("after")))))
                .replace("__HOLES__", json.dumps(holes)))
    open(out_html, "w", encoding="utf-8").write(html)
    print(f"wrote {out_html}  ({os.path.getsize(out_html)//1024} KB, {len(holes)} holes)")

PAGE = r"""<meta charset="utf-8">
<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700&family=Barlow:wght@400;500&display=swap">
<style>
  :root{ --ink:#f4f7f4; --flag:#e0483f; --accent:#5ecb83; --panel:rgba(12,18,14,.70); }
  html,body{margin:0;height:100%;overflow:hidden;background:#9cc0dd;
    font-family:Barlow,system-ui,Arial;color:var(--ink);
    -webkit-user-select:none;user-select:none;touch-action:none}
  canvas{display:block}
  #hud{position:fixed;left:20px;top:18px;z-index:5;padding:12px 18px;border-radius:10px;
    background:var(--panel);backdrop-filter:blur(6px);border-left:3px solid var(--flag);
    box-shadow:0 6px 24px rgba(0,0,0,.28)}
  #hud .name{font-family:"Barlow Condensed",sans-serif;font-weight:700;font-size:30px;line-height:1;letter-spacing:.3px}
  #hud .sub{font-size:14px;font-weight:500;opacity:.9;margin-top:5px;font-variant-numeric:tabular-nums}
  #nav{position:fixed;left:50%;bottom:20px;transform:translateX(-50%);z-index:6;display:flex;
    gap:10px;align-items:center;background:var(--panel);backdrop-filter:blur(6px);
    padding:8px 10px;border-radius:999px;box-shadow:0 6px 24px rgba(0,0,0,.28)}
  #nav button{font-family:"Barlow Condensed",sans-serif;font-weight:700;font-size:18px;color:var(--ink);
    background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.18);border-radius:999px;
    width:44px;height:36px;cursor:pointer}
  #nav button:active{background:var(--flag);border-color:var(--flag)}
  #nav .count{min-width:64px;text-align:center;font-variant-numeric:tabular-nums;font-size:15px}
  #tip{position:fixed;right:18px;bottom:24px;z-index:5;font-size:12px;opacity:.75;text-shadow:0 1px 3px rgba(0,0,0,.6)}
  #pinmark{position:fixed;left:0;top:0;z-index:7;transform:translate(-50%,-50%);display:none;cursor:pointer;
    text-align:center;line-height:1;filter:drop-shadow(0 1px 3px rgba(0,0,0,.7));user-select:none}
  #pinmark .fl{font-size:26px;display:block}
  #pinmark .yd{display:block;margin-top:1px;font-size:11px;font-weight:700;color:var(--ink);
    font-variant-numeric:tabular-nums;text-shadow:0 1px 2px rgba(0,0,0,.8)}
  #err{position:fixed;left:20px;top:96px;z-index:9;display:none;max-width:70vw;background:rgba(150,26,26,.9);padding:9px 13px;border-radius:7px;font-size:13px}
  #toast{position:fixed;left:50%;top:80px;transform:translateX(-50%);z-index:8;background:var(--panel);backdrop-filter:blur(6px);color:var(--ink);padding:8px 18px;border-radius:999px;font-size:15px;font-weight:500;opacity:0;transition:opacity .3s;pointer-events:none;text-shadow:0 1px 2px rgba(0,0,0,.5)}
  #weather{position:fixed;right:16px;top:18px;z-index:5;padding:8px 14px;border-radius:10px;background:var(--panel);backdrop-filter:blur(6px);color:var(--ink);font-size:14px;font-weight:500;text-shadow:0 1px 2px rgba(0,0,0,.5)}
  @media (max-width:520px){ #hud .name{font-size:23px} }
  /* --- high-end sim UI --- */
  #shotdata{position:fixed;left:16px;top:96px;z-index:5;display:flex;flex-direction:column;gap:7px;width:158px}
  #shotdata .sdh{font-family:"Barlow Condensed",sans-serif;font-weight:700;font-size:13px;letter-spacing:.14em;
    text-transform:uppercase;opacity:.65;margin:0 0 1px 2px}
  .stat{background:var(--panel);backdrop-filter:blur(8px);border:1px solid rgba(255,255,255,.06);
    border-left:3px solid var(--accent);border-radius:9px;padding:6px 12px;box-shadow:0 4px 16px rgba(0,0,0,.28)}
  .stat .lab{font-size:10px;letter-spacing:.10em;text-transform:uppercase;opacity:.55}
  .stat .val{font-family:"Barlow Condensed",sans-serif;font-weight:700;font-size:22px;line-height:1.1;
    font-variant-numeric:tabular-nums;letter-spacing:.3px}
  #holemap{position:fixed;right:14px;top:70px;bottom:82px;z-index:5;width:154px;background:var(--panel);
    backdrop-filter:blur(8px);border:1px solid rgba(255,255,255,.07);border-radius:12px;padding:9px 9px 6px;
    box-shadow:0 6px 22px rgba(0,0,0,.34);display:flex;flex-direction:column}
  #holemap .mh{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:5px}
  #holemap .mh .t{font-family:"Barlow Condensed",sans-serif;font-weight:700;font-size:14px;letter-spacing:.06em}
  #holemap .mh .w{font-size:11px;opacity:.72;font-variant-numeric:tabular-nums}
  #holemap svg{flex:1;width:100%;height:100%;display:block}
  @media (max-width:760px){ #shotdata{display:none} #holemap{width:118px;bottom:88px} }
</style>
<div id="hud"><div class="name" id="name">loading&#8230;</div><div class="sub" id="sub"></div></div>
<div id="weather"></div>
<div id="shotdata">
  <div class="sdh">Shot Data</div>
  <div class="stat"><div class="lab">Club</div><div class="val" id="sdClub">&#8212;</div></div>
  <div class="stat"><div class="lab">Carry</div><div class="val" id="sdCarry">&#8212;</div></div>
  <div class="stat"><div class="lab">Total</div><div class="val" id="sdTotal">&#8212;</div></div>
  <div class="stat"><div class="lab">Apex</div><div class="val" id="sdApex">&#8212;</div></div>
  <div class="stat"><div class="lab">Ball Speed</div><div class="val" id="sdSpeed">&#8212;</div></div>
  <div class="stat"><div class="lab">To Pin</div><div class="val" id="sdPin">&#8212;</div></div>
</div>
<div id="holemap"><div class="mh"><span class="t" id="mapHole">Hole</span><span class="w" id="mapWind"></span></div>
  <svg id="mapsvg" viewBox="0 0 100 300" preserveAspectRatio="xMidYMid meet"></svg></div>
<div id="nav"><button id="prev" aria-label="previous hole">&#9664;</button><div class="count" id="count"></div><button id="next" aria-label="next hole">&#9654;</button><button id="viewbtn" style="width:auto;padding:0 14px">Down the line</button><button id="teebtn" style="width:auto;padding:0 12px">Tee</button><button id="plbtn" style="width:auto;padding:0 12px" title="players">1P</button></div>
<div id="pinmark" title="aim at the pin"><span class="fl">&#9971;</span><span class="yd" id="pinYd"></span></div>
<div id="tip">tap to hit toward there &#183; drag to look &#183; pinch to zoom</div>
<div id="toast"></div>
<div id="err"></div>
<script>/*THREE*/</script>
<script>/*ORBIT*/</script>
<script>
const HOLES = __HOLES__;
const WEATHER = __WEATHER__;
const show = m => { const e=document.getElementById('err'); e.style.display='block'; e.textContent=m; };
window.addEventListener('error', e => show('error: '+e.message));
try {
  const R = new THREE.WebGLRenderer({antialias:true});
  R.setSize(innerWidth, innerHeight); R.setPixelRatio(Math.min(devicePixelRatio,1.25));   // cap render resolution (fewer fragments = faster) without a visible quality drop
  R.outputEncoding = THREE.sRGBEncoding;
  R.toneMapping = THREE.NoToneMapping;   // ACES was desaturating the brightly-lit near turf to a pale wash - back to the plain tone that kept the fairway green
  R.shadowMap.enabled = true; R.shadowMap.type = THREE.PCFSoftShadowMap;         // real cast shadows
  document.body.appendChild(R.domElement);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xbfd8ea);
  scene.fog = new THREE.Fog(0xcfe0ec, 800, 4600);   // pulled back: the near/mid hole reads crisp, not hazy
  const cam = new THREE.PerspectiveCamera(58, innerWidth/innerHeight, 0.5, 6000);
  // A strong directional sun for crisp shade and cast shadows, with only a little
  // fill so relief and shadows read (a big hemisphere light flattens everything).
  scene.add(new THREE.HemisphereLight(0xcfe0ef, 0x4a5a3c, 0.30));
  scene.add(new THREE.AmbientLight(0xffffff, 0.10));
  const sun = new THREE.DirectionalLight(0xfff4e6, 1.5); sun.position.set(-320,360,150); sun.castShadow=true;   // normal daylight - a higher value blows the near turf out to pale
  sun.shadow.mapSize.set(1024,1024); sun.shadow.bias=-0.0005;   // smaller shadow map = cheaper shadow pass each frame
  sun.shadow.camera.near=40; sun.shadow.camera.far=1400;
  sun.shadow.camera.left=-240; sun.shadow.camera.right=240; sun.shadow.camera.top=340; sun.shadow.camera.bottom=-240;
  scene.add(sun); scene.add(sun.target);

  // Sky: a gradient dome (deep blue overhead, pale at the horizon) with a soft
  // sun, instead of a flat colour. The water reflects it via scene.environment.
  const skyMat=new THREE.ShaderMaterial({
    uniforms:{ top:{value:new THREE.Color(0x2f74c0)}, bot:{value:new THREE.Color(0xe2edf3)},
               cloud:{value:new THREE.Color(0xffffff)}, off:{value:90.0}, ex:{value:0.72}, time:{value:0} },
    vertexShader:'varying vec3 wp; void main(){ wp=(modelMatrix*vec4(position,1.0)).xyz;'+
      ' gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }',
    fragmentShader:
      'uniform vec3 top,bot,cloud; uniform float off,ex,time; varying vec3 wp;'+
      'float hsh(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}'+
      'float vn(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.0-2.0*f);'+
      ' float a=hsh(i),b=hsh(i+vec2(1.,0.)),c=hsh(i+vec2(0.,1.)),d=hsh(i+vec2(1.,1.));'+
      ' return mix(mix(a,b,f.x),mix(c,d,f.x),f.y);}'+
      'float fbm(vec2 p){float v=0.,a=0.55;for(int k=0;k<5;k++){v+=a*vn(p);p*=2.03;a*=0.5;}return v;}'+
      'void main(){ float h=normalize(wp+vec3(0.0,off,0.0)).y;'+
      ' vec3 sky=mix(bot,top,pow(max(h,0.0),ex));'+
      ' vec3 d=normalize(wp);'+
      ' if(d.y>0.02){ vec2 uv=d.xz/max(d.y,0.05)*0.85+vec2(time*0.005,time*0.002);'+
      '   float c=fbm(uv*1.25); c=smoothstep(0.52,0.86,c)*smoothstep(0.02,0.22,d.y);'+       // fluffy cumulus, thinning at the horizon
      '   float sh=fbm(uv*1.25+vec2(0.35,0.2)); sky=mix(sky, cloud*(0.80+0.20*sh), c*0.92); }'+  // soft shading in the clouds
      ' gl_FragColor=vec4(sky,1.0); }',
    side:THREE.BackSide, depthWrite:false, fog:false });
  const skyDome=new THREE.Mesh(new THREE.SphereGeometry(4000,32,16), skyMat);
  skyDome.renderOrder=-10; scene.add(skyDome);   // background: drawn before the ranges so it doesn't paint over them
  const BACKDROP = __BACKDROP__;   // {height, snow}: only the landscape naturally there - low forested coastal hills for Pebble, tall snowy peaks only for real mountain courses
  // Distant mountains as REAL 3D terrain ringing the horizon: an annular band whose
  // height is 2D-noise peaks, LIT by the scene's sun so the faces carry light and
  // shadow (that is what makes a mountain read as a mountain, not a flat blob), shaded
  // by elevation forested -> rock -> snow. Stylised and clean, like the E6 backdrop.
  function mountainRange(){
    const A=200, RN=30, rIn=820, rOut=3600;
    const maxH=(BACKDROP.type==='flat') ? Math.max(BACKDROP.height||0, 95) : BACKDROP.height;   // flat links get a low dune horizon, never a dark -60 seam
    const geo=new THREE.BufferGeometry(), pos=[], col=[], idx=[];
    const h2=(x,y)=>{ const s=Math.sin(x*12.9898+y*78.233)*43758.5453; return s-Math.floor(s); };
    const vn=(x,y)=>{ const xi=Math.floor(x),yi=Math.floor(y),xf=x-xi,yf=y-yi;
      const u=xf*xf*(3-2*xf), v=yf*yf*(3-2*yf);
      const a=h2(xi,yi),b=h2(xi+1,yi),c=h2(xi,yi+1),d=h2(xi+1,yi+1);
      return a*(1-u)*(1-v)+b*u*(1-v)+c*(1-u)*v+d*u*v; };
    const fbm=(x,y)=>{ let f=0,amp=0.5,fr=1; for(let k=0;k<6;k++){ f+=amp*vn(x*fr,y*fr); fr*=2.07; amp*=0.5; } return f; };
    const forest=new THREE.Color(0x2c3a22), rock=new THREE.Color(0x655f54), snow=new THREE.Color(0xeef3f6), c=new THREE.Color();
    const S=THREE.MathUtils.smoothstep;
    for(let a=0;a<=A;a++){ const ang=a/A*Math.PI*2, ca=Math.cos(ang), sa=Math.sin(ang);
      for(let r=0;r<=RN;r++){ const rt=r/RN, rad=rIn+(rOut-rIn)*rt;
        const env=Math.pow(Math.sin(Math.PI*Math.min(rt*1.18,1.0)),1.1);   // band: 0 at the edges, tall through the middle distance
        let n=fbm(ang*3.6+7.0, rt*3.2+1.0); n=Math.pow(n,1.35);            // 2D noise -> distinct, sharper peaks
        const y=env*maxH*(0.24+0.76*n) - 60.0;
        pos.push(ca*rad, y, sa*rad);
        const hf=Math.max(0,Math.min(1,(y+60.0)/maxH));
        c.copy(forest).lerp(rock, S(hf,0.50,0.82));                        // forested well up the slope
        if(BACKDROP.snow) c.lerp(snow, S(hf,0.80,0.96));                   // snow only where there are real snowy peaks
        col.push(c.r,c.g,c.b); } }
    for(let a=0;a<A;a++) for(let r=0;r<RN;r++){ const p=a*(RN+1)+r,q=p+1,u=p+(RN+1),v=u+1; idx.push(p,u,q,q,u,v); }
    geo.setAttribute('position',new THREE.Float32BufferAttribute(pos,3));
    geo.setAttribute('color',new THREE.Float32BufferAttribute(col,3));
    geo.setIndex(idx); geo.computeVertexNormals();
    const m=new THREE.Mesh(geo, new THREE.MeshStandardMaterial({vertexColors:true, roughness:0.96, metalness:0.0,
      side:THREE.DoubleSide, fog:false}));
    m.renderOrder=-4; return m;   // after the sky, before the terrain
  }
  if(BACKDROP.type==='mountains' || BACKDROP.type==='flat') scene.add(mountainRange());   // forest courses get a treeline instead (built per hole)
  // Coastal rocks as real photo billboards (boulder cutouts), exactly like the trees.
  const ROCK_IMAGES = __ROCKIMAGES__;
  const ROCK_KINDS = ROCK_IMAGES.map((im)=>({
    geo: billboardGeo(im.a),
    mat: new THREE.MeshBasicMaterial({map:treeTexFrom(im.d), alphaTest:0.5, side:THREE.DoubleSide, depthWrite:true, fog:true}) }));
  const sunCv=document.createElement('canvas'); sunCv.width=sunCv.height=128;
  { const g=sunCv.getContext('2d'), gr=g.createRadialGradient(64,64,0,64,64,64);
    gr.addColorStop(0,'rgba(255,251,238,1)'); gr.addColorStop(0.22,'rgba(255,245,214,0.75)');
    gr.addColorStop(1,'rgba(255,240,200,0)'); g.fillStyle=gr; g.fillRect(0,0,128,128); }
  const sunS=new THREE.Sprite(new THREE.SpriteMaterial({map:new THREE.CanvasTexture(sunCv),
    transparent:true, depthWrite:false, fog:false }));
  sunS.position.copy(sun.position.clone().normalize().multiplyScalar(3200)); sunS.scale.set(700,700,1);
  scene.add(sunS);
  try{ const pm=new THREE.PMREMGenerator(R), ss=new THREE.Scene();
    ss.add(new THREE.Mesh(new THREE.SphereGeometry(4000,24,12), skyMat.clone()));
    scene.environment=pm.fromScene(ss).texture; }catch(e){}

  const EXAG = 1.35;   // gentle vertical emphasis so grade is readable
  const group = new THREE.Group(); scene.add(group);
  // A dimpled golf-ball surface: a canvas of soft pits used as a bump map, so the
  // ball reads as a real golf ball rather than a smooth sphere.
  function makeDimpleTex(){
    const s=256, cv=document.createElement('canvas'); cv.width=cv.height=s; const g=cv.getContext('2d');
    g.fillStyle='#ffffff'; g.fillRect(0,0,s,s); const R=13;
    for(let y=-1;y*R*1.5<s+R;y++){ for(let x=-1;x*R*1.7<s+R;x++){
      const cx=x*R*1.7+(y&1)*R*0.85, cy=y*R*1.5, gr=g.createRadialGradient(cx,cy,0,cx,cy,R*0.72);
      gr.addColorStop(0,'#8f8f8f'); gr.addColorStop(0.6,'#dcdcdc'); gr.addColorStop(1,'#ffffff');
      g.fillStyle=gr; g.beginPath(); g.arc(cx,cy,R*0.72,0,6.2832); g.fill(); } }
    const t=new THREE.CanvasTexture(cv); t.wrapS=t.wrapT=THREE.RepeatWrapping; t.repeat.set(4,3); return t;
  }
  const dimpleTex=makeDimpleTex();
  const makeBallMat=(col)=>new THREE.MeshStandardMaterial({color:col, roughness:0.34, metalness:0.02,
    bumpMap:dimpleTex, bumpScale:0.02});
  const ball = new THREE.Mesh(new THREE.SphereGeometry(0.5,30,30), makeBallMat(0xffffff));
  ball.visible=false; scene.add(ball);
  const frac=x=>x-Math.floor(x);
  // Trees are real photographs - clean transparent cutouts, one per species -
  // drawn as camera-facing billboards: a broadleaf, a spreading oak, a conifer,
  // and an autumn variant. No procedural foliage. Cutouts: pngall.com (free).
  const TREE_IMAGES = __TREEIMAGES__;
  function billboardGeo(aspect){ const g=new THREE.PlaneGeometry(aspect,1.0); g.translate(0,0.5,0); return g; } // base on ground
  // Two perpendicular quads in a "+" so a tree has real volume from any angle - not a
  // single flat cardboard cutout. Base sits on the ground (y 0..1).
  function crossedGeo(aspect){ const a=aspect*0.5, g=new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array([
      -a,0,0, a,0,0, a,1,0, -a,1,0,   0,0,-a, 0,0,a, 0,1,a, 0,1,-a ]),3));
    g.setAttribute('uv', new THREE.BufferAttribute(new Float32Array([0,0,1,0,1,1,0,1, 0,0,1,0,1,1,0,1]),2));
    g.setIndex([0,1,2,0,2,3, 4,5,6,4,6,7]); g.computeVertexNormals(); return g; }
  function treeTexFrom(dataurl){ const im=new Image(); const t=new THREE.Texture(im);
    im.onload=()=>{ t.needsUpdate=true; }; im.src=dataurl;
    t.anisotropy=8; t.encoding=THREE.sRGBEncoding; t.minFilter=THREE.LinearMipmapLinearFilter; t.generateMipmaps=true; return t; }
  const _TALL=[10.5, 8.5, 15.5, 8.5, 12.0];    // height (yd): broadleaf, spreading oak/cypress, conifer, autumn, palm
  const TREE_KINDS = TREE_IMAGES.map((im,i)=>({
    geo: crossedGeo(im.a),                               // crossed quads: real 3D volume, not a flat cutout
    mat: new THREE.MeshBasicMaterial({map:treeTexFrom(im.d), alphaTest:0.5, side:THREE.DoubleSide, depthWrite:true, fog:true}),
    tall: _TALL[i] }));
  // per-course species weights [broadleaf, broadleaf2, conifer, autumn]
  const TREE_PROFILE = __TREEPROFILE__;
  const TALLGRASS = __TALLGRASS__;   // per-course amount of TALL native grass (manicured parkland ~0.1, links ~0.9)
  const PALETTE = __PALETTE__;   // per-course ocean + turf + rock colours, measured from real photos
  // On-hole turf/blade colours: default to the approved green (unchanged for the first 22),
  // overridable per course so links can carry golden-tan marram rough instead of green.
  const GFAIR = (PALETTE&&PALETTE.grassFair)||[0.36,0.64,0.21];
  const GROUGH = (PALETTE&&PALETTE.grassRough)||[0.13,0.30,0.08];
  if(PALETTE.rock) ROCK_KINDS.forEach(k=>k.mat.color.setRGB(PALETTE.rock[0],PALETTE.rock[1],PALETTE.rock[2]));  // tint the rock photo per course (dark charcoal granite at Pebble, grey links stone)
  const shadowGeo=new THREE.CircleGeometry(1,14); shadowGeo.rotateX(-Math.PI/2);
  const shadowMat=new THREE.MeshBasicMaterial({color:0x0c1509, transparent:true, opacity:0.17, depthWrite:false});
  // Real photographic turf (CC0, ambientCG Grass004): a seamless grass photo tiled
  // crisply and imprinted over the aerial, so the ground looks like actual grass up
  // close instead of a smeared satellite pixel. The aerial still drives the macro
  // fairway/rough colour; the grass just adds real blade texture and fades with
  // distance. This is what closes the gap to a proper simulator's turf.
  const GRASS_URL = __GRASSTEX__;
  const grassTex = new THREE.TextureLoader().load(GRASS_URL);
  grassTex.wrapS=grassTex.wrapT=THREE.RepeatWrapping; grassTex.anisotropy=8; grassTex.encoding=THREE.LinearEncoding;
  const grassDetailTex = new THREE.TextureLoader().load(__GRASSDETAIL__);   // fine blade grain for near the camera
  grassDetailTex.wrapS=grassDetailTex.wrapT=THREE.RepeatWrapping; grassDetailTex.anisotropy=8; grassDetailTex.encoding=THREE.LinearEncoding;
  const grassSampleTex = new THREE.TextureLoader().load(__GRASSSAMPLE__); grassSampleTex.wrapS=grassSampleTex.wrapT=THREE.RepeatWrapping; grassSampleTex.generateMipmaps=false; grassSampleTex.minFilter=THREE.LinearFilter; grassSampleTex.magFilter=THREE.LinearFilter; grassSampleTex.anisotropy=16; grassSampleTex.encoding=THREE.sRGBEncoding;
  const tuftTex = new THREE.TextureLoader().load(__TUFTTEX__); tuftTex.encoding=THREE.sRGBEncoding; tuftTex.anisotropy=8;
  const tuftMat = new THREE.MeshBasicMaterial({map:tuftTex, alphaTest:0.35, side:THREE.DoubleSide, transparent:false, fog:true});
  { const tc=__TALLGRASSCOL__; tuftMat.color.setRGB(tc[0],tc[1],tc[2]); }   // tint the native grass to the real course (golden fescue on links)
  let tuftMesh=null, tuftSpecs=null;
  function faceTufts(){ if(!tuftMesh||!tuftSpecs) return;
    const cx=cam.position.x, cz=cam.position.z;
    for(let i=0;i<tuftSpecs.length;i++){ const s=tuftSpecs[i];
      _E.set(0, Math.atan2(cx-s[0], cz-s[2]), 0); _q.setFromEuler(_E);
      _P.set(s[0],s[1],s[2]); _S.set(s[3],s[3],s[3]); _M.compose(_P,_q,_S);
      tuftMesh.setMatrixAt(i,_M); }
    tuftMesh.instanceMatrix.needsUpdate=true; }
  // Bunker sand: fine warm granular speckle plus faint rake ridges, tiled and
  // multiplied into the aerial only where the ground is sand-coloured.
  function makeSand(){
    const S=256, cv=document.createElement('canvas'); cv.width=cv.height=S; const g=cv.getContext('2d');
    g.fillStyle='#807d77'; g.fillRect(0,0,S,S);
    const wrap=(fn)=>{ for(let ox=-S;ox<=S;ox+=S) for(let oy=-S;oy<=S;oy+=S) fn(ox,oy); };
    // coarse mottle so the sand reads at distance (soft light/dark scoops)
    for(let i=0;i<120;i++){ const x=Math.random()*S, y=Math.random()*S, r=12+Math.random()*36;
      const v=128+(Math.random()*2-1)*26, a=(0.10+Math.random()*0.14).toFixed(3);
      wrap((ox,oy)=>{ const gr=g.createRadialGradient(x+ox,y+oy,0,x+ox,y+oy,r);
        gr.addColorStop(0,'rgba('+Math.round(v+5)+','+Math.round(v)+','+Math.round(v-8)+','+a+')');
        gr.addColorStop(1,'rgba(0,0,0,0)'); g.fillStyle=gr; g.beginPath(); g.arc(x+ox,y+oy,r,0,6.283); g.fill(); }); }
    // rake ridges: clear parallel bands (the signature bunker look)
    for(let y=0;y<S;y+=7){ const v=128+Math.sin(y*0.9)*10;
      g.strokeStyle='rgba('+Math.round(v+6)+','+Math.round(v)+','+Math.round(v-10)+',0.18)';
      g.lineWidth=2.4; wrap((ox,oy)=>{ g.beginPath(); g.moveTo(-S+ox,y+oy); g.lineTo(2*S+ox,y+oy); g.stroke(); }); }
    // dense fine grain - the granular sparkle of real sand (light and dark grains)
    for(let i=0;i<34000;i++){ const x=Math.random()*S, y=Math.random()*S, r=0.5+Math.random()*0.9;
      const v=128+(Math.random()*2-1)*54;
      g.fillStyle='rgba('+Math.round(v+8)+','+Math.round(v)+','+Math.round(v-13)+','+(0.30+Math.random()*0.45)+')';
      wrap((ox,oy)=>{ g.beginPath(); g.arc(x+ox,y+oy,r,0,6.283); g.fill(); }); }
    const t=new THREE.CanvasTexture(cv); t.wrapS=t.wrapT=THREE.RepeatWrapping; t.anisotropy=8; return t;
  }
  // Real photo textures (CC0): fine bunker sand and coastal rock, embedded like the trees.
  const sandTex=new THREE.TextureLoader().load(__SANDTEX__);
  sandTex.wrapS=sandTex.wrapT=THREE.RepeatWrapping; sandTex.anisotropy=8; sandTex.encoding=THREE.sRGBEncoding;
  // 3D grass blades near the camera: instanced tapered blades that wave in the wind,
  // so up close you see individual blades (like a real simulator), fading out into
  // the aerial farther off. Each blade takes its colour from the aerial beneath it,
  // so fairway/rough/green read right, and only grows where the ground is grass.
  const GRASSN=3000000, GRASSR=600.0;   // FULL-HOLE dense grass (tee to green, no band) - the approved dense look; do NOT reduce or widen (looks cheap); GRASSR = camera fade distance
  const GOFF=new Float32Array(GRASSN*4);
  (function(){ let s=99173; const rnd=()=>{ s=(s*1103515245+12345)&0x7fffffff; return s/0x7fffffff; };
    for(let i=0;i<GRASSN;i++){ GOFF[i*4]=rnd(); GOFF[i*4+1]=rnd(); GOFF[i*4+2]=rnd()*6.283; GOFF[i*4+3]=0.7+rnd()*0.55; } })();
  // Each instance is a tuft of several blades, so ~42k tufts read as a dense turf
  // carpet rather than scattered spikes. Blades fan at varied angles/heights.
  function bladeGeo(){ const g=new THREE.BufferGeometry(); const pos=[], uv=[];
    let s=7; const rr=()=>{ s=(s*1103515245+12345)&0x7fffffff; return s/0x7fffffff; };
    for(let k=0;k<4;k++){ const ox=(rr()-0.5)*0.26, oz=(rr()-0.5)*0.26, ang=rr()*6.283;   // fine dense blades - do NOT widen (looks cheap/Roblox)
      const w=0.130+rr()*0.070, h=0.24+rr()*0.10, c=Math.cos(ang)*w, sn=Math.sin(ang)*w;
      const lx=(rr()-0.5)*0.22*h, lz=(rr()-0.5)*0.22*h;          // random LEAN so blades bend different ways (breaks the vertical streaking)
      pos.push(ox-c,0,oz-sn,  ox+c,0,oz+sn,  ox+lx,h,oz+lz);     // one blade = a tapered, leaning triangle
      uv.push(0,0, 1,0, 0.5,1); }
    g.setAttribute('position',new THREE.Float32BufferAttribute(pos,3));
    g.setAttribute('uv',new THREE.Float32BufferAttribute(uv,2));
    g.computeVertexNormals(); return g; }
  // Grass = thousands of thin BLADES (like the reference drawing), dense over the
  // dark rough ground so there are no gaps. NO cards.
  const grassMat=new THREE.ShaderMaterial({ side:THREE.DoubleSide,
    uniforms:{ time:{value:0}, windDir:{value:new THREE.Vector2(1,0)}, windStr:{value:0.3},
               camPos:{value:new THREE.Vector3()}, fadeR:{value:GRASSR},
               sunDir:{value:new THREE.Vector3(-0.532,0.816,0.230)} },   // same upper-left sun as the scene light
    vertexShader:
      'varying vec3 vCol; varying float vA; varying float vT; varying float vL; varying float vR;\n'
      +'uniform float time; uniform vec2 windDir; uniform float windStr; uniform vec3 camPos; uniform float fadeR; uniform vec3 sunDir;\n'
      +'void main(){ vCol=instanceColor; vT=uv.y;\n'
      +' vec4 wp=instanceMatrix*vec4(0.0,0.0,0.0,1.0);\n'
      +' float d=distance(wp.xz,camPos.xz); float fade=1.0-smoothstep(fadeR*0.78,fadeR,d); vA=fade;\n'
      +' vR=fract(sin(wp.x*12.9898+wp.z*78.233)*43758.5453);\n'                          // per-tuft random: distance fade thins whole tufts, no hard edge
      +' vec3 nw=normalize(mat3(modelMatrix)*mat3(instanceMatrix)*normal);\n'
      +' vL=0.80+0.40*abs(dot(nw,sunDir));\n'                                             // blades catch the sun by their facing (lit like the ground, not flat cartoon colour)
      +' vec3 p=position; float t=uv.y;\n'
      +' float ph=wp.x*0.6+wp.z*0.6; float s=(windStr*0.05+windStr*0.04*sin(time*2.0+ph))*t;\n'
      +' p.x+=windDir.x*s*2.5; p.z+=windDir.y*s*2.5;\n'
      +' gl_Position=projectionMatrix*modelViewMatrix*instanceMatrix*vec4(p,1.0); }',
    fragmentShader:
      'varying vec3 vCol; varying float vA; varying float vT; varying float vL; varying float vR;\n'
      +'void main(){ if(vA<0.03 || vA<vR*0.999) discard;\n'                                // soft, tuft-by-tuft thinning into the ground at distance
      +' gl_FragColor=vec4(vCol*(0.60+0.42*vT)*vL, 1.0); }' });                        // natural blade: self-shadowed base, tip at ~full colour (no pale over-bright tips), sun-lit
  let grassMesh=new THREE.InstancedMesh(bladeGeo(), grassMat, GRASSN);                  // thin grass blades (no cards)
  grassMesh.frustumCulled=false;
  grassMesh.instanceColor=new THREE.InstancedBufferAttribute(new Float32Array(GRASSN*3),3);
  scene.add(grassMesh);
  grassMesh.visible=true;    // raised grass, built patch-by-patch on a jittered GRID (no gaps between batches), over the user's grass ground
  let gOrthoPix=null, gOrthoW=0, gOrthoH=0, lastGrassX=1e9, lastGrassZ=1e9;
  // Natural turf patchiness, shared by the 3D blades AND the ground shader (one texture,
  // so near blades and far ground always agree - no seam). Low-frequency only: 6-16yd
  // patches of drier/lighter and deeper/darker grass, never fine streaks.
  let gMottle=null, gMU=0, gMV=0, gMottleTex=null;
  function makeMottle(W, a0, a1, seed){
    const MU=96, MV=Math.max(64, Math.min(320, Math.round((a1-a0)/2.5)));
    const hs=(x,y)=>{ let h=(Math.imul(x,374761393)+Math.imul(y,668265263)+Math.imul(seed|0,-2048144789))|0;
      h=Math.imul(h^(h>>>13),1274126177); return ((h^(h>>>16))>>>0)/4294967295; };
    const vn=(x,y)=>{ const xi=Math.floor(x), yi=Math.floor(y), xf=x-xi, yf=y-yi, u=xf*xf*(3-2*xf), v=yf*yf*(3-2*yf);
      return hs(xi,yi)*(1-u)*(1-v)+hs(xi+1,yi)*u*(1-v)+hs(xi,yi+1)*(1-u)*v+hs(xi+1,yi+1)*u*v; };
    const arr=new Float32Array(MU*MV), data=new Uint8Array(MU*MV*4);
    for(let r=0;r<MV;r++){ const al=a0+(a1-a0)*r/(MV-1);
      for(let c=0;c<MU;c++){ const ac=-W+2*W*c/(MU-1);
        let n=0.65*vn(ac/16.0, al/16.0) + 0.35*vn(ac/6.0+31.7, al/6.0+17.3);
        n=Math.min(1,Math.max(0,(n-0.22)/0.56)); n=n*n*(3-2*n);
        arr[r*MU+c]=n; const b=Math.round(n*255), k=(r*MU+c)*4; data[k]=b; data[k+1]=b; data[k+2]=b; data[k+3]=255; } }
    if(gMottleTex) gMottleTex.dispose();
    const tx=new THREE.DataTexture(data, MU, MV, THREE.RGBAFormat);
    tx.magFilter=tx.minFilter=THREE.LinearFilter; tx.wrapS=tx.wrapT=THREE.ClampToEdgeWrapping; tx.needsUpdate=true;
    gMottle=arr; gMU=MU; gMV=MV; gMottleTex=tx; return tx;
  }
  function mottleAt(u,v){                       // bilinear, same filtering as the shader's texture lookup
    if(!gMottle) return 0.5;
    const x=Math.max(0,Math.min(gMU-1,u*(gMU-1))), y=Math.max(0,Math.min(gMV-1,v*(gMV-1)));
    const x0=Math.floor(x), y0=Math.floor(y), x1=Math.min(gMU-1,x0+1), y1=Math.min(gMV-1,y0+1), fx=x-x0, fy=y-y0;
    const a=gMottle[y0*gMU+x0], b=gMottle[y0*gMU+x1], c=gMottle[y1*gMU+x0], d=gMottle[y1*gMU+x1];
    return (a*(1-fx)+b*fx)*(1-fy)+(c*(1-fx)+d*fx)*fy;
  }
  // The rough/fairway colour at a mottle value - the SAME formula is used in the ground shader.
  function turfCol(base, m, isRough){
    if(isRough){ const k=0.80+0.34*m; return [base[0]*k*(1.0+0.10*m), base[1]*k*(1.0+0.05*m), base[2]*k*(1.0-0.16*m)]; }
    const k=0.95+0.10*m; return [base[0]*k, base[1]*k, base[2]*k];
  }
  const _gm=new THREE.Matrix4(), _gp=new THREE.Vector3(), _gq=new THREE.Quaternion(), _ge=new THREE.Euler(), _gs=new THREE.Vector3(), _gc=new THREE.Color();
  let rockGroups=null, rockSpecs=null;
  // Coastal rocks as photo billboards: read the shoreline from the aerial (blue water
  // meeting land) and stand real-rock cutouts along it - Pebble's cliff rock. Rebuilt
  // per hole; faced to the camera each frame in faceRocks().
  function placeRocks(){
    if(rockGroups){ for(const gr of rockGroups) if(gr.mesh) group.remove(gr.mesh); }
    rockGroups=null; rockSpecs=null;
    if(!waterMesh) return;                              // coastal rocks only where there is an ocean plane
    const wy=waterMesh.position.y, na=gNA, nx=gNX, shore=[];
    for(let i=1;i<na;i++){ for(let j=1;j<nx;j++){ const h=gH[i][j]*EXAG;
      if(h<wy+0.2) continue;                            // must be land (above the water)
      if(gH[i+1][j]*EXAG<wy || gH[i-1][j]*EXAG<wy || gH[i][j+1]*EXAG<wy || gH[i][j-1]*EXAG<wy){   // touches open water = the waterline
        const al=gA0+(gA1-gA0)*i/na, ac=-gW+2*gW*j/nx;
        shore.push([ac, Math.min(h, wy+1.2)-0.7, al]); } } }   // seat rocks at the water's edge, not up the cliff
    if(!shore.length) return;
    const step=Math.max(1, Math.floor(shore.length/420)), pts=[];   // dense: a rugged rock rim, not scattered stones
    for(let k=0;k<shore.length;k+=step) pts.push(shore[k]);
    rockSpecs=[];
    for(let k=0;k<pts.length;k++){ const b=pts[k], nR=frac(k*0.31)<0.6?3:2;   // clusters of 2-3, stacked
      for(let c=0;c<nR;c++){ const f=frac(k*0.37+c*1.7), f2=frac(k*2.3+c*0.7);
        const kind=Math.floor(frac(k*0.53+c*0.91)*ROCK_KINDS.length)%ROCK_KINDS.length;
        const sz=3.8+9.5*f*f;                                      // bigger boulders, up to ~13yd - a granite cliff edge
        const x=b[0]+(frac(k*1.1+c)-0.5)*8.0, z=b[2]+(frac(k*1.7+c)-0.5)*8.0;
        rockSpecs.push([x, b[1]-0.8-f2*1.5, z, sz, kind, 0]); } }   // seated at the waterline, varied depth
    rockGroups=ROCK_KINDS.map(()=>({mesh:null, idx:[]}));
    for(let i=0;i<rockSpecs.length;i++){ const kd=rockSpecs[i][4]; rockSpecs[i][5]=rockGroups[kd].idx.length; rockGroups[kd].idx.push(i); }
    for(let gI=0;gI<ROCK_KINDS.length;gI++){ const grp=rockGroups[gI]; if(!grp.idx.length) continue;
      grp.mesh=new THREE.InstancedMesh(ROCK_KINDS[gI].geo, ROCK_KINDS[gI].mat, grp.idx.length); grp.mesh.frustumCulled=false; group.add(grp.mesh); }
    faceRocks();
  }
  function faceRocks(){
    if(!rockGroups||!rockSpecs) return;
    const cx=cam.position.x, cz=cam.position.z;
    for(let i=0;i<rockSpecs.length;i++){ const s=rockSpecs[i];
      _E.set(0, Math.atan2(cx-s[0], cz-s[2]), 0); _q.setFromEuler(_E);
      _P.set(s[0],s[1],s[2]); _S.set(s[3],s[3],s[3]); _M.compose(_P,_q,_S);
      const grp=rockGroups[s[4]]; if(grp.mesh) grp.mesh.setMatrixAt(s[5],_M); }
    for(let gI=0;gI<rockGroups.length;gI++){ if(rockGroups[gI].mesh) rockGroups[gI].mesh.instanceMatrix.needsUpdate=true; }
  }
  function placeGrass(cx,cz){
    if(!grassMesh||!gOrthoPix) return;
    lastGrassX=lieA; lastGrassZ=lieC;   // remember the BALL position this grass band was built for
    const PF=(PALETTE&&PALETTE.fairway)||[0.17,0.43,0.13], PR=(PALETTE&&PALETTE.rough)||[0.12,0.30,0.11], GPP=clAtA(gGreenA);
    // Dense rough in a band centred on the BALL (lieA), reaching ahead toward the green;
    // it moves with the ball so wherever you play there is full dense grass (and the far
    // distance beyond the band is carried by the ground). Re-placed only when the ball moves.
    const gridW=Math.min(gW-2.0, 60.0);
    const gA=gA0+2.0, gB=gA1-2.0;   // FULL HOLE: grass runs tee to green so there is no band far-edge seam (the transition the user hated)
    const NG=0, BN=GRASSN-NG, GRN=clAtA(gGreenA);   // no reserved green patch: full coverage already grasses the green
    const aRange=gB-gA, cRange=2.0*gridW;
    const T0C=clAtA(0.0)[1];   // tee-box centre across (the box spans along -9.5..6.5, +/-7.5yd)
    const acrossN=Math.max(1, Math.round(Math.sqrt(BN*cRange/aRange)));
    const alongN=Math.floor(BN/acrossN);
    const cellA=aRange/alongN, cellC=cRange/acrossN;
    for(let i=0;i<GRASSN;i++){ let along, across, atGreen=false;
      if(i<NG){ const a=GOFF[i*4]*6.283, rr2=15.0*Math.sqrt(GOFF[i*4+1]);   // a patch of short grass on/around the green, always present
        across=GRN[1]+Math.cos(a)*rr2; along=GRN[0]+Math.sin(a)*rr2; atGreen=true; }
      else { const k=i-NG, ia=Math.floor(k/acrossN), jc=k%acrossN;
        along = gA + cellA*(ia+0.5) + (GOFF[i*4]-0.5)*cellA*0.95;
        across = -gridW + cellC*(jc+0.5) + (GOFF[i*4+1]-0.5)*cellC*0.95; }
      const bx=across, bz=along;
      let ok = along>gA0 && along<gA1 && across>-gW && across<gW;
      let r=0,gg=0,b=0;
      if(ok){ const u=(across+gW)/(2*gW), v=(along-gA0)/(gA1-gA0);
        const px=Math.max(0,Math.min(gOrthoW-1,Math.round(u*(gOrthoW-1))));
        const py=Math.max(0,Math.min(gOrthoH-1,Math.round((1.0-v)*(gOrthoH-1))));
        const o=(py*gOrthoW+px)*4; r=gOrthoPix[o]; gg=gOrthoPix[o+1]; b=gOrthoPix[o+2];
        ok = gg>=b-8 && !(r>168&&gg>158&&b<gg-8) && (r+gg+b)>28; }               // cover everything (incl. bright fairway + brown patches); only skip real SAND (bright & warm) and WATER (blue)
      if(ok){ for(let pi=0;pi<gCart.length;pi++){ if(Math.hypot(along-gCart[pi][0],across-gCart[pi][1])<3.2){ ok=false; break; } } }   // keep grass OFF the cart path
      if(!ok){ _gs.set(0,0,0); _gm.compose(_gp.set(bx,-999,bz),_gq,_gs); grassMesh.setMatrixAt(i,_gm); continue; }
      const y=sampleH(along,across);
      const clc=clAtA(along)[1], dG=Math.hypot(along-GPP[0],across-GPP[1]);
      const onG=dG<16.0;                                                              // green (core) + collar - never tall rough
      const nearTee=along>-9.5 && along<6.5 && Math.abs(across-T0C)<7.5;             // the TEE BOX itself = short mown (not the whole width of the hole)
      // REAL fairway shape: the hole centreline is placed to the actual hole (the dogleg
      // around the bunker), so distance from it defines the mown fairway corridor.
      const distCL=Math.abs(across-clc);
      const fwHW=fwHalf(along, across-clc);                                             // 0 where this hole's aerial shows rough (incl. the tee apron); >0 in the real fairway
      const inFair=!onG && !nearTee && fwHW>1.5 && distCL<fwHW && along>14.0;            // real per-hole fairway body + start, traced from this hole's aerial (asymmetric L/R)
      const inR=!onG && !nearTee && !inFair;                                      // rough outside it (tall, dark); tee & green are NOT rough
      // fairway = smooth LOW mown texture; rough = TALL, UNIFORM grass carpet (matches the reference)
      _ge.set(0,GOFF[i*4+2],0); _gq.setFromEuler(_ge);
      const hj=0.94+0.10*frac(GOFF[i*4]*1.7+GOFF[i*4+1]*0.9);        // near-UNIFORM height (no random tall patches)
      const edgeFade=1.0;  // full-hole coverage: no artificial band taper - the camera distance-fade (fadeR) handles the far horizon
      // height: putting green almost flat, ramping up over the collar to the rough (no hard ring)
      let baseH;
      if(nearTee) baseH=0.45;
      else if(dG<9.0) baseH=0.0;                                                  // PUTTING SURFACE = smooth ground, NO blades (no specks)
      else if(onG) baseH = 0.05 + (dG-9.0)/7.0*(1.30-0.05);                       // fringe/collar ramps up to the rough over 9-16yd
      else if(inFair) baseH=0.6;
      else baseH=1.3;
      const sc=baseH*hj*edgeFade;
      _gs.set(sc, sc, sc);
      _gp.set(bx,y,bz); _gm.compose(_gp,_gq,_gs); grassMesh.setMatrixAt(i,_gm);
      const mU=(across+gW)/(2*gW), mV=(along-gA0)/(gA1-gA0), mt=mottleAt(mU,mV);
      const bc=(onG||nearTee)?turfCol([0.34,0.56,0.20],mt,false):(inFair?turfCol(GFAIR,mt,false):turfCol(GROUGH,mt,true));  // per-course palette + natural patchiness (same as the ground under it)
      const stripe=inFair?(Math.sin(across*0.85)>0.0?1.07:0.95):1.0;            // gentle mow stripes in the fairway only
      const jitr=0.90+0.20*frac(GOFF[i*4]*0.7+GOFF[i*4+1]*1.3);                 // gentle per-tuft light/dark (reduced, so it is not streaky)
      _gc.setRGB(bc[0]*stripe*jitr, bc[1]*stripe*jitr, bc[2]*stripe*jitr); grassMesh.setColorAt(i,_gc);
    }
    grassMesh.instanceMatrix.needsUpdate=true; if(grassMesh.instanceColor) grassMesh.instanceColor.needsUpdate=true;
  }
  let flagMesh=null, flagBase=null, windStrength=0.3;   // flag mesh + its flat rest positions
  // Ocean: a self-contained water shader. Deep colour looking down, a Fresnel
  // mirror toward the horizon, sky-tinted reflections, sun glitter, and layered
  // animated ripples. Land pokes through wherever it is above sea level.
  const waterMat=new THREE.ShaderMaterial({
    transparent:true, side:THREE.DoubleSide,
    uniforms:{ time:{value:0}, sunDir:{value:sun.position.clone().normalize()},
      skyTop:{value:new THREE.Color(0x2f74c0)}, skyBot:{value:new THREE.Color(0xe2edf3)},
      deep:{value:new THREE.Color(PALETTE.deep)}, shallow:{value:new THREE.Color(PALETTE.shallow)},
      wind:{value:0.3}, windDir:{value:new THREE.Vector2(1.0,0.0)}, rain:{value:0.0},
      flow:{value:new THREE.Vector2(0.0,0.0)} },
    vertexShader:
      'uniform float time,wind; uniform vec2 windDir,flow; varying vec3 wpos;'+
      'float wv(vec2 q){ vec2 wd=normalize(windDir+vec2(0.001,0.0)); float h=0.0;'+
      ' for(int i=0;i<3;i++){ float fi=float(i);'+
      '  vec2 dir=normalize(mix(vec2(sin(fi*2.1+0.7),cos(fi*1.7-0.3)), wd, 0.6));'+
      '  float len=12.0+fi*10.0, am=(0.05+wind*0.4)/(1.0+fi*0.7);'+     // gentle swell, grows with wind
      '  h+=sin(dot(q,dir)*(6.2831/len)+time*(0.8+fi*0.4)*(0.4+wind))*am; }'+
      ' return h; }'+
      'void main(){ vec2 q=vec2(position.x,-position.y)-flow; vec3 p=position; p.z+=wv(q);'+
      ' wpos=(modelMatrix*vec4(p,1.0)).xyz;'+
      ' gl_Position=projectionMatrix*modelViewMatrix*vec4(p,1.0); }',
    fragmentShader:
      'uniform float time,wind,rain; uniform vec3 sunDir,skyTop,skyBot,deep,shallow; uniform vec2 windDir,flow; varying vec3 wpos;'+
      'vec3 ripN(vec2 p){ vec2 d=vec2(0.0); vec2 wd=normalize(windDir+vec2(0.001,0.0));'+
      ' for(int i=0;i<4;i++){ float fi=float(i);'+
      '  vec2 b=vec2(sin(fi*1.7+0.4),cos(fi*2.3+0.1)); vec2 dir=normalize(mix(b,wd,0.55));'+
      '  float fr=(0.16+fi*0.10)*(1.0+wind*0.4), sp=(0.6+fi*0.4)*(0.5+wind*1.7), am=(0.16+wind*0.95)/(1.0+fi);'+
      '  float ph=dot(p,dir)*fr+time*sp; d+=dir*fr*cos(ph)*am; }'+
      ' for(int j=0;j<2;j++){ float fj=float(j); vec2 dir=vec2(cos(fj*2.1),sin(fj*2.1));'+
      '  float fr=0.7+fj*0.6, ph=dot(p,dir)*fr+time*(3.0+fj*1.5); d+=dir*fr*cos(ph)*(0.05*rain); }'+
      ' return normalize(vec3(-d.x,1.0,-d.y)); }'+
      'vec3 skyC(vec3 dir){ float h=clamp(dir.y*0.5+0.42,0.0,1.0); return mix(skyBot,skyTop,pow(h,0.7)); }'+
      'void main(){ vec2 wq=wpos.xz-flow; vec3 N=ripN(wq); vec3 V=normalize(cameraPosition-wpos);'+
      ' float f=clamp(0.02+0.98*pow(1.0-max(dot(V,N),0.0),3.0),0.0,1.0);'+
      ' vec3 R=reflect(-V,N); vec3 refl=skyC(R); vec3 base=mix(deep,shallow,0.26);'+
      // a gentle moving swell: the wind-driven ripple slope tints light/dark bands
      ' base=mix(base, shallow, clamp(N.x*0.6 - N.z*0.6 + 0.5, 0.0, 1.0)*0.18);'+
      ' vec3 col=mix(base,refl,f);'+
      ' float spec=pow(max(dot(R,normalize(sunDir)),0.0),150.0); col+=vec3(1.0,0.95,0.82)*spec*1.5*(1.0-rain*0.6);'+
      // Travelling swell bands applied to the FINAL colour, so the motion reads even
      // at a grazing angle where the water just mirrors the sky (distant tee views).
      ' vec2 wds=normalize(windDir+vec2(0.001,0.0));'+
      ' float sh=0.5*sin(dot(wq,wds)*0.28+time*(1.2+wind*3.0)) + 0.5*sin(dot(wq,vec2(-wds.y,wds.x))*0.20+time*(0.8+wind*2.0));'+
      ' col += vec3(0.05,0.08,0.10)*sh*(0.4+wind*0.7);'+                    // gentle moving swell, wind-scaled
      ' float crest=smoothstep(0.72,0.99,sh); col += vec3(0.5,0.56,0.60)*crest*(0.02+wind*0.13);'+  // a little foam only on the highest crests, in stronger wind
      ' col=mix(col, vec3(0.42,0.47,0.5), rain*0.22);'+
      ' gl_FragColor=vec4(col, mix(0.82,1.0,f)); }'
  });
  let waterMesh=null, waterBaseY=0;
  function rippleWater(){ waterMat.uniforms.time.value += 0.016; }
  // Flag streams downwind; its cloth ripples as a travelling wave that grows toward
  // the free end and flaps harder the stronger the real wind.
  function flutterFlag(){ if(!flagMesh||!flagBase) return; const t=waterMat.uniforms.time.value;
    const wd=waterMat.uniforms.windDir.value; flagMesh.rotation.set(0, Math.atan2(-wd.x,-wd.y), 0);
    const arr=flagMesh.geometry.attributes.position.array, amp=0.06+windStrength*0.34, sp=4.0+windStrength*7.0;
    for(let i=0;i<arr.length;i+=3){ const x=flagBase[i], y=flagBase[i+1], f=x/2.3;   // 0 at pole, 1 at tip
      arr[i]=x; arr[i+1]=y + Math.sin(x*2.4 - t*sp*0.7)*0.05*f;                        // slight vertical sag/lift
      arr[i+2]=(Math.sin(x*3.0 - t*sp) + 0.5*Math.sin(x*6.0 - t*sp*1.6 + y*3.0))*amp*f*f; }  // out-of-plane flap
    flagMesh.geometry.attributes.position.needsUpdate=true; flagMesh.geometry.computeVertexNormals(); }
  // Casual water (puddles) that the weather leaves on the course. Not a penalty:
  // free relief, but they pull a rolling ball up. More of them the wetter it is.
  const puddleMat=new THREE.MeshStandardMaterial({color:0x2e5f74, transparent:true,
    opacity:0.72, roughness:0.1, metalness:0.1});
  let wetness=0, gPuddles=[], puddleGroup=null;
  function makePuddles(){
    if(puddleGroup) group.remove(puddleGroup);
    puddleGroup=new THREE.Group(); group.add(puddleGroup); gPuddles=[];
    if(wetness<0.3) return;
    const n=Math.round(wetness*12), L=gGreenA;
    for(let k=0;k<n;k++){
      const al=12+(L-24)*((k*0.618034)%1), ac=-26+52*((k*0.317+0.2)%1), r=3+((k*7)%4);
      gPuddles.push([al,ac,r]);
      const disc=new THREE.Mesh(new THREE.CircleGeometry(r,18), puddleMat);
      disc.rotation.x=-Math.PI/2; disc.position.set(ac, sampleH(al,ac)+0.08, al); disc.renderOrder=1;
      puddleGroup.add(disc);
    }
  }
  // The course plays as the LIVE wind + tide at its real coast. We walk the baked
  // hourly-wind forecast and NOAA tide curve by the browser clock (so it stays
  // dynamic offline / in the preview sandbox), and try a live refresh on load to
  // override them whenever there is internet.
  function windName(deg){ const D=['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];
    return D[Math.round((((deg%360)+360)%360)/22.5)%16]; }
  let windHourly=WEATHER.wind_hourly||[], tideData=WEATHER.tide||null;
  const shortA=(d)=>((d%360)+540)%360-180;
  function windAt(ts){ const A=windHourly; if(!A||!A.length) return {mph:WEATHER.wind_speed_mph||7,deg:WEATHER.wind_dir_deg||270,precip:0};
    if(ts<=A[0][0]) return {mph:A[0][1],deg:A[0][2],precip:A[0][3]||0};
    for(let i=0;i<A.length-1;i++){ if(ts<A[i+1][0]){ const f=(ts-A[i][0])/((A[i+1][0]-A[i][0])||1);
      return {mph:A[i][1]+(A[i+1][1]-A[i][1])*f, deg:A[i][2]+shortA(A[i+1][2]-A[i][2])*f, precip:(A[i][3]||0)+((A[i+1][3]||0)-(A[i][3]||0))*f}; } }
    const L=A[A.length-1]; return {mph:L[1],deg:L[2],precip:L[3]||0}; }
  function tideAt(ts){ if(!tideData||!tideData.points||tideData.points.length<2) return {h:0,rate:0,mean:0};
    const P=tideData.points; let i=0; while(i<P.length-2 && ts>=P[i+1][0]) i++;
    const t0=P[i][0],h0=P[i][1],t1=P[i+1][0],h1=P[i+1][1];
    const u=Math.max(0,Math.min(1,(ts-t0)/((t1-t0)||1))), c=(1-Math.cos(u*Math.PI))/2, dh=((t1-t0)/3600)||1;
    return {h:h0+(h1-h0)*c, rate:(h1-h0)*Math.PI*Math.sin(u*Math.PI)/2/dh, mean:tideData.mean_m||0}; }
  let flow=new THREE.Vector2(0,0), lastCond=0;
  function applyConditions(){
    const now=Date.now()/1000, dt=lastCond?Math.min(3,now-lastCond):0; lastCond=now;
    const w=windAt(now); windStrength=Math.min(1,(w.mph||6)/22); const wrad=(w.deg||270)*Math.PI/180;
    waterMat.uniforms.wind.value=windStrength;
    waterMat.uniforms.windDir.value.set(Math.cos(wrad),Math.sin(wrad));
    waterMat.uniforms.rain.value=Math.min(1,(w.precip||0)/2); wetness=Math.min(1,(w.precip||0)/2);
    const td=tideAt(now);
    const lvl=Math.max(-1.0,Math.min(0.8,(td.h-td.mean)*1.0936*EXAG));     // real tide height, occlusion-safe
    if(waterMesh) waterMesh.position.y=waterBaseY+lvl;
    const cn=Math.max(-1,Math.min(1,td.rate/1.2));                          // flood(+)/ebb(-) current
    flow.x+=Math.cos(wrad)*cn*0.6*dt; flow.y+=Math.sin(wrad)*cn*0.6*dt;
    waterMat.uniforms.flow.value.copy(flow);
    const we=document.getElementById('weather');
    if(we){ let s='Wind '+Math.round(w.mph)+' mph '+windName(w.deg);
      if(tideData) s+=' · tide '+(td.rate>=0?'rising ▲':'falling ▼')+' '+td.h.toFixed(1)+'m';
      if(w.precip>0.1) s+=' · rain'; we.textContent=s; } }
  applyConditions(); setInterval(applyConditions, 3000);
  (async function liveWind(){   // live wind when online (open-meteo allows CORS). Tide uses the
    try{ if(WEATHER.lat==null) return;   // baked harmonic prediction curve, which is accurate ahead of time.
      const wr=await fetch('https://api.open-meteo.com/v1/forecast?latitude='+WEATHER.lat+'&longitude='+WEATHER.lon+'&hourly=wind_speed_10m,wind_direction_10m,precipitation&wind_speed_unit=mph&forecast_days=2&timeformat=unixtime');
      const H=(await wr.json()).hourly; if(H&&H.time){ windHourly=H.time.map((t,i)=>[t,H.wind_speed_10m[i]||0,H.wind_direction_10m[i]||0,H.precipitation[i]||0]); applyConditions(); }
    }catch(e){}
  })();
  // Trees are camera-facing billboards, turned to the camera every frame so they
  // never go edge-on; many overlapping per clump so a clump reads as a full mass.
  let treeGroups=null, treeSpecs=null, trunkMesh=null;
  const _M=new THREE.Matrix4(),_q=new THREE.Quaternion(),_P=new THREE.Vector3(),_S=new THREE.Vector3(),_E=new THREE.Euler();
  const _cam=new THREE.Quaternion(),_lean=new THREE.Quaternion(),_axis=new THREE.Vector3();
  function faceTrees(){                                          // 3D trees: sway trunk + canopy together
    if(!treeGroups||!treeSpecs) return;
    const t=waterMat.uniforms.time.value;
    // Trees lean and sway downwind; the whole tree pivots from the trunk base.
    const wd=waterMat.uniforms.windDir.value, dx=-wd.x, dz=-wd.y;   // downwind
    _axis.set(dz,0,-dx); if(_axis.lengthSq()<1e-6) _axis.set(1,0,0); _axis.normalize();
    // Realistic scale: a light breeze barely moves a tree; it only really leans
    // in a strong wind. A fixed random yaw per tree keeps the instances varied.
    const gust=0.7+0.3*Math.sin(t*0.17+2.0);                     // gentle gusting
    const lean=windStrength*0.05;                                // steady downwind lean
    const swayAmp=windStrength*0.03*gust;                        // sway oscillation
    const cx=cam.position.x, cz=cam.position.z;
    for(let i=0;i<treeSpecs.length;i++){ const s=treeSpecs[i], ph=s[0]*0.15+s[2]*0.11;
      _E.set(0, s[0]*0.7+s[2]*1.3, 0); _cam.setFromEuler(_E);   // crossed quads: a fixed per-tree yaw, NOT camera-facing (they have volume from every side)
      const th=lean + swayAmp*Math.sin(t*1.5+ph) + swayAmp*0.3*Math.sin(t*4.1+ph*1.7);
      _lean.setFromAxisAngle(_axis, th); _q.multiplyQuaternions(_lean,_cam);
      _P.set(s[0],s[1],s[2]); _S.set(s[3],s[3],s[3]); _M.compose(_P,_q,_S);
      if(trunkMesh) trunkMesh.setMatrixAt(i,_M);
      const grp=treeGroups[s[4]]; if(grp.mesh) grp.mesh.setMatrixAt(s[6],_M);
    }
    if(trunkMesh) trunkMesh.instanceMatrix.needsUpdate=true;
    for(let gI=0;gI<treeGroups.length;gI++){ if(treeGroups[gI].mesh) treeGroups[gI].mesh.instanceMatrix.needsUpdate=true; }
  }
  const loader = new THREE.TextureLoader();
  let ctl = new THREE.OrbitControls(cam, R.domElement);
  ctl.enableDamping = true; ctl.maxPolarAngle = Math.PI*0.49;

  // Where the player is hitting from. Starts on the tee; when the launch monitor
  // is wired in, the tracked shot's finishing point sets it. No ball is drawn
  // until a struck shot is flown - the player is hitting a real ball.
  let at=0, VIEW='play', groundMesh=null;
  let gA0=0, gA1=0, gNA=0, gNX=0, gW=0, gH=null, gCL=null, gGreenA=0, gSub='', gCart=[], gFW=null, gFwStart=0, gFwWidth=null;
  let lieA=0, lieC=0;                          // current lie: along, across (yards)
  let gTreeClumps=null, gWater=null, gBunkers=null, flight=null, toastT=0, holeBearing=0, flightClock=0;
  let pinC=0, pinA=0;   // world position of the pin, kept current per hole so a shot can always be aimed at it even when the green is blind
  let gHasSea=false, gSeaY=-1e9;   // this hole's real ocean surface, if any - only then is low ground water (inland downhill is NOT a penalty)
  let gOB=null;                    // across-distance to the white OB stakes, ONLY on holes the real course marks OB (null = no out of bounds on this hole)
  // Active player's live state mirrors players[cur]; hotseat multiplayer takes turns.
  let strokes=0, holed=false, resting=false, holeDone=false;
  let players=[{a:0,c:0,strokes:0,holed:false}], cur=0, nplayers=1;
  const BALLCOL=[0xffffff,0xffd23f,0x54d6ff,0xff86cf];   // one ball colour per player
  const TIP_HIT='tap to hit toward there · drag to look · pinch to zoom';
  const TIP_WALK='tap to walk up to your ball', TIP_NEXT='tap to continue — next player';
  const setTip=(t)=>{ const e=document.getElementById('tip'); if(e) e.textContent=t; };
  const sampleH=(al,ac)=>{                                   // BILINEAR height (smooth) - nearest-neighbour terraced the grass into 2yd rows (banding)
    const fi=(al-gA0)/(gA1-gA0)*gNA, fj=(ac+gW)/(2*gW)*gNX;
    const i0=Math.max(0,Math.min(gNA-1,Math.floor(fi))), j0=Math.max(0,Math.min(gNX-1,Math.floor(fj)));
    const ti=Math.max(0,Math.min(1,fi-i0)), tj=Math.max(0,Math.min(1,fj-j0));
    const a=gH[i0][j0], b=gH[i0+1][j0], c=gH[i0][j0+1], d=gH[i0+1][j0+1];
    return ((a*(1-ti)+b*ti)*(1-tj)+(c*(1-ti)+d*ti)*tj)*EXAG; };
  const inCircle=(list,al,ac)=>{ if(!list) return false;
    for(let i=0;i<list.length;i++){ if(Math.hypot(al-list[i][0],ac-list[i][1])<=list[i][2]) return true; } return false; };
  const treeHit=(x,y,z)=>{ const L=gTreeClumps; if(!L) return false;
    for(let i=0;i<L.length;i++){ const t=L[i], gy=sampleH(t[0],t[1]);
      if(Math.hypot(z-t[0],x-t[1])<t[2]+1.5 && (y-gy)<9 && (y-gy)>0.6) return true; } return false; };
  const toast=(m)=>{ const e=document.getElementById('toast'); e.textContent=m; e.style.opacity='1';
    clearTimeout(toastT); toastT=setTimeout(()=>e.style.opacity='0',2400); };
  // Fly a shot from the current lie toward a target, stopping at the first thing
  // it truly meets: a tree in the air, water (penalty) or a bunker on landing.
  // Short range stays low and runs (a chip or putt); full shots climb with carry.
  const peakLift=(d)=> d<25 ? Math.max(0.2,d*0.04) : Math.min(5+d*0.16,34);
  function hitShot(tx,tz){
    if(holed) return;
    { const lk=lieKind(lieA,lieC), cs=(lk==='bunker'?0.72 : lk==='rough'?0.87 : 1.0);
      tx=lieC+(tx-lieC)*cs; tz=lieA+(tz-lieA)*cs; }   // a rough or sand lie can't be struck clean: the ball comes up short of the target
    const fx=lieC, fz=lieA, fy=sampleH(lieA,lieC)+0.4, d=Math.hypot(tx-fx,tz-fz);
    if(d<2) return;
    strokes++; ball.material.color.setHex(BALLCOL[cur%4]);
    const dirx=(tx-fx)/d, dirz=(tz-fz)/d;
    // Real wind resolved into this hole's frame (along = play direction): a headwind
    // shortens the carry, a tailwind stretches it, a crosswind bows the flight sideways.
    const delta=(((WEATHER.wind_dir_deg||270)+180)-holeBearing)*Math.PI/180;
    const wScale=(WEATHER.wind_speed_mph||0)*(d/200)*(peakLift(d)/24);
    const driftC=Math.sin(delta)*wScale*1.0, driftA=Math.cos(delta)*wScale*0.7;
    const peak=peakLift(d), N=72, path=[]; let stop=-1, outcome='';
    lastShot={carry:Math.round(d), apex:Math.round(peak), sa:lieA, sc:lieC, club:clubFor(d)};   // for the shot-data panel
    for(let i=0;i<=N;i++){ const f=i/N, wf=f*f,
        x=fx+(tx-fx)*f+driftC*wf, z=fz+(tz-fz)*f+driftA*wf, gy=sampleH(z,x);
      const y=(fy+(sampleH(tz,tx)-fy)*f)+peak*4*f*(1-f); path.push([x,y,z]);
      if(i>3 && treeHit(x,y,z)){ stop=i; outcome='trees'; break; }
      if(i>3 && y<=gy+0.3){ stop=i;
        outcome = onGrn(z,x)?'roll' : inCircle(gWater,z,x)?'water' : inCircle(gBunkers,z,x)?'bunker' :
                  inCircle(gPuddles,z,x)?'puddle' : 'roll'; break; }
    }
    if(stop<0){ stop=N; const lx=tx+driftC, lz=tz+driftA;
      outcome = onGrn(lz,lx)?'roll' : inCircle(gWater,lz,lx)?'water' : inCircle(gBunkers,lz,lx)?'bunker' :
                inCircle(gPuddles,lz,lx)?'puddle' : 'roll'; }
    if(outcome==='roll'){
      // Real ground roll: the ball carries momentum, gravity pulls it down the true
      // slope (so it accelerates downhill, stalls and rolls BACK up a hill, and bends
      // across a sidehill), and turf friction — heavier when wet — brings it to rest.
      const li=path[stop], baseRoll=Math.min(d*0.11,26), cup=clAtA(gGreenA);  // cup = [along, across]
      const dt=0.05, g=16, e=1.0, afk=4.5+wetness*8.0;   // gravity, sample step, kinetic friction
      let v0=Math.sqrt(2*4.5*Math.max(baseRoll,0.4));
      let vx=dirx*v0, vz=dirz*v0, rx=li[0], rz=li[2];
      outcome='fairway';
      for(let step=0; step<500; step++){
        const gx=(sampleH(rz,rx+e)-sampleH(rz,rx-e))/(2*e);   // slope gradient (uphill)
        const gz=(sampleH(rz+e,rx)-sampleH(rz-e,rx))/(2*e);
        vx+=(-g*gx)*dt; vz+=(-g*gz)*dt;                        // gravity accelerates downhill
        let sp=Math.hypot(vx,vz);
        const afkR=afk*(Math.abs(rx-clAtA(rz)[1])>17.0 ? 2.5 : 1.0);              // off the fairway ribbon the rough grabs the ball, killing the roll
        if(sp>0){ const ns=Math.max(0,sp-afkR*dt); vx*=ns/sp; vz*=ns/sp; sp=ns; }  // friction (heavier in rough)
        if(sp>45){ vx*=45/sp; vz*=45/sp; sp=45; }
        const gradMag=Math.hypot(gx,gz);
        if(sp<0.25 && g*gradMag < afk*0.6) break;             // at rest and the slope can't restart it
        rx+=vx*dt; rz+=vz*dt; const gy=sampleH(rz,rx);
        path.push([rx, gy+0.4, rz]);
        if(Math.hypot(rz-cup[0],rx-cup[1])<0.7 && sp<7.0){ outcome='holed'; break; }  // dropped in (fast putts run past)
        const og=onGrn(rz,rx);
        if(!og && gHasSea && gy < gSeaY){ outcome='water'; break; }  // trickled into the SEA (only where the hole has ocean; inland downhill is NOT water)
        if(!og && inCircle(gPuddles,rz,rx)){ outcome='puddle'; break; }
        if(!og && inCircle(gWater,rz,rx)){ outcome='water'; break; }
        if(!og && inCircle(gBunkers,rz,rx)){ outcome='bunker'; break; }
      }
      if(outcome==='holed'){ const cy=sampleH(cup[0],cup[1]);  // snap to the cup and drop the ball in
        path.push([cup[1], cy+0.4, cup[0]]);
        for(let k=1;k<=5;k++) path.push([cup[1], cy+0.42-k*0.20, cup[0]]); }
      stop=path.length-1;
    }
    flight={path:path.slice(0,stop+1), i:0, outcome, restC:path[stop][0], restA:path[stop][2]};
    ball.visible=true;
  }
  function stepFlight(){
    if(!flight){ flightClock=0; return; }
    const now=performance.now(); if(!flightClock) flightClock=now;
    const dt=Math.min(0.1,(now-flightClock)/1000); flightClock=now;
    flight.i += 78*dt;   // ~1.3 steps/frame at 60fps, but TIME-based so a low framerate no longer slows the ball down
    const idx=Math.min(Math.floor(flight.i), flight.path.length-1), p=flight.path[idx];
    ball.position.set(p[0], p[1], p[2]);
    if(idx>=flight.path.length-1){
      let o=flight.outcome; const ra=flight.restA, rc=flight.restC; flight=null;
      if(o!=='holed' && isOB(ra,rc)) o='ob';                        // finished beyond the course boundary
      if(o==='ob'){ strokes++;                                      // OUT OF BOUNDS: stroke-and-distance...
        lieA=(lastShot?lastShot.sa:ra); lieC=(lastShot?lastShot.sc:rc); }   // ...replay from where the shot was played
      else { lieA=ra; lieC=rc; if(o==='water') strokes++; }        // otherwise the new lie (water = penalty stroke)
      if(o==='holed' || ((o==='fairway'||o==='roll') && distToPinRaw()<=0.9)) holed=true;  // in the cup, or a tap-in
      syncToPlayer();                                              // record this player's lie and score
      // Holed: the ball is in the cup (hidden). Otherwise leave it at the lie (OB -> back at the previous spot).
      if(holed){ ball.visible=false; }
      else { ball.position.set(lieC, sampleH(lieA,lieC)+0.5, lieA); ball.visible=true; }
      resting=true; setTip(nplayers>1?TIP_NEXT:TIP_WALK);
      if(lastShot){ const total=Math.round(Math.hypot(rc-lastShot.sc, ra-lastShot.sa));
        setShotData(lastShot.club, lastShot.carry, total, lastShot.apex, Math.round(lastShot.carry*0.58)); }
      _sd('sdPin', holeDone?'—':(distToPin()+' y')); updateMapBall();
      document.getElementById('sub').textContent=statusLine();
      const pre=nplayers>1?('P'+(cur+1)+': '):'';
      toast(holed ? (pre+'holed in '+strokes+'!')
           : pre + (o==='ob'?'out of bounds — penalty (+1), replaying the shot' :
                    o==='trees'?'in the trees' : o==='water'?'in the water — penalty (+1)' :
                    o==='bunker'?'in a bunker' : o==='puddle'?'casual water — no penalty' :
                    (distToPin()+' yds to the pin')) + ' — tap to continue');
    }
  }
  const clAtA=(a)=>{ const cl=gCL; if(!cl) return [a,0];
    for(let k=0;k<cl.length-1;k++){ if(a<=cl[k+1][0]||k===cl.length-2){
      const t=(a-cl[k][0])/((cl[k+1][0]-cl[k][0])||1);
      return [cl[k][0]+(cl[k+1][0]-cl[k][0])*t, cl[k][1]+(cl[k+1][1]-cl[k][1])*t]; } }
    return cl[cl.length-1]; };
  // Fairway half-width along the hole, TRACED PER HOLE from that hole's own aerial
  // (gFW = [[along,leftHW,rightHW],...]). Asymmetric: the real fairway body is not
  // centred on the straight centreline, so left and right widths differ. `side` is
  // (across - centreline): negative = left, positive = right. Falls back to a gentle
  // generic corridor when a hole has no traced profile.
  const fwHalf=(a, side)=>{
    if(a < gFwStart) return 0.0;                     // hole-map: rough from the tee up to where the fairway starts
    if(gFwWidth!=null) return gFwWidth;              // hole-map recorded this fairway's width -> use it (symmetric)
    const P=gFW;
    if(!P||P.length===0){ const w=13.0+2.0*Math.sin(a*0.02); return w; }
    let l,r;
    if(a<=P[0][0]){ l=P[0][1]; r=P[0][2]; }
    else if(a>=P[P.length-1][0]){ l=P[P.length-1][1]; r=P[P.length-1][2]; }
    else { l=P[0][1]; r=P[0][2];
      for(let k=0;k<P.length-1;k++){ if(a<=P[k+1][0]){
        const t=(a-P[k][0])/((P[k+1][0]-P[k][0])||1);
        l=P[k][1]+(P[k+1][1]-P[k][1])*t; r=P[k][2]+(P[k+1][2]-P[k][2])*t; break; } } }
    return (side===undefined)?Math.max(l,r):(side<0?l:r); };
  const distToPinRaw=()=>{ const gp=clAtA(gGreenA); return Math.hypot(gp[1]-lieC, gp[0]-lieA); };
  const distToPin=()=>Math.round(distToPinRaw());
  // What the ball is sitting on: the green, a bunker, the mown fairway ribbon, or the
  // rough beyond it. This drives realistic play - rough and sand cost you distance and
  // roll, exactly as on a real course.
  const lieKind=(a,c)=>{ const gp=clAtA(gGreenA);
    if(Math.hypot(a-gp[0],c-gp[1])<=9.0) return 'green';
    if(inCircle(gBunkers,a,c)) return 'bunker';
    const cl=clAtA(a); return Math.abs(c-cl[1])>17.0 ? 'rough' : 'fairway'; };
  // Out of bounds is REAL and course-specific: only holes the actual course marks OB
  // have it (gOB = across-distance to the white stakes), never between holes, and most
  // holes (like this course's 1st) have NONE - trees and bunkers define them instead.
  const isOB=(a,c)=> (gOB!=null) && (Math.abs(c) > gOB);
  // On the putting green a ball is never in a hazard - a water/bunker circle that laps
  // onto the green must not penalise a ball that is actually on the green.
  const onGrn=(a,c)=>{ const gpp=clAtA(gGreenA); return Math.hypot(a-gpp[0],c-gpp[1])<10.0; };
  // --- high-end UI: shot-data panel + top-down hole map ---
  let lastShot=null;
  const clubFor=(y)=>{ if(y<7)return'Putter'; if(y<32)return'LW'; if(y<62)return'SW'; if(y<92)return'PW';
    if(y<112)return'9i'; if(y<132)return'8i'; if(y<150)return'7i'; if(y<166)return'6i'; if(y<183)return'5i';
    if(y<200)return'4i'; if(y<217)return'3i'; if(y<236)return'5W'; if(y<256)return'3W'; return'Driver'; };
  const _sd=(id,v)=>{ const e=document.getElementById(id); if(e) e.textContent=v; };
  function setShotData(club,carry,total,apex,speed){
    _sd('sdClub',club||'—'); _sd('sdCarry',carry!=null?carry+' y':'—');
    _sd('sdTotal',total!=null?total+' y':'—'); _sd('sdApex',apex!=null?apex+' y':'—');
    _sd('sdSpeed',speed!=null?speed+' mph':'—'); }
  function addressData(){ if(holed){ setShotData('—',null,null,null,null); } else setShotData(clubFor(distToPin()),null,null,null,null);
    _sd('sdPin', holeDone?'—':(distToPin()+' y')); }
  // draw the hole top-down: rough corridor, fairway ribbon, water, bunkers, green, pin, tee, ball, aim line
  const MVW=100, MVH=300, MPX=9, MPY=12;
  const mX=(c)=>MVW/2 + (c/(gW||60))*(MVW/2-MPX);
  const mY=(a)=>MVH-MPY - (a/((gA1)||400))*(MVH-2*MPY);
  const mR=(r)=>Math.max(1.4, (r/(gW||60))*(MVW/2-MPX));
  function renderMap(){
    const svg=document.getElementById('mapsvg'); if(!svg) return;
    const cl=gCL||[[0,0],[gA1,0]]; let s='';
    s+='<rect x="'+MPX+'" y="'+MPY+'" width="'+(MVW-2*MPX)+'" height="'+(MVH-2*MPY)+'" rx="7" fill="#26401f"/>';
    const fw=Math.min(22, (gW||60)*0.5);
    const left=cl.map(p=>mX(p[1]-fw)+','+mY(p[0])), right=cl.slice().reverse().map(p=>mX(p[1]+fw)+','+mY(p[0]));
    s+='<polygon points="'+left.concat(right).join(' ')+'" fill="#4c7a3c"/>';
    (gWater||[]).forEach(w=>{ s+='<circle cx="'+mX(w[1]).toFixed(1)+'" cy="'+mY(w[0]).toFixed(1)+'" r="'+mR(w[2]).toFixed(1)+'" fill="#2e6f9e"/>'; });
    (gBunkers||[]).forEach(b=>{ s+='<circle cx="'+mX(b[1]).toFixed(1)+'" cy="'+mY(b[0]).toFixed(1)+'" r="'+mR(b[2]).toFixed(1)+'" fill="#ddca8f"/>'; });
    const gp=clAtA(gGreenA);
    s+='<circle cx="'+mX(gp[1]).toFixed(1)+'" cy="'+mY(gp[0]).toFixed(1)+'" r="8" fill="#79c765"/>';
    for(let a=0;a<=(gA1||0);a+=50){ const y=mY(a).toFixed(1); s+='<line x1="'+(MPX-3)+'" y1="'+y+'" x2="'+MPX+'" y2="'+y+'" stroke="#fff" opacity="0.35" stroke-width="1"/>'; }
    s+='<rect x="'+(mX(0)-2).toFixed(1)+'" y="'+(mY(0)-2).toFixed(1)+'" width="4" height="4" rx="1" fill="#e8e8e8"/>';
    s+='<g id="mapdyn"></g>';
    svg.innerHTML=s;
    const mh=document.getElementById('mapHole'); if(mh) mh.textContent=(gSub? gSub.split('·')[0].trim() : '');
    updateMapBall();
  }
  function updateMapBall(){
    const g=document.getElementById('mapdyn'); if(!g) return;
    const gp=clAtA(gGreenA), bx=mX(lieC).toFixed(1), by=mY(lieA).toFixed(1);
    let s='<circle cx="'+mX(gp[1]).toFixed(1)+'" cy="'+mY(gp[0]).toFixed(1)+'" r="1.6" fill="#e0483f"/>';
    if(!holed){ s='<line x1="'+bx+'" y1="'+by+'" x2="'+mX(gp[1]).toFixed(1)+'" y2="'+mY(gp[0]).toFixed(1)+'" stroke="#fff" stroke-width="0.8" stroke-dasharray="2.5 2.5" opacity="0.7"/>'+s
      +'<circle cx="'+bx+'" cy="'+by+'" r="2.4" fill="#fff" stroke="#111" stroke-width="0.5"/>'; }
    g.innerHTML=s;
  }
  const distForPin=(a,c)=>{ const gp=clAtA(gGreenA); return Math.hypot(gp[1]-c, gp[0]-a); };
  const statusLine=()=> holeDone ? scoreSummary()
      : (nplayers>1?('Player '+(cur+1)+'  ·  '):'')
        + (holed ? gSub+'  ·  holed in '+strokes
                 : gSub+'  ·  '+distToPin()+' to the pin'+(strokes>0?'  ·  shot '+(strokes+1):''));
  function scoreSummary(){ return players.map((p,i)=>'P'+(i+1)+' '+p.strokes).join('   '); }
  // A ball marker per player so you see the whole group out on the hole. The player
  // who is up has none (they are at address, hitting a real ball).
  let markers=[];
  function buildMarkers(){
    markers.forEach(m=>group.remove(m)); markers=[];
    for(let i=0;i<nplayers;i++){ const m=new THREE.Mesh(new THREE.SphereGeometry(0.5,24,24), makeBallMat(BALLCOL[i%4]));
      m.visible=false; group.add(m); markers.push(m); }
  }
  function refreshMarkers(){
    for(let i=0;i<markers.length;i++){ const p=players[i], m=markers[i];
      const out = p && p.strokes>0 && !p.holed && i!==cur;   // on the course, not this player's turn
      m.visible=out; if(out) m.position.set(p.c, sampleH(p.a,p.c)+0.5, p.a); }
  }
  function syncFromPlayer(){ const p=players[cur]; lieA=p.a; lieC=p.c; strokes=p.strokes; holed=p.holed; }
  function syncToPlayer(){ const p=players[cur]; p.a=lieA; p.c=lieC; p.strokes=strokes; p.holed=holed; }
  function resetPlayers(){ players=[]; for(let i=0;i<nplayers;i++) players.push({a:0,c:0,strokes:0,holed:false}); cur=0; syncFromPlayer(); }
  function nextToPlay(){                 // furthest from the pin among the unfinished plays next
    let best=-1;
    for(let i=0;i<players.length;i++){ if(players[i].holed) continue;
      if(best<0){ best=i; continue; }
      const di=distForPin(players[i].a,players[i].c), db=distForPin(players[best].a,players[best].c);
      if(di>db+0.01 || (Math.abs(di-db)<=0.01 && players[i].strokes<players[best].strokes)) best=i; }
    return best;
  }
  function proceed(){                    // done watching the last shot; hand off to whoever plays next
    resting=false; ball.visible=false; setTip(TIP_HIT);
    const nx=nextToPlay();
    if(nx<0){ holeDone=true; place(); toast('Hole complete   '+scoreSummary()); return; }
    cur=nx; syncFromPlayer(); place(nplayers>1);          // glide up to the next player
    lastShot=null; addressData(); updateMapBall();
    if(nplayers>1) toast('Player '+(cur+1)+' to play  ·  '+distToPin()+' to the pin');
  }
  // Where the camera stands for the current lie and view (down-the-line, or elevated).
  function camPose(){
    const ap=clAtA(Math.min(lieA+230, gGreenA-6)), lieY=sampleH(lieA,lieC);
    let dx=ap[1]-lieC, dz=ap[0]-lieA; const dl=Math.hypot(dx,dz)||1; dx/=dl; dz/=dl;
    return VIEW==='play'
      ? {fov:62, pos:[lieC-dx*5, lieY+1.75, lieA-dz*5], tar:[lieC+dx*45, lieY+1.4, lieA+dz*45]}
      : {fov:60, pos:[lieC-dx*24, lieY+16, lieA-dz*24], tar:[lieC+dx*70, lieY+2, lieA+dz*70]};
  }
  let camTween=null;
  function place(glide){                       // snap, or glide (walking up to the next player)
    const P=camPose(); refreshMarkers();
    document.getElementById('sub').textContent=statusLine();
    if(glide){ camTween={fromP:cam.position.clone(), fromT:ctl.target.clone(),
        toP:new THREE.Vector3(P.pos[0],P.pos[1],P.pos[2]), toT:new THREE.Vector3(P.tar[0],P.tar[1],P.tar[2]),
        fov0:cam.fov, fov1:P.fov, t:0, dur:40}; }
    else { camTween=null; cam.fov=P.fov; cam.position.set(P.pos[0],P.pos[1],P.pos[2]);
      ctl.target.set(P.tar[0],P.tar[1],P.tar[2]); cam.updateProjectionMatrix(); ctl.update(); }
  }
  const _tv=new THREE.Vector3();
  function stepCam(){                          // advance an in-progress camera glide
    if(!camTween) return;
    camTween.t++; const u=Math.min(1,camTween.t/camTween.dur), e=u<0.5?2*u*u:1-Math.pow(-2*u+2,2)/2;
    cam.position.lerpVectors(camTween.fromP,camTween.toP,e);
    _tv.lerpVectors(camTween.fromT,camTween.toT,e); ctl.target.copy(_tv); cam.lookAt(_tv);
    cam.fov=camTween.fov0+(camTween.fov1-camTween.fov0)*e; cam.updateProjectionMatrix();
    if(u>=1){ camTween=null; ctl.update(); }
  }

  function build(k){
    at = (k % HOLES.length + HOLES.length) % HOLES.length;
    const D = HOLES[at];
    while(group.children.length) group.remove(group.children[0]);
    const na=D.grid.na, nx=D.grid.nx;
    const a0=D.extent.along0, a1=D.extent.along1, W=D.extent.across;
    const H=D.heights.map(r=>r.slice());         // a copy, so re-visiting a hole doesn't re-carve it
    gNA=na; gNX=nx; gA0=a0; gA1=a1; gW=W; gH=H;
    gCL=D.centreline||[[0,0],[a1,0]]; gGreenA=gCL[gCL.length-1][0]; gSub=D.sub; gFW=D.fw||null;
    gFwStart=(D.fwStart!=null?D.fwStart:14.0); gFwWidth=(D.fwWidth!=null?D.fwWidth:null);   // hole-map: fairway starts past the card tee; map width if recorded
    gTreeClumps=D.trees||[]; gWater=D.water||[]; gBunkers=D.bunkers||[]; holeBearing=D.bearing||0;
    gOB=(typeof D.ob==='number')?D.ob:null;    // OB only on holes the real course marks it; absent -> no out of bounds here
    gOrthoPix=null; lastGrassX=1e9; rockSpecs=null; rockGroups=null;   // hide 3D grass/rocks until this hole's aerial pixels load
    flight=null; ball.visible=false; resting=false; holeDone=false; setTip(TIP_HIT); resetPlayers(); buildMarkers();   // new hole: everyone on the tee
    // Bunkers: press a shallow sandy bowl into the terrain so they read as real pits
    // (the aerial already carries the sand colour), and a ball settles into them.
    // The green: a protected putting surface around the pin - it is grass, never sand,
    // so the hole is never left sitting in a bunker.
    const pin=clAtA(gGreenA), GREENR=8.5;
    // The pin is never in a hazard: clip any water or bunker that reaches the putting
    // surface so a ball on the green is always a clean lie, never a penalty. A hazard
    // sitting on the green is dropped; one that merely licks it is pulled back clear.
    const PINCLR=GREENR+2.0;
    const clipHaz=(list)=>(list||[]).map(function(h){ const dd=Math.hypot(h[0]-pin[0],h[1]-pin[1]);
      if(dd>=h[2]+PINCLR) return h; const nr=dd-PINCLR; return nr>=2.0 ? [h[0],h[1],nr] : null; }).filter(Boolean);
    gWater=clipHaz(gWater); gBunkers=clipHaz(gBunkers);
    pinC=pin[1]; pinA=pin[0];                        // world pin, so a shot can always be aimed at it
    const greenF=new Float32Array((na+1)*(nx+1));
    for(let i=0;i<=na;i++){ const al=a0+(a1-a0)*i/na;
      for(let j=0;j<=nx;j++){ const ac=-W+2*W*j/nx, dd=Math.hypot(al-pin[0],ac-pin[1]);
        greenF[i*(nx+1)+j]=1.0-Math.min(1.0,Math.max(0.0,(dd-GREENR)/3.0)); } }
    const bunkF=new Float32Array((na+1)*(nx+1));   // per-vertex bunker mask, so sand is painted exactly on the pits
    gBunkers.forEach(function(b){ const R=b[2], D0=Math.min(0.30+R*0.02,0.6)/EXAG;
      const i0=Math.max(0,Math.floor((b[0]-R-a0)/(a1-a0)*na)), i1=Math.min(na,Math.ceil((b[0]+R-a0)/(a1-a0)*na));
      const j0=Math.max(0,Math.floor((b[1]-R+W)/(2*W)*nx)), j1=Math.min(nx,Math.ceil((b[1]+R+W)/(2*W)*nx));
      for(let i=i0;i<=i1;i++){ const al=a0+(a1-a0)*i/na;
        for(let j=j0;j<=j1;j++){ const ac=-W+2*W*j/nx, dd=Math.hypot(al-b[0],ac-b[1]);
          if(dd<R && Math.hypot(al-pin[0],ac-pin[1])>GREENR){ const t=dd/R; H[i][j]-=D0*(1-t*t);   // never carve/sand the green
            const m=1.0-Math.max(0,(t-0.72)/0.28); bunkF[i*(nx+1)+j]=Math.max(bunkF[i*(nx+1)+j], m); } } } });   // feathered edge
    const g=new THREE.BufferGeometry(), pos=[], uv=[], idx=[];
    for(let i=0;i<=na;i++){ const al=a0+(a1-a0)*i/na;
      for(let j=0;j<=nx;j++){ pos.push(-W+2*W*j/nx, H[i][j]*EXAG, al); uv.push(j/nx, i/na);} }
    for(let i=0;i<na;i++)for(let j=0;j<nx;j++){ const a=i*(nx+1)+j,b=a+1,c=a+(nx+1),d=c+1; idx.push(a,c,b,b,c,d);}
    // per-vertex FAIRWAY mask from the real hole centreline - the SAME corridor the
    // grass blades use, so the ground's fairway/rough matches the blades exactly (no
    // pale mismatch where dark rough blades sit on a light fairway-painted ground)
    const fairF=new Float32Array((na+1)*(nx+1));
    const t0g=clAtA(0.0)[1];
    for(let i=0;i<=na;i++){ const al=a0+(a1-a0)*i/na, clc=clAtA(al)[1];
      for(let j=0;j<=nx;j++){ const ac=-W+2*W*j/nx, dcl=Math.abs(ac-clc);
        let f = al>14.0 ? (1.0-Math.min(1.0,Math.max(0.0,(dcl-fwHalf(al, ac-clc))/1.8))) : 0.0;   // real per-hole fairway body + start (fwHalf=0 where the aerial shows rough, incl. the tee apron)
        const teeF=Math.min(Math.min(1.0,Math.max(0.0,(al+9.5)/1.5)), Math.min(1.0,Math.max(0.0,(6.5-al)/1.5)),
                            Math.min(1.0,Math.max(0.0,(7.5-Math.abs(ac-t0g))/1.5)));   // the tee box: mown, striped turf with a soft edge
        fairF[i*(nx+1)+j]=Math.max(f, teeF); } }
    g.setAttribute('position',new THREE.Float32BufferAttribute(pos,3));
    g.setAttribute('uv',new THREE.Float32BufferAttribute(uv,2));
    g.setAttribute('bunker',new THREE.Float32BufferAttribute(bunkF,1));
    g.setAttribute('grn',new THREE.Float32BufferAttribute(greenF,1));
    g.setAttribute('fair',new THREE.Float32BufferAttribute(fairF,1));
    g.setIndex(idx); g.computeVertexNormals();
    const tex=loader.load(D.ortho);
    tex.encoding=THREE.sRGBEncoding;
    tex.generateMipmaps=false;               // keep full res: mipmaps force NPOT downscale to 512
    tex.minFilter=THREE.LinearFilter; tex.magFilter=THREE.LinearFilter;
    tex.wrapS=tex.wrapT=THREE.ClampToEdgeWrapping;
    tex.anisotropy=R.capabilities.getMaxAnisotropy();
    const groundMat = new THREE.MeshStandardMaterial({map:tex, roughness:0.97});
    const mottleTex = makeMottle(W, a0, a1, Math.round((a1-a0)*13 + W*7));   // shared with the blades (mottleAt)
    const grassRep = new THREE.Vector2((2*W)/1.6, (a1-a0)/1.6);   // ~1.6yd per grass tile
    const sandRep = new THREE.Vector2((2*W)/2.6, (a1-a0)/2.6);     // ~2.6yd tile: real sand photo at natural scale
    groundMat.onBeforeCompile = (sh)=>{
      sh.uniforms.grassCol={value:grassTex}; sh.uniforms.sand={value:sandTex}; sh.uniforms.grassDet={value:grassDetailTex};
      sh.uniforms.grassSample={value:grassSampleTex}; sh.uniforms.mottle={value:mottleTex};
      sh.uniforms.grassRep={value:grassRep}; sh.uniforms.sandRep={value:sandRep};
      sh.uniforms.HW={value:W};
      sh.uniforms.FAIRWAY={value:new THREE.Color(PALETTE.fairway[0],PALETTE.fairway[1],PALETTE.fairway[2])};
      sh.uniforms.ROUGH={value:new THREE.Color(PALETTE.rough[0],PALETTE.rough[1],PALETTE.rough[2])};
      sh.uniforms.GFAIR={value:new THREE.Color(GFAIR[0],GFAIR[1],GFAIR[2])};
      sh.uniforms.GROUGH={value:new THREE.Color(GROUGH[0],GROUGH[1],GROUGH[2])};
      sh.vertexShader = 'attribute float bunker;\nattribute float grn;\nattribute float fair;\nvarying float vBunk;\nvarying float vGreen;\nvarying float vFair;\n'
        + sh.vertexShader.replace('#include <begin_vertex>','#include <begin_vertex>\n vBunk=bunker; vGreen=grn; vFair=fair;');
      sh.fragmentShader = 'uniform sampler2D grassCol;\nuniform sampler2D sand;\nuniform sampler2D grassDet;\nuniform sampler2D grassSample;\nuniform sampler2D mottle;\nuniform vec2 grassRep;\nuniform vec2 sandRep;\nuniform float HW;\nuniform vec3 FAIRWAY;\nuniform vec3 ROUGH;\nuniform vec3 GFAIR;\nuniform vec3 GROUGH;\nvarying float vBunk;\nvarying float vGreen;\nvarying float vFair;\n'
        + sh.fragmentShader.replace('#include <map_fragment>',
        '#include <map_fragment>\n{ float o=0.0016;\n'
        + '  vec3 bl=(texture2D(map,vUv+vec2(o,0.0)).rgb+texture2D(map,vUv-vec2(o,0.0)).rgb+texture2D(map,vUv+vec2(0.0,o)).rgb+texture2D(map,vUv-vec2(0.0,o)).rgb)*0.25;\n'
        + '  diffuseColor.rgb += (diffuseColor.rgb-bl)*0.85;\n'                  // unsharp: counter the soft aerial
        + '  float lum0=dot(diffuseColor.rgb,vec3(0.333));\n'
        + '  float grn0=diffuseColor.g-max(diffuseColor.r,diffuseColor.b);\n'
        + '  float gW0=smoothstep(-0.03,0.03,grn0);\n'
        + '  float shad=smoothstep(0.32,0.10,lum0);\n'
        + '  diffuseColor.rgb=mix(diffuseColor.rgb, diffuseColor.rgb*vec3(1.6,1.62,1.4)+vec3(0.05,0.06,0.03), shad*gW0*0.8);\n'  // lift baked tree-shadow smears on grass
        + '  diffuseColor.rgb=(diffuseColor.rgb-0.5)*1.10+0.5;\n'               // punchier contrast
        + '  float lg=dot(diffuseColor.rgb,vec3(0.333)); diffuseColor.rgb=mix(vec3(lg),diffuseColor.rgb,1.16);\n'  // richer colour
        + '  float fade=1.0-clamp((length(vViewPosition)-30.0)/150.0,0.0,1.0);\n'
        + '  float lum=dot(diffuseColor.rgb, vec3(0.333));\n'
        + '  float blueW=smoothstep(0.015,0.10, diffuseColor.b-max(diffuseColor.r,diffuseColor.g));\n'   // water in the aerial
        + '  float warm=(diffuseColor.r+diffuseColor.g)*0.5-diffuseColor.b;\n'
        + '  float sandW=smoothstep(0.42,0.56,lum)*smoothstep(0.02,0.09,warm)*(1.0-blueW);\n'          // bright & warm -> sand
        + '  float grassW=1.0;\n'                                            // ALL turf; sand is painted only on tagged bunkers (below), never guessed from aerial brightness (which was paling the bright near fairway)
        + '  vec3 sandPhoto=texture2D(sand, vUv*sandRep).rgb;\n'                     // real bunker-sand photo (two scales)
        + '  vec3 sandCoarse=texture2D(sand, vUv*sandRep*0.32).rgb;\n'
        + '  vec3 sandReal=mix(sandPhoto, sandPhoto*sandCoarse*2.2, 0.4)*1.08;\n'     // blend scales, slight lift
        // Grass = a real BLADE texture (vertical strokes) tinted to the course green,
        // NOT flat paint. Two shades: lighter fairway, darker rough. The aerial only
        // decides where the fairway is (snapped, so tree-shadow blotches don't bleed
        // through as grey bands).
        + '  vec3 fairway=FAIRWAY, rough=ROUGH;\n'
        + '  vec3 base=mix(rough, fairway, vFair);\n'                           // fairway/rough from the REAL centreline corridor (matches the grass blades exactly - no pale mismatch)
        + '  float gb1=dot(texture2D(grassDet, vUv*grassRep).rgb, vec3(0.333));\n'          // blade texture, main scale
        + '  float gb2=dot(texture2D(grassDet, vUv*grassRep*0.42).rgb, vec3(0.333));\n'     // coarser clumps
        + '  float gb3=dot(texture2D(grassDet, vUv*grassRep*2.3).rgb, vec3(0.333));\n'      // fine near-camera blades
        + '  float blade=clamp(gb1*0.5+gb2*0.28+gb3*0.22, 0.0, 1.0);\n'
        + '  vec3 gd=vec3(0.55+0.7*blade);\n'                                   // blade grain (also used by the putting-green tint below)
        + '  float mt=texture2D(mottle, vUv).r;\n'                                                      // natural turf patchiness - SAME texture + formula as the blades (turfCol)
        + '  vec3 roughM=GROUGH*(0.80+0.34*mt)*vec3(1.0+0.10*mt, 1.0+0.05*mt, 1.0-0.16*mt);\n'
        + '  vec3 fairM=GFAIR*(0.95+0.10*mt);\n'
        + '  vec3 clean=mix(roughM, fairM, vFair);\n'   // turf colour = EXACT blade palette (per-course GFAIR/GROUGH) so where the 3D blades distance-fade there is NO colour seam - ground matches blades (green parkland, golden-tan links)
        + '  float across=(vUv.x*2.0-1.0)*HW;\n'
        + '  float mb=sin(across*0.85); mb=sign(mb)*pow(abs(mb),0.40);\n'       // crisp mow bands
        + '  clean*=1.0+0.09*mb*vFair;\n'                                       // gentle mowing stripes on the FAIRWAY only
        + '  float faird=1.0-clamp((abs(across)-70.0)/46.0,0.0,1.0);\n'          // WIDE clean-turf field over the whole hole ground (out to ~116yd across), not just a narrow ribbon
        + '  float turfW=grassW*(0.88+0.12*faird)*(1.0-0.85*blueW);\n'           // solid turf off-line too so raw pale satellite (beach/wet sand/bare dune) never shows; keep real aerial water as water
        + '  diffuseColor.rgb=mix(diffuseColor.rgb, clean, turfW);\n'            // solid turf on the hole ground'
        + '  diffuseColor.rgb=mix(diffuseColor.rgb, sandReal, clamp(vBunk,0.0,1.0)*(1.0-vGreen));\n'   // real sand photo ONLY on the tagged pits (never guessed from bright aerial, which was paling the near fairway)
        + '  vec3 puttG=vec3(0.36,0.54,0.20)*mix(vec3(1.0),gd,0.45);\n'             // bentgrass green: lighter, cooler and SMOOTHER than the fairway so it reads as a distinct surface
        + '  puttG*=1.0+0.025*sin(across*2.4);\n'                                    // fine green grain, not the big fairway mow bands
        + '  float gcore=smoothstep(0.45,0.95,vGreen);\n'                            // the putting surface
        + '  float gcollar=smoothstep(0.05,0.45,vGreen)*(1.0-gcore);\n'             // the fringe/collar ring around it
        + '  diffuseColor.rgb=mix(diffuseColor.rgb, ROUGH*0.82, gcollar*0.6);\n'     // darker collar band
        + '  diffuseColor.rgb=mix(diffuseColor.rgb, puttG, gcore);\n'               // distinct putting green
        + '  float nearCam=1.0-smoothstep(18.0,45.0,length(vViewPosition));\n'                 // up close, the gaps between blades show the shadowed lower turf, not a bright lit floor
        + '  diffuseColor.rgb*=mix(1.0,0.56,nearCam*(1.0-gcore)*(1.0-clamp(vBunk,0.0,1.0)));\n' // (no pale specks between blades; distant colour balance unchanged; green + sand untouched)
        + '  diffuseColor.rgb=clamp(diffuseColor.rgb,0.0,1.0); }');
    };
    groundMesh = new THREE.Mesh(g, groundMat); groundMesh.receiveShadow=true;
    group.add(groundMesh);
    // Aim the sun's shadow frustum at THIS hole so tree/rock shadows fall across it.
    { const mid=(gA0+gA1)/2, half=Math.max(gW+40,(gA1-gA0)/2+60);
      sun.target.position.set(0,0,mid); sun.target.updateMatrixWorld();
      sun.position.set(-300, 460, mid+130);                              // consistent upper-left sun
      const sc=sun.shadow.camera; sc.left=-half; sc.right=half; sc.top=half; sc.bottom=-half;
      sc.near=20; sc.far=half*4+700; sc.updateProjectionMatrix(); }
    // Coastal rocks are placed once the aerial pixels load (see the ortho onload
    // below), because the shoreline shows in the imagery, not in the flat-water DEM.
    // The real land around the hole: a wide, coarse mesh from real imagery + elevation,
    // so the background is the actual coast/hills - the true landscape, not a fake ridge.
    if(D.surround){ const SU=D.surround, sg=SU.grid, sp=SU.span, SH=SU.heights;
      const sgeo=new THREE.BufferGeometry(), spos=[], suv=[], sidx=[];
      for(let i=0;i<=sg;i++) for(let j=0;j<=sg;j++){ spos.push(-sp+2*sp*j/sg, SH[i][j]*EXAG, -sp+2*sp*i/sg); suv.push(j/sg, i/sg); }
      for(let i=0;i<sg;i++) for(let j=0;j<sg;j++){ const a=i*(sg+1)+j,b=a+1,c=a+(sg+1),d=c+1; sidx.push(a,c,b,b,c,d); }
      sgeo.setAttribute('position',new THREE.Float32BufferAttribute(spos,3));
      sgeo.setAttribute('uv',new THREE.Float32BufferAttribute(suv,2));
      sgeo.setIndex(sidx); sgeo.computeVertexNormals();
      const stex=loader.load(SU.ortho); stex.encoding=THREE.sRGBEncoding;
      stex.generateMipmaps=false; stex.minFilter=THREE.LinearFilter; stex.magFilter=THREE.LinearFilter;
      stex.wrapS=stex.wrapT=THREE.ClampToEdgeWrapping; stex.anisotropy=R.capabilities.getMaxAnisotropy();
      const smat=new THREE.MeshStandardMaterial({map:stex, roughness:1.0, color:new THREE.Color(0.14,0.20,0.10),
        polygonOffset:true, polygonOffsetFactor:2.0, polygonOffsetUnits:2.0});   // darkened toward deep grass-green so the near-foreground surround reads as dark turf (not pale) under the blades
      const smesh=new THREE.Mesh(sgeo, smat); smesh.renderOrder=-2; group.add(smesh);
    }
    // read the aerial's pixels (for colouring the 3D grass blades under the camera)
    { const oimg=new Image(); oimg.onload=function(){ try{
        const oc=document.createElement('canvas'); oc.width=oimg.width; oc.height=oimg.height;
        const octx=oc.getContext('2d'); octx.drawImage(oimg,0,0);
        gOrthoPix=octx.getImageData(0,0,oimg.width,oimg.height).data; gOrthoW=oimg.width; gOrthoH=oimg.height;
        placeGrass(cam.position.x, cam.position.z); placeRocks();
      }catch(e){} }; oimg.src=D.ortho; }
    const gp=clAtA(gGreenA), gyy=sampleH(gp[0],gp[1]);
    const pole=new THREE.Mesh(new THREE.CylinderGeometry(.07,.09,4.0,8), new THREE.MeshStandardMaterial({color:0xf2f2ee,roughness:0.6}));
    pole.position.set(gp[1], gyy+2.0, gp[0]); group.add(pole);
    // A cloth pennant: a subdivided plane rippled per-frame in flutterFlag() so it
    // waves like real fabric (attached at the pole top, streaming downwind).
    const flagGeo=new THREE.PlaneGeometry(2.3,1.0,16,4); flagGeo.translate(1.15,0,0);   // left edge at the pole
    flagMesh=new THREE.Mesh(flagGeo, new THREE.MeshStandardMaterial({color:0xd8362f,side:THREE.DoubleSide,roughness:0.9}));
    flagMesh.position.set(gp[1], gyy+3.5, gp[0]); group.add(flagMesh);
    flagBase=flagGeo.attributes.position.array.slice();
    // The cup, and a faint target ring so the pin reads from down the fairway.
    const cup=new THREE.Mesh(new THREE.CircleGeometry(0.34,16), new THREE.MeshBasicMaterial({color:0x0d0d08}));
    cup.rotation.x=-Math.PI/2; cup.position.set(gp[1], gyy+0.05, gp[0]); cup.renderOrder=2; group.add(cup);
    const ring=new THREE.Mesh(new THREE.RingGeometry(5.4,6.0,44),
      new THREE.MeshBasicMaterial({color:0xffffff, transparent:true, opacity:0.28, side:THREE.DoubleSide, depthWrite:false}));
    ring.rotation.x=-Math.PI/2; ring.position.set(gp[1], gyy+0.1, gp[0]); ring.renderOrder=2; group.add(ring);
    // A neighbouring hole across the way: a green + flagstick, so this hole sits inside a
    // real course you can see around you (a yellow flag marks it as a different hole).
    { const nAl=gA0+(gA1-gA0)*0.52, nAc=118.0;
      if(nAc<gW-10){ const nyy=sampleH(nAl,nAc);
        const nd=new THREE.Mesh(new THREE.CircleGeometry(8.5,28), new THREE.MeshStandardMaterial({color:0x5aa03a,roughness:0.85}));
        nd.rotation.x=-Math.PI/2; nd.position.set(nAc,nyy+0.08,nAl); nd.renderOrder=2; group.add(nd);
        const np=new THREE.Mesh(new THREE.CylinderGeometry(.06,.08,3.6,8), new THREE.MeshStandardMaterial({color:0xf2f2ee,roughness:0.6}));
        np.position.set(nAc,nyy+1.8,nAl); group.add(np);
        const nfg=new THREE.PlaneGeometry(2.0,0.9); nfg.translate(1.0,0,0);
        const nf=new THREE.Mesh(nfg, new THREE.MeshStandardMaterial({color:0xf5c518,side:THREE.DoubleSide,roughness:0.9}));
        nf.position.set(nAc,nyy+3.15,nAl); group.add(nf); }
    }
    // White out-of-bounds stakes - ONLY on holes the real course marks OB (never between holes).
    if(gOB!=null){ const obx=gOB, sm=new THREE.MeshStandardMaterial({color:0xf4f4ef,roughness:0.7});
      for(let al=gA0+10; al<=gA1-10; al+=24){ for(let s=-1;s<=1;s+=2){ const xx=s*obx;
        if(Math.abs(xx)>gW-1) continue;
        const st=new THREE.Mesh(new THREE.CylinderGeometry(0.09,0.09,1.5,6), sm);
        st.position.set(xx, sampleH(al,xx)+0.75, al); group.add(st); } } }

    // Trees: many camera-facing billboards packed through each real clump so it
    // reads as a full leafy mass. Positions/scales are stored here; faceTrees()
    // turns them to the camera every frame (see the render loop).
    const T=D.trees||[]; treeSpecs=[];
    const wsum=TREE_PROFILE.reduce((a,b)=>a+b,0)||1;
    const pickKind=(r)=>{ let x=r*wsum; for(let kk=0;kk<TREE_PROFILE.length;kk++){ x-=TREE_PROFILE[kk]; if(x<=0) return kk; } return 0; };
    for(let c=0;c<T.length;c++){ const al=T[c][0], ac=T[c][1], rad=T[c][2];
      const nT=Math.max(1,Math.min(7,Math.round(rad/1.7)));      // photo trees read full: fewer per clump
      for(let k=0;k<nT;k++){
        const th=c*2.4+k*1.37, rr=rad*Math.sqrt(frac(c*0.61+k*0.37));
        const al2=al+Math.sin(th)*rr, ac2=ac+Math.cos(th)*rr;
        if(inCircle(gBunkers,al2,ac2)) continue;                       // never a tree standing in a bunker
        const kind=pickKind(frac(c*0.317+k*0.613+ac2*0.019));
        const sc=(0.82+0.34*frac(c*1.3+k*2.1))*TREE_KINDS[kind].tall;   // height in yards
        treeSpecs.push([ac2, sampleH(al2,ac2), al2, sc, kind, 0, 0]);   // [5]=unused, [6]=canopy local index
      }
    }
    // Cart path: a winding paved ribbon just off the fairway, draped on the ground.
    // The single strongest "this is a real course, not a void" cue beside the hole.
    let cartPts=[];
    (function(){
      const side=(frac(gA1*0.13)<0.5?1:-1), base=30.0, hw=2.4, eps=0.22;   // out at the tree-line edge, clear of the fairway and first cut - not in the line of play
      const gp=clAtA(gGreenA), greenClear=8.5+hw+4.0;
      const pts=cartPts;
      for(let al=gA0+4; al<=gGreenA-4; al+=5){                        // stop short of the green - never pave over it
        const cx=clAtA(al)[1], meander=3.2*Math.sin(al*0.028)+1.6*Math.sin(al*0.097);
        let ac=cx+side*(base+meander);
        const dg=gGreenA-al; if(dg<30) ac=cx+side*(base+(30-dg)*0.5);   // ease in around the green side as it nears
        for(let bi=0;bi<gBunkers.length;bi++){ const b=gBunkers[bi];    // shove clear of any bunker in the way
          const dd=Math.hypot(al-b[0],ac-b[1]), need=b[2]+hw+3.0;
          if(dd<need) ac += (ac>=b[1]?1:-1)*(need-dd); }
        if(Math.hypot(al-gp[0],ac-gp[1])<greenClear) ac=gp[1]+side*greenClear;   // never sit on the green
        pts.push([al, ac]); }
      if(pts.length>2){
        const vpos=[], vidx=[];
        for(let i=0;i<pts.length;i++){ const al=pts[i][0], ac=pts[i][1];
          vpos.push(ac-hw, sampleH(al,ac-hw)+eps, al,  ac+hw, sampleH(al,ac+hw)+eps, al); }
        for(let i=0;i<pts.length-1;i++){ const a=i*2; vidx.push(a,a+1,a+2, a+1,a+3,a+2); }
        const pg=new THREE.BufferGeometry();
        pg.setAttribute('position', new THREE.Float32BufferAttribute(vpos,3));
        pg.setIndex(vidx); pg.computeVertexNormals();
        const pmat=new THREE.MeshStandardMaterial({color:0x86837b, roughness:0.97, metalness:0.0, side:THREE.DoubleSide,
          polygonOffset:true, polygonOffsetFactor:-3.0, polygonOffsetUnits:-3.0});   // asphalt; double-sided so the ribbon shows from above
        const pmesh=new THREE.Mesh(pg, pmat); pmesh.renderOrder=2; group.add(pmesh);
      }
    })();
    // (Tee box is now real mown turf - painted in the ground shader via fairF and covered
    // in short blades - instead of the old flat, untextured near-black slab that filled
    // the bottom of every tee shot.)
    // Forest backdrop: for parkland/coastal courses, line the corridor edges and far
    // end with a dense band of trees, so the hole is ringed by woods (the natural
    // backdrop at Pebble, Augusta, etc.) - not hills or mountains.
    if(BACKDROP.type==='forest'){
      // On coastal holes the sea, not woods, is the edge: don't line the ocean with
      // trees. Find the hole's low point; where there's an ocean, skip any tree that
      // would stand on the beach or over the water (Pebble's 7th is an open sea point).
      let _mnH=1e9; for(let i=0;i<=na;i++)for(let j=0;j<=nx;j++){ if(H[i][j]<_mnH)_mnH=H[i][j]; }
      const hasSea=_mnH<-3, seaH=_mnH+4.5;
      const addTree=(al2,ac2,tall)=>{ if(al2<gA0+2||al2>gA1-2||ac2<-gW+2||ac2>gW-2) return;
        if(hasSea && sampleH(al2,ac2)<seaH) return;                      // no trees on the beach / over the sea
        if(inCircle(gBunkers,al2,ac2)) return;                           // no trees standing in a bunker
        for(let pi=0;pi<cartPts.length;pi++){ if(Math.hypot(al2-cartPts[pi][0],ac2-cartPts[pi][1])<5.0) return; }   // no trees on the cart path
        const kind=pickKind(frac(al2*0.031+ac2*0.047));
        const sc=(1.15+0.6*frac(al2*0.13+ac2*0.21))*TREE_KINDS[kind].tall;   // backdrop trees stand a touch taller
        treeSpecs.push([ac2, sampleH(al2,ac2), al2, sc, kind, 0, 0]); };
      const along0=gA0+6, along1=gA1-6, rows=6;
      for(let al2=along0; al2<=along1; al2+=4.0){                        // both flanks: a deep band that HUGS the fairway, not a distant wall
        const cx=clAtA(al2)[1];                                          // follow the hole's bend so the tree corridor doglegs with it
        for(let r=0;r<rows;r++){ const off=26+r*7.5+6*frac(al2*0.3+r);   // innermost pines ~26yd off the fairway centre, then back into the woods
          if(off>gW-3) break;
          addTree(al2+3*frac(al2*0.7), cx-off, 1);
          addTree(al2+3*frac(al2*0.9+1), cx+off, 1); } }
      for(let ac2=-gW+6; ac2<=gW-6; ac2+=5.0){                          // treeline across the far end (behind the green)
        for(let r=0;r<rows;r++){ const inset=6+r*7+8*frac(ac2*0.3+r);
          addTree(gA1-inset, ac2+3*frac(ac2*0.6), 1); } }
    }
    // one billboard mesh per species, with a little per-tree brightness jitter so
    // no two copies of the same photo look identical
    treeGroups=TREE_KINDS.map(()=>({mesh:null, idx:[]}));
    for(let i=0;i<treeSpecs.length;i++){ const kind=treeSpecs[i][4];
      treeSpecs[i][6]=treeGroups[kind].idx.length; treeGroups[kind].idx.push(i); }
    const _cj=new THREE.Color();
    for(let gI=0;gI<treeGroups.length;gI++){ const grp=treeGroups[gI]; if(!grp.idx.length) continue;
      const mesh=new THREE.InstancedMesh(TREE_KINDS[gI].geo, TREE_KINDS[gI].mat, grp.idx.length); mesh.frustumCulled=false;
      mesh.castShadow=true;                                              // trees cast real shadows on the turf
      mesh.customDepthMaterial=new THREE.MeshDepthMaterial({depthPacking:THREE.RGBADepthPacking, map:TREE_KINDS[gI].mat.map, alphaTest:0.5});
      for(let li=0;li<grp.idx.length;li++){ const s=treeSpecs[grp.idx[li]];
        const jt=0.9+0.16*frac(s[0]*0.11+s[2]*0.07); _cj.setRGB(jt,jt,jt); mesh.setColorAt(li,_cj); }
      grp.mesh=mesh; group.add(mesh);
    }
    if(trunkMesh||treeGroups.some(g=>g.mesh)) faceTrees();
    // Grass tufts: SHORT regular grass everywhere for turf detail, and TALL native
    // grass only in the deep rough and only as much as the real course actually has.
    if(tuftMesh){ group.remove(tuftMesh); tuftMesh=null; }
    tuftSpecs=[];
    const okTuft=(al,ac)=>{ if(ac<-gW+3||ac>gW-3) return false;
      if(inCircle(gBunkers,al,ac)||onGrn(al,ac)) return false;
      for(let pi=0;pi<cartPts.length;pi++){ if(Math.hypot(al-cartPts[pi][0],ac-cartPts[pi][1])<3.5) return false; }
      return true; };
    // tall native-grass tufts DISABLED - the dense grass-blade field (grassMesh) now
    // provides all the rough, so these separate tall patches are no longer added.
    gCart=cartPts;   // expose the cart-path points so placeGrass can keep grass off the path
    // Soft ground shadow under each tree so it reads as planted, not floating.
    if(treeSpecs.length){
      const shI=new THREE.InstancedMesh(shadowGeo, shadowMat, treeSpecs.length);
      const _sm=new THREE.Matrix4(), _sp=new THREE.Vector3(), _sq=new THREE.Quaternion(), _ss=new THREE.Vector3();
      for(let i=0;i<treeSpecs.length;i++){ const t=treeSpecs[i], r=t[3]*0.19;
        _sp.set(t[0], t[1]+0.12, t[2]); _ss.set(r,r,r); _sm.compose(_sp,_sq,_ss); shI.setMatrixAt(i,_sm); }
      shI.instanceMatrix.needsUpdate=true; shI.frustumCulled=false; group.add(shI);
    }

    // Ocean: one coherent flat plane at the low sea level, where a big region sits
    // well below the tee. Land pokes through wherever the terrain rises above it.
    waterMesh=null; gHasSea=false; gSeaY=-1e9;
    let minH=1e9, low=0, tot=(na+1)*(nx+1);
    for(let i=0;i<=na;i++)for(let j=0;j<=nx;j++){ if(H[i][j]<minH) minH=H[i][j]; }
    for(let i=0;i<=na;i++)for(let j=0;j<=nx;j++){ if(H[i][j]<minH+1.2) low++; }
    if(minH<-7 && low/tot>0.06){                     // only a real COASTLINE (deep, extensive) becomes an ocean plane - an inland pond stays a designed water hazard, never a fake lake
      const segX=Math.min(150,Math.max(40,Math.round(2*W/4))), segZ=Math.min(190,Math.max(40,Math.round((a1-a0)/4)));
      const wp=new THREE.Mesh(new THREE.PlaneGeometry(2*W, a1-a0, segX, segZ), waterMat);
      waterBaseY=minH*EXAG+3.0;                      // headroom so low tide still covers the bay
      wp.rotation.x=-Math.PI/2; wp.position.set(0, waterBaseY, (a0+a1)/2);
      wp.renderOrder=1; group.add(wp); waterMesh=wp;
      gHasSea=true; gSeaY=waterBaseY;                 // only now does ground below sea level count as water
    }
    applyConditions();                               // set this hole's water to the current wind + tide
    // designed water hazards (ponds, creeks) as rippling water-surface discs
    gWater.forEach(function(h){ const dsc=new THREE.Mesh(new THREE.CircleGeometry(h[2],22), waterMat);
      dsc.rotation.x=-Math.PI/2; dsc.position.set(h[1], sampleH(h[0],h[1])+0.15, h[0]);
      dsc.renderOrder=1; group.add(dsc); });
    makePuddles();

    document.getElementById('name').textContent=D.name;
    document.getElementById('count').textContent=(at+1)+' / '+HOLES.length;
    lastShot=null; renderMap(); addressData();
    { const mw=document.getElementById('mapWind'); if(mw) mw.textContent=Math.round(WEATHER.wind_speed_mph||0)+' mph'; }
    place();
  }
  document.getElementById('prev').onclick=()=>build(at-1);
  document.getElementById('next').onclick=()=>build(at+1);
  document.getElementById('viewbtn').onclick=()=>{ VIEW=(VIEW==='play'?'over':'play');
    document.getElementById('viewbtn').textContent=(VIEW==='play'?'Down the line':'Overview');
    if(resting){ proceed(); } else place(); };
  document.getElementById('teebtn').onclick=()=>{ flight=null; ball.visible=false; resting=false; holeDone=false; setTip(TIP_HIT);
    resetPlayers(); VIEW='play'; document.getElementById('viewbtn').textContent='Down the line'; place(); toast('Back on the tee'); };
  document.getElementById('plbtn').onclick=()=>{ nplayers=nplayers%4+1;
    document.getElementById('plbtn').textContent=nplayers+'P';
    flight=null; ball.visible=false; resting=false; holeDone=false; setTip(TIP_HIT);
    resetPlayers(); buildMarkers(); VIEW='play'; document.getElementById('viewbtn').textContent='Down the line'; place();
    toast(nplayers>1?(nplayers+' players · furthest from the pin plays'):'Single player'); };

  // Preview the shot view from anywhere: tap a spot to stand at that lie and look
  // down the line. When the launch monitor is wired in, the tracked shot's
  // finishing point sets the lie automatically - no tap, and no ball drawn until
  // the struck ball is flown.
  const ray=new THREE.Raycaster(), ndc=new THREE.Vector2(); let dX=0,dY=0;
  R.domElement.addEventListener('pointerdown',e=>{dX=e.clientX;dY=e.clientY;});
  R.domElement.addEventListener('pointerup',e=>{
    if(Math.hypot(e.clientX-dX,e.clientY-dY)>8 || !groundMesh) return;   // a drag, not a tap
    if(resting){ proceed(); return; }          // hand off to whoever plays next
    if(flight || holeDone) return;
    ndc.x=(e.clientX/innerWidth)*2-1; ndc.y=-(e.clientY/innerHeight)*2+1;
    ray.setFromCamera(ndc,cam);
    const hit=ray.intersectObject(groundMesh)[0];
    if(hit) hitShot(hit.point.x, hit.point.z);                // hit a shot toward the tapped point
    else hitShot(pinC, pinA);                                 // tapped past the ground (blind green / over a dune): play at the pin
  });
  // The pin marker: an on-screen flag pinned to the green's screen position every frame,
  // clamped to the edges when the green is off-screen or hidden behind a dune, so a blind
  // hole is never unplayable - tap it to fire a shot straight at the pin.
  const _pv=new THREE.Vector3(), pinEl=document.getElementById('pinmark');
  pinEl.addEventListener('click',()=>{ if(flight||resting||holeDone||!gH) return; hitShot(pinC, pinA); });
  function updatePinMark(){
    if(!gH || flight || resting || holeDone){ pinEl.style.display='none'; return; }
    _pv.set(pinC, sampleH(pinA,pinC)+2.6, pinA);
    const behind=_pv.clone().sub(cam.position).dot(cam.getWorldDirection(new THREE.Vector3()))<=0;
    const v=_pv.clone().project(cam);
    let px=(v.x*0.5+0.5)*innerWidth, py=(-v.y*0.5+0.5)*innerHeight;
    if(behind){ px=innerWidth-px; py=innerHeight-24; }        // pin is behind you: park the marker low, flipped
    const m=30; px=Math.max(m,Math.min(innerWidth-m,px)); py=Math.max(70,Math.min(innerHeight-90,py));
    pinEl.style.left=px+'px'; pinEl.style.top=py+'px'; pinEl.style.display='block';
    document.getElementById('pinYd').textContent = holeDone?'' : (distToPin()+'y');
  }
  addEventListener('keydown',e=>{ if(e.key==='ArrowLeft')build(at-1); if(e.key==='ArrowRight')build(at+1);});
  let sx=null;
  addEventListener('touchstart',e=>{ if(e.touches.length===1) sx=e.touches[0].clientX;},{passive:true});
  addEventListener('touchend',e=>{ if(sx===null)return; const dx=e.changedTouches[0].clientX-sx;
    if(Math.abs(dx)>60) build(at + (dx<0?1:-1)); sx=null;},{passive:true});
  addEventListener('resize',()=>{cam.aspect=innerWidth/innerHeight;cam.updateProjectionMatrix();R.setSize(innerWidth,innerHeight);});
  build(0);
  function stepGrass(){
    if(!gOrthoPix || !grassMesh.visible) return;
    // Full-hole coverage is STATIC (placed once at build), so there is no per-shot
    // re-scatter hitch - the grass already covers tee to green wherever the ball goes.
    grassMat.uniforms.time.value=waterMat.uniforms.time.value;
    grassMat.uniforms.camPos.value.copy(cam.position);
    grassMat.uniforms.windDir.value.copy(waterMat.uniforms.windDir.value);
    grassMat.uniforms.windStr.value=windStrength;
  }
  // Adaptive resolution: the dense grass is never touched. If the framerate sags
  // on a slower machine, quietly lower the render resolution (and the shadow map)
  // until it recovers, and raise it back when there is headroom. This keeps the
  // look identical on a strong GPU and only trades sharpness for smoothness where
  // the machine actually needs it.
  let _prMax=Math.min(devicePixelRatio,1.25), _pr=_prMax, _fAcc=0, _fN=0, _fLast=performance.now();
  function adaptRes(){ const t=performance.now(); _fAcc+=t-_fLast; _fLast=t; _fN++;
    if(_fN>=30){ const fps=1000/(_fAcc/_fN); _fAcc=0; _fN=0;
      if(fps<26 && _pr>0.65){ _pr=Math.max(0.65,_pr-0.15); R.setPixelRatio(_pr);
        if(sun.shadow.mapSize.width>512){ sun.shadow.mapSize.set(512,512); if(sun.shadow.map){sun.shadow.map.dispose(); sun.shadow.map=null;} } }
      else if(fps>52 && _pr<_prMax){ _pr=Math.min(_prMax,_pr+0.1); R.setPixelRatio(_pr); } } }
  (function loop(){ if(camTween) stepCam(); else ctl.update(); stepFlight();faceTrees();faceRocks();faceTufts();rippleWater();flutterFlag();stepGrass();updatePinMark();adaptRes();
    skyMat.uniforms.time.value=waterMat.uniforms.time.value; R.render(scene,cam);requestAnimationFrame(loop);})();
} catch(e){ show('scene error: '+e.message); }
</script>
"""

if __name__ == "__main__":
    build_course(
        r"C:\Users\User\OneDrive\Desktop\B PITA\unity\Assets\Resources\Terrain\Stillwater_Shore",
        "Pebble Beach", os.path.join(SCRATCH, "pebble_course.html"))

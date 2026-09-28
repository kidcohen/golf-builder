"""Procedural dune mounding for courses with no fine LIDAR (SRTM base is smooth).
Adds coherent multi-octave movement - big in the rough, gentle on the fairway
ribbon and green - so links read as duney. Amplitude per hole is set from ground
photos (flat opener vs heaving dunes). This is INVENTED movement, used only where
real 1m survey doesn't exist; GB/US keep their true LIDAR.
"""
import sys, json, os
import numpy as np

def coherent(shape, cells, seed, octaves=3):
    na1, nx1 = shape
    out = np.zeros(shape)
    amp = 1.0; tot = 0.0
    rng = np.random.default_rng(seed)
    for o in range(octaves):
        c = max(2, int(cells * (2 ** o)))
        coarse = rng.standard_normal((c + 1, max(2, int(c * nx1 / na1)) + 1))
        yi = np.linspace(0, coarse.shape[0] - 1, na1)
        xi = np.linspace(0, coarse.shape[1] - 1, nx1)
        y0 = np.floor(yi).astype(int); x0 = np.floor(xi).astype(int)
        y1 = np.minimum(y0 + 1, coarse.shape[0] - 1); x1 = np.minimum(x0 + 1, coarse.shape[1] - 1)
        fy = (yi - y0)[:, None]; fx = (xi - x0)[None, :]
        top = coarse[y0][:, x0] * (1 - fx) + coarse[y0][:, x1] * fx
        bot = coarse[y1][:, x0] * (1 - fx) + coarse[y1][:, x1] * fx
        out += amp * (top * (1 - fy) + bot * fy)
        tot += amp; amp *= 0.5
    return out / tot

def clc_at(cl, a):
    for k in range(len(cl) - 1):
        if a <= cl[k + 1][0] or k == len(cl) - 2:
            span = (cl[k + 1][0] - cl[k][0]) or 1.0
            t = (a - cl[k][0]) / span
            return cl[k][1] + (cl[k + 1][1] - cl[k][1]) * t
    return cl[-1][1]

def dune_hole(path, amp_yards, fair_keep=0.3):
    h = json.load(open(path))
    ext = h["extent"]; cl = h.get("centreline") or [[0, 0], [ext["along1"], 0]]
    H = np.array(h["heights"], float)
    na1, nx1 = H.shape
    a0, a1, aw = ext["along0"], ext["along1"], ext["across"]
    noise = coherent((na1, nx1), cells=max(3, int((a1 - a0) / 70)), seed=int(abs(a1) * 7 + na1))
    # amplitude mask: gentle on the fairway/green corridor, full in the rough
    alongs = a0 + np.arange(na1) / (na1 - 1) * (a1 - a0)
    across = -aw + np.arange(nx1) / (nx1 - 1) * (2 * aw)
    mask = np.zeros((na1, nx1))
    for i, al in enumerate(alongs):
        d = np.abs(across - clc_at(cl, al))
        m = np.clip((d - 14.0) / 16.0, 0.0, 1.0)      # 0 on fairway centre, 1 in rough
        mask[i] = fair_keep + (1.0 - fair_keep) * m
    H2 = H + noise * amp_yards * mask
    h["heights"] = np.round(H2, 2).tolist()
    h["height_min"] = round(float(H2.min()), 2); h["height_max"] = round(float(H2.max()), 2)
    json.dump(h, open(path, "w"))
    return round(float(H2.max() - H2.min()), 1)

if __name__ == "__main__":
    course = sys.argv[1]
    # optional per-hole amplitude override: "1:2,2:2,6:7,..." else default from argv[2]
    default = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
    over = {}
    if len(sys.argv) > 3:
        for kv in sys.argv[3].split(","):
            k, v = kv.split(":"); over[int(k)] = float(v)
    base = fr"C:/Users/User/OneDrive/Desktop/B PITA/unity/Assets/Resources/Terrain/{course}"
    for n in range(1, 19):
        p = f"{base}/hole_{n:02d}.json"
        if not os.path.exists(p):
            continue
        rel = dune_hole(p, over.get(n, default))
        print(f"h{n}: amp={over.get(n, default)} relief={rel}y")
    print("done", course)

"""Re-do the procedural dunes correctly: recover the pre-dune base (subtract the
old untapered dune, deterministic), then re-apply mounding that TAPERS to zero at
the tee box and the green so those stay level. Saves heights_base for idempotency.
"""
import sys, json, os
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from add_dunes import coherent, clc_at

def process(path, amp):
    h = json.load(open(path)); ext = h["extent"]
    cl = h.get("centreline") or [[0, 0], [ext["along1"], 0]]
    H = np.array(h["heights"], float); na1, nx1 = H.shape
    a0, a1, aw = ext["along0"], ext["along1"], ext["across"]
    cells = max(3, int((a1 - a0) / 70)); seed = int(abs(a1) * 7 + na1)
    alongs = a0 + np.arange(na1) / (na1 - 1) * (a1 - a0)
    across = -aw + np.arange(nx1) / (nx1 - 1) * (2 * aw)
    # across mask (fairway gentle, rough full) - identical to the original run
    oldmask = np.zeros((na1, nx1))
    for i, al in enumerate(alongs):
        d = np.abs(across - clc_at(cl, al)); m = np.clip((d - 14.0) / 16.0, 0, 1)
        oldmask[i] = 0.3 + 0.7 * m
    noise = coherent((na1, nx1), cells=cells, seed=seed)
    if "heights_base" in h:
        base = np.array(h["heights_base"], float)
    else:
        base = H - noise * amp * oldmask            # undo the first (untapered) dune
    # new dune: same across mask, but tapered to 0 at tee (<~30y) and green (last ~24y)
    mask = np.zeros((na1, nx1))
    for i, al in enumerate(alongs):
        tp = min(np.clip((al - 12.0) / 18.0, 0, 1), np.clip((a1 - 6.0 - al) / 18.0, 0, 1))
        mask[i] = tp * oldmask[i]
    H2 = base + noise * amp * mask
    h["heights_base"] = np.round(base, 2).tolist()
    h["heights"] = np.round(H2, 2).tolist()
    h["height_min"] = round(float(H2.min()), 2); h["height_max"] = round(float(H2.max()), 2)
    json.dump(h, open(path, "w"))
    return round(float(H2.max() - H2.min()), 1)

if __name__ == "__main__":
    course = sys.argv[1]; default = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
    over = {}
    if len(sys.argv) > 3:
        for kv in sys.argv[3].split(","):
            k, v = kv.split(":"); over[int(k)] = float(v)
    base = fr"C:/Users/User/OneDrive/Desktop/B PITA/unity/Assets/Resources/Terrain/{course}"
    for n in range(1, 19):
        p = f"{base}/hole_{n:02d}.json"
        if os.path.exists(p):
            process(p, over.get(n, default))
    print("re-duned", course)

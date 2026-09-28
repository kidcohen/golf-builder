"""See the ball before it is struck: is it teed up, or on the ground?

The same camera that watches the body, the swing, and the ball at impact also
sees the ball sitting at address. From that one still we need two things:

    WHERE the ball is        - so the launch read and the swing read agree on it
    HOW it is sitting        - teed up (and how high) or down on the turf/mat

Why it matters: the simulator already knows the lie the golfer is meant to be
playing - the tee on a tee shot, the turf on an approach. If the ball is teed
when the lie is a fairway, or on the ground when it is a tee shot, the golfer
is hitting from the wrong spot and the shot should be flagged, not scored.

Tee height is not fixed. One golfer tees a driver high, another barely off the
mat, the same golfer tees an iron lower still - so we measure the height each
time rather than assume it, and report it in ball-diameters (a golf ball is
42.67 mm, which gives every pixel measurement a real-world scale).

This module is deliberately hardware-free: it takes an image (an H x W x 3
array, the frame the camera already grabs) and returns what it sees. The
heuristics here are a first, explainable pass - a bright near-circular blob for
the ball, a strong horizontal edge for the mat/turf line beneath it. They are
meant to be validated and then tightened against real frames from the actual
rig; the interface is what the rest of the system builds on and will not change
when the detector behind it improves.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np

BALL_MM = 42.67                      # a golf ball's diameter, for pixel -> real scale


@dataclass
class Ball:
    cx: float                        # centre, pixels
    cy: float
    r: float                         # radius, pixels
    confidence: float                # 0..1, how ball-like the blob was


@dataclass
class Lie:
    ball: Ball | None
    surface_y: float | None          # detected mat/turf line under the ball, pixels (y grows down)
    teed: bool
    tee_height_mm: float | None      # gap from ball bottom to the surface, in mm
    tee_height_diam: float | None    # the same gap in ball-diameters (scale-free)
    confidence: float


def _gray(img):
    a = np.asarray(img, dtype=np.float32)
    if a.ndim == 2:
        return a
    return a[..., :3] @ np.array([0.299, 0.587, 0.114], dtype=np.float32)


def _saturation(img):
    a = np.asarray(img, dtype=np.float32)
    if a.ndim == 2:
        return np.zeros(a.shape, dtype=np.float32)
    mx = a[..., :3].max(-1); mn = a[..., :3].min(-1)
    return np.where(mx > 1e-3, (mx - mn) / np.maximum(mx, 1e-3), 0.0)


def _largest_blob(mask):
    """Bounding box + pixel count of the largest 4-connected True region.

    A tiny iterative label-by-propagation so we need no scipy. Fine for one
    ball-sized blob in a frame; not meant for crowded masks."""
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    best = None
    ys, xs = np.nonzero(mask)
    for sy, sx in zip(ys, xs):
        if seen[sy, sx]:
            continue
        stack = [(sy, sx)]; seen[sy, sx] = True
        minx = maxx = sx; miny = maxy = sy; n = 0
        while stack:
            y, x = stack.pop(); n += 1
            if x < minx: minx = x
            if x > maxx: maxx = x
            if y < miny: miny = y
            if y > maxy: maxy = y
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True; stack.append((ny, nx))
        if best is None or n > best[0]:
            best = (n, minx, miny, maxx, maxy)
    return best


def detect_ball(img, roi=None):
    """Find the ball: the brightest, least-coloured, roughly round blob.

    `roi` is an optional (x0, y0, x1, y1) to look inside (the rig knows roughly
    where the ball sits). Returns a Ball or None.
    """
    a = np.asarray(img)
    x0, y0 = 0, 0
    if roi:
        x0, y0, x1, y1 = roi
        a = a[y0:y1, x0:x1]
    g = _gray(a); s = _saturation(a)
    # a golf ball reads bright and near-white (high value, low saturation)
    thr = max(180.0, float(np.percentile(g, 99.0)) - 20.0)
    mask = (g >= thr) & (s <= 0.25)
    if mask.sum() < 12:
        return None
    blob = _largest_blob(mask)
    if not blob:
        return None
    n, minx, miny, maxx, maxy = blob
    bw, bh = (maxx - minx + 1), (maxy - miny + 1)
    r = (bw + bh) / 4.0
    fill = n / max(1.0, bw * bh)                 # circle fills ~pi/4 of its box
    aspect = min(bw, bh) / max(bw, bh)
    conf = float(max(0.0, min(1.0, (fill / 0.785) * aspect)))
    return Ball(cx=x0 + (minx + maxx) / 2.0, cy=y0 + (miny + maxy) / 2.0, r=r, confidence=conf)


def surface_line(img, ball, band=2.5):
    """The mat/turf top edge directly under the ball: the row of strongest
    downward brightness change in a narrow column beneath it."""
    if ball is None:
        return None
    a = _gray(img); h, w = a.shape
    cx = int(round(ball.cx)); x0 = max(0, int(cx - ball.r * band)); x1 = min(w, int(cx + ball.r * band) + 1)
    y_from = int(min(h - 1, ball.cy + ball.r * 0.5))
    if y_from >= h - 2 or x1 - x0 < 2:
        return None
    col = a[y_from:h, x0:x1].mean(axis=1)
    if col.size < 3:
        return None
    d = np.abs(np.diff(col))
    return float(y_from + int(np.argmax(d)) + 1)


def classify_lie(img, roi=None, mm_per_px=None):
    """Full read: ball, the surface under it, teed-or-not, and tee height.

    If `mm_per_px` is unknown we still get a scale from the ball itself, whose
    real diameter is fixed - so tee height comes back in millimetres and, more
    robustly, in ball-diameters.
    """
    ball = detect_ball(img, roi=roi)
    if ball is None:
        return Lie(None, None, False, None, None, 0.0)
    surf = surface_line(img, ball)
    scale = mm_per_px if mm_per_px else (BALL_MM / max(1.0, 2.0 * ball.r))
    teed, gap_mm, gap_diam, conf = False, None, None, ball.confidence
    if surf is not None:
        ball_bottom = ball.cy + ball.r
        gap_px = surf - ball_bottom               # >0 means the ball floats above the surface
        gap_diam = gap_px / max(1.0, 2.0 * ball.r)
        gap_mm = gap_px * scale
        teed = bool(gap_diam > 0.25)               # a clear gap of a quarter-ball or more = teed
        # more confident the further the gap is from the teed/grounded boundary
        conf = float(max(0.0, min(1.0, ball.confidence * min(1.0, abs(gap_diam - 0.25) / 0.5 + 0.4))))
    return Lie(ball=ball, surface_y=surf, teed=teed,
               tee_height_mm=(round(gap_mm, 1) if gap_mm is not None else None),
               tee_height_diam=(round(gap_diam, 2) if gap_diam is not None else None),
               confidence=round(conf, 2))


def wrong_spot(lie, expects_tee):
    """Does the ball's state disagree with the lie the golfer should be playing?

    `expects_tee` is True on a tee shot. Returns None when we cannot tell
    (no ball / low confidence) so a shaky read never wrongly voids a shot.
    """
    if lie is None or lie.ball is None or lie.confidence < 0.5 or lie.surface_y is None:
        return None
    return bool(lie.teed != bool(expects_tee))


@dataclass
class Guard:
    register: bool                   # may the swing be recorded?
    reason: str | None               # why not, to show the golfer
    lie: Lie | None                  # what the camera read (for logging / display)

    def as_context(self):
        """The block the shot pipeline carries in `shot_context["vision"]`, so the
        game can refuse a swing without importing the vision code."""
        d = {"register": self.register, "reason": self.reason}
        if self.lie is not None:
            d.update(teed=self.lie.teed, tee_height_mm=self.lie.tee_height_mm,
                     tee_height_diam=self.lie.tee_height_diam, confidence=self.lie.confidence)
        return d


# lie_type strings (from the game's shot_context) that mean a tee is expected
TEE_LIE_TYPES = {"tee", "teed", "tee box", "teebox"}


def expects_tee(lie_type):
    """Whether the lie the golfer is playing calls for a teed ball."""
    return str(lie_type or "").strip().lower() in TEE_LIE_TYPES


def guard_shot(shot_context, lie):
    """Guard a swing against the game's own `shot_context` (which names the lie)."""
    return guard_swing(lie, expects_tee((shot_context or {}).get("lie_type")))


def guard_swing(lie, expects_tee):
    """Decide whether a swing may register, given what the camera saw and the lie
    the golfer is meant to be playing.

    Blocks only a confident mismatch - a ball teed on a turf shot, or on the
    ground on a tee shot. On any uncertain read (no ball, low confidence, no
    surface) it lets the swing through: the guard must never be the reason a
    real shot vanishes.
    """
    w = wrong_spot(lie, expects_tee)
    if not w:                        # None (unsure) or False (correct) -> allow
        return Guard(register=True, reason=None, lie=lie)
    reason = ("Tee shot - tee the ball up (it's on the ground); the swing won't register."
              if expects_tee else
              "Shot from the turf - take the ball off the tee; the swing won't register.")
    return Guard(register=False, reason=reason, lie=lie)


def guard_frame(img, expects_tee, roi=None, mm_per_px=None):
    """Read the frame and return the swing guard in one call - the entry point
    the shot pipeline uses just before it would record a monitor's shot."""
    return guard_swing(classify_lie(img, roi=roi, mm_per_px=mm_per_px), expects_tee)

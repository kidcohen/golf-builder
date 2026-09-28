"""A view down the hole, from behind the ball, with the shot in the air.

Everything modelled here has been numbers on a wire. A golfer standing in a bay
is looking at a screen, and what they need to see is the hole in front of them
and their ball flying down it - not a paragraph describing one.

This builds that view as a single self-contained page: the ground drawn in
perspective from behind the ball, the fairway and its trouble where they
actually are, the green with the way it falls, and the real trajectory the
physics produced played back through the air with its bounce and its run-out.

It reads the same description the server sends a client and the same trajectory
points it sends after a shot. Nothing here is invented for the picture - if it
can be drawn from this, a Unity client can draw it from the same messages, and
anything missing here is missing from the wire.
"""

import json

VIEW_TEMPLATE = """<!doctype html>
<meta charset="utf-8">
<title>__TITLE__</title>
<style>
  html,body{margin:0;height:100%;background:#0e1a10;color:#e8f0e6;
    font-family:Georgia,serif;overflow:hidden}
  canvas{display:block;width:100vw;height:100vh}
  .card{position:fixed;left:18px;top:16px;line-height:1.5;text-shadow:0 1px 3px #000}
  .card h1{margin:0;font-size:21px;font-weight:normal}
  .card p{margin:2px 0 0;font-size:13px;opacity:.85}
  .read{position:fixed;left:18px;bottom:16px;font-size:13px;opacity:.85;
    line-height:1.6;text-shadow:0 1px 3px #000}
  .flight{position:fixed;right:18px;top:16px;text-align:right;font-size:13px;
    line-height:1.7;text-shadow:0 1px 3px #000}
  .flight b{font-size:22px;font-weight:normal}
  .hint{position:fixed;right:18px;bottom:16px;font-size:12px;opacity:.55}
</style>
<canvas id="c"></canvas>
<div class="card"><h1>__TITLE__</h1><p>__SUBTITLE__</p></div>
<div class="read">__READ__</div>
<div class="flight" id="flight"></div>
<div class="hint">click to replay the shot</div>
<script>
const HOLE = __HOLE__, SHOT = __SHOT__;
const cv = document.getElementById('c'), cx = cv.getContext('2d');

// Camera sits behind and above the ball, looking down the hole. A golfer in a
// bay is standing behind the ball, so that is where the view comes from.
const EYE = {x:-14, y:5.5, z:0}, LOOK_DOWN = 0.055, FOCAL = 1.15;

function project(p, w, h){
  // Into camera space: x runs down the hole, z across it, y up.
  const dx = p.x - EYE.x, dy = p.y - EYE.y, dz = p.z - EYE.z;
  if (dx <= 0.4) return null;                       // behind the eye
  const tilt = dy + dx * LOOK_DOWN;
  const scale = (h * FOCAL) / dx;
  return {x: w/2 + dz * scale, y: h/2 - tilt * scale, d: dx};
}

function poly(points, w, h, fill, stroke){
  const seen = points.map(p => project(p, w, h)).filter(Boolean);
  if (seen.length < 3) return;
  cx.beginPath(); cx.moveTo(seen[0].x, seen[0].y);
  for (let i=1;i<seen.length;i++) cx.lineTo(seen[i].x, seen[i].y);
  cx.closePath();
  if (fill){ cx.fillStyle = fill; cx.fill(); }
  if (stroke){ cx.strokeStyle = stroke; cx.lineWidth = 1; cx.stroke(); }
}

function ground(y, x0, x1, spread, w, h, fill){
  poly([{x:x0,y:y,z:-spread},{x:x1,y:y,z:-spread},
        {x:x1,y:y,z:spread},{x:x0,y:y,z:spread}], w, h, fill);
}

// The fairway follows the centreline, so it is drawn as a run of quads rather
// than one long rectangle - that is what makes a dogleg look like a dogleg.
function corridor(w, h, halfWidth, fill){
  const line = HOLE.centreline;
  for (let i=0;i<line.length-1;i++){
    const a = line[i], b = line[i+1];
    const run = Math.hypot(b.x-a.x, b.z-a.z) || 1;
    const nx = -(b.z-a.z)/run, nz = (b.x-a.x)/run;
    poly([
      {x:a.x+nx*halfWidth, y:a.height_yards, z:a.z+nz*halfWidth},
      {x:b.x+nx*halfWidth, y:b.height_yards, z:b.z+nz*halfWidth},
      {x:b.x-nx*halfWidth, y:b.height_yards, z:b.z-nz*halfWidth},
      {x:a.x-nx*halfWidth, y:a.height_yards, z:a.z-nz*halfWidth},
    ], w, h, fill);
  }
}

function disc(centre, radius, w, h, fill, stroke){
  const ring = [];
  for (let a=0;a<Math.PI*2;a+=Math.PI/16)
    ring.push({x:centre.x+Math.cos(a)*radius, y:centre.y,
               z:centre.z+Math.sin(a)*radius});
  poly(ring, w, h, fill, stroke);
}

let started = null;
function frame(now){
  const w = cv.width = cv.clientWidth * devicePixelRatio;
  const h = cv.height = cv.clientHeight * devicePixelRatio;
  cx.setTransform(1,0,0,1,0,0);

  const sky = cx.createLinearGradient(0,0,0,h);
  sky.addColorStop(0,'#7fa4c4'); sky.addColorStop(0.55,'#c9d6cf');
  sky.addColorStop(0.56,'#3f6b3a'); sky.addColorStop(1,'#22401f');
  cx.fillStyle = sky; cx.fillRect(0,0,w,h);

  const far = HOLE.length_yards + 90;
  ground(0, -30, far, 260, w, h, '#3f6b3a');                    // rough
  corridor(w, h, HOLE.fairway_width_yards/2 + 12, '#4c8043');   // first cut
  corridor(w, h, HOLE.fairway_width_yards/2, '#5f9c50');        // fairway

  for (const z of HOLE.hazards){
    const ink = {bunker:'#e3d5a1', water:'#4a86c8', trees:'#24401f',
                 out_of_bounds:'#b04a4a'}[z.kind] || '#e3d5a1';
    disc({x:z.x, y:0.02, z:z.z}, z.radius_yards, w, h, ink, 'rgba(0,0,0,.35)');
  }

  const g = HOLE.green;
  disc({x:g.x, y:0.03, z:g.z}, g.radius_yards, w, h, '#7fc26a', 'rgba(0,0,0,.25)');

  // The flag, and the line the green falls down.
  const base = project({x:g.x, y:0.03, z:g.z}, w, h);
  if (base){
    const top = project({x:g.x, y:2.4, z:g.z}, w, h);
    if (top){
      cx.strokeStyle='#f4f4ee'; cx.lineWidth=2;
      cx.beginPath(); cx.moveTo(base.x,base.y); cx.lineTo(top.x,top.y); cx.stroke();
      cx.fillStyle='#d64a3f';
      cx.beginPath(); cx.moveTo(top.x,top.y);
      cx.lineTo(top.x+16,top.y+6); cx.lineTo(top.x,top.y+12); cx.fill();
    }
  }

  // The shot, drawn as the trace it actually flew.
  if (started === null) started = now;
  const played = Math.min((now - started)/1000 * 1.35, SHOT.duration_sec);
  let last = null, shown = 0;
  cx.beginPath();
  for (const p of SHOT.points){
    if (p.t > played) break;
    const s = project({x:p.x, y:p.y, z:p.z}, w, h);
    shown = p.carry;
    if (!s) continue;
    if (!last) cx.moveTo(s.x, s.y); else cx.lineTo(s.x, s.y);
    last = s;
  }
  cx.strokeStyle = 'rgba(255,255,255,.85)'; cx.lineWidth = 2.5; cx.stroke();
  if (last){
    cx.fillStyle = '#ffffff';
    cx.beginPath(); cx.arc(last.x, last.y, 4.5, 0, Math.PI*2); cx.fill();
  }

  document.getElementById('flight').innerHTML =
    '<b>' + shown.toFixed(0) + ' yds</b><br>' + SHOT.summary;

  requestAnimationFrame(frame);
}
addEventListener('click', () => { started = null; });
requestAnimationFrame(frame);
</script>
"""


def build_view(course, hole, layout, flight, read_lines):
    """One hole with one shot on it, as a page that opens anywhere."""
    points = flight.get("trajectory_points") or []
    carried = 0.0
    trace = []
    for point in points:
        x, y, z, _, _, _, t = point[0], point[1], point[2], 0, 0, 0, point[6]
        carried = max(carried, x)
        trace.append({"x": round(x, 2), "y": round(max(y, 0.0), 2),
                      "z": round(z, 2), "t": round(t, 3),
                      "carry": round(x, 1)})

    shot = {
        "points": trace,
        "duration_sec": trace[-1]["t"] if trace else 0.0,
        "summary": (
            f"carry {flight.get('total_carry_yards', 0):.0f} &#183; "
            f"total {flight.get('total_distance_with_roll_yards', 0):.0f} &#183; "
            f"peak {flight.get('max_height_yards', 0):.0f} yds"
        ),
    }
    subtitle = (f"Hole {hole.number} &#183; {hole.length_yards:.0f} yards "
                f"&#183; par {hole.par} &#183; stroke index {hole.stroke_index}")

    return (VIEW_TEMPLATE
            .replace("__TITLE__", course.name)
            .replace("__SUBTITLE__", subtitle)
            .replace("__READ__", "<br>".join(read_lines))
            .replace("__HOLE__", json.dumps(layout))
            .replace("__SHOT__", json.dumps(shot)))

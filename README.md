# golf-builder

Autonomous course builder. GitHub Actions runs `cloud_autopilot.py` every 6h
(no local app needed): crawl OSM (reconstructing holes from greens+tees when hole
lines are missing) -> audit -> classify landscape -> terrain/dunes -> build sim.
Resumable via state.json. Built sims land in courses/, terrain data in terrain/.

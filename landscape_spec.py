# Per-course landscape identity, keyed by built-folder name, grounded in what each
# REAL course looks like (turf tone, rough tone, distant backdrop, terrain character).
# Merges into each _course.json without touching holes/hazards/weather.
import json, os
T = r"C:/Users/User/OneDrive/Desktop/B PITA/unity/Assets/Resources/Terrain"

# palette templates: grassFair / grassRough / water(deep,shallow ints) / rock
LINKS_GOLD = dict(grassFair=[0.44,0.60,0.22], grassRough=[0.56,0.47,0.22], rock=[0.34,0.35,0.36])   # fescue gold rough
LINKS_IRE  = dict(grassFair=[0.34,0.58,0.18], grassRough=[0.40,0.42,0.20], rock=[0.30,0.31,0.32])   # greener Irish links
HEATH      = dict(grassFair=[0.34,0.54,0.17], grassRough=[0.40,0.30,0.20], rock=[0.32,0.30,0.30])   # heather purple-brown
SANDBELT   = dict(grassFair=[0.42,0.60,0.20], grassRough=[0.52,0.46,0.26], rock=[0.66,0.62,0.55])   # white sand, native tan
PARK_LUSH  = dict(grassFair=[0.28,0.55,0.15], grassRough=[0.18,0.38,0.12], rock=[0.34,0.34,0.33])   # deep parkland green
PARK_WARM  = dict(grassFair=[0.40,0.58,0.18], grassRough=[0.34,0.40,0.18], rock=[0.36,0.34,0.30])   # kikuyu/warm parkland
DESERT     = dict(grassFair=[0.46,0.58,0.22], grassRough=[0.70,0.58,0.34], rock=[0.55,0.34,0.24])   # tan waste, red rock
COAST_CYP  = dict(grassFair=[0.32,0.55,0.16], grassRough=[0.42,0.42,0.22], rock=[0.40,0.40,0.42])   # Monterey coast
FESCUE_INL = dict(grassFair=[0.40,0.58,0.20], grassRough=[0.56,0.50,0.26], rock=[0.40,0.40,0.38])   # treeless inland fescue (Erin/Chambers/Shinnecock)

def W(deep, shallow): return dict(deep=deep, shallow=shallow)
OCEAN = W(0x0d2338, 0x3f8aa0); ATLANTIC = W(0x0b2233, 0x356f86); FIRTH = W(0x14303f, 0x4a86a0)
LAKE  = W(0x123047, 0x3a7fa2); INLAND = W(0x1a3a3a, 0x407a72)

# tree_profile: [pine, broadleaf, palm/other, birch, dead-standing]
NO_TREE   = [0.0,0.0,0.0,0.0,0.0]
PINES     = [0.80,0.10,0.0,0.10,0.0]
HEATH_TR  = [0.55,0.12,0.0,0.33,0.0]     # scots pine + silver birch
PARK_TR   = [0.30,0.60,0.02,0.08,0.0]
CYPRESS   = [0.55,0.20,0.20,0.05,0.0]
EUCALYPT  = [0.20,0.10,0.60,0.10,0.0]

def bd(t, h=0, snow=False): return dict(type=t, height=h, snow=snow)

# (folder): (palette-template, water, tree_profile, backdrop, is_links_for_dunes)
SPEC = {
 # ---- LINKS (golden fescue, dunes, treeless) ----
 "Ailsa_Shore":     (LINKS_GOLD, OCEAN,    NO_TREE, bd("mountains",240), True),   # Turnberry: Ailsa Craig + Arran
 "Auld_Links":      (LINKS_GOLD, FIRTH,    NO_TREE, bd("flat",0),        True),   # St Andrews Old: flat, town edge
 "Bandon_Dunes":    (LINKS_GOLD, OCEAN,    NO_TREE, bd("forest",90),     True),   # gorse, Pacific, shore pines behind
 "Barry_Burn":      (LINKS_GOLD, OCEAN,    [0.2,0.1,0,0.1,0], bd("flat",0), True),# Carnoustie: flat, a few trees
 "Cabot_Cliffs":    (LINKS_GOLD, OCEAN,    [0.3,0.1,0,0.2,0], bd("forest",120), True),
 "Cabot_Highlands": (LINKS_GOLD, FIRTH,    NO_TREE, bd("mountains",180), True),   # Castle Stuart: Moray Firth hills
 "Cape_Kidnappers": (LINKS_GOLD, OCEAN,    [0.2,0.1,0,0.1,0], bd("mountains",210), False), # sheep-station ridges (real relief)
 "Chambers_Bay":    (FESCUE_INL, OCEAN,    [0.05,0,0,0,0.05], bd("flat",0), True),# treeless, lone fir, Puget Sound
 "Cruden_Bay":      (LINKS_GOLD, OCEAN,    NO_TREE, bd("flat",0),        True),   # wild dunes
 "Dune_Valleys":    (LINKS_GOLD, OCEAN,    NO_TREE, bd("flat",0),        True),
 "Dunluce_Head":    (LINKS_GOLD, ATLANTIC, NO_TREE, bd("flat",0),        True),   # Royal Portrush: dunes, sea
 "Gullane_Honours": (LINKS_GOLD, FIRTH,    NO_TREE, bd("mountains",120), True),   # Gullane Hill, Firth of Forth
 "Kingsbarns":      (LINKS_GOLD, OCEAN,    NO_TREE, bd("flat",0),        True),
 "Lahinch":         (LINKS_IRE,  ATLANTIC, NO_TREE, bd("flat",0),        True),
 "New_South_Wales": (LINKS_GOLD, OCEAN,    [0.15,0.05,0,0.05,0], bd("mountains",90), False), # clifftop, Botany Bay
 "Portmarnock":     (LINKS_IRE,  OCEAN,    NO_TREE, bd("flat",0),        True),
 "Postage_Links":   (LINKS_GOLD, OCEAN,    NO_TREE, bd("mountains",200), True),   # Royal Troon: Ailsa Craig
 "Prestwick":       (LINKS_GOLD, OCEAN,    NO_TREE, bd("flat",0),        True),
 "Slieve_Links":    (LINKS_IRE,  OCEAN,    [0.1,0,0,0.1,0], bd("mountains",650), True), # RCD: Mourne Mountains
 "Struie_Links":    (LINKS_GOLD, FIRTH,    [0.2,0,0,0.2,0], bd("mountains",150), True),  # Royal Dornoch
 "Sand_Hills":      (SANDBELT,   INLAND,   NO_TREE, bd("flat",0),        True),   # Nebraska sandhills, treeless
 "Sandwich_Dunes":  (LINKS_GOLD, OCEAN,    NO_TREE, bd("flat",0),        True),   # Royal St George's: humpy dunes
 "Barnbougle_Dunes":(LINKS_GOLD, OCEAN,    NO_TREE, bd("flat",0),        True),
 "Tara_Iti":        (LINKS_GOLD, OCEAN,    [0.5,0.05,0,0.05,0], bd("forest",70), True),  # NZ, shore pines
 "Te_Arai_South":   (LINKS_GOLD, OCEAN,    [0.5,0.05,0,0.05,0], bd("forest",70), True),
 "Whistling_Straits":(LINKS_GOLD,LAKE,     NO_TREE, bd("flat",0),        True),   # man-made dunes, Lake Michigan
 # ---- HEATHLAND (heather, pines & birch, sandy) ----
 "Sunningdale_Old": (HEATH, INLAND, HEATH_TR, bd("forest",130), False),
 "Swinley_Forest":  (HEATH, INLAND, HEATH_TR, bd("forest",150), False),
 "St_Georges_Hill": (HEATH, INLAND, HEATH_TR, bd("forest",170), False),
 "Morfontaine":     (HEATH, INLAND, HEATH_TR, bd("forest",140), False),
 "Hirono":          (PARK_LUSH, INLAND, PINES, bd("forest",160), False),
 # ---- SANDBELT (Melbourne: white sand, native, eucalypts) ----
 "Kingston_Heath":  (SANDBELT, INLAND, EUCALYPT, bd("forest",70), False),
 "Royal_Melbourne": (SANDBELT, INLAND, EUCALYPT, bd("forest",80), False),
 # ---- PARKLAND ----
 "Azalea_National": (PARK_LUSH, INLAND, PINES, bd("forest",180), False),   # Augusta: tall pines
 "Muirfield_Village":(PARK_LUSH, LAKE,  PARK_TR, bd("forest",150), False),
 "Quail_Hollow":    (PARK_LUSH, LAKE,  PARK_TR, bd("forest",120), False),
 "Bethpage_Black":  (PARK_LUSH, INLAND, PARK_TR, bd("forest",140), False),
 "Oakmont_Country_Club":(PARK_LUSH, INLAND, [0.1,0.15,0,0.05,0], bd("flat",0), False), # famously treeless
 "Riviera":         (PARK_WARM, INLAND, EUCALYPT, bd("mountains",160), False),  # barranca, kikuyu, Santa Monica canyon
 "Pine_Hollow":     (PARK_LUSH, LAKE,  PARK_TR, bd("forest",120), False),
 "Furrow_Ridge":    (PARK_LUSH, LAKE,  PARK_TR, bd("forest",130), False),
 "Quarry_East":     (DESERT,    INLAND, [0.1,0.05,0.05,0,0], bd("mountains",180), False),
 "Sebonack_Hills":  (FESCUE_INL,OCEAN, [0.3,0.2,0,0.1,0], bd("forest",90), False),
 "Shinnecock_Hills_Golf_Club":(FESCUE_INL, OCEAN, [0.1,0.1,0,0.05,0], bd("flat",0), False), # undulating fescue, few trees
 "Erin_Hills":      (FESCUE_INL,LAKE,  NO_TREE, bd("flat",0), True),      # treeless glacial fescue
 # ---- COASTAL CYPRESS / Monterey ----
 "Foothill_West":   (COAST_CYP, OCEAN, CYPRESS, bd("mountains",210), False), # Pebble: Santa Lucia
 "Cypress_Head":    (COAST_CYP, OCEAN, CYPRESS, bd("forest",160), False),    # Cypress Point: Monterey pines/cypress
 "Stillwater_Shore":(COAST_CYP, OCEAN, CYPRESS, bd("mountains",200), False),
}

def apply():
    changed=[]
    for folder,(pal,water,tp,backdrop,links) in SPEC.items():
        cj=os.path.join(T,folder,"_course.json")
        if not os.path.exists(cj): print("skip (missing)",folder); continue
        c=json.load(open(cj))
        palette=dict(pal); palette["deep"]=water["deep"]; palette["shallow"]=water["shallow"]
        # keep any existing fairway/rough aerial-measured values but override the turf/rough tone
        palette.setdefault("fairway", pal["grassFair"])
        palette.setdefault("rough", pal["grassRough"])
        c["palette"]=palette
        c["tree_profile"]=tp
        c["backdrop"]=backdrop
        c["links"]=links
        json.dump(c, open(cj,"w"), indent=1)
        changed.append(folder)
    print(f"applied landscape spec to {len(changed)} courses")
    return changed

if __name__=="__main__":
    apply()

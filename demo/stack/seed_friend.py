"""A friend's first evenings, sent the way the shipper sends them: dev/seed.py's
events, renamed to invented characters (no "demo" flag, so the page looks
like a friend's), POSTed to /ingest with the credentials the installer
wrote in the throwaway HOME. Prints no credential.

    uv run --no-project python demo/stack/seed_friend.py <config.toml> <First-Surname> [first|all]

first: one evening (the wizard's checks tick); all: the six evenings, the
alt, the gear (the tour). Every name is invented: the characters, the players
met, the realm.
"""

import base64
import json
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
conf, main = sys.argv[1], sys.argv[2]
part = sys.argv[3] if len(sys.argv) > 3 else "all"
# Forever names are "First Surname" (the folder form: First-Surname). The realm
# the price scans name is invented too.
REALM = "Dawnwatch"
ALT = f"Pellwick-{main.split('-', 1)[1]}"
NAMES = {"Testchar-Demo": main, "Testalt-Demo": ALT,
         "Demoplayer-One": "Thorgrim-Ashvale", "Demoplayer-Two": "Elunara-Moonbrook",
         "Demoplayer-Three": "Bramwell-Hollis", "Demoplayer-Four": "Fizzlecog-Sparkwick",
         "Demoplayer-Five": "Demofriend-Thorne"}
NAMES.update({f"Demopasserby-{c}": n for c, n in zip("ABCDEFGH", (
    "Korvash-Grimm", "Tilda-Reed", "Harnell-Vance", "Osric-Dale", "Wrenna-Holt", "Dagmar-Stone", "Quill-Ashby",
    "Jorund-Frost"), strict=True)})
# The main's look: a Dwarf hunter (dark take) or a Night Elf huntress (light).
LOOK = {"race": "Night Elf", "sex": "female"} if main.startswith("Ysolde") else {"race": "Dwarf", "sex": "male"}
DROP = {"Testhunt-Demo", "Testgun-Demo", "Testsky-Demo"}
# The seed's guides by the ids Polestar gives them, so the route and the
# session plan know where the character is.
GUIDES = {"Polestar 20-30": "forever/alliance/06-wetlands",
          "Polestar 30-40": "forever/alliance/30-40/01-hillsbrad-foothills"}

with tempfile.TemporaryDirectory() as d:
    out = Path(d) / "events.ndjson"
    subprocess.run(["uv", "run", "--no-project", "python", str(REPO / "dev" / "seed.py"), "--out", str(out)],
                   check=True, stdout=subprocess.DEVNULL)
    raw = out.read_text(encoding="utf-8")

# The seed's quests are numbered placeholders: each becomes a real quest of
# about the right level (data/forever-reference.json), name and id together.
QUESTS = json.loads((REPO / "data" / "forever-reference.json").read_text(encoding="utf-8"))["quests"]
POOL = sorted(int(k) for k, v in QUESTS.items() if 22 <= v[1] <= 34)


def real_quest(e: dict) -> None:
    if e.get("kind") != "turnin" or not str(e.get("quest_name", "")).startswith("Demo quest"):
        return
    qid = POOL[int(e["quest_id"]) * 7919 % len(POOL)]
    e.update(quest_id=qid, quest_name=QUESTS[str(qid)][0])


events = []
hunter_gear = None
for line in raw.splitlines():
    e = json.loads(line)
    if e.get("feed") == "wow-gear" and e.get("char") == "Testhunt-Demo":
        hunter_gear = e  # the main wears this sheet (real Classic items), tabard aside
    if e.get("char") in DROP:
        continue
    e.pop("demo", None)
    s = json.dumps(e)
    for old, new in {**NAMES, **GUIDES}.items():
        s = s.replace(old, new)
    s = s.replace("Testalt", ALT.split("-")[0]).replace('"realm": "Demo"', f'"realm": "{REALM}"')
    e = json.loads(s)
    real_quest(e)
    if e.get("feed") == "wow-character" and e.get("char") == main:
        e.update(LOOK, faction="Alliance")
        e["class"] = "HUNTER"
    if e.get("feed") == "wow-character" and e.get("char") == ALT:
        e.update(race="Gnome", sex="male", faction="Alliance")
        e["class"] = "MAGE"
    events.append(e)

if hunter_gear:
    g = json.loads(json.dumps(hunter_gear))
    g.pop("demo", None)
    g.update(LOOK, char=main, source_id=f"gear:{main}:1", _msg=f"{main}: equipped")
    g["class"] = "HUNTER"
    g["items"] = [i for i in g["items"] if i["slot"] != 19]
    g["_time"] = max(e["_time"] for e in events if e.get("char") == main)
    events.append(g)

if part == "first":
    # The first evening only, the way a first sync after one session looks.
    first_day = min(e["_time"][:10] for e in events if e.get("char") == main)
    events = [e for e in events if e["_time"][:10] == first_day and e.get("char") == main]

with open(conf, "rb") as f:
    ship = tomllib.load(f)["ship"]
auth = base64.b64encode(f"{ship['user']}:{ship['password']}".encode()).decode()
sent = 0
for i in range(0, len(events), 500):
    chunk = events[i:i + 500]
    body = "\n".join(json.dumps(e) for e in chunk).encode() + b"\n"
    req = urllib.request.Request(ship["url"], data=body, method="POST",
                                 headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-ndjson"})
    with urllib.request.urlopen(req, timeout=60) as r:
        r.read()
    sent += len(chunk)
print(f"shipped {sent} events for {main}")

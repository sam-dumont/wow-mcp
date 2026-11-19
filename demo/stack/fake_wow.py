"""A WoW Forever folder with a few minutes of play in it, for the terminal
takes: the account's Polestar_Recorder.lua with the shipper tests' chunks
(tests/test_shipper_client.py), Polestar.lua, and one folder per invented
character. The shipper's first run then has something to send.

    uv run --frozen python demo/stack/fake_wow.py <_forever_ folder> <Main-Realm> <Alt-Realm>
"""

import sys
from pathlib import Path

from tests.test_shipper_client import CHUNKS, MAPS, lua_str

wow, main, alt = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
chars = {"aaaa": main, "bbbb": alt}
acct = wow / "WTF" / "Account" / "DEMO"
(acct / "SavedVariables").mkdir(parents=True, exist_ok=True)
(wow / "Logs").mkdir(parents=True, exist_ok=True)
(wow / "Interface" / "AddOns").mkdir(parents=True, exist_ok=True)
keys = [k for k in CHUNKS if k[0] in chars]
body = ",\n".join(f"[{i}] = {lua_str(CHUNKS[k])}" for i, k in enumerate(keys, 1))
maps = ",\n".join(f"[{k}] = {lua_str(v)}" for k, v in MAPS.items())
(acct / "SavedVariables" / "Polestar_Recorder.lua").write_text(
    f'PolestarRecorderDB = {{\n["chunks"] = {{\n{body}\n}},\n["maps"] = {{\n{maps}\n}},\n}}\n', encoding="utf-8")
alts = "".join(f'["{n}"] = {{\n["name"] = "{n.replace("-", " ")}",\n["seen"] = 1794928683,\n'
               f'["level"] = 12,\n}},\n' for n in chars.values())
(acct / "SavedVariables" / "Polestar.lua").write_text(
    f'PolestarDB = {{\n["alts"] = {{\n["chars"] = {{\n{alts}}},\n}},\n}}\n', encoding="utf-8")
for cid, name in chars.items():
    d = acct / name.split("-", 1)[1] / name / "SavedVariables"  # the shipper names it after this folder
    d.mkdir(parents=True, exist_ok=True)
    (d / "Polestar_Recorder.lua").write_text(f'PolestarRecorderCharDB = {{\n["id"] = "{cid}",\n}}\n', encoding="utf-8")

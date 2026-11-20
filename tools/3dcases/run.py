"""The 3D character test cases: real characters, Blizzard's own renders as
the reference, our assembly and viewer drawing the same look. README.md
next to this file says how to use it; in short:

    uv run python tools/3dcases/run.py add <name-surname>      # one of yours
    uv run python tools/3dcases/run.py discover --guild <slug> ...
    uv run python tools/3dcases/run.py refresh                 # Battle.net, dated
    uv run --group browser python tools/3dcases/run.py run     # draw, compare, grade
    uv run python tools/3dcases/run.py coverage

The game files come from your own install, read-only (the shipper's CASC
reader): a case whose files your client never downloaded is skipped and
listed, never fetched from anywhere else.
"""

from __future__ import annotations

import argparse
import html
import importlib.util
import json
import os
import random
import sys
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT)]

import checks  # noqa: E402
import imgcmp  # noqa: E402
import store  # noqa: E402

from wowstats import assets, character3d  # noqa: E402

DEFAULT_WOW = {"darwin": "/Applications/World of Warcraft",
               "win32": r"C:\Program Files (x86)\World of Warcraft"}.get(sys.platform, "")
DENYLIST = Path(os.environ.get("WOW_STATS_DENYLIST") or Path.home() / ".config/wow-stats-public/denylist.txt")
# Turns tried around facing the camera (radians): Blizzard's camera is a
# little to one side; the best silhouette match wins, then a finer pass.
YAWS = [-0.15 + i * 0.075 for i in range(-4, 5)]
INV_2H, INV_SHIELD, INV_ROBE = 17, 14, 20


def shipper():
    spec = importlib.util.spec_from_file_location("wow_stats_shipper", ROOT / "shipper" / "wow_stats.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ the install

class Install:
    """The player's own client: its tables and files, read-only."""

    def __init__(self, root: Path):
        self.ship = shipper()
        self.casc = self.ship.Casc(Path(root))
        need = {n: f for n, f in assets.TABLES.items() if n in character3d.TABLES}
        got, by_table, missing = self.ship.casc_files(self.casc, [], need, follow=False)
        self.table_problems = {k: v for k, v in missing.items() if not str(v).startswith("partly")}
        blobs = {t: got[f] for t, f in by_table.items()}
        version = self.casc.version

        class Source:
            def table(self, name):
                return blobs.get(name)

            def build(self):
                return version

        self.tables = character3d.load_tables(Source())
        self.ix = character3d.index(self.tables)
        self.files: dict[int, bytes] = {}
        self.absent: dict[int, str] = {}
        opts = self.ix.t("ChrCustomizationOption")
        self.option_names = {oid: str(r.get("Name_lang") or "") for oid, r in opts.items()}
        self.first_choice = {oid: ch[0] for oid, ch in self.ix.choices.items() if ch}

    def read(self, fdids) -> dict[int, str]:
        """Read these files and what they name; returns the ones this
        install can't give, with why."""
        want = {int(f) for f in fdids} - set(self.files) - set(self.absent)
        if want:
            got, _t, missing = self.ship.casc_files(self.casc, sorted(want), (), follow=True)
            self.files.update(got)
            self.absent.update({int(k): v for k, v in missing.items() if isinstance(k, int)})
        return {f: self.absent[f] for f in fdids if f in self.absent}

    def item_facts(self, item_id: int) -> tuple[int, int, int] | None:
        r = self.ix.t("Item").get(item_id)
        return (r.get("ClassID", 0), r.get("SubclassID", 0), r.get("InventoryType", 0)) if r else None


def gear_from_tables(inst: Install, items: list[dict]) -> dict:
    """Cells' gear facts from the client's Item table (no equipment call)."""
    inv_names = {INV_2H: "TWOHWEAPON", INV_SHIELD: "SHIELD", INV_ROBE: "ROBE"}
    out = {}
    for it in items:
        f = inst.item_facts(it["item_id"])
        if f:
            out[str(it["slot"])] = [f[0], f[1], inv_names.get(f[2], str(f[2]))]
    return out


def case_cells(spec: dict, inst: Install | None, models=("updated", "classic")) -> list[str]:
    s = dict(spec)
    if inst is not None:
        s["gear"] = gear_from_tables(inst, spec["items"]) or spec.get("gear") or {}
    out = store.cells(s, inst.option_names if inst else None, inst.first_choice if inst else None)
    return sorted(set(out) | {f"model:{m}" for m in models})


# ------------------------------------------------------------------ one case

def assemble(inst: Install, spec: dict, models: str, items=None, choices=None) -> dict:
    return character3d.assemble(inst.tables, spec["race"], spec["sex"], spec["class"],
                                spec["choices"] if choices is None else choices,
                                spec["items"] if items is None else items, models)


def option_effects(inst: Install, spec: dict, base: dict) -> dict[int, bool]:
    """For each non-default choice: does it change the updated model's
    recipe? (A choice that changes nothing is a look we lose.)"""
    out = {}
    look = character3d.battlenet_geosets()
    own = {o for o, _r in inst.ix.options.get(inst.ix.race_models.get((spec["race"], spec["sex"])), [])}
    for c in spec["choices"]:
        opt, ch = c["optionId"], c["choiceId"]
        if opt in inst.first_choice and opt not in own:
            continue  # another model's (a druid form's look)
        if opt in inst.first_choice:
            if inst.first_choice[opt] == ch:
                continue
        elif not look.get(ch):
            continue
        rest = [x for x in spec["choices"] if x is not c]
        alt = assemble(inst, spec, "hd", choices=rest)
        out[opt] = json.dumps(alt["recipe"], sort_keys=True) != json.dumps(base["recipe"], sort_keys=True)
    return out


def hidden_slots(inst: Install, items: list[dict]) -> set[int]:
    """Slots the player hides: Battle.net's appearance names the game's
    "Hidden Helm"-style cosmetic items there, which have no look."""
    out = set()
    for it in items:
        f = inst.item_facts(it["item_id"])
        if f and f[0] == 4 and f[1] == 5 and not inst.ix.appearance.get(it["item_id"]):
            out.add(it["slot"])
    return out


HELD = (16, 17, 18)


def bare(recipe: dict) -> dict:
    """The recipe without what the hands hold (its weapons' mask)."""
    return {**recipe, "attachments": [a for a in recipe["attachments"] if a["slot"] not in HELD]}


def best_yaw(sess, target: int, ref: imgcmp.Img) -> tuple[float, imgcmp.Img | None, float]:
    rr = imgcmp.mask_rows(ref)
    best = (0.0, None, -1.0)

    def score(d):
        shot, _grid = sess.shot(render_facing() + d, target)
        if shot is None:
            return None, 0.0
        ro = imgcmp.mask_rows(shot)
        n = max(len(rr), len(ro))
        _dx, _dy, v = imgcmp.align(rr + [0] * (n - len(rr)), ro, search=6)
        return shot, v

    for d in YAWS:
        shot, v = score(d)
        if v > best[2]:
            best = (d, shot, v)
    return best


def render_facing() -> float:
    import render

    return render.FACING


def evaluate(inst: Install, renderer, spec: dict, refs: dict[str, bytes], out_dir: Path, case_id: str,
             foreign: dict[str, str] | None = None) -> dict:
    """Both models of one case: draw, compare, grade. `foreign`: model ->
    why its reference is someone else's render (store.foreign_render)."""
    result = {"case": case_id, "cells": spec.get("cells", []), "models": {}}
    hidden = hidden_slots(inst, spec["items"])
    spec = {**spec, "items": [it for it in spec["items"] if it["slot"] not in hidden]}
    base = assemble(inst, spec, "hd")
    effects = option_effects(inst, spec, base) if base["recipe"] else {}
    heights: dict[str, int] = {}
    for model, ref_png in sorted(refs.items()):
        r: dict = {}
        result["models"][model] = r
        if (foreign or {}).get(model):
            r.update(grade="skipped", reasons=[foreign[model]])
            continue
        got = assemble(inst, spec, model)
        if got["recipe"] is None:
            r.update(grade="fail", reasons=[got.get("why") or "no model"])
            continue
        # A race with no Classic model (the Skyborne): Blizzard's Classic render is its Updated
        # model, so ours is too (the assembly falls back to it), with a note.
        updated_for_classic = model == "sd" and not got["classic"]
        recipe = got["recipe"]
        absent = inst.read(got["needs"])
        essential = {recipe["model"]} | {x["model"] for x in recipe["attachments"] + recipe["collections"]}
        if essential & set(absent):
            r.update(grade="skipped", reasons=["not on this disk"],
                     files={str(k): v for k, v in sorted(absent.items())})
            continue
        sess = renderer.session(recipe, inst.files, ref_png)
        try:
            ref, body = sess.reference()
            if ref is None:
                r.update(grade="skipped", reasons=["the reference render is empty"])
                continue
            heights[model] = body
            other = heights.get("hd")
            if model == "sd" and other and not 0.8 <= body / other <= 1.25:
                r.update(grade="skipped", reasons=[
                    f"Blizzard's Classic render shows another body than its Updated one (height {body} vs {other} px)"
                    ": a stale render, not this look"])
                continue
            yaw, _shot, _v = best_yaw(sess, body, ref)
            shot, grid = sess.shot(render_facing() + yaw, body)
            plain = None
            if any(x["slot"] in HELD for x in recipe["attachments"]):
                sess.redraw(bare(recipe))
                plain, _g = sess.shot(render_facing() + yaw, body, grid)
            cmp = imgcmp.compare(ref, shot, plain)
            placed = cmp.pop("_placed")
            sheet = out_dir / f"{case_id}-{model}.png"
            sheet.write_bytes(imgcmp.contact_sheet(ref, placed, cmp["gain"]))
            reasons = checks.recipe_checks(spec, recipe, got["missing"], effects if model == "hd" else {},
                                           inst.option_names, hidden) + checks.picture_checks(cmp)
            if hidden:
                names = ", ".join(checks.SLOT_NAMES.get(x, str(x)) for x in sorted(hidden))
                reasons.append(("pass", f"hidden in the game: {names}"))
            if updated_for_classic:
                reasons.append(("pass", "no Classic model for this race: the Updated one, as the game draws it"))
            if absent:
                reasons.append(("warn", f"{len(absent)} texture file(s) not on this disk"))
            r.update(grade=checks.grade(reasons), reasons=[f"{lvl}: {why}" for lvl, why in reasons],
                     yaw=round(yaw, 3), iou=cmp["iou"], regions=cmp["regions"], weapon_side=cmp["weapon_side"],
                     sheet=str(sheet), viewer_missing=sess.missing(), viewer_errors=sess.errors())
        finally:
            sess.close()
    return result


# ------------------------------------------------------------------ commands

def denied_terms() -> set[str]:
    try:
        return {ln.strip().lower() for ln in DENYLIST.read_text(encoding="utf-8").splitlines() if ln.strip()}
    except OSError:
        return set()


def next_id(src: dict) -> str:
    n = max([int(k[1:]) for k in src if k[1:].isdigit()] or [0]) + 1
    return f"c{n:02d}"


def cmd_add(a, st: store.Store) -> None:
    src = st.sources()
    if any(v.get("slug") == a.slug.lower() for v in src.values()):
        print("already a case")
        return
    cid = next_id(src)
    src[cid] = {"slug": a.slug.lower(), "region": a.region, "note": a.note or ""}
    st.save_sources(src)
    print(f"{cid} added: run `refresh {cid}`")


def cmd_refresh(a, st: store.Store) -> None:
    src = st.sources()
    api = store.Api(a.region, budget=a.budget)
    inst = Install(Path(a.wow_root)) if a.wow_root else None
    specs = {s["id"]: s for s in store.load_specs()}
    for cid in a.cases or sorted(src):
        if cid not in src:
            print(f"{cid}: no such case")
            continue
        day = store.today()
        into = st.root / "fetch" / cid / day
        got = api and store.fetch_character(api, src[cid]["slug"], into)
        for old in st.days(cid):
            if old != day:
                import shutil

                shutil.rmtree(st.root / "fetch" / cid / old)
        spec = store.spec_from(into)
        if spec is None:
            print(f"{cid}: Battle.net doesn't know it now ({got['missing']})")
            continue
        models = [m for m, f in (("updated", "hd.png"), ("classic", "sd.png")) if (into / f).is_file()]
        cl = case_cells(spec, inst, models)
        specs[cid] = store.anonymous(spec, cid, cl, specs.get(cid, {}).get("picked") or day)
        print(f"{cid}: {', '.join(got['saved'])}" + (f"; missing {got['missing']}" if got["missing"] else ""))
    store.save_specs([specs[k] for k in sorted(specs)])
    print(f"{api.calls} requests")


def cmd_discover(a, st: store.Store) -> None:
    """Fill the coverage cells from public guild rosters."""
    api = store.Api(a.region, budget=a.budget)
    inst = Install(Path(a.wow_root))
    deny = denied_terms() | {t.lower() for t in a.exclude}
    src = st.sources()
    have = set()
    for s in store.load_specs():
        # a case whose reference is someone else's covers nothing
        d, _why = st.latest(s["id"])
        if (src.get(s["id"]) or {}).get("foreign") or (d and store.foreign_render(d, "hd")):
            continue
        have |= set(s.get("cells") or [])
    known = {v["slug"] for v in src.values()}
    rng = random.Random(a.seed)
    pool = []
    for g in a.guild:
        if g.lower() in {x.lower() for x in a.exclude_guild}:
            continue
        body = api.get(f"/data/wow/guild/{g}/roster", api.profile_ns())
        for m in (body or {}).get("members") or []:
            ch = m.get("character") or {}
            slug = ch.get("slug") or ""
            words = {w for w in slug.lower().split("-") if w}
            if ch.get("level", 0) < a.min_level or not slug or slug in known or words & deny or slug in deny:
                continue
            pool.append((ch.get("playable_race", {}).get("id"), slug))
    rng.shuffle(pool)
    print(f"{len(pool)} candidates from {len(a.guild)} rosters")
    picked = 0
    tried_race: dict = {}
    try:
        for race, slug in sorted(pool, key=lambda p: tried_race.setdefault(p[0], rng.random())):
            if picked >= a.max_new:
                break
            if all(c in have for c in store.ALL_CELLS if not c.startswith("model:")):
                break
            race_done = all(f"race:{store.RACES.get(race)}/{x}" in have for x in store.SEXES)
            if race_done and rng.random() < 0.85:
                continue
            look = api.get(f"/profile/wow/character/{urllib.parse.quote(slug, safe='-')}/appearance", api.profile_ns())
            if not look:
                continue
            spec = {"race": (look.get("playable_race") or {}).get("id"),
                    "class": (look.get("playable_class") or {}).get("id"),
                    "sex": 1 if (look.get("gender") or {}).get("type") == "FEMALE" else 0,
                    "choices": [{"optionId": c["option"]["id"], "choiceId": c["choice"]["id"]}
                                for c in look.get("customizations") or []],
                    "items": store.items_of(look, None)}
            new = set(case_cells(spec, inst, ())) - have
            if not new:
                continue
            cid = next_id(src)
            src[cid] = {"slug": slug, "region": a.region, "note": "public roster"}
            have |= new
            picked += 1
            print(f"{cid}: fills {', '.join(sorted(new))}")
    except SystemExit as e:
        print(e)
    finally:
        st.save_sources(src)
    print(f"{picked} new cases, {api.calls} requests: run `refresh` next")


def cmd_run(a, st: store.Store) -> None:
    import render

    src = st.sources()
    specs = {s["id"]: s for s in store.load_specs()}
    ids = a.cases or sorted(src)
    stamp = time.strftime("%Y-%m-%dT%H%M")
    out = Path(a.out) if a.out else st.root / "runs" / stamp
    out.mkdir(parents=True, exist_ok=True)
    inst = Install(Path(a.wow_root))
    renderer = render.Renderer()
    results = []
    try:
        for cid in ids:
            fetched, why = st.latest(cid)
            if fetched is None:
                results.append({"case": cid, "cells": specs.get(cid, {}).get("cells", []),
                                "models": {"hd": {"grade": "skipped", "reasons": [why]}}})
                print(f"{cid}: {why}")
                continue
            spec = store.spec_from(fetched)
            if spec is None:
                results.append({"case": cid, "models": {"hd": {"grade": "skipped", "reasons": ["no profile"]}}})
                continue
            spec["cells"] = specs.get(cid, {}).get("cells") or case_cells(spec, inst)
            refs = {m: (fetched / f"{m}.png").read_bytes() for m in ("hd", "sd") if (fetched / f"{m}.png").is_file()}
            if a.models:
                refs = {m: v for m, v in refs.items() if m in a.models}
            t = time.time()
            marked = (src.get(cid) or {}).get("foreign") or {}
            foreign = {m: (f"marked by hand: {marked[m]}" if marked.get(m) else store.foreign_render(fetched, m))
                       for m in refs}
            res = evaluate(inst, renderer, spec, refs, out, cid, foreign)
            results.append(res)
            print(f"{cid} ({time.time() - t:.0f}s): " + "; ".join(
                f"{m} {r.get('grade')} {r.get('iou', '')} "
                + " | ".join(x for x in r.get("reasons", []) if not x.startswith("pass"))
                for m, r in res["models"].items()), flush=True)
    finally:
        renderer.close()
    report(results, out)
    print(f"report: {out / 'index.html'}")


def tally(results: list[dict]) -> dict:
    out = {"pass": 0, "warn": 0, "fail": 0, "skipped": 0}
    for r in results:
        for m in r["models"].values():
            out[m.get("grade", "fail")] = out.get(m.get("grade", "fail"), 0) + 1
    return out


def coverage(results: list[dict] | None = None, specs: list[dict] | None = None) -> dict[str, dict]:
    """cell -> {cases, ran: cases whose run wasn't skipped}."""
    specs = store.load_specs() if specs is None else specs
    ran = set()
    for r in results or []:
        if any(m.get("grade") != "skipped" for m in r["models"].values()):
            ran.add(r["case"])
    out = {c: {"cases": [], "ran": []} for c in store.ALL_CELLS}
    for s in specs:
        for c in s.get("cells") or []:
            out.setdefault(c, {"cases": [], "ran": []})["cases"].append(s["id"])
            if s["id"] in ran:
                out[c]["ran"].append(s["id"])
    return out


def report(results: list[dict], out: Path) -> None:
    (out / "report.json").write_text(json.dumps(results, indent=1, sort_keys=True), encoding="utf-8")
    t = tally(results)
    cov = coverage(results)
    rows = []
    for r in results:
        for m, x in r["models"].items():
            img = f"<img src='{html.escape(Path(x['sheet']).name)}' loading=lazy>" if x.get("sheet") else ""
            reasons = "<br>".join(html.escape(s) for s in x.get("reasons", []))
            rows.append(f"<tr class='{x.get('grade')}'><td>{r['case']}<br><small>{m}</small></td>"
                        f"<td>{x.get('grade')}</td><td>{x.get('iou', '')}</td><td>{reasons}"
                        f"<br><small>{html.escape(', '.join(r.get('cells', [])))}</small></td><td>{img}</td></tr>")
    cells = "".join(f"<tr><td>{c}</td><td>{', '.join(v['cases']) or '-'}</td><td>{', '.join(v['ran']) or '-'}</td></tr>"
                    for c, v in cov.items())
    page = ("<!doctype html><meta charset=utf-8><title>3D cases</title><style>body{font:14px system-ui;margin:16px}"
            "td{vertical-align:top;padding:4px 8px;border-bottom:1px solid #ddd}img{max-height:420px}"
            ".pass td:nth-child(2){color:#080}.warn td:nth-child(2){color:#a60}.fail td:nth-child(2){color:#c00}"
            "</style>"
            f"<h1>3D cases</h1><p>{t}</p><p>Each picture: Blizzard's render | ours | diff (red: only theirs, "
            "blue: only ours, orange: colour differs).</p>"
            f"<table>{''.join(rows)}</table><h2>Coverage</h2><table><tr><th>cell</th><th>cases</th><th>ran</th></tr>"
            f"{cells}</table>")
    (out / "index.html").write_text(page, encoding="utf-8")


def cmd_mark(a, st: store.Store) -> None:
    """Say a reference is another character's (seen on its contact sheet):
    the run skips it with this reason. Local only: it's about a character."""
    src = st.sources()
    if a.case not in src:
        raise SystemExit(f"{a.case}: no such case")
    marks = src[a.case].setdefault("foreign", {})
    for m in a.models or ["hd", "sd"]:
        if a.clear:
            marks.pop(m, None)
        else:
            marks[m] = a.why
    st.save_sources(src)


def cmd_coverage(a, st: store.Store) -> None:
    for c, v in coverage().items():
        print(f"{c:22} {', '.join(v['cases']) or '-'}")


def cmd_prune(a, st: store.Store) -> None:
    for p in st.prune():
        print(f"dropped {p}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", default=str(store.DEFAULT_STORE), help="where Battle.net's data and the runs live")
    ap.add_argument("--wow-root", default=os.environ.get("WOW_ROOT", DEFAULT_WOW), help="the folder with .build.info")
    ap.add_argument("--region", default="eu")
    ap.add_argument("--budget", type=int, default=400, help="at most this many Battle.net requests")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add", help="add one character (name-surname)")
    p.add_argument("slug")
    p.add_argument("--note", default="")
    p = sub.add_parser("discover", help="pick public characters from guild rosters to fill the cells")
    p.add_argument("--guild", action="append", default=[], required=True)
    p.add_argument("--exclude-guild", action="append", default=[])
    p.add_argument("--exclude", action="append", default=[], help="a name or surname never to pick")
    p.add_argument("--min-level", type=int, default=25)
    p.add_argument("--max-new", type=int, default=19)
    p.add_argument("--seed", type=int, default=1)
    p = sub.add_parser("refresh", help="fetch each case's Battle.net data and renders (dated)")
    p.add_argument("cases", nargs="*")
    p = sub.add_parser("run", help="draw, compare and grade")
    p.add_argument("cases", nargs="*")
    p.add_argument("--models", action="append", choices=("hd", "sd"))
    p.add_argument("--out", default="")
    p = sub.add_parser("mark", help="mark a case's reference as another character's render")
    p.add_argument("case")
    p.add_argument("why")
    p.add_argument("--models", action="append", choices=("hd", "sd"))
    p.add_argument("--clear", action="store_true")
    sub.add_parser("coverage", help="the cells and the cases filling them")
    sub.add_parser("prune", help="drop data older than 30 days")
    a = ap.parse_args(argv)
    st = store.Store(Path(a.store).expanduser())
    {"add": cmd_add, "mark": cmd_mark, "discover": cmd_discover, "refresh": cmd_refresh, "run": cmd_run,
     "coverage": cmd_coverage, "prune": cmd_prune}[a.cmd](a, st)


if __name__ == "__main__":
    main()

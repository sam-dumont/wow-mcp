"""The analysis' knowledge base: game reference and levelling knowledge,
searched with SQLite FTS5 (retrieval, not conclusions loaded up front).

Three sources, each document carrying its own `source`, `date` and `label`:

- `data/kb-reference.json.gz` (tools/kb/export.py): forever-db's quests,
  NPCs and items, and Polestar's own notes on its guides, dungeon runs and
  time model, quoted (GPL-3.0, Polestar's release in the file);
- Forever's client tables on wago.tools (product `wowf`, the latest build):
  spell names and descriptions, zones (AreaTable, with the levels of
  forever-db's quests there), talents, and the game's constraints: dungeon
  and raid level ranges (GroupFinderActivity), riding (mount items, riding
  skill), profession trainer tiers (SpellEffect). Fetched once a
  day at most and kept in the cache folder (MODEL3D_CACHE_DIR, shared with
  the 3D view and the item names);
- `data/kb/*.md`: short notes written from those sources, each saying so in
  its header (`label`).

Forever first; anything from Classic Era is labelled as such (D-345). No
website text: forever-db and Polestar are open (GPL-3.0), the client tables
are the game's own.

Two more inputs, both sourced statement by statement:

- `data/kb/rules.json`: the static rules code reads (riding tiers, rested
  rates, the quest XP share...), each with `source_url`, `source_type`,
  `date`, `confidence` and `scope` (forever | classic_fallback). Code reads
  a rule with `rule(name)`; a rule that isn't there is unknown, and the
  feature that needs it says so and skips (never a guess);
- `data/kb/research/`: the research catalogue, Markdown documents whose
  statements carry their sources, and JSON rules files in the same shape as
  rules.json (their rules win over rules.json's of the same name).

Every document carries where it comes from (`url`, `source_type`,
`confidence`, `scope`), so whoever reads the KB (the model driving the analysis,
the planner, the MCP tools kb_search and kb_rule) can cite it.
`shared(env)` is the one loaded copy a long-running process keeps (rebuilt
once a day at most).
"""

from __future__ import annotations

import contextlib
import csv
import gzip
import io
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

from wowstats import talent_hotfixes, upstream

_HERE = Path(__file__).resolve()
DATA_DIRS = (_HERE.parents[1] / "data", _HERE.parents[2] / "data")
DAY = 86400
GAME_TABLES = ("SpellName", "Spell", "AreaTable", "Talent", "ChrClasses", "GroupFinderActivity", "Item",
               "ItemSparse", "SpellEffect", "SkillLine", "SkillLineAbility")
SKILL_STEP = "44"  # SpellEffect.Effect: learning a profession's next tier (MiscValue_0 skill, base points the tier)
MOUNT = ("15", "5")  # Item ClassID, SubclassID: Miscellaneous, Mount
RIDING = "762"  # SkillLine


def data_file(name: str) -> Path | None:
    for d in DATA_DIRS:
        p = d / name
        if p.exists():
            return p
    return None


def parse_md(text: str, name: str) -> dict:
    """A note: `---` front matter (title, source, date, label), then the body."""
    meta, body = {}, text
    if text.startswith("---\n"):
        head, _, body = text[4:].partition("\n---\n")
        for line in head.splitlines():
            k, _, v = line.partition(":")
            if k.strip():
                meta[k.strip()] = v.strip()
    return {"id": f"note:{name}", "kind": "note", "title": meta.get("title") or name, "body": body.strip(),
            "source": meta.get("source", ""), "date": meta.get("date", ""), "label": meta.get("label", "")}


class GameTables:
    """Forever's client tables from wago.tools, as KB documents."""

    def __init__(self, wago_url: str, product: str, cache: Path | None, on: bool = True):
        self.wago, self.product, self.cache, self.on = wago_url.rstrip("/"), product, cache, on
        self.lock = threading.Lock()

    def docs(self) -> tuple[list[dict], str | None]:
        """(documents, build) from the cache when it is under a day old, else
        from wago.tools; ([], None) when neither has them."""
        if not self.on:
            return [], None
        with self.lock:
            cached = self._cached()
            if cached and time.time() - cached["checked"] < DAY:
                return cached["docs"], cached["build"]
            from wowstats.upstream import Upstream, build_of, builds_url, fetch, table_url
            from wowstats.upstream import write_file as _write

            try:
                build = build_of(fetch(builds_url(self.wago, self.product)))
                if cached and cached["build"] == build:
                    docs = cached["docs"]
                else:
                    tables = {}
                    for t in GAME_TABLES:
                        body = fetch(table_url(self.wago, t, build), timeout=180)
                        tables[t] = list(csv.DictReader(io.StringIO((body or b"").decode("utf-8-sig"))))
                    docs = game_docs(tables, f"Forever client tables {build} (wago.tools, product {self.product})")
                    if self.product == upstream.PRODUCT and build == talent_hotfixes.BUILD:
                        docs = talent_hotfixes.game_docs(docs)
            except (Upstream, ValueError, KeyError, TypeError):
                return (cached["docs"], cached["build"]) if cached else ([], None)
            if self.cache:
                with contextlib.suppress(OSError):
                    _write(self.cache / "kb-game.json.gz", gzip.compress(json.dumps(
                        {"build": build, "checked": time.time(), "docs": docs, "format": 2},
                        separators=(",", ":")).encode(),
                        mtime=0))
            return docs, build

    def _cached(self) -> dict | None:
        if not self.cache:
            return None
        p = self.cache / "kb-game.json.gz"
        with contextlib.suppress(OSError, ValueError, KeyError):
            d = json.loads(gzip.decompress(p.read_bytes()))
            d["checked"] = float(d["checked"])
            if d.get("format") != 2 and self.product == upstream.PRODUCT and d["build"] == talent_hotfixes.BUILD:
                d["docs"] = talent_hotfixes.game_docs(d["docs"])
            return d
        return None


def game_docs(tables: dict[str, list[dict]], source: str) -> list[dict]:
    """KB documents from the client tables: one per named spell, zone and talent."""
    names = {r["ID"]: r.get("Name_lang") or "" for r in tables.get("SpellName", [])}
    desc = {r["ID"]: r for r in tables.get("Spell", [])}
    classes = {r["ID"]: r.get("Name_lang") or r.get("Filename") or r["ID"] for r in tables.get("ChrClasses", [])}
    docs = []

    def clean(s: str) -> str:
        return re.sub(r"\s+", " ", s or "").strip()

    for sid, name in names.items():
        if not name:
            continue
        d = desc.get(sid) or {}
        body = f"Spell {sid}: {name}."
        if clean(d.get("NameSubtext_lang")):
            body += f" ({clean(d['NameSubtext_lang'])})"
        if clean(d.get("Description_lang")):
            body += f" Description: {clean(d['Description_lang'])}"
        if clean(d.get("AuraDescription_lang")):
            body += f" Aura: {clean(d['AuraDescription_lang'])}"
        docs.append({"id": f"spell:{sid}", "kind": "spell", "title": name, "body": body, "source": source,
                     "date": "", "label": "game data"})
    areas = {r["ID"]: r for r in tables.get("AreaTable", [])}
    for aid, r in areas.items():
        name = r.get("AreaName_lang") or r.get("ZoneName") or ""
        if not name:
            continue
        parent = areas.get(r.get("ParentAreaID") or "")
        body = (f"Area {aid}: {name}." + (f" Inside {parent.get('AreaName_lang')} (area {parent['ID']})."
                                           if parent and parent.get("AreaName_lang") else "")
                + (f" Exploration level {r['ExplorationLevel']}." if r.get("ExplorationLevel") not in (None, "", "0")
                   else ""))
        docs.append({"id": f"zone:{aid}", "kind": "zone", "title": name, "body": body, "source": source, "date": "",
                     "label": "game data"})
    for r in tables.get("Talent", []):
        spells = [r.get(f"SpellRank_{i}") for i in range(9)]
        spells = [s for s in spells if s and s != "0"]
        if not spells:
            continue
        name = names.get(spells[0], "")
        d = desc.get(spells[0]) or {}
        body = (f"Talent {r['ID']}: {name} ({classes.get(r.get('ClassID'), 'class ' + str(r.get('ClassID')))}, "
                f"tab {r.get('TabID')}, row {r.get('TierID')}, column {r.get('ColumnIndex')}, {len(spells)} ranks; "
                f"rank spells {', '.join(spells)}). {clean(d.get('Description_lang'))}")
        docs.append({"id": f"talent:{r['ID']}", "kind": "talent", "title": name or f"talent {r['ID']}", "body": body,
                     "source": source, "date": "", "label": "game data"})
    docs += rule_docs(tables, source)
    return docs


def _num(v) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError, OverflowError):
        return 0


def _money(c: int) -> str:
    g, rest = divmod(c, 10000)
    s, cp = divmod(rest, 100)
    return " ".join(f"{n}{u}" for n, u in ((g, "g"), (s, "s"), (cp, "c")) if n) or "0"


def rule_docs(tables: dict[str, list[dict]], source: str) -> list[dict]:
    """The game's own constraints, from the client tables: dungeon level
    ranges (Group Finder), riding (mount items, riding skill), profession
    tiers. Facts only; the analysis looks them up."""
    names = {r["ID"]: r.get("Name_lang") or "" for r in tables.get("SpellName", [])}
    docs = []
    for r in tables.get("GroupFinderActivity", []):
        lo, hi = _num(r.get("MinLevel")), _num(r.get("MaxLevelSuggestion"))
        name = r.get("FullName_lang") or ""
        if not name or not hi:
            continue
        size = _num(r.get("MaxPlayers"))
        kind = "raid" if size > 5 else "dungeon"
        docs.append({"id": f"{kind}:{r['ID']}", "kind": kind, "title": name, "source": source, "date": "",
                     "label": "game data",
                     "body": f"{name}: the Group Finder lists it for levels {lo} to {hi} (Forever client, "
                             f"GroupFinderActivity {r['ID']}; map {r.get('MapID')}"
                             + (f", up to {size} players" if size else "") + ")."})
    item = {r["ID"]: r for r in tables.get("Item", [])}
    sparse = {r["ID"]: r for r in tables.get("ItemSparse", [])}
    tiers: dict[tuple[int, int], list[str]] = {}
    for iid, r in item.items():
        if (r.get("ClassID"), r.get("SubclassID")) != MOUNT or iid not in sparse:
            continue
        sp = sparse[iid]
        tiers.setdefault((_num(sp.get("RequiredLevel")), _num(sp.get("BuyPrice"))), []).append(
            f"{sp.get('Display_lang')} ({iid})")
    riding = [(names.get(r.get("Spell"), "") or f"spell {r.get('Spell')}", r.get("Spell"),
               _num(r.get("MinSkillLineRank"))) for r in tables.get("SkillLineAbility", [])
              if r.get("SkillLine") == RIDING]
    if tiers or riding:
        lines = []
        for (lv, price), its in sorted(tiers.items(), key=lambda x: (x[0][0], x[0][1])):
            lines.append(f"{len(its)} mount items need level {lv}" + (f" and list a vendor price of {_money(price)}"
                                                                     if price else ", no vendor price")
                         + f" (e.g. {', '.join(sorted(its)[:3])})")
        body = ("Riding in the Forever client's tables. Mount items (Item class 15, subclass 5): "
                + "; ".join(lines) + ". "
                + ("Riding skill spells: " + "; ".join(f"{n} (spell {i}, from riding skill {k})"
                                                     for n, i, k in sorted(riding, key=lambda x: x[2])) + ". "
                   if riding else "")
                + "Vendor prices are the client's, before any reputation discount; the trainer's fee is not in the "
                  "client (see the riding note for the Classic Era numbers).")
        docs.append({"id": "rule:riding", "kind": "rule", "title": "Riding: levels and prices", "body": body,
                     "source": source, "date": "", "label": "game data"})
    skills = {r["ID"]: r.get("DisplayName_lang") or "" for r in tables.get("SkillLine", [])}
    steps: dict[str, list[tuple[int, str, str]]] = {}
    for r in tables.get("SpellEffect", []):
        if r.get("Effect") != SKILL_STEP:
            continue
        sk = skills.get(r.get("EffectMiscValue_0") or "")
        tier = _num(r.get("EffectBasePoints") or r.get("EffectBasePointsF"))
        if sk and tier:
            steps.setdefault(sk, []).append((tier, names.get(r.get("SpellID"), ""), r.get("SpellID")))
    for sk, ts in sorted(steps.items()):
        seen, parts = set(), []
        for tier, n, sid in sorted(ts):
            if tier in seen:
                continue
            seen.add(tier)
            parts.append(f"tier {tier}: {n or 'spell'} (spell {sid})")
        docs.append({"id": f"rule:profession:{sk.lower().replace(' ', '-')}", "kind": "rule",
                     "title": f"{sk}: trainer tiers", "source": source, "date": "", "label": "game data",
                     "body": f"{sk} trainer tiers in the Forever client: " + "; ".join(parts)
                             + ". The skill cap per tier is not in the client (see the professions note)."})
    return docs


QUEST_RE = re.compile(r"Level (\d+), needs level (\d+).*?area (\d+)\.")


def zone_levels(docs: list[dict]) -> dict[str, str]:
    """Per area id, a line on the levels of the quests given there, from
    forever-db's quests (the packaged KB)."""
    by: dict[str, list[tuple[int, int]]] = {}
    for d in docs:
        if d.get("kind") == "quest" and (m := QUEST_RE.search(d.get("body") or "")):
            by.setdefault(m.group(3), []).append((int(m.group(1)), int(m.group(2))))
    out = {}
    for area, qs in by.items():
        lv = sorted(q[0] for q in qs if q[0] > 0)
        if len(lv) < 3:
            continue
        mid = lv[len(lv) // 4], lv[(3 * len(lv)) // 4]
        out[area] = (f" Quests here (forever-db, {len(lv)}): levels {lv[0]} to {lv[-1]}, the middle half {mid[0]} "
                     f"to {mid[1]}; the lowest needs level {min(q[1] for q in qs)}.")
    return out


class KB:
    """The index: one in-memory SQLite database with an FTS5 table."""

    def __init__(self, docs: list[dict], meta: dict | None = None, db: sqlite3.Connection | None = None):
        self.meta = meta or {}
        self.lock = threading.Lock()
        if db is not None:  # an index saved by save(), opened read-only
            self.db = db
            return
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.execute("CREATE TABLE docs (id TEXT PRIMARY KEY, kind TEXT, title TEXT, body TEXT, source TEXT, "
                        "date TEXT, label TEXT, url TEXT, source_type TEXT, confidence TEXT, scope TEXT, "
                        "last_verified TEXT)")
        self.db.execute("CREATE VIRTUAL TABLE fts USING fts5(title, body, kind UNINDEXED, content='docs', "
                        "content_rowid='rowid', tokenize='unicode61 remove_diacritics 2')")
        seen = set()
        rows = []
        for d in docs:
            if d["id"] in seen:
                continue
            seen.add(d["id"])
            prov = provenance(d)
            rows.append((d["id"], d["kind"], d["title"], d["body"], d.get("source", ""), d.get("date", ""),
                         d.get("label", ""), prov["url"], prov["source_type"], prov["confidence"], prov["scope"],
                         prov.get("last_verified") or ""))
        self.db.executemany("INSERT INTO docs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        self.db.execute("INSERT INTO fts(rowid, title, body, kind) SELECT rowid, title, body, kind FROM docs")
        self.db.execute("CREATE TABLE meta (value TEXT)")
        self.db.execute("INSERT INTO meta VALUES (?)", (json.dumps(self.meta, default=list),))
        self.db.commit()

    def save(self, path: str) -> None:
        """The index as a file: each tool server of a run opens it read-only
        instead of building its own."""
        out = sqlite3.connect(path)
        with self.lock:
            self.db.backup(out)
        out.close()

    @classmethod
    def open(cls, path: str) -> KB:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        meta = json.loads(db.execute("SELECT value FROM meta").fetchone()[0])
        return cls([], meta, db)

    def contents(self) -> str:
        """A table of contents: the kinds with their counts, and the titles of
        every document that isn't one game record (guides, notes, models)."""
        with self.lock:
            kinds = self.db.execute("SELECT kind, COUNT(*), MIN(source), MIN(label) FROM docs GROUP BY kind "
                                    "ORDER BY kind").fetchall()
            rows = self.db.execute("SELECT kind, id, title FROM docs WHERE kind NOT IN "
                                   "('spell', 'item', 'quest', 'npc', 'zone', 'talent', 'kb-statement', "
                                   "'kb-contradiction', 'kb-open-question', 'static-rule') ORDER BY kind, id"
                                   ).fetchall()
        lines = [f"- {k}: {n} documents (source: {s}; {lab})" for k, n, s, lab in kinds]
        lines += ["", "Documents other than single game records, by kind:"]
        for kind in sorted({r[0] for r in rows}):
            lines.append(f"{kind}: " + "; ".join(f"{i} {t}" for k, i, t in rows if k == kind))
        rules = self.rules()
        if rules:
            groups: dict[str, list[str]] = {}
            for n in sorted(rules):
                head, _, rest = n.partition(".")
                groups.setdefault(head, []).append(rest or head)
            lines += ["", "Static rules (kb_rule <name> gives the value, its source, scope, confidence and the day it "
                          "was last checked; a name@lane is another lane's differing version, a contradiction), by "
                          "prefix:"] + [f"{h}.: {', '.join(ns)}" for h, ns in sorted(groups.items())]
        for lane, x in sorted((self.meta.get("research") or {}).items()):
            lines += ["", f"Research lane {lane} (as of {x.get('last_verified')}): {x.get('statements')} statements "
                          f"(kb-statement), {x.get('contradictions')} contradictions (kb-contradiction), "
                          f"{x.get('open_questions')} open questions (kb-open-question). Documents: "
                      + "; ".join(f"{d} {t}" for d, t in (x.get("documents") or {}).items())]
        return "\n".join(lines)
    def __len__(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM docs").fetchone()[0]

    def kinds(self) -> dict[str, int]:
        with self.lock:
            return dict(self.db.execute("SELECT kind, COUNT(*) FROM docs GROUP BY kind ORDER BY kind").fetchall())

    @staticmethod
    def _match(q: str) -> str:
        """Free text to an FTS5 query: words ANDed, each a quoted prefix-free term."""
        words = re.findall(r"[\w']+", q.lower())
        return " ".join('"' + w.replace('"', "") + '"' for w in words[:12])

    def search(self, query: str, kind: str | None = None, limit: int = 8, scope: str | None = None) -> list[dict]:
        m = self._match(query)
        if not m:
            return []
        limit = max(1, min(int(limit or 8), 20))
        with self.lock:
            sql = ("SELECT d.id, d.kind, d.title, snippet(fts, 1, '[', ']', ' ... ', 24), d.source, d.label, d.url, "
                   "d.source_type, d.date, d.confidence, d.scope, d.last_verified FROM fts "
                   "JOIN docs d ON d.rowid = fts.rowid WHERE fts MATCH ?")
            args: list = [m]
            if kind:
                sql += " AND d.kind = ?"
                args.append(kind)
            if scope:
                sql += " AND d.scope = ?"
                args.append(scope)
            sql += " ORDER BY bm25(fts, 4.0, 1.0) LIMIT ?"
            args.append(limit)
            rows = self.db.execute(sql, args).fetchall()
            if not rows and " " in m:  # nothing with every word: any word
                args[0] = " OR ".join(m.split(" "))
                rows = self.db.execute(sql, args).fetchall()
        return [{"id": r[0], "kind": r[1], "title": r[2], "snippet": r[3], "source": r[4], "label": r[5],
                 "url": r[6], "source_type": r[7], "date": r[8], "confidence": r[9], "scope": r[10],
                 "last_verified": r[11]} for r in rows]

    def get(self, ids: list[str], cap: int = 4000) -> list[dict]:
        out = []
        with self.lock:
            for i in ids[:10]:
                r = self.db.execute("SELECT id, kind, title, body, source, date, label, url, source_type, "
                                    "confidence, scope, last_verified FROM docs WHERE id = ?", (str(i),)).fetchone()
                if r:
                    out.append({"id": r[0], "kind": r[1], "title": r[2], "body": r[3][:cap], "source": r[4],
                                "date": r[5], "label": r[6], "url": r[7], "source_type": r[8], "confidence": r[9],
                                "scope": r[10], "last_verified": r[11]})
                else:
                    out.append({"id": str(i), "missing": True})
        return out

    def rule(self, name: str) -> dict | None:
        """A static rule by name (rules.json, or the research's rules), with
        its source; None when the KB has no sourced rule of that name. When
        another lane says otherwise (`<name>@<lane>`), the other versions
        come under `conflicts`: a contradiction stays explicit."""
        rules = self.meta.get("rules") or {}
        r = rules.get(name)
        if r is None:
            return None
        others = [v for k, v in sorted(rules.items()) if k.startswith(name + "@")]
        return {**r, "conflicts": others} if others else r

    def rules(self) -> dict:
        return dict(self.meta.get("rules") or {})

    def names(self, kind: str) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT title FROM docs WHERE kind = ?", (kind,)) if r[0]}


def packaged() -> tuple[list[dict], dict]:
    p = data_file("kb-reference.json.gz")
    if p is None:
        return [], {}
    d = json.loads(gzip.decompress(p.read_bytes()))
    meta = {"polestar": d.get("polestar"), "forever_db": d.get("forever_db"),
            "ui_map": {int(k): tuple(v) for k, v in (d.get("ui_map") or {}).items()}}
    return d.get("docs") or [], meta


def notes() -> list[dict]:
    d = data_file("kb")
    if d is None or not d.is_dir():
        return []
    return [parse_md(p.read_text(encoding="utf-8"), p.stem) for p in sorted(d.glob("*.md"))]


RULE_FIELDS = ("source_url", "source_type", "date", "confidence", "scope", "last_verified")
SCOPES = ("forever", "classic_fallback")


def load_rules(paths: list[Path], allow: dict | None = None) -> tuple[dict, list[str]]:
    """Rules files in order (later ones win): {name: rule}; a rule that fails
    the schema (wowstats.analysis.kbschema: a source field missing, an
    unknown scope or confidence, a domain off data/kb/sources.json) is left
    out, and said so."""
    from . import kbschema

    allow = kbschema.load_allow() if allow is None else allow
    out, bad = {}, []
    for p in paths:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            bad.append(f"{p.name}: {e}")
            continue
        strict = p.parent.name == "research"
        for name, r in ((d.get("rules") if isinstance(d, dict) else None) or {}).items():
            errs = kbschema.check(r, allow, strict_type=strict) if isinstance(r, dict) else ["not an object"]
            if errs:
                bad.append(f"{p.name}: {name}: {'; '.join(errs)}")
                continue
            out[name] = {**r, "name": name, "file": p.name}
    return out, bad


def research_records(allow: dict | None = None) -> tuple[list[dict], list[str]]:
    """The research lanes' statements, contradictions and open questions
    (data/kb/research/*.json, tools/kb/import_lane.py) as KB documents, each
    with its sources and last_verified; a record that fails the schema is
    left out, and said so."""
    from . import kbschema

    d = data_file("kb")
    root = (d / "research") if d else None
    if root is None or not root.is_dir():
        return [], []
    allow = kbschema.load_allow() if allow is None else allow
    docs, bad, files = [], [], []
    for p in sorted(root.glob("*.json")):
        try:
            files.append((p, json.loads(p.read_text(encoding="utf-8"))))
        except (OSError, ValueError) as e:
            bad.append(f"{p.name}: {e}")
    # A refresh (tools/kb/refresh.py) replaces a statement by naming it in
    # `supersedes`, or the old one names its replacement in `superseded_by`;
    # past its `expires` a statement is out. The weekly job
    # (tools/kb/lifecycle.py) removes them from the files; until it runs,
    # the loader already leaves them out.
    from datetime import UTC, datetime

    today = datetime.now(UTC).date()
    gone = kbschema.retired_ids([r for _, f in files for r in f.get("statements") or []], today)
    for p, f in files:
        lane = f.get("lane") or p.stem
        for key, kind in (("statements", "kb-statement"), ("contradictions", "kb-contradiction")):
            for r in f.get(key) or []:
                if r.get("id") in gone:
                    continue
                errs = kbschema.check(r, allow)
                if errs:
                    bad.append(f"{p.name}: {r.get('id')}: {'; '.join(errs)}")
                    continue
                title = (r.get("title") or "") + (" (contradiction)" if kind == "kb-contradiction" else "")
                docs.append({"id": r["id"], "kind": kind, "title": f"{lane} {r.get('doc') or ''} {title}".strip(),
                             "body": r["text"] + (" Sources: " + " ".join(r.get("sources") or []) if
                                                  len(r.get("sources") or []) > 1 else ""),
                             "source": r["source_url"], "date": r["date"], "label": f"research lane {lane}",
                             "url": r["source_url"], "source_type": r["source_type"],
                             "confidence": r["confidence"], "scope": r["scope"],
                             "last_verified": r["last_verified"], "lane": lane})
        for q in f.get("open_questions") or []:
            docs.append({"id": q.get("id") or f"{lane}:open", "kind": "kb-open-question",
                         "title": f"{lane}: open question", "body": q.get("text", "") + (
                             f" ({q['why']})" if q.get("why") else ""),
                         "source": f"research lane {lane}: an open question, no source answers it", "date": "",
                         "label": "no source answers this: unknown", "url": "", "source_type": "open question",
                         "confidence": "", "scope": "", "lane": lane})
    return docs, bad


def rule_docs_of(rules: dict) -> list[dict]:
    return [{"id": f"static-rule:{n}", "kind": "static-rule", "title": f"Rule {n}",
             "body": f"{r.get('statement') or ''} Value: {json.dumps(r.get('value'), ensure_ascii=False)}.",
             "source": r["source_url"], "date": r["date"], "label": r["confidence"], "url": r["source_url"],
             "source_type": r["source_type"], "confidence": r["confidence"], "scope": r["scope"],
             "last_verified": r.get("last_verified", "")}
            for n, r in sorted(rules.items())]


STATEMENT_URL = re.compile(r"https?://[^\s)>\]]+")


def research_docs() -> list[dict]:
    """The research catalogue's Markdown: one document per file, plus one per
    statement that names its source (a line or bullet with a URL; its
    `type:`, `date:`, `confidence:` and `scope:` read from the same line
    when written there, else from the file's front matter)."""
    d = data_file("kb")
    root = (d / "research") if d else None
    if root is None or not root.is_dir():
        return []
    out = []
    for p in sorted(root.glob("*.md")):
        doc = parse_md(p.read_text(encoding="utf-8"), p.stem)
        doc.update(id=f"research:{p.stem}", kind="research")
        front = {k: doc.get(k) for k in ("source_type", "confidence", "scope")}
        out.append(doc)
        for i, line in enumerate(doc["body"].splitlines(), 1):
            urls = STATEMENT_URL.findall(line)
            if not urls:
                continue
            meta = {k: (re.search(rf"\b{k}:\s*([\w.-]+)", line, re.I) or [None, None])[1]
                    for k in ("type", "date", "confidence", "scope")}
            text = re.sub(r"^[\s*-]+", "", line).strip()
            out.append({"id": f"research:{p.stem}:{i}", "kind": "research-statement",
                        "title": text[:80], "body": text, "source": urls[0], "date": meta["date"] or doc["date"],
                        "label": doc.get("label", ""), "url": urls[0],
                        "source_type": meta["type"] or front.get("source_type") or "research",
                        "confidence": meta["confidence"] or front.get("confidence") or "",
                        "scope": meta["scope"] or front.get("scope") or ""})
    return out


def rules_paths() -> list[Path]:
    d = data_file("kb")
    if d is None:
        return []
    return [p for p in [d / "rules.json"] + sorted((d / "research").glob("*.json") if (d / "research").is_dir()
                                                  else []) if p.exists()]


def research_summary() -> dict:
    """Per research lane: its title, last_verified and counts (the MCP's
    kb_topics and the analysis brief list them)."""
    d = data_file("kb")
    root = (d / "research") if d else None
    out = {}
    for p in sorted(root.glob("*.json")) if root and root.is_dir() else []:
        with contextlib.suppress(OSError, ValueError):
            f = json.loads(p.read_text(encoding="utf-8"))
            # A lane's research documents, by id. Statements folded in from
            # another lane or written by a model pass carry no document: they
            # count as statements, never as a nameless "" document.
            docs: dict[str, str] = {}
            for s in f.get("statements") or []:
                if s.get("doc"):
                    docs.setdefault(s["doc"], s.get("title") or "")
            out[f.get("lane") or p.stem] = {
                "title": f.get("title"), "last_verified": f.get("last_verified"),
                "statements": len(f.get("statements") or []), "rules": len(f.get("rules") or {}),
                "contradictions": len(f.get("contradictions") or []),
                "open_questions": len(f.get("open_questions") or []), "documents": docs}
    return out


POLESTAR_URL = "https://github.com/fitzforge/polestar/blob/{commit}/{path}"


def provenance(d: dict) -> dict:
    """Where a document comes from, for a citation: url, source_type,
    confidence, scope. Set on the document when it says (rules, research);
    otherwise from its kind and source line."""
    if d.get("source_type"):
        return {k: d.get(k) or "" for k in ("url", "source_type", "confidence", "scope", "last_verified")}
    kind, src, label = d.get("kind") or "", d.get("source") or "", d.get("label") or ""
    if kind in ("spell", "zone", "talent", "dungeon", "raid", "rule"):
        build = (re.search(r"tables ([\d.]+)", src) or [None, ""])[1]
        return {"url": f"https://wago.tools/db2?build={build}" if build else "https://wago.tools/db2",
                "source_type": "Forever client tables (DB2, wago.tools)", "confidence": "high", "scope": "forever",
                "last_verified": ""}
    if kind.startswith("polestar") or kind in ("quest", "npc", "item"):
        m = re.search(r"\(([0-9a-f]{7,40})\)[^,]*, GPL-3.0, ([^ ,]+)", src)
        url = POLESTAR_URL.format(commit=m.group(1), path=m.group(2)) if m else "https://github.com/fitzforge/polestar"
        classic = "Classic" in src or "Classic" in label
        return {"url": url, "source_type": "Polestar data (forever-db, guides, model)" if kind in (
            "quest", "npc", "item") or kind.startswith("polestar") else "Polestar data",
                "confidence": "low" if classic else "medium", "scope": "classic_fallback" if classic else "forever",
                "last_verified": ""}
    if kind == "note":
        return {"url": "", "source_type": "note written by an LLM from the sources it names (no URL)",
                "confidence": "low", "scope": "classic_fallback" if "Classic" in label else "forever",
                "last_verified": ""}
    return {"url": "", "source_type": "none", "confidence": "", "scope": "", "last_verified": ""}


_RULES: dict = {}
_SHARED_LOCK = threading.Lock()


def static_rule(name: str) -> dict | None:
    """One sourced rule without building the index (the Overview, the
    Dungeons panel and the gear advice read a few rules on every request):
    rules.json and the research rules, loaded once per process, with any
    other lane's version under `conflicts`. None: unknown."""
    with _SHARED_LOCK:
        if "rules" not in _RULES:
            _RULES["rules"] = load_rules(rules_paths())[0]
    rules = _RULES["rules"]
    r = rules.get(name)
    if r is None:
        return None
    others = [v for k, v in sorted(rules.items()) if k.startswith(name + "@")]
    return {**r, "conflicts": others} if others else r


_SHARED: dict = {}


def shared(env: dict | None = None) -> KB:
    """One KB per process, rebuilt once a day at most: the MCP tools, the
    planner and the MCP tools read this one; the nightly analysis builds its own."""
    with _SHARED_LOCK:
        k = _SHARED.get("kb")
        if k is None or time.time() - _SHARED.get("at", 0) > DAY:
            k = build(env)
            _SHARED.update(kb=k, at=time.time())
        return k


def build(env: dict | None = None, game: GameTables | None = None) -> KB:
    env = dict(os.environ if env is None else env)
    docs, meta = packaged()
    rules, bad = load_rules(rules_paths())
    research, refused = research_records()
    meta["rules"], meta["rules_refused"] = rules, bad + refused
    meta["research"] = research_summary()
    docs = docs + notes() + rule_docs_of(rules) + research_docs() + research
    if game is None:
        from wowstats.upstream import PRODUCT, WAGO_URL, allow

        cache = Path(env.get("MODEL3D_CACHE_DIR") or Path(tempfile.gettempdir()) / "wow-stats-model3d")
        try:
            cache.mkdir(parents=True, exist_ok=True)
        except OSError:
            cache = None
        # Wago's tables, like the item and talent catalogs: WOW_ITEMS=off (the
        # compose kit's small preset) means no download from there at all.
        off = ("off", "0", "false", "no")
        on = (env.get("KB_GAME_TABLES", "on").strip().lower() not in off
              and env.get("WOW_ITEMS", "on").strip().lower() not in off)
        game = GameTables(allow(env.get("MODEL3D_WAGO_URL", WAGO_URL)), env.get("MODEL3D_PRODUCT", PRODUCT), cache,
                          on=on)
    gdocs, build_id = game.docs()
    meta["game_build"] = build_id
    levels = zone_levels(docs)
    gdocs = [{**d, "body": d["body"] + levels[d["id"][5:]]} if d.get("kind") == "zone" and d["id"][5:] in levels
             else d for d in gdocs]
    return KB(docs + gdocs, meta)

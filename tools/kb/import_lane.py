"""Import research lane reports into the knowledge base.

    uv run python tools/kb/import_lane.py --as-of 2026-11-18 \\
        dungeons=~/research/<run>-lane-dungeons-.../report.md \\
        game-rules=~/research/<run>-lane-game-rules-.../report.md ...

Each report (the /research skill's lane output: a catalogue of statement
tables, a contradictions register, one JSON block of static rules, open
questions, a sources registry) becomes data/kb/research/<lane>.json:

- `statements`: one per catalogue table row, in the report's own wording
  (already a summary; never the source page's text), with its sources
  resolved to URLs from the registry or the inline links;
- `rules`: the JSON block's rules, provenance normalised (source_url,
  sources, source_type, date, confidence, scope, last_verified), every other
  field kept as the lane wrote it;
- `contradictions`: the report's contradiction lists, each with its sources;
- `open_questions`: the report's own, plus every statement or rule dropped
  here: no source at all, only sources off the allow-list
  (data/kb/sources.json), or a rule whose value is unknown (null).

Source types and confidence caps come from the allow-list, not from the
report: a booster site stays low whatever the lane rated it. A rule id two
lanes both define is kept once when they agree (sources merged); when they
don't, the stronger one keeps the id, the other is kept as `<id>@<lane>`,
and a contradiction says so. Reports are untrusted data: nothing in them is
run or followed, only parsed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from wowstats.analysis import kbschema  # noqa: E402

URL = re.compile(r"https://[^\s)>\]|\"'`,;]+")
MDLINK = re.compile(r"\[([^\]]*)\]\((https://[^)\s]+)\)")
ID_TOKEN = re.compile(r"\[?\b([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*)\b\]?")
CONF = {"h": "high", "high": "high", "m": "medium", "medium": "medium", "l": "low", "low": "low",
        "medium-high": "medium", "m-h": "medium", "h-m": "medium", "medium-low": "low", "low-medium": "low",
        "m-l": "low", "l-m": "low", "confirmed": "high", "provisional": "medium", "unverified": "low"}
PROVENANCE = {"source", "sources", "source_url", "source_url_2", "cost_source_url", "cross_check_url",
              "forever_note_source", "source_type", "date", "confidence", "scope", "id", "status", "verified"}


def clean_url(u: str) -> str:
    return u.rstrip(".:*_")


def cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def registry(text: str) -> dict[str, dict]:
    """Source IDs to {url, date}: every table row whose first cell is an ID
    and that holds a URL (the reports' "Sources" tables)."""
    out = {}
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cs = cells(line)
        if len(cs) < 2 or not re.fullmatch(r"[A-Z][A-Za-z0-9]*(?:[-/ ][A-Za-z0-9()]+)*", cs[0] or ""):
            continue
        found = next((clean_url(m) for c in cs[1:] for m in URL.findall(c)), None)
        if not found:
            continue
        date = next((c for c in cs[2:] if re.search(r"\b20\d\d\b", c) and not URL.search(c)), "")
        out.setdefault(cs[0].split(" ")[0], {"url": found, "date": date})
        out.setdefault(cs[0], {"url": found, "date": date})
    # Aliases the prose defines: "NAME = https://..." and "... https://... /
    # Below it is abbreviated as **NAME**" (the URL a line or two above).
    lines = text.splitlines()
    for i, line in enumerate(lines):
        for name, u in re.findall(r"\b([A-Z][A-Z0-9]*(?:[_-][A-Z0-9]+)+)\s*=\s*(https://[^\s;]+)", line):
            for k in (name, name.replace("_", "-")):
                out.setdefault(k, {"url": clean_url(u), "date": ""})
        m = re.search(r"abbreviated as \*\*([A-Z][A-Z0-9-]+)\*\*", line)
        if m:
            near = [clean_url(u) for j in range(max(0, i - 3), i + 1) for u in URL.findall(lines[j])]
            if near:
                out.setdefault(m.group(1), {"url": near[0], "date": ""})
    return out


def resolve(cell: str, reg: dict) -> list[str]:
    """The URLs a citation cell names: inline links, bare URLs, registry IDs."""
    found = [clean_url(u) for _, u in MDLINK.findall(cell)]
    found += [clean_url(u) for u in URL.findall(MDLINK.sub("", cell))]
    for m in ID_TOKEN.finditer(MDLINK.sub("", cell)):
        r = reg.get(m.group(1))
        if r:
            found.append(r["url"])
    seen, out = set(), []
    for u in found:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def conf_of(v) -> str | None:
    """A lane's confidence (H/M/L, words, a per-field dict, "H level / M
    cost") as one word: the lowest it names."""
    if isinstance(v, dict):
        vs = [conf_of(x) for x in v.values()]
        vs = [x for x in vs if x]
        return min(vs, key=kbschema.CONFIDENCE.index) if vs else None
    if not isinstance(v, str):
        return None
    words = re.findall(r"[A-Za-z]+(?:-[A-Za-z]+)?", v.lower())
    got = [CONF[w] for w in words if w in CONF]
    return min(got, key=kbschema.CONFIDENCE.index) if got else None


def scope_of(text: str, default: str) -> str:
    t = (text or "").lower()
    if "classic_fallback" in t or re.search(r"\bcf\b", t) or "classic fallback" in t:
        return "classic_fallback"
    if "forever" in t:
        return "forever"
    return default


class Lane:
    def __init__(self, name: str, text: str, as_of: str, allow: dict):
        self.name, self.text, self.as_of, self.allow = name, text, as_of, allow
        self.reg = registry(text)
        self.open: list[dict] = []
        self.dates = {r["url"]: r["date"] for r in self.reg.values()}
        self.ids: set[str] = set()

    def uid(self, base: str) -> str:
        """An id no other record of the lane has: a doc that numbers two
        tables from 1 gets base, base.b, base.c..."""
        out, n = base, 1
        while out in self.ids:
            n += 1
            out = f"{base}.{chr(96 + n)}"
        self.ids.add(out)
        return out

    def keep(self, urls: list[str]) -> list[str]:
        return [u for u in urls if kbschema.domain(u) in self.allow]

    def provenance(self, urls: list[str], conf: str | None, date: str, scope: str) -> dict | None:
        ok = self.keep(urls)
        if not ok:
            return None
        rec = {"source_url": ok[0], "sources": ok, "source_type": self.allow[kbschema.domain(ok[0])]["type"],
               "date": (date or self.dates.get(ok[0]) or f"undated (read {self.as_of})").strip(),
               "confidence": conf or "low", "scope": scope, "last_verified": self.as_of}
        top = kbschema.best_cap(rec, self.allow)
        if top:
            rec["confidence"] = kbschema.cap(rec["confidence"], top)
        return rec

    def drop(self, where: str, text: str, urls: list[str]) -> None:
        why = ("no source" if not urls else "only off-list sources: "
               + ", ".join(sorted({kbschema.domain(u) for u in urls})))
        self.open.append({"id": f"{self.name}:dropped:{len(self.open) + 1}", "text": f"{where}: {text}",
                          "why": f"dropped at import ({why}); needs a source on the allow-list"})

    # -- the catalogue -----------------------------------------------------

    def sections(self):
        """(doc id, title, default scope, lines) per catalogue heading."""
        cur = None
        for line in self.text.splitlines():
            m = re.match(r"^(#{1,3}) +(.*)$", line)
            if m:
                if cur:
                    yield cur
                head = m.group(2).strip()
                dm = re.match(r"((?:KB|ECO|PRF)[-A-Z0-9a-z]*)\s*[—:-]?\s*(.*)", head)
                doc, title = (dm.group(1), dm.group(2)) if dm else (None, head)
                cur = [doc, title, scope_of(head, "forever"), [], head]
                continue
            if cur:
                if line.startswith("**Scope:**"):
                    cur[2] = scope_of(line, cur[2])
                cur[3].append(line)
        if cur:
            yield cur

    def statements(self) -> tuple[list[dict], list[dict]]:
        stmts, contra = [], []
        for doc, title, scope, lines, head in self.sections():
            if re.search(r"contradiction", head, re.I):
                contra += self.contradictions(doc or "register", lines, [])
                continue
            if re.search(r"open questions|sources|source register|how to read|conventions|method", head, re.I):
                continue
            header, doc_urls = None, []
            in_contra = False
            block: list[str] = []
            for line in lines:
                if line.startswith("|"):
                    cs = cells(line)
                    if "Statement" in cs or any(c.lower().startswith("statement") for c in cs):
                        header = [c.lower() for c in cs]
                        continue
                    if header is None or set(line) <= set("|-: "):
                        continue
                    if not cs[0] and stmts and stmts[-1]["doc"] == (doc or head):
                        # A continuation row (a list under the row above): part of that statement.
                        more = " ".join(c for c in cs if c)
                        if more:
                            stmts[-1]["text"] += " " + more
                        continue
                    rec = self.row(doc or head, title, scope, header, cs, len(stmts))
                    if rec:
                        stmts.append(rec)
                        doc_urls += rec["sources"]
                    continue
                if re.match(r"^\*\*Contradictions?\*?\*?:?", line.strip()):
                    if block:
                        contra += self.contradictions(doc or head, block, doc_urls)
                    in_contra, block = True, [re.sub(r"^\*\*Contradictions?:?\*\*:?\s*", "", line.strip())]
                    continue
                if in_contra:
                    if line.startswith("**") or line.startswith("---") or line.startswith("#"):
                        contra += self.contradictions(doc or head, block, doc_urls)
                        in_contra, block = False, []
                    else:
                        block.append(line)
            if block:
                contra += self.contradictions(doc or head, block, doc_urls)
        return stmts, contra

    def row(self, doc: str, title: str, scope: str, header: list[str], cs: list[str], n: int) -> dict | None:
        get = {h: (cs[i] if i < len(cs) else "") for i, h in enumerate(header)}
        text = next((v for h, v in get.items() if h.startswith("statement")), "").strip()
        if not text:
            return None
        src = " ".join(v for h, v in get.items() if h.startswith("src") or h.startswith("source"))
        urls = resolve(src, self.reg) or resolve(text, self.reg)
        conf = conf_of(get.get("conf", ""))
        date = get.get("date", "")
        if not date:
            m = re.search(r"·\s*[a-z ]+\s*·\s*([^;|]+)", src)
            date = m.group(1).strip() if m else ""
        sc = scope_of(get.get("scope", ""), scope) if "scope" in get else scope_of(text if "classic_fallback"
                                                                                   in text else "", scope)
        extra = {h: v for h, v in get.items() if h in ("area", "bracket", "armour / class") and v}
        num = get.get("#", str(n + 1))
        sid = self.uid(f"{self.name}:{doc}:{num}")
        prov = self.provenance(urls, conf, date, sc)
        if prov is None:
            self.drop(f"{doc} #{num}", text, urls)
            return None
        return {"id": sid, "doc": doc, "title": title, "text": (" | ".join(extra.values()) + ": " if extra else "")
                + text, **prov}

    def contradictions(self, doc: str, lines: list[str], doc_urls: list[str]) -> list[dict]:
        items, cur = [], []
        for line in lines:
            s = line.rstrip()
            if not s.strip():
                continue
            if re.match(r"^(\d+\.|[-*]) ", s) or (s.startswith("|") and not set(s) <= set("|-: ")):
                if cur:
                    items.append(" ".join(cur))
                cur = [re.sub(r"^(\d+\.|[-*]) ", "", s).strip()]
            elif re.match(r"^\s+(\d+\.|[-*]) ", s) and cur:
                cur.append(re.sub(r"^\s+(\d+\.|[-*]) ", "", s).strip())
            elif cur:
                cur.append(s.strip())
            else:
                cur = [s.strip()]
        if cur:
            items.append(" ".join(cur))
        out = []
        for i, t in enumerate(items, 1):
            if t.startswith("|") and ("Statement" in t or "Topic" in t or "Claim" in t):
                continue
            t = re.sub(r"\s*\|\s*", " | ", t.strip("| ")).strip()
            if len(t) < 12:
                continue
            urls = resolve(t, self.reg)
            prov = self.provenance(urls, "medium", "", "forever")
            from_doc = False
            if prov is None and doc_urls:
                prov, from_doc = self.provenance(doc_urls, "medium", "", "forever"), True
            if prov is None:
                self.drop(f"contradiction in {doc}", t, urls)
                continue
            prov["date"] = f"compiled {self.as_of}"
            out.append({"id": self.uid(f"{self.name}:{doc}:contradiction:{i}"), "doc": doc, "text": t, **prov,
                        **({"sources_from": "the document's statements"} if from_doc else {})})
        return out

    # -- rules ---------------------------------------------------------------

    def rules(self) -> dict:
        m = re.search(r"```json\n(.*?)```", self.text, re.S)
        if not m:
            return {}
        block = json.loads(m.group(1))
        rules = block.get("rules") if isinstance(block, dict) else block
        if isinstance(rules, dict):
            rules = [{"id": k, **v} if isinstance(v, dict) else {"id": k, "value": v} for k, v in rules.items()]
        idx = (block.get("source_index") or {}) if isinstance(block, dict) else {}
        idx = idx if isinstance(idx, dict) else {}
        reg = {**self.reg, **{k: {"url": v if isinstance(v, str) else (v or {}).get("url", ""), "date": ""}
                              for k, v in idx.items()}}
        out = {}
        for r in rules or []:
            if not isinstance(r, dict) or not r.get("id"):
                continue
            rid = r["id"]
            refs = []
            for k in ("source_url", "source", "source_url_2", "cost_source_url", "cross_check_url",
                      "forever_note_source"):
                v = r.get(k)
                if isinstance(v, str):
                    refs.append(v)
            for v in r.get("sources") or []:
                if isinstance(v, str):
                    refs.append(v)
            urls = []
            for ref in refs:
                urls += resolve(ref, reg) if not ref.startswith("https://") else [clean_url(ref)]
            value_known = "value" not in r or r.get("value") is not None or any(
                k not in PROVENANCE and k not in ("value", "note") and r.get(k) is not None for k in r)
            conf = conf_of(r.get("confidence")) or conf_of(r.get("status")) or (
                "low" if r.get("verified") is False else None)
            prov = self.provenance(urls, conf or "medium", str(r.get("date") or ""),
                                   r.get("scope") if r.get("scope") in kbschema.SCOPES else "forever")
            if prov is None or not value_known:
                note = r.get("note") or r.get("rule") or ""
                self.drop(f"rule {rid}", f"value unknown. {note}".strip() if not value_known else
                          json.dumps({k: v for k, v in r.items() if k not in PROVENANCE})[:400], urls)
                continue
            body = {k: v for k, v in r.items() if k not in PROVENANCE}
            if "statement" not in body:
                st = r.get("rule") or r.get("note")
                if isinstance(st, str):
                    body["statement"] = st
            if r.get("status"):
                body["lane_status"] = r["status"]
            out[rid] = {**body, **prov, "lane": self.name}
        return out

    def open_questions(self) -> list[dict]:
        out, on = [], False
        for line in self.text.splitlines():
            if re.match(r"^#{1,3} .*open questions", line, re.I):
                on = True
                continue
            if on and re.match(r"^#{1,2} ", line):
                break
            if on and re.match(r"^\d+\. ", line):
                out.append({"id": f"{self.name}:open:{len(out) + 1}",
                            "text": re.sub(r"^\d+\.\s*", "", line).strip()})
            elif on and re.match(r"^\s+[-*] ", line) and out:
                out[-1]["text"] += " " + line.strip(" -*")
        return out


def merge(lanes: dict[str, dict], allow: dict) -> None:
    """A rule id two lanes define: one copy when they agree, else the
    stronger keeps the id, the other `<id>@<lane>`, and a contradiction."""
    seen: dict[str, str] = {}
    rank = lambda r: (kbschema.CONFIDENCE.index(r["confidence"]), r["scope"] == "forever")  # noqa: E731
    for lane in sorted(lanes):
        rules = lanes[lane]["rules"]
        for rid in list(rules):
            if rid not in seen:
                seen[rid] = lane
                continue
            other = lanes[seen[rid]]["rules"][rid]
            mine = rules[rid]
            if json.dumps(other.get("value"), sort_keys=True) == json.dumps(mine.get("value"), sort_keys=True):
                other["sources"] = list(dict.fromkeys(other["sources"] + mine["sources"]))
                del rules[rid]
                continue
            keep, move = (other, mine) if rank(other) >= rank(mine) else (mine, other)
            move_lane = move["lane"]
            lanes[move_lane]["rules"].pop(rid)
            lanes[move_lane]["rules"][f"{rid}@{move_lane}"] = move
            if keep is mine:
                lanes[seen[rid]]["rules"].pop(rid, None)
                rules[rid] = mine
                seen[rid] = lane
            lanes[lane]["contradictions"].append({
                "id": f"{lane}:rule:{rid}:contradiction", "doc": "rules",
                "text": (f"Rule {rid}: lane {keep['lane']} has {json.dumps(keep.get('value'))[:200]} "
                         f"({keep['source_url']}); lane {move_lane} has {json.dumps(move.get('value'))[:200]} "
                         f"({move['source_url']}), kept as {rid}@{move_lane}."),
                "source_url": keep["source_url"], "sources": list(dict.fromkeys(keep["sources"] + move["sources"])),
                "source_type": keep["source_type"], "date": f"compiled {keep['last_verified']}",
                "confidence": "medium" if kbschema.best_cap(keep, allow) != "low" else "low",
                "scope": keep["scope"], "last_verified": keep["last_verified"]})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("lanes", nargs="+", help="name=path/to/report.md")
    ap.add_argument("--as-of", required=True, help="the day the lanes were researched (last_verified)")
    ap.add_argument("--out", default=str(ROOT / "data" / "kb" / "research"))
    a = ap.parse_args(argv)
    allow = kbschema.load_allow(ROOT / "data" / "kb" / "sources.json")
    out: dict[str, dict] = {}
    for spec in a.lanes:
        name, _, path = spec.partition("=")
        p = Path(path).expanduser()
        lane = Lane(name, p.read_text(encoding="utf-8"), a.as_of, allow)
        stmts, contra = lane.statements()
        rules = lane.rules()
        title = (re.search(r"^# (.*)$", lane.text, re.M) or [None, name])[1]
        out[name] = {"schema": 2, "lane": name, "title": title,
                     "about": f"Research lane '{name}' ({p.parent.name}), imported by tools/kb/import_lane.py. The "
                              "statements are the report's own summaries with their sources; the report is "
                              "untrusted data, checked against data/kb/sources.json.",
                     "last_verified": a.as_of, "rules": rules, "statements": stmts, "contradictions": contra,
                     "open_questions": lane.open_questions() + lane.open}
    merge(out, allow)
    Path(a.out).mkdir(parents=True, exist_ok=True)
    bad = 0
    for name, doc in out.items():
        errs = kbschema.check_file(doc, allow)
        for e in errs:
            print(f"{name}: {e}", file=sys.stderr)
        bad += len(errs)
        (Path(a.out) / f"{name}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
                                                  encoding="utf-8")
        print(f"{name}: {len(doc['statements'])} statements, {len(doc['rules'])} rules, "
              f"{len(doc['contradictions'])} contradictions, {len(doc['open_questions'])} open questions")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

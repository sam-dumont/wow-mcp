"""The refresh job's checks before it takes a new addon release on its own
(.github/workflows/kb-refresh.yaml, watch mode; docs/kb.md).

    uv run python tools/kb/bump.py polestar v0.4.1.1 --why why.json   # check, then move the tag
    uv run python tools/kb/bump.py addons --why why.json --handoff seen.json   # check the addons' contracts
    uv run python tools/kb/bump.py foreverdb --why why.json --handoff seen.json --summary fdb.md
    uv run python tools/kb/bump.py golden --as foreverdb --why why.json   # take moved upgrades only

What an addon writes is what the shipper reads, so a release is taken when
what we depend on is unchanged between the known tag and the new one, read
from the repository at both tags (GitHub's API through `gh`):

- the SavedVariables lines of the addon's .toc (a renamed or dropped saved
  table breaks the import);
- for an addon with a `contract` in data/kb/watch.json, the source lines the
  shipper relies on (Questie: `Questie.db.char.complete = `);
- for Polestar, every LICENSE, NOTICE and COPYING file (THIRD_PARTY.md copies
  its notices: a licence change needs a person). Its data notices
  (LICENSE-DATA.txt) carry each source's revision and the counts of names
  taken, which move with every data build: a line that changed only there
  (a `Revision:` line, or numbers and plurals on a line naming no licence
  term) is not a licence change. v0.4.2.1 (2026-11-21) was refused for
  exactly that, and the watch never took it.

Release notes are never read: a release's text is untrusted, and nothing in
it is run or followed. The tag itself goes through watch.TAG (a plain
version) before it reaches this script. A refusal is written to `--why`
({kind: reason}), for the kind's issue; the job then leaves that kind out of
its PR. `addons --handoff` writes the release each addon was checked at
({"addons": {name: tag}}): `watch.py --accept addons --handoff` records
exactly those, never a newer release that came out meanwhile, and an addon
whose release couldn't be read isn't in it (it stays pending, the next run
checks it). `polestar` also writes the new tag to tools/polestar-tag and the
clone lines that name it; the workflow then reruns the exports, and
`regen` the test data derived from them.

Polestar is the source of truth (owner, 2026-11-22): what its release says
(route times, levels, gear) is taken as it is, and the test data moves with
it. Only the licence terms, a saved table, an export or a generator that
fails stop a bump. v0.4.2.1 was refused because its routes to 60 got 16-25 h
shorter, the release's own rebuilt 50-60 routes. A client build's regen still
refuses test data that moved: that's a game build, not Polestar's word.

`foreverdb` is ForeverDB.net's addon data (tools/foreverdb_sources/export.py,
owner 2026-11-24): the only place the refresh fetches it. It downloads the
manifest's files (each SHA-256 checked), then refuses when a file's format
comments (the export's contract) or one of the licence files
(data/foreverdb/LICENSE.txt, NOTICE.txt, COPYING.txt) isn't byte for byte
what is committed: both need a person. Otherwise it reruns both exports
(item sources and the stat weights) from that download, writes the
fingerprint it took to `--handoff` and what moved to `--summary` (for the
PR). Like Polestar, its data is taken as it is: `regen --as foreverdb`
accepts the test data that moved. `golden --as foreverdb` then takes the
golden characters' moved upgrades into tests/slices/golden.json, and only
those (at most GOLDEN_UPGRADES of them): a dungeon, a raid prep, a plan or a
class meta key that went away still stops it, for the behaviour gate.
"""

from __future__ import annotations

import argparse
import collections
import difflib
import gzip
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import watch  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TAG_FILE = ROOT / "tools" / "polestar-tag"
POLESTAR_TOC = "addon/Polestar/Polestar.toc"
LICENCE = re.compile(r"(^|/)(LICEN[CS]E|NOTICE|COPYING)[^/]*$", re.I)
NAME = re.compile(r"[A-Za-z0-9_]+")
SAFE = re.compile(r"[^\w./+ -]")
PLAIN = re.compile(r"[^\w./+ :,=-]")  # a tool's last line in an issue: no markup
# A line that names one of these is about the terms: any change to it needs a person.
LICENCE_TERMS = re.compile(r"licen[cs]|copyright|\(c\)|©|\bgpl|\bmit\b|\bbsd|apache|\bmpl|\bcc\b|cc[ -]by|"
                           r"creative commons|public domain|rights|permission|warrant|trademark", re.I)
# Files that name the Polestar tag in a clone line, and THIRD_PARTY.md's "Polestar <tag>".
CLONE = re.compile(r"(--branch )(v[\d.]+)( https://github\.com/fitzforge/polestar)")


class Repo:
    """Files of a GitHub repository at a ref, through `gh api` (GH_TOKEN)."""

    def _api(self, path: str, raw: bool = False) -> str:
        cmd = ["gh", "api", path] + (["-H", "Accept: application/vnd.github.raw"] if raw else [])
        return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout

    def file(self, repo: str, path: str, ref: str) -> str | None:
        try:
            return self._api(f"repos/{repo}/contents/{urllib.parse.quote(path)}?ref={urllib.parse.quote(ref, safe='')}",
                             raw=True)
        except subprocess.CalledProcessError:
            return None

    def tree(self, repo: str, ref: str) -> dict[str, str] | None:
        """{path: blob sha}, or None when GitHub cut the listing short."""
        try:
            t = json.loads(self._api(f"repos/{repo}/git/trees/{urllib.parse.quote(ref, safe='')}?recursive=1"))
        except (subprocess.CalledProcessError, ValueError):
            return None
        if t.get("truncated"):
            return None
        return {e["path"]: e["sha"] for e in t.get("tree") or [] if e.get("type") == "blob"}


def saved_variables(toc: str) -> dict[str, list[str]]:
    """{"SavedVariables": [names], "SavedVariablesPerCharacter": [...]} of a .toc."""
    out: dict[str, list[str]] = {}
    for line in toc.splitlines():
        m = re.match(r"##\s*(SavedVariables(?:PerCharacter)?)\s*:(.*)", line.strip())
        if m:
            out[m.group(1)] = sorted(NAME.findall(m.group(2)))
    return out


def toc_change(repo_api, repo: str, toc: str, old: str, new: str) -> str:
    """Why the addon's saved tables changed between two tags ("" when they didn't)."""
    a, b = repo_api.file(repo, toc, old), repo_api.file(repo, toc, new)
    if a is None or b is None:
        return f"`{toc}` couldn't be read at {'the known tag' if a is None else 'the new tag'}"
    sa, sb = saved_variables(a), saved_variables(b)
    if sa != sb:
        return f"`{toc}` saves other tables: {json.dumps(sa)} at the known tag, {json.dumps(sb)} at the new one"
    return ""


def check_addon(repo_api, addon: dict, old: str, new: str) -> list[str]:
    c = addon.get("contract") or {}
    why = []
    if c.get("toc"):
        w = toc_change(repo_api, addon["repo"], c["toc"], old, new)
        why += [w] if w else []
    for path, needles in (c.get("files") or {}).items():
        text = repo_api.file(addon["repo"], path, new)
        if text is None:
            why.append(f"`{path}` is gone at the new tag")
            continue
        why += [f"`{path}` no longer has `{n}` (shipper/wow_stats.py reads what it saves)"
                for n in needles if n not in text]
    return why


def _shape(line: str) -> str:
    """A line with its numbers and revision ids as one token, plurals as singulars."""
    s = re.sub(r"[0-9a-f]*\d[0-9a-f.,-]*", "#", line.strip().lower())
    return re.sub(r"(\w)s\b", r"\1", s)


def _bookkeeping(a: str, b: str) -> bool:
    """Whether a changed line only moved a source's revision or a count."""
    key_a, key_b = (x.split(":", 1)[0].strip().lower() if ":" in x else "" for x in (a, b))
    if key_a == key_b == "revision":
        return True
    return not LICENCE_TERMS.search(a + " " + b) and _shape(a) == _shape(b)


def terms_changed(old: str, new: str) -> bool:
    """Whether two versions of a licence or notice file differ beyond
    bookkeeping lines (revisions, counts): a line added, removed or reworded
    is a change."""
    a, b = old.splitlines(), new.splitlines()
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        if op != "replace" or i2 - i1 != j2 - j1:
            return True
        if not all(_bookkeeping(x, y) for x, y in zip(a[i1:i2], b[j1:j2], strict=True)):
            return True
    return False


def check_polestar(repo_api, repo: str, old: str, new: str) -> list[str]:
    why = []
    w = toc_change(repo_api, repo, POLESTAR_TOC, old, new)
    why += [w] if w else []
    a, b = repo_api.tree(repo, old), repo_api.tree(repo, new)
    if a is None or b is None:
        return why + ["the licence files couldn't be listed (GitHub's tree was cut short or unreadable)"]
    la = {p: s for p, s in a.items() if LICENCE.search(p)}
    lb = {p: s for p, s in b.items() if LICENCE.search(p)}
    changed = []
    for p in sorted(set(la) | set(lb)):
        if la.get(p) == lb.get(p):
            continue
        ta, tb = (repo_api.file(repo, p, ref) if p in tree else None for ref, tree in ((old, la), (new, lb)))
        if ta is None or tb is None or terms_changed(ta, tb):
            changed.append(p)
    if changed:
        why.append("licence or notice files changed beyond source revisions and counts (THIRD_PARTY.md copies "
                   "them): "
                   + ", ".join(f"`{SAFE.sub('', p)}`" for p in changed[:20]))
    return why


def move_tag(old: str, new: str, root: Path = ROOT) -> list[str]:
    """Write the new tag where the repo names it; the files changed."""
    out = []
    tag_file = root / "tools" / "polestar-tag"
    tag_file.write_text(new + "\n", encoding="utf-8")
    out.append(str(tag_file.relative_to(root)))
    # Never tests/: a test that quotes a clone line is a test, not a pin.
    files = subprocess.run(["git", "-C", str(root), "grep", "-l", "--", "github.com/fitzforge/polestar", ":!tests"],
                           capture_output=True, text=True).stdout.split()
    for rel in files + ["THIRD_PARTY.md"]:
        p = root / rel
        if not p.is_file():
            continue
        s = p.read_text(encoding="utf-8")
        t = CLONE.sub(lambda m: m.group(1) + new + m.group(3) if m.group(2) == old else m.group(0), s)
        if rel == "THIRD_PARTY.md":
            t = re.sub(rf"\bPolestar {re.escape(old)}(?![\d.]*\d)", f"Polestar {new}", t)
        if t != s:
            p.write_text(t, encoding="utf-8")
            out.append(rel)
    return sorted(set(out))


DERIVED = ("tests/data", "tests/analysis")


def _normal(data: bytes, versions: list[str]) -> str:
    """A derived file's text with every given Polestar version (longest
    first, with or without its `v`) replaced by one token."""
    text = (gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data).decode("utf-8", "replace")
    for v in sorted({x.lstrip("v") for x in versions if x}, key=len, reverse=True):
        text = re.sub(rf"(?<![\w.])v?{re.escape(v)}(?![.\d])", "<POLESTAR>", text)
    return text


def regen(old: str = "", new: str = "", root: Path = ROOT) -> tuple[list[str], list[str]]:
    """Regenerate the fixtures and the layer pages' snapshot from the new data
    (tools/fixtures/generate.py, WOW_UPDATE_SNAPSHOT=1), as a person does
    after a bump. Returns (why a generator failed, the files that moved by
    more than the Polestar version); `refusals` says which of them stop the
    kind."""
    def tracked() -> dict[str, bytes]:
        names = subprocess.run(["git", "-C", str(root), "ls-files", *DERIVED], capture_output=True, text=True,
                               check=True).stdout.split()
        return {n: (root / n).read_bytes() for n in names if (root / n).is_file()}

    before = tracked()
    # A generator that stops on the new data is a refusal like any other, with
    # its last line as the reason (2026-11-22, v0.4.2.1: a traceback, no reason,
    # and the issue said the run had stopped).
    for what, cmd, env in (
            ("tools/fixtures/generate.py", ["uv", "run", "--frozen", "python", "tools/fixtures/generate.py"], None),
            ("the layer pages' snapshot (tests/test_layer_pages.py)",
             ["uv", "run", "--frozen", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_layer_pages.py"],
             {**os.environ, "WOW_UPDATE_SNAPSHOT": "1"})):
        try:
            subprocess.run(cmd, cwd=root, check=True, env=env, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            sys.stderr.write((e.stdout or "")[-3000:] + (e.stderr or "")[-3000:])
            last = ((e.stderr or "").strip() or (e.stdout or "").strip() or f"exit {e.returncode}").splitlines()[-1]
            last = PLAIN.sub("", last)[:200]
            return [f"{what} failed on the new data ({last}): fix the generator, not the data"], []
    after = tracked()
    moved = [n for n in sorted(after) if after[n] != before.get(n)
             and _normal(after[n], [old, new]) != _normal(before.get(n, b""), [old, new])]
    return [], moved


def refusals(kind: str, failed: list[str], moved: list[str]) -> list[str]:
    """What stops `kind` after a regen: a failed generator always; data that
    moved only for a client build (Polestar's and ForeverDB's numbers are
    taken as they are)."""
    if failed or kind in ("polestar", "foreverdb") or not moved:
        return failed
    return ["the regenerated test data changed: " + ", ".join(f"`{n}`" for n in moved[:10])]


def refuse(path: str | None, kind: str, reasons: list[str]) -> None:
    text = "The job's check of the new release refused it, so it isn't in the PR:\n" + "\n".join(
        f"- {r}" for r in reasons)
    print(text, file=sys.stderr)
    if path:
        p = Path(path)
        why = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        why[kind] = text
        p.write_text(json.dumps(why, indent=1) + "\n", encoding="utf-8")


def note(path: str | None, kind: str, text: str) -> None:
    """Why part of `kind` stays out of the PR, for its issue; not a refusal."""
    print(text, file=sys.stderr)
    if path:
        p = Path(path)
        why = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        why[kind] = text
        p.write_text(json.dumps(why, indent=1) + "\n", encoding="utf-8")


def _tally(doc: dict) -> tuple[collections.Counter, collections.Counter]:
    kinds, labels = collections.Counter(), collections.Counter()
    for e in (doc.get("items") or {}).values():
        for x in e.get("sources") or []:
            kinds[x.get("kind")] += 1
            labels[x.get("label")] += 1
    return kinds, labels


def foreverdb_summary(was: dict, now: dict, size: int) -> str:
    """What moved, for the PR: items, sources by kind and by label, size."""
    a, b = was.get("items") or {}, now.get("items") or {}
    (k0, l0), (k1, l1) = _tally(was), _tally(now)
    changed = sum(1 for i in set(a) & set(b) if a[i] != b[i])
    rows = "\n".join(f"| {k} | {k0.get(k, 0)} | {k1.get(k, 0)} |" for k in sorted(set(k0) | set(k1), key=str))
    labs = ", ".join(f"{k} {l0.get(k, 0)} -> {l1.get(k, 0)}" for k in sorted(set(l0) | set(l1), key=str))
    s0, s1 = was.get("source") or {}, now.get("source") or {}
    return (f"\n## ForeverDB.net data\nData {s0.get('data_version')} -> {s1.get('data_version')}, build "
            f"{s1.get('build')}. Items with a source: {len(a)} -> {len(b)} ({len(set(b) - set(a))} new, "
            f"{len(set(a) - set(b))} gone, {changed} changed). Labels: {labs}. "
            f"`data/foreverdb/item-sources.json`: {size:,} bytes.\n\n| Kind | Before | Now |\n|---|---|---|\n{rows}\n")


def foreverdb(why: str | None, handoff: str | None, summary: str | None, known: str) -> int:
    """Download, check, export (see the module's doc). 0 taken, 3 refused."""
    fdb = watch.foreverdb()
    committed = json.loads(fdb.OUT.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="foreverdb-") as tmp:
        src = Path(tmp)
        try:
            man = fdb.download(src)
            fp = fdb.fingerprint(man)
            if fp == known:
                print(f"foreverdb: {fp} is the known data, nothing to take")
                return 0
            _, parsed, lic = fdb.read_files(src)
            _, _, contract = fdb.notice_and_contract(parsed)
        except Exception as e:  # noqa: BLE001 - any failure of an untrusted download is a refusal
            refuse(why, "foreverdb", [f"the download wasn't usable ({PLAIN.sub('', str(e))[:200]})"])
            return 3
        reasons = [f"`{p}`: its format comments changed (the export's contract): read them against "
                   "tools/foreverdb_sources/export.py" for p, h in contract.items()
                   if (committed.get("source") or {}).get("contract", {}).get(p) != h]
        reasons += [f"`{n}` isn't the committed data/foreverdb/{n}: the licence needs a person" for n, b in lic.items()
                    if not (fdb.OUT_DIR / n).exists() or (fdb.OUT_DIR / n).read_bytes() != b]
        if reasons:
            refuse(why, "foreverdb", reasons)
            return 3
        for tool in ("tools/foreverdb_sources/export.py", "tools/foreverdb_weights/export.py"):
            r = subprocess.run(["uv", "run", "--frozen", "python", "-I", tool, "--files", str(src)], cwd=ROOT,
                               capture_output=True, text=True)
            if r.returncode:
                last = PLAIN.sub("", ((r.stderr or r.stdout).strip().splitlines() or ["?"])[-1])[:200]
                refuse(why, "foreverdb", [f"{tool} refused the new data ({last})"])
                return 3
    now = json.loads(fdb.OUT.read_text(encoding="utf-8"))
    if summary:
        Path(summary).write_text(foreverdb_summary(committed, now, fdb.OUT.stat().st_size), encoding="utf-8")
    if handoff:
        Path(handoff).write_text(json.dumps({"foreverdb": {"data": fp}}, indent=1) + "\n", encoding="utf-8")
    print(f"foreverdb: took {fp} (data {now['source']['data_version']})")
    return 0


GOLDEN_UPGRADES = 40
# A golden key that names one item to get: what ForeverDB's data moves.
UPGRADE_KEY = re.compile(r"^[\w-]+: (?:moves: upgrade\|\d+|next_moves: #up-\d+)$")


def golden(why: str | None, kind: str) -> int:
    """Take the golden characters' moved upgrades (see the module's doc)."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "golden.json"
        subprocess.run(["uv", "run", "--frozen", "python", "-m", "tests.slices.golden", "--check", "--json", str(out)],
                       cwd=ROOT, capture_output=True, text=True)
        got = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
    if got is None:
        refuse(why, kind, ["the golden characters couldn't be served (tests/slices/golden.py)"])
        return 3
    gone = got.get("gone") or []
    other = [g for g in gone if not UPGRADE_KEY.match(g)]
    if other or len(gone) > GOLDEN_UPGRADES:
        refuse(why, kind, [f"golden characters: {len(other)} keys that aren't upgrades went away "
                           f"({', '.join(other[:5])}), {len(gone)} in all (at most {GOLDEN_UPGRADES} upgrades)"])
        return 3
    if gone:
        subprocess.run(["uv", "run", "--frozen", "python", "-m", "tests.slices.golden", "--write"], cwd=ROOT,
                       check=True, capture_output=True)
    print(f"golden: {len(gone)} upgrades moved with the data")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("kind", choices=["polestar", "addons", "regen", "foreverdb", "golden"])
    ap.add_argument("tag", nargs="?", help="polestar: the new release tag")
    ap.add_argument("--old", default="", help="regen: the Polestar tag before the bump")
    ap.add_argument("--new", default="", help="regen: the Polestar tag after it")
    ap.add_argument("--as", dest="as_kind", default="polestar", help="regen: the kind a refusal is filed under")
    ap.add_argument("--why", help="append a refusal here ({kind: reason})")
    ap.add_argument("--state", default=str(watch.STATE))
    ap.add_argument("--handoff", help="addons, foreverdb: write what was checked and taken, for watch.py --accept")
    ap.add_argument("--summary", help="foreverdb: write what moved here (Markdown, for the PR)")
    a = ap.parse_args(argv)
    state = json.loads(Path(a.state).read_text(encoding="utf-8"))
    if a.kind == "foreverdb":
        return foreverdb(a.why, a.handoff, a.summary, state["foreverdb"]["known"])
    if a.kind == "golden":
        return golden(a.why, a.as_kind)
    if a.kind == "regen":
        failed, moved = regen(a.old, a.new)
        reasons = refusals(a.as_kind, failed, moved)
        if reasons:
            refuse(a.why, a.as_kind, reasons)
            return 3
        print("regenerated: " + ("moved with the new data: " + ", ".join(moved) if moved else
                                 "only the Polestar version moved"))
        return 0
    api = Repo()
    gh = watch.web.GitHub()
    if a.kind == "polestar":
        old, new = state["polestar"]["known"], a.tag or ""
        if not watch.TAG.match(new) or not watch.TAG.match(old):
            refuse(a.why, "polestar", [f"not a plain release tag: {SAFE.sub('', new)[:40]!r}"])
            return 3
        reasons = check_polestar(api, state["polestar"]["repo"], old, new)
        if reasons:
            refuse(a.why, "polestar", reasons)
            return 3
        print(json.dumps({"polestar": new, "changed": move_tag(old, new)}))
        return 0
    reasons, checked, unread = [], {}, []
    for addon in state["addons"]:
        if not addon.get("repo"):
            continue
        new = watch.addon_release(gh, addon)
        if not new:
            unread.append(addon["name"])
            continue
        if new == addon["known"]:
            continue
        reasons += [f"{addon['name']}: {r}" for r in check_addon(api, addon, addon["known"], new)]
        checked[addon["name"]] = new
    if reasons:
        refuse(a.why, "addons", reasons)
        return 3
    if a.handoff:
        # `message`: the commit's subject (tools/kb/versions.sh), which names a version taken backwards.
        back = watch.moved_back(state, checked)
        msg = "Addon releases: what the shipper reads is unchanged" + (f"; {'; '.join(back)}" if back else "")
        Path(a.handoff).write_text(json.dumps({"addons": checked, "message": msg}, indent=1) + "\n",
                                   encoding="utf-8")
    if unread:
        # Not a refusal: the others land; these stay pending, with the reason on the issue.
        note(a.why, "addons", f"The latest release of {', '.join(unread)} couldn't be read; the next run checks it.")
    seen = ", ".join(f"{k} {v}" for k, v in checked.items()) or "none"
    print(f"addons: what the shipper reads is unchanged (checked: {seen})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

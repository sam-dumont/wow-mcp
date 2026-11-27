"""The plan in words, written by the client's model, checked here.

No model runs on the server (owner, 2026-11-18). plan_session hands the
client the estimator's facts (name -> formatted value), the blocks and
CLIENT_BRIEF; the client writes the plan up and check_plan runs `check()`:
every number in the text must be in a fact's value. `fallback()` is the
estimator's own text, which the full view shows.
"""

from __future__ import annotations

import re

NUM = re.compile(r"\d+(?:[.,:]\d+)*")

CLIENT_BRIEF = """Write this plan up for the player from `facts` only: every number exactly as a fact gives it \
(never compute or round one), the blocks in order (time window, what, why with its source), then checkpoints, the \
town stop, what to look at or maybe skip, the optional dungeon branch, the mount and where to log out; \
leave out a section with no facts. Short, for a phone: headings, bullets, no table wider than three columns. \
Practical and friendly, never guilt-tripping: a detour has a price, not a ban; \
interruptions are "unplanned stops". A game rule you add \
needs a knowledge base source (kb_search) or is "unknown". Then call check_plan with your text: it lists any \
number that isn't in the facts; fix those before answering."""


# A plan by level (no Recorder events: no route step known).
ESTIMATED_BRIEF = (" This plan is estimated by level from the player's level times, not from guide steps: say "
                   "'estimated' once, give the checkpoints by level, and end with the `unlock` line as it is.")


def check(text: str, facts: dict, constraints: dict | None = None, hours: float | None = None) -> dict:
    """A write-up (anyone's) against a plan: the numbers in it that no fact
    holds, and the player's own constraints from the session request
    (estimate.parse_constraints: the activities they ruled out, and the
    time budget, `hours`). Matching numbers alone passed "spend the entire
    session in a dungeon" after "no dungeons" (review, 2026-11-19)."""
    errs = rendered_ok(text, facts)
    cons = constraint_checks(text, facts, constraints or {}, hours)
    errs += [c["problem"] for c in cons if not c["ok"]]
    return {"ok": not errs, "problems": errs, "constraints": cons}


# Words that turn a sentence naming an activity into one ruling it out.
NEGATION = re.compile(r"\b(no|not|never|none|skip(?:ping|ped)?|without|avoid(?:ing)?|instead of|don'?t|do not|"
                      r"isn'?t|aren'?t|won'?t|left out|leave out|leaving out|as you asked|ruled out)\b", re.I)
DUNGEON_WORDS = re.compile(r"\b(dungeons?|instances?|group(?:ing)? up|clears?|loop(?:ing)? it)\b", re.I)
# Profession work, not a profession's name alone: a guide step or a quest
# can be titled after one ("Camping 101: First Aid").
_PROFS = (r"(?:tailoring|enchanting|cooking|first aid|fishing|alchemy|blacksmithing|leatherworking|engineering|"
          r"herbalism|mining|skinning)")
PROFESSION_WORDS = re.compile(r"\b(professions?|crafting|craft\s+\w+|" + _PROFS + r"\s+(?:trainer|skill(?:-ups?)?)|"
                              r"(?:level|train|skill(?:\s+up)?|work on|push|grind)\s+(?:up\s+)?(?:your\s+|my\s+)?"
                              + _PROFS + r")\b", re.I)
# A duration as the plans and people write it: 2h00, +1h51, 3 hours, 4h, 1.5 hrs, 45 min.
DUR = re.compile(r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*(h(?:ours?|rs?)?\b|h(?=\d)|min(?:ute)?s?\b)(\d{1,2}(?!\d))?",
                 re.I)
SLACK_S = 60  # the plan's own rounding


def sentences(text: str) -> list[str]:
    return [x.strip() for x in re.split(r"[.!?;\n]+", text or "") if x.strip()]


def durations(s: str) -> list[tuple[str, int]]:
    out = []
    for m in DUR.finditer(s or ""):
        v = float(m.group(1).replace(",", "."))
        secs = v * 3600 + int(m.group(3) or 0) * 60 if m.group(2).lower().startswith("h") else v * 60
        out.append((m.group(0).strip(), round(secs)))
    return out


def _activity(name: str, asked: str, words: re.Pattern, names: set[str], text: str) -> dict:
    bad = [x for x in sentences(text)
           if (words.search(x) or any(n.lower() in x.lower() for n in names)) and not NEGATION.search(x)]
    out = {"constraint": name, "ok": not bad, "breaks": bad[:3]}
    if bad:
        out["problem"] = f'constraint "{name}" (you asked: "{asked}") broken by: "{bad[0][:120]}"'
    return out


def constraint_checks(text: str, facts: dict, cons: dict, hours: float | None) -> list[dict]:
    asked = str(cons.get("text") or "").strip()
    out = []
    if cons.get("no_dungeons"):
        names = {str(v) for k, v in facts.items() if re.fullmatch(r"d\d+\.name|branch\.dungeon", k) and v}
        out.append(_activity("no dungeons", asked, DUNGEON_WORDS, names, text))
    if cons.get("no_professions"):
        out.append(_activity("no professions", asked, PROFESSION_WORDS, set(), text))
    if hours:
        budget = float(hours) * 3600
        # A fact's own duration may be longer (the mount's ETA, a dungeon's
        # whole trip): it names something else, not the session.
        known = {s for v in facts.values() for _, s in durations(str(v))}
        over = [span for span, secs in durations(text) if secs > budget + SLACK_S and secs not in known]
        label = f"time budget {facts.get('hours') or f'{hours:g}h'}"
        c = {"constraint": label, "ok": not over, "breaks": over[:3]}
        if over:
            c["problem"] = (f'{label} ({f"you asked: {asked!r}" if cons.get("hours") else "the plan"}): '
                            f'"{over[0]}" is longer than the session')
        out.append(c)
    return out


def numbers(s: str) -> set[str]:
    return {n.replace(",", "") for n in NUM.findall(s or "")}


def rendered_ok(text: str, facts: dict) -> list[str]:
    allowed: set[str] = set()
    for v in facts.values():
        allowed |= numbers(str(v))
    stray = sorted(numbers(text) - allowed)
    return ["numbers not in the facts: " + ", ".join(stray[:10])] if stray else []


def fallback(est: dict) -> str:
    """The estimator's own plan text: every number straight from the facts."""
    f = est["facts"]
    xp = (f" ({f['end.xp']} XP" + (f", {f['end.rested_used']} of it rested" if est['end']['rested_used'] else "")
          + ")") if "end.xp" in f else " (on the route's own clock: none of your levels measured yet)"
    if est.get("mode") == "by_level":
        # No route step known: an estimate by level, said once, with the
        # one thing that makes it the step-by-step plan.
        basis = (f"your last {f['xph.n']} levels, {f['xph']} XP an hour with your breaks" if "xph" in f
                 else "Polestar's own time per level")
        out = [f"## Your next {f['hours']} (estimated)",
               f"Level {f['start.level']} now, about {f['end.level']} at the end{xp}, at the pace of {basis}."]
    else:
        out = [f"## Your next {f['hours']}",
               f"Level {f['start.level']} now, about {f['end.level']} at the end{xp}. "
               f"{f['play']} of play, {f['stops']} kept for unplanned stops ({f['stops.share']} of your recent "
               f"sessions)."]
    out.append("\n### Blocks")
    for b in est["blocks"]:
        i = b["id"]
        out.append(f"- **{f[i + '.start']} to {f[i + '.end']}** · {f[i + '.title']} → level {f[i + '.level']}")
        for j in range(1, 4):
            if f"{i}.why{j}" in f:
                out.append(f"  - {f[i + '.why' + str(j)]}")
        if b.get("town_stop") and est.get("town_stop"):
            out.append("  - Town stop here: " + "; ".join(f[k] for k in sorted(f) if k.startswith("town.errand")))
    cps = [k for k in f if re.fullmatch(r"cp\d+\.level", k)]
    if cps:
        out.append("\n### Checkpoints")
        out += [f"- Level {f[k]} at about {f[k[:-6] + '.at']}" for k in sorted(cps, key=lambda k: int(k[2:-6]))]
    if est.get("town_stop") and not any(b.get("town_stop") for b in est["blocks"]):
        out.append("\n### Town stop (at the end)")
        out += [f"- {f[k]}" for k in sorted(f) if k.startswith("town.errand")]
    sk = [k for k in sorted(f) if re.match(r"(skip|abandon)\d+$", k)]
    if sk:
        out.append("\n### Look at, maybe skip")
        out += [f"- {f[k]}" for k in sk]
    if "branch.dungeon" in f:
        out.append("\n### If you get a group")
        gear = f", gear {f['branch.gear']} points expected" if f["branch.gear"] not in ("+0", "+0.0") else ""
        xp = (f"{f['branch.xp']} quest XP, kill XP unknown" if est["branch"].get("kills_unknown") else
              f"about {f['branch.xp']} XP ({f['branch.xph']} XP/h)")
        out.append(f"- {f['branch.dungeon']}: {f['branch.verdict']}. {f['branch.time']} for the whole trip, "
                   f"{xp}{gear} ({f['branch.from']}).")
        if "branch.vs_guide" in f:
            out.append(f"  - {f['branch.vs_guide']}")
    m = est["mount"]
    cur = m.get("current")
    if cur:
        i = m["tiers"].index(cur) + 1
        if cur.get("quest"):
            line = f"- {f[f'mount{i}.quest']} Paid trainer alternative: {f[f'mount{i}.trainer']}."
        elif est["start"].get("gold_copper") is None:  # no character sheet yet: the gold isn't known
            line = f"- Riding at {f[f'mount{i}.level']}: {f[f'mount{i}.need']} needed (your gold isn't known yet)"
        else:
            line = (f"- Riding at {f[f'mount{i}.level']}: {f[f'mount{i}.need']} needed, {f[f'mount{i}.left']} to go"
                    + (f" at {f['gold.rate']} net" if f.get("gold.rate") != "n/a" else ", no gold rate measured yet"))
        if f"mount{i}.eta" in f and not cur.get("quest"):
            line += f": about {f[f'mount{i}.eta']} of play" + (f" (level {f[f'mount{i}.eta_level']})"
                                                               if f"mount{i}.eta_level" in f else
                                                               ", past the route's last level at this rate")
        out.append("\n### Mount")
        out.append(line)
        out += [f"- {f[k]}" for k in sorted(f) if k.startswith("goldopt")]
    out.append("\n### Log out")
    out.append(f"- {f['logout.where']}" + (f" ({f['logout.how']})" if "logout.how" in f else "")
               + (f": {f['logout.rested']}" if "logout.rested" in f else ""))
    if f.get("unlock"):
        out.append("\n" + f["unlock"])
    return "\n".join(out)

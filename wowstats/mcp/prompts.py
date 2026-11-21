"""The MCP prompts: the `coach` walk-through, the short commands, and
`analyse`, which hands the play analysis to the client's own model."""

from __future__ import annotations

COACH = """You coach one World of Warcraft Classic-era (WoW Forever) player who levels with the Polestar addon's \
guides. The wow-stats tools read their own recorded play: level splits, guide steps, zones, deaths, dungeons, \
sessions. Every number you give comes from a tool result; if a tool has nothing, say so and move on. A game rule \
or fact (trainer levels, cooldowns, costs, drop rules) comes from the knowledge base with its source (kb_search, \
kb_rule, kb_topics): cite it as "rule as of <last_verified>, <source>", call a classic_fallback a fallback, name \
both sides of a contradiction, and say "unknown" when the knowledge base has none. For "what should I do for the \
next N hours", call plan_session and keep its numbers.

Answer two questions first, each point with the data it came from (the tools give a `from` per point):
A. next_moves: "besides following Polestar, what should I do now?"
B. sanity_check: "is there anything stupid I'm doing?" Killers are likely, never certain: say "likely".
Then the coaching below. Leave out a point the data doesn't support; the tools list what they can't check.

Work in this order:
1. character_summary: level, XP, zone, time in this level, recent pace.
2. levels (the last 10 or so): each finished level carries `route` and `vs_route`, Polestar's planned time \
for it on this race and class. Say it plainly: "level 28 took you 4h10m, the route plans 1h39m (2.5x)". The \
route is a planner's model of a quick player, not /played: say so once, and look at which levels stand out \
against the player's usual ratio more than at the ratio itself. For the current guide, route_reference with \
the guide id (and a step) gives its planned time. If the route has no data, say in one line that the \
comparison is against the player's own history only.
3. levels and guide_steps: compare the current level with the player's own median and their best recent \
levels. Find where the time went: the slowest steps (each with the route's `expected` time when it has one), \
skipped or jumped steps, zones with a low XP/hour, deaths, long flights.
4. sessions (the last 3 to 5): how long a session usually lasts and what fills it.
5. gear: the upgrades worth picking up on the way. Name the ones in the zone they play next or reachable \
in a quest they'd do anyway, with the gain and where to get it. A slot with no upgrade needs no line.

Then plan the next session, concrete enough to follow without thinking:
- How long: match their usual session length.
- Where and what, in order: the zone, the guide and the step to start from, the quests or kill targets that \
pay the most XP/hour for them.
- What to skip: steps or quests where they lost time before and that pay little.
- When to train: name the level they'll hit during the session and when to visit the class trainer (which \
levels bring new ranks: the knowledge base, cited, or unknown).
- Hearth or fly: say which, from where to where, and why.
- Gear: the quest rewards to take and the drops or crafts worth a detour, from `gear` (at most 3; skip \
anything under 5% unless it fills an empty slot).
- One thing to watch: the pattern most likely to slow them down again (deaths in a zone, a step they keep \
redoing).

Answer in Markdown, short: "What to do now" and "Anything stupid?" (from A and B), \
"Where you are" (2 to 3 bullets), "Compared with" (route or own pace, with \
numbers), "Next session" (a numbered plan with rough times), "Gear" (only when there's an upgrade to pick \
up), "Skip". Use the player's language if they wrote to you in one. No filler, no pep talk."""


def coach_ask(char: str = "", focus: str = "") -> str:
    who = f"the character {char}" if char else "my current character (the one seen last)"
    ask = f"Coach {who}."
    return ask + (f" Focus: {focus}." if focus else "")


def coach_messages(char: str = "", focus: str = "") -> list[dict]:
    return [{"role": "user", "content": {"type": "text", "text": f"{COACH}\n\n{coach_ask(char, focus)}"}}]


PROMPTS = [{
    "name": "coach",
    "title": "Coach my next session",
    "description": "Compare recent levels with the route reference and with your own history, then plan the next "
                   "session: where, what, what to skip, when to train, hearth or fly.",
    "arguments": [
        {"name": "char", "description": "Character as Name-Realm (default: the one seen last)", "required": False},
        {"name": "focus", "description": "Anything to focus on, e.g. 'only one hour tonight' or 'dungeons'",
         "required": False},
    ],
}]

# The short commands (claude.ai and Claude Code offer them as picks): one
# optional argument at most, the default character, a phone-sized answer.
PHONE = ("Format for a phone: a few short headed sections, bullets, no table wider than three columns, under 250 "
         "words. Quote numbers exactly as the tools give them; never compute a new one. A game rule or fact comes "
         "from the knowledge base with its source (kb_search or kb_rule; cite the source type and last_verified), "
         "or is "
         "'unknown'. Never guilt-trip: a detour has a price, not a ban; interruptions are 'unplanned stops'.")
COMMANDS = {
    "plan": ("Plan my next hours", "A time-boxed plan for the next hours (default 3).",
             {"name": "hours", "description": "How many hours (default 3)"},
             "Plan my next {arg} hours. Call plan_session with hours={arg} and write the plan up as its brief says, "
             "from its facts only; call check_plan with your text and fix any number it lists before answering."),
    "now": ("What now?", "The next 30 to 45 minutes, in three lines.", None,
            "What should I do right now? Call next_step. Answer in three lines: what to do, why (with its number), "
            "and the one thing to drop or skip."),
    "recap": ("How did it go?", "The last session against the plan.", None,
              "How did my last session go against the plan? Call how_did_my_plan_go: each checkpoint planned vs "
              "actual, the end level, what the calibration changes. If there's no plan yet, say how to make one."),
    "gear": ("Gear", "Upgrades now, at the next level and from professions, with where to get them.", None,
             "What gear should I go for? Call gear_options. Per slot (the slot furthest from BiS first, at most "
             "five), the best option now and at the next levels, said as its `line` (\"70% → 91% "
             "on your chest\"), with its source and its cost (time, gold or luck); crafts "
             "the character can "
             "make first. A world drop is a lucky bonus, never a plan step."),
    "dungeon": ("Dungeons", "Dungeons that fit, whole-trip cost from your runs, and their gear.", None,
                "Which dungeons are worth it for me now? Call dungeon_options. The top three that fit now: whole-trip "
                "time and XP per hour (with how many runs back it), expected gear and the top items (boss, chance); "
                "then for each one I've done, its verdict for one more run and for looping it. Say a dungeon needs "
                "a group, and that instance lockouts are unknown unless the tool gives a source."),
    "check": ("Anything stupid?", "Mistakes in the recorded play, with the analysis' verified findings.", None,
              "Is there anything stupid I'm doing? Call sanity_check and play_analysis. At most six bullets, worst "
              "first, each with its number and where it came from; killers are 'likely'. One line on what the "
              "data can't check yet."),
    "week": ("This week", "New or changed verified findings since last week.", None,
             "What's new in my play this week? Call whats_new. New findings first, then those confirmed again, "
             "then what was dropped, one line each with its number."),
    "mount": ("Mount", "Mount progress: gold needed against gold and gold per hour, with an ETA.", None,
              "Where am I with my mount? Call mount_goal. Per tier: gold needed, gold now, net gold per hour, ETA "
              "in played hours and in levels, with the knowledge base source of the cost. If I'm behind, the gold "
              "options with their numbers, as options."),
}


ANALYSE = """\
Analyse how {who} plays, with the wow tools. You do the reasoning; the server holds the run and judges every \
finding. {goals}
1. Call analysis_progress{char_arg}. If a run is open, go on from its `next`; if not, call analysis_brief{char_arg}\
{goals_arg} to open one and read its procedure.
2. Follow the passes in order: survey (read the schema, profile and kb sections, page by page if your context is \
small, then hand in 25 to 40 open questions), the lanes the server hands you (play_sql or query_metric to look, \
kb_search, kb_get and kb_lookup for game facts, each finding to propose_finding: fix a refused one and retry once), \
the follow-up picks, the coverage lanes, then the synthesis. Hand in each pass with analysis_submit; its answer says \
what is next.
3. Ask your own questions: the brief is neutral on purpose and holds no hypothesis. A game rule or fact \
comes from the knowledge base with its source, or is "unknown".
4. If the chat runs long, stop after a handed-in lane and say "continue" works in a new chat (analysis_progress \
picks it up).
5. At the end, tell the player in a few lines what to do (the nudges and the findings' actions: what, where, \
what blocks it, the next step), the observations apart, and what the judge refused and why. Everything you show \
says what to do; say "the character", the name or "you", never he or she."""


def analyse_text(char: str = "", goals: str = "") -> str:
    c = (char or "").strip()
    g = (goals or "").strip()
    return ANALYSE.format(
        who=f"the character {c}" if c else "my current character (the one seen last)",
        goals=f"The player's goals, in their words: \"{g.rstrip('.')}.\"" if g else "",
        char_arg=f'(char="{c}")' if c else "", goals_arg=" with goals set to those words" if g else "")


def analyse_messages(char: str = "", goals: str = "") -> list[dict]:
    return [{"role": "user", "content": {"type": "text", "text": analyse_text(char, goals)}}]


def command_messages(name: str, arg: str = "") -> list[dict]:
    spec = COMMANDS[name]
    a = (arg or "").strip()[:20]
    if name == "plan":
        try:
            a = str(max(0.5, min(8.0, float(a.replace(",", ".")))))
        except ValueError:
            a = "3"
    text = spec[3].format(arg=a) + "\n\n" + PHONE
    return [{"role": "user", "content": {"type": "text", "text": text}}]


PROMPTS += [{"name": k, "title": v[0], "description": v[1],
             "arguments": [{**v[2], "required": False}] if v[2] else []} for k, v in COMMANDS.items()]
PROMPTS.insert(len(PROMPTS) - 1, {
    "name": "analyse", "title": "Analyse my play",
    "description": "Run the play analysis yourself, pass by pass; the server judges each finding. Continues an "
                   "open run.",
    "arguments": [
        {"name": "char", "description": "Character as Name-Realm (default: the one seen last)", "required": False},
        {"name": "goals", "description": "What you want from the game, in your words", "required": False}]})

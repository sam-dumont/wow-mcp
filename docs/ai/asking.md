# Ask about your character

**For:** anyone whose AI assistant is connected to wow-stats. **At the end:** you know what to
ask, what the assistant can and can't see, and how to tell a checked number from a guess.

## What to ask

Plain questions work. Name wow-stats in the first one so the assistant picks the right tools:
"With wow-stats, ...". Three that lead somewhere:

<!-- generated:ai-asks -->
<!-- From wowstats/mcp/helppage.py (ASKS): edit it there, then run tools/manual/generate.py. -->

- "Plan my next 2 hours, no dungeons."
- "Which upgrade should I chase next, and where?"
- "Is another run of my last dungeon worth it?"

<!-- /generated:ai-asks -->

More that work on any character:

- "Which level took longest, and how much of it was in a dungeon?"
- "Why was level 24 so slow?"
- "What's my next step for the next 40 minutes?"
- "How much gold do I need for riding, and how long at my rate?"
- "Is there anything I keep doing wrong?" (deaths in the same place, steps far over plan,
  upgrades not taken, unspent talent points)
- "How did my last plan go?"
- "Analyse how I play." (a long, step-by-step analysis: see below)

In claude.ai the `+` menu has ready-made prompts (`plan`, `now`, `gear`, `dungeon`, `check`,
`mount`, `analyse`...); in Claude Code they're slash commands. The full list, with every tool:
[MCP tools and prompts](../reference/mcp-tools.md).

## What the assistant can see

<!-- generated:ai-groups -->
<!-- From wowstats/mcp/helppage.py (GROUPS): edit it there, then run tools/manual/generate.py. -->

- **Levels and pace**: time, XP and gold per level, against the route.
- **Sessions and zones**: each session, and time, XP and deaths per zone.
- **Guide steps**: time per step, skipped steps.
- **Deaths**: where, at what level, what killed you.
- **Dungeons**: your runs, and which ones are worth it now.
- **Gear**: what you wear and every way to an upgrade.
- **Talents**: how your talents changed, day by day.
- **Gold**: your auction prices, and the next riding tier.
- **Plans**: session plans at your pace, and how they went.
- **Analysis**: findings about how you play, re-checked before they're kept.
- **People**: players your game showed you.
- **Game rules**: game facts: quests, items, NPCs, zones, rules, how to level your class and its meta.
- **News**: what's new on Forever this week, and what is coming.

<!-- /generated:ai-groups -->

All of it comes from your own game files: your addons' saves and your combat log. The
assistant sees your characters only, and on a shared server only yours.

What it doesn't get:

- **Battle.net data** (WoW Token, your look from the armory) stays off unless
  you turn on "Battle.net data to my AI" in your Settings, on a shared server. Blizzard's
  terms keep that data 30 days at most, and a chat keeps what a tool answered for as long as
  the chat exists, so that's your call. Your own Auctionator scans aren't Battle.net data: the
  assistant always has those.
- **Anything you didn't record.** No addon saves who was in your group or what your bags are
  worth to a vendor, so the tools say "not known" instead of guessing.

Whatever the assistant reads, your AI provider receives: it's in your chat.

## How numbers get checked

The assistant writes the answers. The server checks them. Two tools do it:

- **`play_sql`** runs one read-only `SELECT` on a database of your play, built from your
  events: levels, sessions, the timeline of where you were at what level, deaths, loot, gear.
  The assistant uses it to count and compare.
- **`propose_finding`** is the judge. A finding the assistant wants to keep goes in with its
  SQL. The server runs the SQL again itself, checks every number in the claim against the
  result, and keeps the finding or refuses it with the reason. A number above 100 typed by
  hand instead of taken from the query is refused.

Kept findings are re-run every night on fresh data: one that still holds gains weight, one
that stops holding fades and goes after three nights.

So when the assistant quotes a kept finding, the number came out of your data through a query
the server ran. When it says something without a finding behind it, it's the assistant's own
reading: ask "show me the query" and it can run one.

### An example on the demo

On the demo data (see [Try the demo](../self-host/demo.md)), ask "Which level took
longest, and how much of it was in a dungeon?" about `Testchar-Demo`. The assistant runs a
query like this one through `play_sql`:

```sql
SELECT l.level, l.played_s,
       (SELECT round(sum(dur_s)) FROM timeline t
         WHERE t.level = l.level AND t.instance IS NOT NULL) AS dungeon_s,
       (SELECT group_concat(DISTINCT instance) FROM timeline t
         WHERE t.level = l.level AND t.instance IS NOT NULL) AS dungeons
FROM levels l ORDER BY l.played_s DESC LIMIT 1
```

and gets level 30: 11,240 seconds played (3 h 07 min), 2,667 seconds of it in Scarlet
Monastery: Library. The `dungeons` tool gives the run itself as 2,650 seconds (44 min), about
24% of the level. The two dungeon numbers differ by 17 seconds because the timeline keeps a
segment's state until the next record. Ask the assistant to show its query and the rows it
got: the numbers above are what the demo returns.

## The long analysis

"Analyse how I play" (the `analyse` prompt) runs a step-by-step analysis you drive from the
chat. The server hands the assistant a neutral brief (the tables, a profile of each, your
goals), and the assistant asks its own questions:

1. **Survey:** 25 to 40 open questions about your play.
2. **Lanes:** the questions in groups of three, each answered with queries; findings go to the
   judge.
3. **Follow-ups:** a second round on the most promising leads.
4. **Coverage:** any table nothing has read yet gets its own look.
5. **Synthesis:** the kept findings against each other; clashes get a deciding query.

It takes a while and many tool calls. On a small model, say "continue" when it stops: the
server keeps where the run stands, so a new chat picks it up. What comes out lands on your
page (the full view), and the screened parts on your zogzog link.

How it works inside: [MCP and the play analysis](../mcp.md).

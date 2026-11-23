# Ask about your character

**For:** anyone whose AI assistant is connected to wow-stats. **At the end:** you know what to
ask, what the assistant can and can't see, and how to tell a checked number from a guess.

## What to ask

Most answers come straight from the tools: they work out where you stand, your levels,
sessions, dungeons, gear and plans in code, from your data, and the assistant puts them in a
sentence. The assistant reasons on its own only in the play analysis, and there the server
checks its numbers ([below](#how-numbers-get-checked)).

Plain questions work. Name wow-stats in the first one so the assistant picks the right tools:
"With wow-stats, ...". Three that lead somewhere:

<!-- generated:ai-asks -->
<!-- From wowstats/mcp/helppage.py (ASKS): edit it there, then run tools/manual/generate.py. -->

- "Plan my next 2 hours, no dungeons."
- "Which upgrade should I chase next, and where?"
- "Is another run of my last dungeon worth it?"

<!-- /generated:ai-asks -->

More: "Why was level 24 so slow?", "How much gold do I need for riding, and how long at my
rate?", "Is there anything I keep doing wrong?", "How did my last plan go?", "Analyse how I
play." In claude.ai the `+` menu has ready-made prompts (`plan`, `now`, `gear`, `dungeon`...);
in Claude Code they're slash commands. Every tool and prompt:
[MCP tools and prompts](../reference/mcp-tools.md).

## What the assistant can see

<details>
<summary>What it reads, by subject</summary>

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
- **Analysis**: findings about how you play, their numbers re-run before they're kept.
- **People**: players your game showed you.
- **Game rules**: game facts: quests, items, NPCs, zones, rules, how to level your class and its meta.
- **News**: what's new on Forever this week, and what is coming.

<!-- /generated:ai-groups -->

</details>

Your own characters only, from your own game files. **Battle.net data** (the WoW Token, your
look, talents) stays off unless you turn on "Battle.net data to my AI" in Settings: Blizzard's
terms keep it 30 days at most, and a chat keeps a tool's answer as long as the chat exists, so
that's your call. Anything no addon records, the tools call "not known" instead of guessing.
Whatever the assistant reads, your AI provider receives: it's in your chat.

Other players' names, forum posts and news are text strangers wrote. The tools strip invisible
characters from them (the kind that can hide instructions) and mark them `untrusted`, so your
assistant treats them as data, never as orders.

## How numbers get checked

The assistant writes the answers; the server checks the numbers. A finding it keeps is checked
against its saved query: the server ran that query on your data itself and got the same
numbers. You can read the query (ask "show me the query"): a query that counts the wrong thing
still passes, so the check is on the numbers, not on the reading. Anything else is the
assistant's own reading.

<details>
<summary>How the check works, with an example</summary>

Three tools do it:

- **`play_sql`** runs one read-only `SELECT` on a database of your play, built from your
  events: levels, sessions, the timeline of where you were at what level, deaths, loot, gear.
  The assistant uses it to count and compare.
- **`query_metric`** asks for named numbers with one fixed definition each (time played per
  level, deaths per zone): [Semantic layer](../reference/semantic-layer.md).
- **`propose_finding`** is the judge. A finding the assistant wants to keep goes in with its
  SQL (or its `query_metric` call). The server runs it again itself and keeps the finding or
  refuses it with the reason. The finding's text names its numbers as placeholders over the
  result (`You died {n} times.`), and the server fills them in: the assistant never types a
  measured number. Any other number has to be declared with its kind (a level or a top 3 the
  query filters on, a patch, a game rule from the knowledge base). "You died 3 times." next to a
  query that counts 5 is refused, and so is a query that doesn't read your play at all
  (`SELECT 3` gives 3 whatever you did).

Kept findings are re-run every night once you've played since the last check, for everyone
on the server (friends included), without a model. The text is filled in again from that
night's result, so it shows the current number; the page and `play_analysis` say when each
finding was last checked. One that still holds stays, and gains weight only when you've played
since it last did: the same data checked again proves nothing new. One that stops holding fades
and goes after three or four nights. A level, top-N or threshold the finding's own query writes
("At level 32" with `level = 32`) is not a typed number.

**An example on the demo.**

On the demo data (see [Try the demo](../self-host/demo.md)), ask "Of the levels with a
dungeon run, which took longest, and how much of it was inside?" about `Nibbet-Gearholt`. The
assistant runs a query like this one through `play_sql`:

```sql
SELECT l.level, l.played_s,
       (SELECT round(sum(dur_s)) FROM timeline t
         WHERE t.level = l.level AND t.instance IS NOT NULL) AS dungeon_s,
       (SELECT group_concat(DISTINCT instance) FROM timeline t
         WHERE t.level = l.level AND t.instance IS NOT NULL) AS dungeons
FROM levels l
WHERE EXISTS (SELECT 1 FROM timeline t WHERE t.level = l.level AND t.instance IS NOT NULL)
ORDER BY l.played_s DESC LIMIT 1
```

and gets level 23: 14,229 seconds played (3 h 57 min), 1,578 seconds of it in Blackfathom
Deeps. The `dungeons` tool gives the run itself as 4,980 seconds (83 min): he dinged 24
inside, so most of the run counts for level 24. Ask the assistant to show its query and the
rows it got: the numbers above are what the demo returns.

</details>

## The long analysis

"Analyse how I play" (the `analyse` prompt) runs a step-by-step analysis you drive from the
chat: the assistant asks its own questions about your play and the server checks every
answer. It takes a while; on a small model, say "continue" when it stops (a new chat picks it
up). What comes out lands on your page, and the screened parts on your zogzog link.

<details>
<summary>The five rounds</summary>

1. **Survey:** 25 to 40 open questions about your play.
2. **Lanes:** the questions in groups of three, each answered with queries; findings go to the
   judge.
3. **Follow-ups:** a second round on the most promising leads.
4. **Coverage:** any table nothing has read yet gets its own look.
5. **Synthesis:** the kept findings against each other; clashes get a deciding query.

</details>

**Run it yourself before the server does.** When your host lets the server run the analysis,
it does so after enough play (10 hours by default), on the host's AI credit. From 5 hours
before that, your page shows a card above the Ask links: "Your analysis is 10h 12m of play
old. In about 4h 48m of play the server refreshes it on the host's credit. Be nice to the host: ask
your own assistant to run it." (your host may put their name there). **Copy** puts the prompt on your clipboard;
paste it in your assistant's chat. × hides the card until you close the tab. Your assistant
gets the same reminder now and then in its tool answers. No card when the server's refresh is
off, and never on your public page or zogzog link.

How it works inside: [MCP and the play analysis](../mcp.md).

---
title: Where XP comes from (Forever, with Classic fallbacks labelled)
source: Polestar v0.4.1 docs/forge/model.md and addon/Polestar/Alts/Snapshot.lua (GPL-3.0); Forever client spell table (wago.tools wowf)
date: 2026-11-18
label: written by an LLM (Claude) from the sources named here; the rested numbers are Classic Era's (Forever unverified)
---
Kills: Polestar uses the classic formula for an even-level mob, 5 x level + 45 XP, plus 5% per level the
mob is above the character (up to 4 levels), less below, nothing for grey mobs. An elite gives twice the
XP. Kill records carry the XP the game reported (`xp_reward`); inside dungeons the addon often can't
read it, and Polestar's dungeon runs give the run's XP as a whole (`dungeon_runs.xp`).

Quests: the quest's XP reward, and on Forever a share of it by how far the character is over the quest's
level, as players' recorded hand-ins show: all of it up to 5 levels over, 80% at 6, 60% at 7, half from
8 levels over on. Turn-in records carry the XP actually paid.

Exploration: discovering an area pays XP (`explore` records).

Rested XP (Classic Era rules, which Polestar uses because Forever's rates are unverified): logged out in
an inn or a city a character gains rested XP worth 5% of a level per 8 hours, a quarter of that
elsewhere, up to 1.5 levels. The `xp` records carry what is
left (`rested_xp`). Forever adds its own rest items and auras (search the KB for sleeping bag, campfire,
Boosted Rest, Well Rested): their client descriptions say what they give.

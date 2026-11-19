---
title: Profession tiers and skill caps, with Classic Era fallbacks labelled
source: Forever client tables (wago.tools wowf: SpellEffect, SkillLine; the KB's rule:profession:* documents); Classic Era trainer rules (1.13 and later), not Forever data
date: 2026-11-18
label: written by an LLM (Claude) from the sources named here; the caps and levels are Classic Era's (Forever unverified)
---
Forever's client lists four trainer tiers for each crafting and gathering profession (Apprentice,
Journeyman, Expert, Artisan: `rule:profession:<name>`), learned from a trainer. The caps and level
requirements are not in the client.

Classic Era (fallback, Forever unverified): Apprentice caps the skill at 75, Journeyman at 150 (needs
level 10 and skill 50), Expert at 225 (level 20, skill 125), Artisan at 300 (level 35, skill 200). Skill
points stop at the cap until the next tier is trained. The `professions` table's `max_rank` is the cap the
game reported for this character, which shows the tier actually trained.

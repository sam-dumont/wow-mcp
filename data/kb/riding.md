---
title: Riding on Forever, with Classic Era fallbacks labelled
source: Forever client tables (wago.tools wowf: Item, ItemSparse, SkillLineAbility; the KB's rule:riding); Polestar v0.4.1 docs/forge/model.md (GPL-3.0); Classic Era trainer fees (1.13 and later), not Forever data
date: 2026-11-18
label: written by an LLM (Claude) from the sources named here; the trainer fees are Classic Era's (Forever unverified)
---
Forever (client tables, `rule:riding`): the vendor mounts need level 40 and list 20g; the fast mounts need
level 60. Riding skill comes from the spells Apprentice Riding (skill 75) and Journeyman Riding (skill 150).
Polestar's route model rides from level 40 and walks before (its `mountLevel` 40; walking is 1.6 times
slower than riding); its guides send each race to its riding trainer at the first stop at 40 or above.

Classic Era (fallback, Forever unverified): Apprentice Riding is trained at level 40 for 20g, the mount
costs 80g, both less with a reputation discount; Journeyman Riding at level 60 costs 900g and the fast
mount 100g. A character under level 40 cannot ride a mount at all, whatever their gold.

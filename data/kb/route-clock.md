---
title: What Polestar's route clock measures
source: wow-stats docs/route-reference.md; Polestar v0.4.1 docs/forge/model.md (GPL-3.0)
date: 2026-11-18
label: written by an LLM (Claude) from the sources named here
---
The planned seconds in the `levels` and `route_steps` tables come from Polestar's route planner, run on
Polestar's own guides for every starting race and class (wow-stats tools/route_reference). The planner
models a quick player who knows the route: a walking speed of 7 yards per second, mounted from level 40
at 1.6 times that, a fixed time per kill that grows with level, 6 seconds per quest accept or hand-in,
20 seconds of overhead per flight plus the flight path at 30 yards per second, and a 10-second hearth
with a one-hour cooldown. It has no navigation mesh (walks are straight lines times a detour factor,
which runs Eastern Kingdoms walks about 13% short) and no calibration against recorded players.

Its cost table (model.md "Costs") has no entry for anything outside the guide's own steps: no eating or drinking, no deaths beyond a
fixed risk per dangerous zone crossed, no professions, no dungeons except the runs Polestar injects
into the route, no shopping or bank visits, no reading. A level's planned time is the median over the
planner's runs for that side and level, also given per class.

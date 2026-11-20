# What's shared, and with whom

**For:** players on a friend's server. **At the end:** you know what leaves your computer, who
can read it, and how to take it back.

## What leaves your computer

The program on your computer reads the files WoW and your addons write in the `_forever_`
folder and sends what's new to your friend's server, with your own upload password.

- **Sent:** your addons' saved files (every character), and WoW's combat logs if you turned
  combat logging on.
- **About other players, only this:** the name (no realm) of a player you grouped with, saw
  nearby or targeted, with when and in which zone: what the People tab shows. In your combat
  log, anyone but you and the monsters is "another player".
- **Also read, for item facts only:** the game's hotfix cache, for the items Forever added
  that no item data has yet: the ids of the weapons and armour in it, then name, slot, type,
  quality, item level, required level, stats, speed, icon and sell price of the ones the server
  asks for. `item_facts = false` in the config turns it off.
- **Never sent:** your WoW or Battle.net password, your account name, anything outside the
  WoW folder.
- **Kept on your computer:** a copy of everything it sent, in `WoWArchive` in your home folder.
- **Updates:** the program updates itself from the server within a few hours of a new
  version, and takes only an update signed with the key it was installed with.
- **Battle.net data** (your characters' look, item level, gear, talents) is read every 2
  hours, for your own characters only, when the owner set it up.

## Who sees what

| Who | What they see |
|---|---|
| You, logged in | Everything: every tab, every character, the players your game showed you. |
| The owner | Everything you send. They run the server and its database: trust them like anyone holding your files. |
| Friends with your zogzog link | Your page minus the private parts: never other players, what killed you, or where you are now. |
| Anyone, once you switch your public page on | Your characters' card, climb and dungeons. No gold, zones, journal or other players. Off until you turn it on. |
| Your AI, once you connect it | Your own data, nothing from anyone else's page. Battle.net data only with "Battle.net data to my AI" on in Settings. |

Your zogzog link works like a password. Sent to the wrong person: Settings, **Make a new
link**; the old one stops at the next update. Hide a character from either page: Settings,
Characters, untick it.

<details>
<summary>Everything the owner can see</summary>

- **Your game data**, including the names of other players your game showed you, with when
  and where. Before version 0.7.0 the program sent combat logs line by line, with every player
  in range: the owner can delete those lines ([the event contract](../contract.md)).
- **What you or your AI write down**: goals for the play analysis, what a plan should work
  around, the findings it kept.
- **The web server's log**: each request from your browser, your AI or the program, with the
  time, your IP address, your browser, the page (without the part after `?`) and who you are
  logged in as.
- **The app's own log**: AI logins, a tool that failed, installs and updates of the program,
  when you're added or removed, and a character's name in a few lines.
- **The login service**: your email address or BattleTag, your name, when you logged in.
- **Their overview page**: when you last opened your page, used your AI and sent data, your
  program's version, how many characters, whether the addons are all there. No character
  names, zones or gold.
- **What your AI asks for**: the tool it picked and what it filled in. Never your
  conversation: the answers go to your AI provider.

How long logs and backups last is up to the owner: the Privacy page in the app has their
numbers.

</details>

## Delete your data

1. Ask the owner to take you off their friends list. Within minutes your events, settings,
   goals, plans, upload password and pages are gone. Backups and logs expire on their own,
   after the days the Privacy page shows. Your login account is separate: ask them to delete
   it too.
2. [Uninstall the program](getting-started.md#uninstall).
3. Delete `WoWArchive` in your home folder if you don't want the local copy either.

The rules in full, for whoever runs a server: [Privacy tiers](../explanation/privacy.md).

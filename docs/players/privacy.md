# What's shared, and with whom

**For:** players on a friend's server who want to know who sees their data, and how to take it
back. **At the end:** you know what leaves your computer, who can read it, what stays private,
and how to delete it.

## What leaves your computer

The program on your computer reads the files WoW and your addons write in the `_forever_`
folder, and sends them to the server you were invited to, with your own upload password.

- **Sent:** your addons' saved files (every character), and WoW's combat logs if you turned
  combat logging on.
- **Never sent:** your WoW or Battle.net password, your Battle.net account name, anything
  outside the WoW folder.
- **Kept on your computer too:** a copy of everything it sent, in `WoWArchive` in your home
  folder. Uninstalling leaves it; delete the folder if you don't want it.
- **Updates:** the program updates itself from the server, without asking, within a few hours
  of a new version. You trust the owner (who runs the server) like anyone whose program
  you install.
- **Battle.net data** (your characters' look, item level, talents) is read once a day for your
  own characters only. A character name someone else on the server also sends gets none, unless
  it is the owner's.

## Who sees what

| Who | What they see |
|---|---|
| You, logged in | Everything: your page, every tab, every character. Other players appear in it (the People tab, groups) because the game showed them to you. |
| The owner, who runs the server | Everything you send, and the logs below. They run the database it lives in, so trust them like you'd trust anyone holding your files. |
| Friends you send your zogzog link to | Your page minus the private parts: the journal, gear advice, prices, deaths per zone and level, dungeons. Never other players, what killed you, or where your character is right now. |
| Anyone, once you switch your public page on | Your characters' card, level climb and dungeons cleared. No gold, no zones, no journal, no other players. Off until you turn it on in Settings. |
| Your AI assistant, once you connect it | What its questions read from your own data, the players your game showed you included. Nobody else's page. Battle.net data (look, item level, the WoW Token) only if you turn on "Battle.net data to my AI" in Settings. |

Your zogzog link works like a password: anyone who has it can open it. Send it only to people
you want there. Sent to the wrong person: Settings, **Make a new link**. The old one stops
working at the next update.

Hide a character from either page: Settings, Characters, untick it.

## What the owner can see

- **Your game data**, including the names of other players your game showed you: in groups,
  nearby, and in your combat logs.
- **What you or your AI write down**: the goals you gave the play analysis, what you asked a
  plan to work around, and the findings the analysis kept.
- **The web server's log**: one line each time your browser, your AI or the program on your
  computer asks the server for something. The time, your IP address, your browser, the page
  asked for (without the part after `?`) and who you are logged in as.
- **The app's own log**: when your AI logs in, the name of an AI tool that failed, when you
  install or update the program, and when you get added or removed, plus a character's name
  in a few lines (a picture asked for, an analysis that failed). No other game content.
- **The login service**, which they run or manage: your email address, your name, when you
  logged in.
- **Their overview page**: when you last opened your page, used your AI and sent data, the
  version of the program on your computer, how many characters you have, and whether the
  addons are all there. No character names, zones or gold.
- **What your AI asks for**: the tool it picked (like "show my gear") and what it filled in
  (a character's name, your goals). Never your conversation with your AI. The answers go to
  your AI provider.

How long the logs and backups last is up to the owner. The Privacy page
in the app shows their numbers.

## Delete your data

1. Ask the owner to take you off their friends list. Within minutes your
   events, settings, goals, plans, upload password and pages are gone. Backups and logs
   expire on their own, after the number of days the Privacy page in the app shows. Nobody
   deletes them by hand. Your login account is separate: ask them to delete it too.
2. Uninstall the program: [the uninstall line](getting-started.md#uninstall) for your
   computer.
3. Delete `WoWArchive` in your home folder, if you don't want the local copy either.

Your addons stay in WoW: remove them like any other addon.

The full rules, for the people who run a server: [Privacy tiers](../explanation/privacy.md).

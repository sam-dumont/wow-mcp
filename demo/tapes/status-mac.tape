# Is the shipper running? It runs every 5 minutes and writes a log: the last
# lines say what it found and what reached the page. (The run before the
# take stands in for the background job, which this take doesn't load.)
Source @SETTINGS@
Hide
Type "uv run --script ~/.local/share/wow-stats/wow_stats.py >/dev/null 2>&1; clear"
Enter
Wait@120s /❯\s*$/
Show
Sleep 700ms
Type@35ms "tail -n 4 ~/Library/Logs/wow-stats.log"
Sleep 500ms
Enter
Sleep 6s

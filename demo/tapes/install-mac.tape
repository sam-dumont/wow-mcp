# Step 4 on a Mac: paste the one line from the start page; the installer
# finds WoW, writes the config and sets up the background job. Then the
# config, password left out. record.sh fills @INSTALL_URL@ with the line the
# wizard handed out in the browser take.
Source demo/tapes/.settings.tape
Sleep 700ms
Type@35ms "curl -fsSL @INSTALL_URL@ | bash"
Sleep 600ms
Enter
Wait+Screen@180s /Play a bit/
Sleep 2s
Type `grep -v '^password' ~/.config/wow-stats/config.toml`
Enter
Sleep 4s

# Step 3 on a Mac: paste the one line from the start page; the installer
# finds WoW, writes the config and sets up the background job. Then the
# config, password left out. record.sh fills @INSTALL_URL@ with the line the
# wizard handed out in the browser take.
Source @SETTINGS@
Sleep 700ms
Type@35ms "curl -fsSL @INSTALL_URL@ | bash"
Sleep 600ms
Enter
# The checklist is taller than the terminal: wait for the prompt, not a line of it.
Wait@180s /❯\s*$/
Sleep 2s
Type `grep -v '^password' ~/.config/wow-stats/config.toml`
Enter
Sleep 4s

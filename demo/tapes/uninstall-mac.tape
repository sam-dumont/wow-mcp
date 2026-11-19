# Uninstalling: the line from the help page. The program and the config go,
# the archive (WoWArchive) stays. launchctl is a no-op in this take (a shim on
# PATH): the real one is per macOS user and would stop this Mac's own shipper.
Source demo/tapes/.settings.tape
Sleep 700ms
Type@25ms "@UNINSTALL@"
Sleep 600ms
Enter
Sleep 1500ms
Type@35ms "ls ~/.config/wow-stats ~/.local/share/wow-stats ~/WoWArchive"
Sleep 400ms
Enter
Sleep 5s

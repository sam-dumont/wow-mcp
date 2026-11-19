# Updating: the same line for everyone, no code. It keeps the config and the
# upload password.
Source demo/tapes/.settings.tape
Sleep 700ms
Type@35ms "curl -fsSL @UPDATE_URL@ | bash"
Sleep 600ms
Enter
Wait+Screen@180s /is updated/
Sleep 4s

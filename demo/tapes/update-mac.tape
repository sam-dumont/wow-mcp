# Updating: the same line for everyone, no code. It keeps the config and the
# upload password.
Source demo/tapes/.settings.tape
Sleep 700ms
Type@35ms "curl -fsSL @UPDATE_URL@ | bash"
Sleep 600ms
Enter
# The checklist is taller than the terminal: wait for the prompt, not a line of it.
Wait@180s /❯\s*$/
Sleep 4s

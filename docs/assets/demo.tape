# docs/assets/demo.tape — VHS recipe for a REAL Aida session GIF.
#
# Captures an actual ThreadSession against an isolated temp DB (never touches
# the user's .seedling_db). Re-run after UI changes:
#   vhs docs/assets/demo.tape
#
# Requires: ollama running, llama3.2 pulled, `vhs` installed (brew install vhs).

Output docs/assets/demo.gif
Set Shell zsh
Set FontSize 15
Set Width 900
Set Height 558
Set Theme "Catppuccin Mocha"
Set TypingSpeed 1ms
Set PlaybackSpeed 1.0

# Launch hidden so the GIF does not open on the env/command line.
Hide
Type "AIDA_VOICE=0 AIDA_DEMO_MODEL=llama3.2 FORCE_COLOR=1 ./.venv/bin/python scripts/record_demo.py"
Enter
Sleep 2s
Show

# Live session through restore (~30–60s on llama3.2).
Sleep 75s

# docs/assets/demo.tape — VHS recipe for a REAL Aida session GIF.
#
# Captures an actual ThreadSession against an isolated temp DB (never touches
# the user's .seedling_db). Re-run after UI changes:
#   vhs docs/assets/demo.tape
#
# Requires: ollama running, llama3.2 pulled, `vhs` installed (brew install vhs).
#
# The capture walks four beats — teach, correct, refuse-to-invent, restore.
# All four are deterministic or guard-enforced, so the GIF reproduces. The
# collaborative wall is deliberately absent; see scripts/record_demo.py for why.

Output docs/assets/demo.gif
Set Shell zsh
Set FontSize 15
Set Width 900
Set Height 600
Set Framerate 8
Set Theme "Catppuccin Mocha"
Set TypingSpeed 1ms
Set PlaybackSpeed 1.0

# Launch hidden so the GIF does not open on the env/command line.
Hide
Type "AIDA_VOICE=0 AIDA_DEMO_MODEL=llama3.2 FORCE_COLOR=1 ./.venv/bin/python scripts/record_demo.py"
Enter
Sleep 2s
Show

# Live session: 5 turns across two sessions. Measured at ~35s on an M1 Max with
# llama3.2; 60s leaves headroom without paying for a minute of idle frames.
# Framerate 8 (not the 50fps default) keeps the GIF near ~1MB — the beats are
# streamed text, not animation, so the extra frames buy nothing.
Sleep 60s

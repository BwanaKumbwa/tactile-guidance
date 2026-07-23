"""Shared navigation constants for belt ↔ bracelet handoff."""

# Enter grasp mode (bracelet on, belt off) when depth ≤ this.
HANDOFF_ENTER_CM = 50.0

# Resume approach mode (belt on) only when depth ≥ this.
# Hysteresis avoids chatter when ARCore depth jitters near the boundary.
HANDOFF_EXIT_CM = 70.0

# Practical far limit for approach guidance (matches ~3 m use case).
MAX_APPROACH_CM = 300.0

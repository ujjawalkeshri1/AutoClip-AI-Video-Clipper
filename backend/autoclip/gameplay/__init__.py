"""Gameplay highlight engine: multi-signal detection for gameplay footage.

AutoClip's original highlight stage reads a transcript. That finds nothing in
silent gameplay, so this package adds signals that don't need speech: audio
energy/impact analysis, motion and optical flow, flash and colour cues, cheap
cut detection, optional vision-model keyframe checks, and a configurable
six-factor score. Everything here is a heuristic estimate of interest, not a
prediction of audience performance.
"""

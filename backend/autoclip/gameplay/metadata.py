"""Titles, hooks, descriptions and hashtags.

Everything generated without a language/vision model is built from *evidence the
detector actually has*, so titles stay truthful. A low-confidence detection gets
a generic title rather than a confident claim about what happened.
"""

from __future__ import annotations

import re

from .candidates import LABELS, Candidate
from .signals import STEP_S

TITLES = {
    "EXPLOSION": ["A huge explosion in GTA 6 gameplay", "GTA 6 explosion moment, watch the whole thing",
                  "GTA 6: the explosion and its aftermath"],
    "CRASH": ["GTA 6 gameplay: a sudden crash", "Watch the speed drop to zero in GTA 6",
              "GTA 6: from full speed to a stop"],
    "POLICE_CHASE": ["GTA 6 police chase: sirens and speed", "Can you escape? GTA 6 police chase",
                     "GTA 6: the chase heats up"],
    "SHOOTOUT": ["GTA 6 shootout breaks out", "GTA 6 gunfight, caught on gameplay",
                 "GTA 6: bullets start flying"],
    "STREAMER_REACTION": ["The reaction says it all (GTA 6)", "GTA 6 moment that got a big reaction",
                          "Streamer reacts to this GTA 6 moment"],
    "FUNNY": ["GTA 6 funny moment", "GTA 6 gameplay that made the stream laugh",
              "This GTA 6 moment is hard to explain"],
    "SURPRISE": ["An unexpected GTA 6 moment", "GTA 6 gameplay: out of the blue",
                 "GTA 6: a sudden turn"],
    "NPC_INTERACTION": ["GTA 6 NPC moment", "The NPCs in GTA 6 do the unexpected",
                        "GTA 6 NPC interaction"],
    "MISSION_HIGHLIGHT": ["GTA 6 mission highlight", "A tense GTA 6 mission moment",
                          "GTA 6 story mission clip"],
    "ACTION": ["Intense GTA 6 gameplay moment", "GTA 6 action, uncut", "GTA 6 gameplay highlight"],
}
HOOKS = {
    "EXPLOSION": "Watch what happens next", "CRASH": "Watch the speed", "POLICE_CHASE": "Can they get away?",
    "SHOOTOUT": "It escalates fast", "STREAMER_REACTION": "Listen to the reaction",
    "FUNNY": "Wait for it", "SURPRISE": "Did not see that coming", "NPC_INTERACTION": "Watch the NPC",
    "MISSION_HIGHLIGHT": "This mission gets tense", "ACTION": "Stay for the ending",
}
TAGS = {
    "EXPLOSION": ["#explosion"], "CRASH": ["#crash"], "POLICE_CHASE": ["#policechase"],
    "SHOOTOUT": ["#shootout"], "STREAMER_REACTION": ["#reaction"], "FUNNY": ["#funny"],
    "SURPRISE": ["#unexpected"], "NPC_INTERACTION": ["#npc"], "MISSION_HIGHLIGHT": ["#mission"],
    "ACTION": ["#action"],
}
BASE_TAGS = ["#GTA6", "#GTAVI", "#Shorts", "#gaming", "#gameplay"]


def hashtags(c: Candidate) -> list[str]:
    tags = BASE_TAGS + TAGS.get(c.category, []) + [t for x in c.tags for t in TAGS.get(x, [])[:1]]
    seen: list[str] = []
    for t in tags:
        if t.lower() not in [s.lower() for s in seen]:
            seen.append(t)
    return seen[:10]


def _first_sentence(text: str, max_words: int = 9) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    words = first.split()
    return " ".join(words[:max_words]) + ("…" if len(words) > max_words else "")


def explain(c: Candidate) -> str:
    when = f"{int(c.peak // 60)}:{int(c.peak % 60):02d}"
    ev = "; ".join(c.evidence) if c.evidence else "elevated activity"
    text = f"{LABELS[c.category]} around {when}: {ev}."
    v = c.meta.get("vision") or {}
    if v.get("description"):
        text += f" Vision model saw: {v['description']}"
    else:
        text += " Based on audio and motion cues only (no vision model ran)."
    if c.confidence < 0.55:
        text += " Low confidence: treat this label as a guess."
    return text


def build_metadata(c: Candidate) -> None:
    """Fill title options, hook, description, hashtags and thumbnail time."""
    titles = list(TITLES.get(c.category, TITLES["ACTION"]))
    vt = (c.meta.get("vision") or {}).get("title")
    llm_titles = c.meta.get("llm_titles")
    if llm_titles:
        titles = [t for t in llm_titles if t][:3] + titles
    elif vt:
        titles = [vt] + titles
    if c.confidence < 0.55:  # don't state a specific event we are not sure of
        titles = list(TITLES["ACTION"])
    titles = [t[:95] for t in dict.fromkeys(titles)][:3]

    spoken = _first_sentence(c.transcript_text)
    hook = c.meta.get("llm_hook") or HOOKS.get(c.category, HOOKS["ACTION"])
    c.meta.update({
        "titles": titles,
        "hook": hook,
        "spoken_hook": spoken,
        "hashtags": hashtags(c),
        "description": f"{titles[0]}. {LABELS[c.category]} moment from a long GTA 6 gameplay video. "
                       + " ".join(hashtags(c)[:5]),
        "thumbnail_s": round(c.peak + (0.4 if c.category in ("EXPLOSION", "CRASH") else 0.0), 2),
        "explanation": explain(c),
        "evidence": c.evidence,
        "category_label": LABELS[c.category],
        "peak_s": round(c.peak, 2),
        "confidence": round(c.confidence, 2),
    })

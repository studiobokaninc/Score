"""Color palette helpers for project/shot/task UI cards (SSR templates).

Project palettes are assigned by a stable hash of the project id so the same
project always lands on the same color across pages. Children of a project
(SEQ groups, shots, tasks shown inside that project) can use the matching
family via the ``child_*`` variants to keep the parent color recognizable
while still distinguishing siblings.

Task type palettes are categorical (independent of project), so a task's color
reflects its type regardless of which project it lives under.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


def _palette(key: str) -> Dict[str, str]:
    return {
        "key": key,
        # parent project card
        "card_bg": f"bg-{key}-50",
        "card_border": f"border-{key}-200",
        "title": f"text-{key}-900",
        "badge_bg": f"bg-{key}-100",
        "badge_text": f"text-{key}-700",
        "button_bg": f"bg-{key}-600",
        "button_hover": f"hover:bg-{key}-700",
        "side_bar": f"bg-{key}-400",
        "border_accent": f"border-{key}-400",
        "progress_track": f"bg-{key}-100",
        "progress_fill": f"bg-{key}-500",
        # child card variants (soft / med / strong)
        "child_soft_bg": f"bg-{key}-50",
        "child_soft_border": f"border-{key}-200",
        "child_med_bg": f"bg-{key}-100",
        "child_med_border": f"border-{key}-300",
        "child_strong_bg": f"bg-{key}-200",
        "child_strong_border": f"border-{key}-400",
        "child_badge_bg": f"bg-{key}-100",
        "child_badge_text": f"text-{key}-700",
        "child_icon_bg": f"bg-{key}-100",
        "child_strong_badge_bg": f"bg-{key}-600",
    }


# 12 distinct hues (Tailwind palette names). Order tuned so adjacent palettes
# are visually distinct, avoiding back-to-back analogous colors.
PROJECT_PALETTES: List[Dict[str, str]] = [
    _palette("indigo"),
    _palette("emerald"),
    _palette("amber"),
    _palette("rose"),
    _palette("cyan"),
    _palette("violet"),
    _palette("teal"),
    _palette("orange"),
    _palette("sky"),
    _palette("lime"),
    _palette("pink"),
    _palette("fuchsia"),
]


def _hash_id(value: Any) -> int:
    """Deterministic non-negative hash from any id-like value."""
    if value is None or value == "":
        return 0
    s = str(value)
    h = 0
    for ch in s:
        h = (h * 31 + ord(ch)) & 0x7FFFFFFF
    return h


def get_project_palette(project_id: Any, fallback_idx: int = 0) -> Dict[str, str]:
    if project_id is None or project_id == "":
        idx = fallback_idx
    else:
        idx = _hash_id(project_id)
    return PROJECT_PALETTES[idx % len(PROJECT_PALETTES)]


# ---- SEQ / SHOT hierarchical palette (project hue → SEQ rotation → SHOT shade) ----

# For each project hue, list 4 nearby Tailwind hues to rotate SEQ-by-SEQ.
# Choosing nearby hues keeps the project's family recognizable while giving
# each SEQ a visibly distinct color.
_SEQ_HUE_ROTATION: Dict[str, List[str]] = {
    "indigo":  ["indigo", "blue", "violet", "sky"],
    "emerald": ["emerald", "teal", "green", "lime"],
    "amber":   ["amber", "yellow", "orange", "lime"],
    "rose":    ["rose", "pink", "red", "fuchsia"],
    "cyan":    ["cyan", "sky", "teal", "blue"],
    "violet":  ["violet", "purple", "indigo", "fuchsia"],
    "teal":    ["teal", "cyan", "emerald", "sky"],
    "orange":  ["orange", "amber", "red", "rose"],
    "sky":     ["sky", "blue", "cyan", "indigo"],
    "lime":    ["lime", "green", "yellow", "emerald"],
    "pink":    ["pink", "rose", "fuchsia", "red"],
    "fuchsia": ["fuchsia", "pink", "violet", "purple"],
}

# Shot-level shade rotation within a SEQ's hue. Lighter to denser, keeping
# good text contrast on slate-800.
_SHOT_SHADE_ROTATION: List[int] = [50, 100, 200, 100]


def get_seq_palette(project_id: Any, seq_index: int) -> Dict[str, str]:
    """Return a palette for a SEQ group inside the given project.

    The hue is picked from the project's nearby-hue rotation, indexed by
    ``seq_index`` so successive SEQs visually differ while still sitting in the
    project family.
    """
    proj = get_project_palette(project_id)
    hues = _SEQ_HUE_ROTATION.get(proj["key"], [proj["key"]])
    hue = hues[seq_index % len(hues)]
    return {
        "key": hue,
        "card_bg": f"bg-{hue}-50",
        "card_border": f"border-{hue}-300",
        "title": f"text-{hue}-900",
        "side_bar": f"bg-{hue}-400",
        "badge_bg": f"bg-{hue}-600",
        "badge_text": "text-white",
    }


def get_shot_palette(project_id: Any, seq_index: int, shot_index: int) -> Dict[str, str]:
    """Return a palette for a SHOT inside a SEQ inside a project.

    Hue follows the SEQ; shade rotates SHOT-by-SHOT so SHOTs in the same SEQ
    still differ. Keeps the SEQ recognizable through the family hue.
    """
    seq = get_seq_palette(project_id, seq_index)
    hue = seq["key"]
    shade = _SHOT_SHADE_ROTATION[shot_index % len(_SHOT_SHADE_ROTATION)]
    border_shade = min(shade + 200, 400)
    return {
        "key": hue,
        "shade": shade,
        "bg": f"bg-{hue}-{shade}",
        "border": f"border-{hue}-{border_shade}",
        "title": f"text-{hue}-900",
    }


def attach_project_palettes(projects: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return a list with a ``palette`` key added to each project dict.

    Non-dict items pass through unchanged. Existing ``palette`` keys are
    overwritten so callers can re-attach safely.
    """
    out: List[Dict[str, Any]] = []
    for i, p in enumerate(projects or []):
        if isinstance(p, dict):
            new_p = dict(p)
            new_p["palette"] = get_project_palette(p.get("id"), fallback_idx=i)
            out.append(new_p)
        else:
            out.append(p)
    return out


# ---- Task type palette (categorical) ----

def _task_palette(key: str, label_shade: int = 800) -> Dict[str, str]:
    return {
        "key": key,
        "card_bg": f"bg-{key}-50",
        "card_border": f"border-{key}-200",
        "icon_bg": f"bg-{key}-100",
        "type_badge_bg": f"bg-{key}-100",
        "type_badge_text": f"text-{key}-700",
        "bar": f"bg-{key}-400",
        "border_accent": f"border-{key}-400",
        "label": f"text-{key}-{label_shade}",
    }


_DEFAULT_TASK_PALETTE: Dict[str, str] = {
    "key": "other",
    "card_bg": "bg-slate-50",
    "card_border": "border-slate-200",
    "icon_bg": "bg-slate-100",
    "type_badge_bg": "bg-slate-200",
    "type_badge_text": "text-slate-700",
    "bar": "bg-slate-400",
    "border_accent": "border-slate-400",
    "label": "text-slate-700",
}


TASK_TYPE_PALETTES: Dict[str, Dict[str, str]] = {
    "pm": _task_palette("indigo"),
    "mgmt": _task_palette("violet"),
    "management": _task_palette("violet"),
    "admin": _task_palette("violet"),
    "delivery": _task_palette("emerald"),
    "review": _task_palette("amber"),
    "qc": _task_palette("cyan"),
    "qa": _task_palette("cyan"),
    "check": _task_palette("cyan"),
    "retake": _task_palette("rose"),
    "reference": _task_palette("teal"),
    "shooting": _task_palette("orange"),
    "modeling": _task_palette("sky"),
    "lighting": _task_palette("amber"),
    "comp": _task_palette("violet"),
    "compositing": _task_palette("violet"),
    "anim": _task_palette("pink"),
    "animation": _task_palette("pink"),
    "rig": _task_palette("lime"),
    "rigging": _task_palette("lime"),
    "fx": _task_palette("fuchsia"),
    "other": _DEFAULT_TASK_PALETTE,
}


def get_task_type_palette(task_type: Optional[str]) -> Dict[str, str]:
    if not task_type:
        return _DEFAULT_TASK_PALETTE
    key = str(task_type).strip().lower()
    return TASK_TYPE_PALETTES.get(key, _DEFAULT_TASK_PALETTE)


def attach_task_palettes(tasks: Iterable[Any]) -> List[Any]:
    """Return tasks with a ``palette`` attribute/key based on the ``type`` field.

    Supports both dict tasks and dataclass-like / Pydantic objects (via setattr).
    For unsupported types the original item is returned unchanged.
    """
    out: List[Any] = []
    for t in tasks or []:
        if isinstance(t, dict):
            new_t = dict(t)
            new_t["palette"] = get_task_type_palette(t.get("type"))
            out.append(new_t)
        else:
            try:
                setattr(t, "palette", get_task_type_palette(getattr(t, "type", None)))
                out.append(t)
            except Exception:
                out.append(t)
    return out

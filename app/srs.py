"""Répétition espacée (variante simplifiée de SM-2, proche d'Anki)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

# 0 = À revoir, 1 = Difficile, 2 = Bien, 3 = Facile
GRADES = {0: "À revoir", 1: "Difficile", 2: "Bien", 3: "Facile"}


def schedule(card: dict[str, Any], grade: int) -> dict[str, Any]:
    ease = float(card.get("ease") or 2.5)
    interval = int(card.get("interval") or 0)
    reps = int(card.get("reps") or 0)
    lapses = int(card.get("lapses") or 0)

    if grade <= 0:
        lapses += 1
        reps = 0
        interval = 0  # revient dans la séance du jour
        ease = max(1.3, ease - 0.2)
    elif grade == 1:
        interval = max(1, round(interval * 1.2)) if reps else 1
        ease = max(1.3, ease - 0.15)
        reps += 1
    elif grade == 2:
        interval = 2 if reps == 0 else max(interval + 1, round(interval * ease))
        reps += 1
    else:
        if reps == 0:
            interval = 4
        else:
            interval = max(interval + 2, round(interval * ease * 1.3))
        ease = min(3.5, ease + 0.15)
        reps += 1

    due = date.today() + timedelta(days=interval)
    return {
        "ease": round(ease, 3),
        "interval": interval,
        "reps": reps,
        "lapses": lapses,
        "due": due.isoformat(),
        "last_review": date.today().isoformat(),
    }


def preview_intervals(card: dict[str, Any]) -> dict[int, int]:
    """Intervalle (en jours) qu'obtiendrait la carte pour chaque note — affiché sur les boutons."""
    return {g: schedule(card, g)["interval"] for g in GRADES}

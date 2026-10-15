"""Daily telemetry and manual corrections ("overrides")."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

FIELDS = ("uptime_hours", "consumption_kwh", "hashrate_ths")


@dataclass(frozen=True)
class EffectiveStat:
    uptime_hours: float | None
    consumption_kwh: float | None
    hashrate_ths: float | None
    overridden_fields: tuple[str, ...] = ()

    @property
    def is_override(self) -> bool:
        return bool(self.overridden_fields)


def apply_override(
    original: Mapping[str, float | None], override: Mapping[str, float | None] | None
) -> EffectiveStat:
    """Overlay a (possibly partial) correction on top of the original telemetry.

    A field that is ``None`` in the override means "keep the original value", so an operator
    can fix just the broken sensor reading (say, consumption) without re-entering the rest.
    A reading that was never reported stays ``None`` - a gap is not silently turned into zero.
    The original record is never modified, so it remains available for audits.
    """
    merged: dict[str, float | None] = {}
    overridden: list[str] = []
    for name in FIELDS:
        correction = override.get(name) if override else None
        if correction is not None:
            merged[name] = correction
            overridden.append(name)
        else:
            merged[name] = original.get(name)
    return EffectiveStat(**merged, overridden_fields=tuple(overridden))

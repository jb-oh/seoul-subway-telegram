"""Per-user preset storage backed by JSON files."""

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

PRESETS_DIR = Path(os.getenv("PRESETS_DIR", "presets"))


@dataclass
class Preset:
    name: str
    departure: str
    arrival: str
    line: str | None = None
    direction: str | None = None
    destination: str | None = None


def _user_file(user_id: int) -> Path:
    return PRESETS_DIR / f"{user_id}.json"


def _load(user_id: int) -> dict[str, Preset]:
    path = _user_file(user_id)
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {name: Preset(**p) for name, p in data.items()}
    except Exception:
        logger.exception("Failed to load presets for user %d", user_id)
        return {}


def _save(user_id: int, presets: dict[str, Preset]) -> None:
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    path = _user_file(user_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({name: asdict(p) for name, p in presets.items()}, f, ensure_ascii=False, indent=2)


def add_preset(
    user_id: int,
    name: str,
    departure: str,
    arrival: str,
    line: str | None = None,
    direction: str | None = None,
    destination: str | None = None,
) -> None:
    presets = _load(user_id)
    presets[name] = Preset(
        name=name, departure=departure, arrival=arrival,
        line=line, direction=direction, destination=destination,
    )
    _save(user_id, presets)


def get_preset(user_id: int, name: str) -> Preset | None:
    return _load(user_id).get(name)


def list_presets(user_id: int) -> list[Preset]:
    return list(_load(user_id).values())


def delete_preset(user_id: int, name: str) -> bool:
    presets = _load(user_id)
    if name not in presets:
        return False
    del presets[name]
    _save(user_id, presets)
    return True

"""
Typdefinitionen und Datenmodelle für Konfiguration, Speicherdaten und Events.

Ermöglicht statische Typprüfung (mypy/IDE) und Laufzeitvalidierung für:
- App-Konfiguration (md_config.json)
- ClientWork Server-Antworten aus Master Duel (md_memory.py, match_history.py)
"""

from typing import Dict, List, Literal, Optional, Tuple, TypedDict, Union, Any
from dataclasses import dataclass, field


SpeedProfile = Literal["fast", "normal", "slow"]
ReadMethod = Literal["memory", "ocr"]
Language = Literal["en", "de"]


class Point2D(TypedDict):
    x: int
    y: int


Coordinate = Union[List[int], Tuple[int, int]]


class AppConfig(TypedDict, total=False):
    """Schema der Konfiguration in md_config.json."""
    SEARCH_BAR: Coordinate
    FIRST_CARD: Coordinate
    TRASH_BTN: Coordinate
    TRASH_CONFIRM: Coordinate
    UNOWNED_BTN: Coordinate
    OFFSET_X: int
    OFFSET_Y: int
    CLICK_SPEED: float
    LANGUAGE: str
    IS_CALIBRATED: bool
    SPEED_PROFILE: str
    SEARCH_DELAY: float
    WINDOW_POS: Optional[Coordinate]
    CALIBRATED_SIZE: Optional[Coordinate]
    DECK_COUNT: Optional[Coordinate]
    AUTO_CALIBRATED: bool
    DECK_SHOW_SECOND_TURN: bool
    DECK_HOVER_ANIMATION: bool
    DECK_HOVER_SCAN_ENABLED: bool
    READ_METHOD: str
    WINRATE_MODE: str
    WINRATE_PERIOD: str
    UPDATE_NOTIFIED: Optional[str]


# ── ClientWork Server-Datenmodelle (md_memory.py / match_history.py) ──

class ClientWorkCardList(TypedDict, total=False):
    CardIds: List[int]


class ClientWorkDeckPart(TypedDict, total=False):
    Main: ClientWorkCardList
    Extra: ClientWorkCardList


class ClientWorkDuelHistoryItem(TypedDict, total=False):
    did: Union[str, int]
    myid: int
    time: Union[int, float]
    mode: int
    res: int
    turn: int
    finish: int
    first_player: Optional[int]
    deck: List[ClientWorkDeckPart]
    invalid: Optional[bool]


class ClientWorkDuel(TypedDict, total=False):
    did: Optional[Union[str, int]]
    MyID: int
    time: Union[int, float]
    FirstPlayer: Optional[int]
    Choice: Optional[int]
    name: Optional[List[str]]
    Deck: Optional[List[ClientWorkDeckPart]]


# ── Validierung der Konfiguration ──

VALID_SPEED_PROFILES = {"fast", "normal", "slow"}
VALID_READ_METHODS = {"memory", "ocr"}


def validate_config(raw: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """
    Prüft Konfigurationswerte auf Gültigkeit und liefert bereinigte Werte sowie Warnungen.
    Verändert ungültige Werte defensiv zu Standardwerten.
    """
    warnings: List[str] = []
    cleaned = dict(raw)

    speed = cleaned.get("SPEED_PROFILE")
    if speed is not None and speed not in VALID_SPEED_PROFILES:
        warnings.append(f"Ungültiges SPEED_PROFILE '{speed}', Fallback auf 'normal'.")
        cleaned["SPEED_PROFILE"] = "normal"

    method = cleaned.get("READ_METHOD")
    if method is not None and method not in VALID_READ_METHODS:
        warnings.append(f"Ungültige READ_METHOD '{method}', Fallback auf 'memory'.")
        cleaned["READ_METHOD"] = "memory"

    for coord_key in ("SEARCH_BAR", "FIRST_CARD", "TRASH_BTN", "TRASH_CONFIRM", "UNOWNED_BTN"):
        coord = cleaned.get(coord_key)
        if coord is not None:
            if not (isinstance(coord, (list, tuple)) and len(coord) == 2
                    and all(isinstance(v, (int, float)) for v in coord)):
                warnings.append(f"Koordinate {coord_key} fehlerhaft: {coord}")

    return cleaned, warnings

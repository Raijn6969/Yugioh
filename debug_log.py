"""
Diagnose-Log (md_debug.log) über Pythons logging.

Ein Lauf beginnt mit start_run(): Die bisherigen Logs werden als md_debug.1.log,
md_debug.2.log … aufbewahrt, damit man auch frühere Läufe noch vergleichen kann.
Ohne laufenden Import (z.B. in Tests) wird nichts geschrieben.
"""

import logging
import os

KEEP_OLD_LOGS = 3

logger = logging.getLogger("md_importer")
logger.setLevel(logging.INFO)
logger.propagate = False

_handler = None


def dlog(message: str) -> None:
    """Eine Zeile ins Diagnose-Log schreiben (abschließende Zeilenumbrüche werden entfernt)."""
    logger.info(message.rstrip("\n"))


def append(path: str, message: str) -> None:
    """Eine Zeile ans Log anhängen, auch ohne laufenden Import (z.B. Meldungen des Deck-Fensters)."""
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(message.rstrip("\n") + "\n")
    except OSError:
        pass


def start_run(path: str) -> None:
    global _handler
    end_run()
    _rotate(path)
    _handler = logging.FileHandler(path, mode="w", encoding="utf-8")
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)


def end_run() -> None:
    global _handler
    if _handler is not None:
        logger.removeHandler(_handler)
        _handler.close()
        _handler = None


def _rotate(path: str) -> None:
    base, ext = os.path.splitext(path)
    try:
        for i in range(KEEP_OLD_LOGS - 1, 0, -1):
            older = f"{base}.{i}{ext}"
            if os.path.exists(older):
                os.replace(older, f"{base}.{i + 1}{ext}")
        if os.path.exists(path):
            os.replace(path, f"{base}.1{ext}")
    except OSError as e:
        # Log ist gerade in einem Editor geöffnet o.Ä. → dann eben ohne Verlauf überschreiben
        logger.warning(f"Log-Rotation fehlgeschlagen: {e}")

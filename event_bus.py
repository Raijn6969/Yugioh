"""
Thread-sicherer Event-Bus zur Entkopplung von Hintergrund-Threads und Benutzeroberfläche.

Hintergrund-Threads (wie EditorWatcher, MatchWatcher, ReplayRecorder) emittieren Events,
ohne direkte Referenzen auf Tkinter-Widgets halten zu müssen. Dies verhindert:
- Race Conditions zwischen Threads
- TclError ('invalid command name') bei zerstörten Widgets
- Verwaiste Callbacks und Speicherlecks
"""

from dataclasses import dataclass, field
import inspect
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Type, TypeVar


logger = logging.getLogger("EventBus")


@dataclass
class Event:
    """Basisklasse für alle Events im System."""
    timestamp: float = field(default_factory=time.monotonic)


E = TypeVar("E", bound=Event)


class EventBus:
    """
    Zentraler, thread-sicherer Event-Bus.
    
    Unterstützt:
    - Typ-basierte Abonnements (subscribe auf spezifische Event-Klassen)
    - Optionale UI-Weiterleitung (über eine Warteschlange oder Haupt-Thread-Dispatcher)
    - Schutz gegen TclError / tote UI-Callbacks
    """

    def __init__(self, ui_dispatcher: Optional[Callable[[Callable, Any], None]] = None):
        """
        ui_dispatcher: Optionale Funktion (z.B. Overlay._run_on_ui), um Callbacks
                       sicher im Haupt-Thread auszuführen.
        """
        self._lock = threading.RLock()
        self._listeners: Dict[Type[Event], List[Callable[[Any], None]]] = {}
        self._ui_dispatcher = ui_dispatcher

    def set_ui_dispatcher(self, dispatcher: Callable[[Callable, Any], None]) -> None:
        """Setzt oder aktualisiert den Haupt-Thread-Dispatcher (z.B. nach UI-Initialisierung)."""
        with self._lock:
            self._ui_dispatcher = dispatcher

    def subscribe(self, event_type: Type[E], listener: Callable[[E], None]) -> Callable[[], None]:
        """
        Registriert einen Listener für einen bestimmten Event-Typ.
        Gibt eine unregister-Funktion zurück.
        """
        with self._lock:
            if event_type not in self._listeners:
                self._listeners[event_type] = []
            if listener not in self._listeners[event_type]:
                self._listeners[event_type].append(listener)

        def unsubscribe():
            self.unsubscribe(event_type, listener)

        return unsubscribe

    def unsubscribe(self, event_type: Type[E], listener: Callable[[E], None]) -> None:
        """Entfernt einen Listener für einen Event-Typ."""
        with self._lock:
            if event_type in self._listeners:
                try:
                    self._listeners[event_type].remove(listener)
                    if not self._listeners[event_type]:
                        del self._listeners[event_type]
                except ValueError:
                    pass

    def emit(self, event: Event, on_ui: bool = False) -> None:
        """
        Verteilt ein Event an alle registrierten Listener (auch für Oberklassen des Events).
        
        on_ui: Wenn True und ein ui_dispatcher konfiguriert ist, werden die Listener
               im UI-Thread ausgeführt.
        """
        event_type = type(event)
        with self._lock:
            target_listeners = []
            seen = set()
            for registered_type, listeners in self._listeners.items():
                if issubclass(event_type, registered_type):
                    for listener in listeners:
                        if id(listener) not in seen:
                            seen.add(id(listener))
                            target_listeners.append(listener)

        for listener in target_listeners:
            if on_ui and self._ui_dispatcher:
                self._ui_dispatcher(self._safe_invoke, listener, event)
            else:
                self._safe_invoke(listener, event)

    def _safe_invoke(self, listener: Callable[[Any], None], event: Event) -> None:
        """Ruft einen Listener auf und fängt Tkinter-TclError oder gelöschte Objekte sauber ab."""
        try:
            listener(event)
        except Exception as err:
            err_str = str(err)
            # Typische Tkinter-Fehler, wenn das Widget bereits geschlossen wurde
            if "invalid command name" in err_str or "application has been destroyed" in err_str:
                self.unsubscribe(type(event), listener)
            else:
                logger.warning("Fehler beim Verarbeiten von %s in Listener %r: %s",
                               type(event).__name__, listener, err)

    def clear(self) -> None:
        """Löscht alle Abonnements."""
        with self._lock:
            self._listeners.clear()


# ── Spezifische Anwendungs-Events ──

@dataclass
class EditorVisibilityEvent(Event):
    """Signalisiert, ob der Master Duel Deck-Editor im Vordergrund sichtbar ist."""
    visible: bool = False


@dataclass
class MatchRecordedEvent(Event):
    """Wird ausgelöst, wenn ein Duell erfasst oder aktualisiert wurde."""
    record: Any = None
    is_new: bool = True


@dataclass
class ReplaySavedEvent(Event):
    """Wird ausgelöst, wenn ein Replay erfolgreich mitgeschnitten und gespeichert wurde."""
    replay_info: Any = None
    steps: int = 0


@dataclass
class MemoryReadyEvent(Event):
    """Signalisiert den Status der Speicherlese-Verbindung zu Master Duel."""
    ready: bool = False
    details: str = ""


# Globaler Standard-Bus für die Anwendung
default_bus = EventBus()

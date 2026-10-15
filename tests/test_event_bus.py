"""Tests für den thread-sicheren EventBus."""

import threading
import time
import unittest
from event_bus import EventBus, Event, EditorVisibilityEvent, MatchRecordedEvent


class TestEventBus(unittest.TestCase):
    def test_subscribe_and_emit(self):
        bus = EventBus()
        received = []

        def listener(evt: EditorVisibilityEvent):
            received.append(evt.visible)

        bus.subscribe(EditorVisibilityEvent, listener)
        bus.emit(EditorVisibilityEvent(visible=True))
        bus.emit(EditorVisibilityEvent(visible=False))

        self.assertEqual(received, [True, False])

    def test_unsubscribe(self):
        bus = EventBus()
        received = []

        unsub = bus.subscribe(EditorVisibilityEvent, lambda e: received.append(e.visible))
        bus.emit(EditorVisibilityEvent(visible=True))
        unsub()
        bus.emit(EditorVisibilityEvent(visible=False))

        self.assertEqual(received, [True])

    def test_ui_dispatcher(self):
        dispatched = []

        def fake_dispatcher(fn, *args):
            dispatched.append((fn, args))
            fn(*args)

        bus = EventBus(ui_dispatcher=fake_dispatcher)
        received = []
        bus.subscribe(MatchRecordedEvent, lambda e: received.append(e.record))

        bus.emit(MatchRecordedEvent(record="123"), on_ui=True)

        self.assertEqual(len(dispatched), 1)
        self.assertEqual(received, ["123"])

    def test_dead_widget_tcl_error_auto_unsubscribes(self):
        bus = EventBus()

        def dead_listener(evt):
            raise RuntimeError("invalid command name .!label2")

        bus.subscribe(EditorVisibilityEvent, dead_listener)
        # Should not raise exception
        bus.emit(EditorVisibilityEvent(visible=True))
        # Listener should have been removed
        self.assertNotIn(EditorVisibilityEvent, bus._listeners)

    def test_multithreaded_emit(self):
        bus = EventBus()
        count = [0]
        lock = threading.Lock()

        def listener(evt):
            with lock:
                count[0] += 1

        bus.subscribe(Event, listener)

        threads = [threading.Thread(target=lambda: bus.emit(Event())) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(count[0], 20)

    def test_polymorphic_subscription(self):
        bus = EventBus()
        received = []

        bus.subscribe(Event, lambda evt: received.append(type(evt).__name__))
        bus.emit(EditorVisibilityEvent(visible=True))
        bus.emit(MatchRecordedEvent(record="abc"))

        self.assertEqual(received, ["EditorVisibilityEvent", "MatchRecordedEvent"])


if __name__ == "__main__":
    unittest.main()

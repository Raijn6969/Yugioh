"""Tests für die dunkle Scrollleiste (dark_scrollbar.py). Nur Tk-Ereignisse, keine echten Eingaben."""

import unittest
from types import SimpleNamespace
from unittest import mock

from dark_scrollbar import DarkScrollbar, HANDLE, HANDLE_DRAG, HANDLE_HOVER
from tests.test_staples import HAS_TK

if HAS_TK:
    import tkinter as tk

HEIGHT = 200


@unittest.skipUnless(HAS_TK, "kein Tk")
class DarkScrollbarTest(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.attributes("-alpha", 0)  # unsichtbar, aber eingeblendet: nur dann kommen Tk-Ereignisse an
        self.root.geometry("300x300+0+0")
        self.command = mock.Mock()
        self.bar = DarkScrollbar(self.root, command=self.command, bg="#1e1e1e", scale=1.0)
        self.bar.place(x=0, y=0, width=8, height=HEIGHT)
        self.root.update()

    def tearDown(self):
        self.root.destroy()

    def handle(self):
        coords = self.bar.coords("griff")
        return (coords[1], coords[3]) if coords else None

    def test_griff_an_der_richtigen_stelle(self):
        self.bar.set(0.25, 0.5)
        y0, y1 = self.handle()
        r = (self.bar.width - 2) / 2  # Linie mit runden Enden: Kappen ragen um r über die Koordinaten hinaus
        # Länge 50 (Viertel), Laufweg 150, first darf bis 0.75: Oberkante bei 0.25 / 0.75 * 150 = 50
        self.assertAlmostEqual(y0 - r, 50, delta=1)
        self.assertAlmostEqual(y1 + r - (y0 - r), 50, delta=1)
        self.assertEqual(self.bar.itemcget("griff", "fill"), HANDLE)
        self.assertEqual(self.bar.cget("width"), "8")

    def test_alles_sichtbar_kein_griff_breite_bleibt(self):
        self.bar.set(0.25, 0.5)
        self.bar.set("0", "1")  # Tk liefert Strings
        self.assertIsNone(self.handle())
        self.assertEqual(self.bar.winfo_reqwidth(), 8)

    def test_mindesthoehe_des_griffs(self):
        self.bar.set(0.0, 0.01)
        y0, y1 = self.handle()
        self.assertAlmostEqual(y1 - y0 + (self.bar.width - 2), 24, delta=1)

    def test_ziehen_ruft_moveto(self):
        self.bar.set(0.0, 0.5)  # Griff 0..100, Laufweg 100 Pixel, first darf bis 0.5
        self.bar._on_press(SimpleNamespace(y=50))
        self.assertEqual(self.bar.itemcget("griff", "fill"), HANDLE_DRAG)
        self.bar._on_drag(SimpleNamespace(y=100))
        self.command.assert_called_once()
        name, value = self.command.call_args[0]
        self.assertEqual(name, "moveto")
        self.assertAlmostEqual(value, 0.25, places=2)
        self.bar._on_release(SimpleNamespace(y=180))
        self.assertEqual(self.bar.itemcget("griff", "fill"), HANDLE)

    def test_klick_in_die_laufbahn_blaettert(self):
        self.bar.set(0.25, 0.5)
        self.bar._on_press(SimpleNamespace(y=HEIGHT - 5))
        self.command.assert_called_with("scroll", 1, "pages")
        self.bar._on_press(SimpleNamespace(y=2))
        self.command.assert_called_with("scroll", -1, "pages")

    def test_hover_faerbt_griff(self):
        self.bar.set(0.0, 0.5)
        self.bar._on_motion(SimpleNamespace(y=10))
        self.assertEqual(self.bar.itemcget("griff", "fill"), HANDLE_HOVER)
        self.bar._on_motion(SimpleNamespace(y=190))
        self.assertEqual(self.bar.itemcget("griff", "fill"), HANDLE)

    def test_tk_ereignisse(self):
        self.bar.set(0.0, 0.5)
        self.bar.event_generate("<ButtonPress-1>", x=3, y=20)
        self.bar.event_generate("<B1-Motion>", x=3, y=70)
        self.bar.event_generate("<ButtonRelease-1>", x=3, y=70)
        name, value = self.command.call_args[0]
        self.assertEqual(name, "moveto")
        self.assertAlmostEqual(value, 0.25, places=2)

    def test_mausrad_ueber_der_leiste(self):
        self.bar.set(0.0, 0.5)
        self.bar.event_generate("<MouseWheel>", delta=-120, x=3, y=20)
        self.command.assert_called_once_with("scroll", 2, "units")
        self.command.reset_mock()
        self.bar._on_wheel(SimpleNamespace(delta=60))  # kleines Delta: mindestens ein Schritt
        self.command.assert_called_once_with("scroll", -1, "units")
        self.command.reset_mock()
        self.bar.set(0, 1)  # alles sichtbar: nichts tun
        self.bar.event_generate("<MouseWheel>", delta=-120, x=3, y=20)
        self.command.assert_not_called()

    def test_mit_canvas_und_text(self):
        canvas = tk.Canvas(self.root, scrollregion=(0, 0, 50, 4 * HEIGHT))
        text = tk.Text(self.root, height=10)
        text.insert("1.0", "\n".join(str(i) for i in range(100)))
        for widget in (canvas, text):
            bar = DarkScrollbar(self.root, command=widget.yview, bg="#1e1e1e")
            widget.config(yscrollcommand=bar.set)
            widget.place(x=20, y=0, width=50, height=HEIGHT)
            bar.place(x=0, y=0, width=8, height=HEIGHT)
            self.root.update()
            self.assertIsNotNone(bar.coords("griff"))  # Teil sichtbar: Griff da
            before = widget.yview()[0]
            bar.command("scroll", 1, "pages")
            self.assertGreater(widget.yview()[0], before)
            bar.command("moveto", 0.5)
            self.assertAlmostEqual(widget.yview()[0], 0.5, places=1)


if __name__ == "__main__":
    unittest.main()

"""
Master Duel per Speicher lesen – NUR LESEND.

Statt den Kartennamen im Detail-Panel per Texterkennung zu lesen, wird die Karten-ID direkt aus dem
Speicher des Spiels gelesen (Windows ReadProcessMemory). Es wird nichts geschrieben und kein Code in das
Spiel geladen.

Master Duel ist ein Unity-Spiel (IL2CPP). Die Namen aller Klassen und Felder stehen in
masterduel_Data/il2cpp_data/Metadata/global-metadata.dat, die das Spiel in den Speicher lädt. Darüber
werden die Adressen bei jedem Start neu gefunden (keine festen Adressen → übersteht Spiel-Updates, solange
Konami die Klassen nicht umbenennt):
  1. Metadaten im Speicher finden (gleicher Dateikopf).
  2. Klasse "ContentViewControllerManager" finden: Ihre Klassenstruktur (Il2CppClass) zeigt bei +0x10 auf
     ihren Namen in den Metadaten (einmalige Suche im Speicher, läuft im Hintergrund vor – siehe preload).
  3. Über ihr statisches Feld "Instance" und deren "viewStack" (offene Fenster) den Deck-Editor finden –
     ohne Suche, immer der gerade angezeigte.
  4. Felder über ihre Namen auflösen (Il2CppClass.fields → Name + Offset).

Im Speicher stehen Konamis interne Karten-IDs (z.B. 12950 = Ash Blossom), nicht die Passcodes.

Außerdem (Winrate-Tracker): Die Klasse "ClientWork" hält die Antworten des Servers als verschachtelte
Dictionaries, z.B. "DuelHistory" (die Match History mit beiden Decks) und "Deck" (die eigenen Decks mit Namen).
"""

import ctypes
import os
import struct
import threading
import time
from ctypes import wintypes
from typing import Dict, List, NamedTuple, Optional, Tuple

import win_api

PROCESS_VM_READ, PROCESS_QUERY_INFORMATION = 0x10, 0x400
MEM_COMMIT, MEM_PRIVATE, MEM_MAPPED = 0x1000, 0x20000, 0x40000
PAGE_GUARD, PAGE_NOACCESS = 0x100, 0x01
CHUNK = 1 << 24  # 16 MB pro Lesevorgang beim Durchsuchen

# Il2CppClass (64 Bit, Unity 2021/2022): Positionen der benötigten Einträge
CLASS_NAME, CLASS_NAMESPACE, CLASS_PARENT, CLASS_FIELDS = 0x10, 0x18, 0x58, 0x80
FIELD_INFO_SIZE = 0x20   # FieldInfo: name, type, parent, offset, token
OBJECT_HEADER = 0x10     # Il2CppObject: klass, monitor
ARRAY_DATA = 0x20        # Il2CppArray: Header + bounds + max_length

MANAGER_CLASS = ("YgomGame.Menu", "ContentViewControllerManager")
EDITOR_CLASS = ("YgomGame", "DeckEditViewController2")
CLIENT_WORK_CLASS = ("YgomSystem.Utility", "ClientWork")
# Spielfeld während eines Replays (replay_recorder): DuelClient.instance → <effectWorker> (RunEffectWorker) →
# <goManager> (DuelGameObjectManager) → cardRoots (eine CardRoot je Karte, die das Spiel gerade zeigt)
DUEL_CLIENT_CLASS = ("YgomGame.Duel", "DuelClient")
# Duell-Protokoll (nur Replays): YgomGame.Duel.DuelLogController hält die Zeitleiste m_LogBaseItemList (je Eintrag
# Bezeichnung + Index) und je Bezeichnung eine Liste m_DataList_<Bezeichnung> mit den Daten (Il2Cpp-Structs)
LOG_CLASS = ("YgomGame.Duel", "DuelLogController")
ARRAY_ELEMENT_SIZE = 0x104   # Il2CppClass eines Arrays: Größe eines Elements (Struct-Listen)
LOG_MAX = 50000              # Schutz vor kaputten Längen
LOG_SEARCH_PAUSE = 10.0      # Sekunden Pause nach erfolgloser Suche nach dem Controller (die Suche dauert ~8 s)
LOG_SEARCH_MAX_PAUSE = 60.0  # jede weitere erfolglose Suche verdoppelt die Pause bis hierhin
LOG_BASE = struct.Struct("<Qi")   # LogBaseData: loglabel (String*), dataindex
# Aufbau der Datensätze (aus einem echten Replay entschlüsselt; die Elementgröße des Spiels wird dazu geprüft):
#   ShowAction (32 Byte): links LogDataSide {dataint i32, boolbits, zone, team, datatype}, rechts ebenso, dann
#   LogDataCenter {dataint0 i32, dataint1 i32, boolbits, databyte0, AKTIONSCODE, datatype} und isCost (Byte 28)
#   ShowChain: cardid u16, chainnum u16, team i32, type i32 (0 = Glied aufgebaut, 1 = löst auf)
#   ShowTurn: Spieler i32, Zug-1 i32, ?, restlpl i32 (Byte 12), restlpr i32 (Byte 16)
LOG_SIDES = struct.Struct("<iBBBBiBBBB")
LOG_CENTER = struct.Struct("<iiBBBB")
CARD_INTS = {"uid": "<uniqueId>k__BackingField", "team": "<team>k__BackingField", "zone": "position",
             "index": "<index>k__BackingField", "card": "<cardId>k__BackingField", "atk": "m_Atk", "def": "m_Def"}
CARD_BYTES = {"face": "<isFace>k__BackingField", "attack": "<isAttack>k__BackingField"}
CLASS_STATIC_FIELDS = 0xB8
DICT_ENTRY_SIZE = 24     # Dictionary<string, object>.Entry: hashCode, next, key, value
MAX_ITEMS = 5000         # Schutz vor kaputten Längen (Spiel lädt gerade um o.Ä.)


# Ausführung einer Karte (CardBaseData.PremiumID / CardDetailView.m_Premium)
PREMIUM_NAMES = {1: "normal", 2: "Shiny", 3: "Royal"}


class SearchEntry(NamedTuple):
    """Ein Platz der Kartenliste: jede Ausführung (normal/Shiny/Royal) ist ein eigener Eintrag."""
    kid: int        # Konami-ID (jedes Artwork hat eine eigene)
    premium: int    # 1 = normal, 2 = Shiny, 3 = Royal
    owned: int      # so viele Kopien dieser Ausführung besitzt der Spieler


class ListScroll(NamedTuple):
    """Scroll-Stand der Kartenliste (UI-Einheiten des Spiels)."""
    position: float     # so weit ist die Liste nach unten gescrollt (0 = ganz oben)
    maximum: float      # weiter geht es nicht (Ende der Liste)
    row_height: float   # Höhe einer Kartenreihe
    columns: int        # Karten pro Reihe
    wheel_step: float   # so weit scrollt eine Mausrad-Raste
    view_height: float = 684.0  # Höhe des sichtbaren Bereichs (Scroll-Leiste: Griff = Sichtbereich / Liste)


class BoardCard(NamedTuple):
    """Eine Karte auf dem Spielfeld (Zonen: Konstanten Engine.Pos*, siehe replay_recorder)."""
    uid: int        # bleibt gleich, solange die Karte im Duell ist (Bewegungen verfolgen)
    team: int       # Spieler (MyID aus ClientWork["Duel"] = man selbst)
    zone: int
    index: int
    card: int       # Konami-ID, 0 = verdeckt bzw. nicht bekannt (z.B. Hand des Gegners)
    face: int       # offen?
    attack: int     # Angriffsposition?
    atk: int
    def_: int


class DuelLog(list):
    """Neue Einträge des Duell-Protokolls; size = so viele Protokollzeilen sind damit gelesen (nächstes start)."""

    def __init__(self, size: int = 0):
        super().__init__()
        self.size = size


def log_entry(label: str, rec: bytes) -> Optional[dict]:
    """Ein Datensatz des Duell-Protokolls → kompakter Eintrag; None = uninteressant oder zu kurz. Rein rechnerisch."""
    if label == "ShowAction" and len(rec) >= 29:
        card, _, zone, team, _, other, _, other_zone, other_team, _ = LOG_SIDES.unpack_from(rec, 0)
        value, _, _, _, code, _ = LOG_CENTER.unpack_from(rec, 16)
        entry = {"k": "act", "a": code, "c": card & 0xFFFF, "t": team, "f": zone, "z": other_zone, "cost": rec[28]}
        if other & 0xFFFF:
            entry.update(rc=other & 0xFFFF, zt=other_team)
        if code == 0:   # LP: der Wert ist die Änderung; sonst steht in dataint0 nur Beiwerk
            entry["v"] = value
        return entry
    if label == "ShowChain" and len(rec) >= 12:
        card, number, team, kind = struct.unpack_from("<HHii", rec, 0)
        return {"k": "chain", "c": card, "n": number, "t": team, "res": 1 if kind == 1 else 0}
    if label == "ShowTurn" and len(rec) >= 20:
        number, left, right = struct.unpack_from("<4xi4xii", rec, 0)
        return {"k": "turn", "n": number + 1, "lp": [left, right]}
    if label == "ShowPhase" and len(rec) >= 4:
        return {"k": "phase", "p": struct.unpack_from("<i", rec, 0)[0]}
    if label == "ShowTag" and len(rec) >= 4:
        return {"k": "tag", "y": struct.unpack_from("<i", rec, 0)[0]}
    return None


class MemoryUnavailable(RuntimeError):
    """Speicher-Modus nicht möglich (Spiel nicht offen, Deck-Editor zu, Spiel-Update hat Namen geändert …)."""


class _MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wintypes.DWORD), ("PartitionId", wintypes.WORD),
                ("RegionSize", ctypes.c_size_t), ("State", wintypes.DWORD), ("Protect", wintypes.DWORD),
                ("Type", wintypes.DWORD)]


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                   ctypes.POINTER(ctypes.c_size_t)]
_k32.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(_MBI), ctypes.c_size_t]
_k32.VirtualQueryEx.restype = ctypes.c_size_t
_k32.GetCurrentThread.restype = wintypes.HANDLE
_k32.GetThreadPriority.argtypes = [wintypes.HANDLE]
_k32.GetThreadPriority.restype = ctypes.c_int
_k32.SetThreadPriority.argtypes = [wintypes.HANDLE, ctypes.c_int]
_k32.SetThreadPriority.restype = wintypes.BOOL
THREAD_PRIORITY_BELOW_NORMAL = -1


class ProcessReader:
    """Lesender Zugriff auf den Speicher eines Prozesses (öffnet ihn nur mit Lese-Rechten)."""

    def __init__(self, pid: int):
        self.pid = pid
        self.handle = _k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
        if not self.handle:
            raise MemoryUnavailable(f"Kein Lesezugriff auf Master Duel (Fehler {ctypes.get_last_error()}).")

    def close(self) -> None:
        if self.handle:
            _k32.CloseHandle(self.handle)
            self.handle = None

    def __del__(self) -> None:
        self.close()

    def __enter__(self) -> "ProcessReader":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def read(self, addr: int, size: int) -> bytes:
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t()
        if not _k32.ReadProcessMemory(self.handle, ctypes.c_void_p(addr), buf, size, ctypes.byref(got)):
            raise OSError(f"Speicher bei {addr:#x} nicht lesbar")
        return buf.raw[:got.value]

    def u64(self, addr: int) -> int:
        return struct.unpack("<Q", self.read(addr, 8))[0]

    def i32(self, addr: int) -> int:
        return struct.unpack("<i", self.read(addr, 4))[0]

    def cstr(self, addr: int, limit: int = 128) -> str:
        return self.read(addr, limit).split(b"\0", 1)[0].decode("utf-8", "replace")

    def regions(self, types=(MEM_PRIVATE,)):
        """Lesbare, belegte Speicherbereiche: (Start, Größe)."""
        addr, mbi = 0, _MBI()
        while _k32.VirtualQueryEx(self.handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            base, size = mbi.BaseAddress or 0, mbi.RegionSize
            if (mbi.State == MEM_COMMIT and mbi.Type in types and not mbi.Protect & PAGE_GUARD
                    and mbi.Protect & 0xFF != PAGE_NOACCESS):
                yield base, size
            addr = base + size
            if addr >= 0x7FFFFFFFFFFF:
                break

    def iter_pointers(self, value: int):
        """8-Byte-ausgerichtete Stellen im privaten Speicher, an denen `value` steht (der Reihe nach)."""
        needle = struct.pack("<Q", value)
        # Die Suche liest GB-weit: mit niedriger Priorität, damit sie das Spiel nicht bremst (nur dieser Thread,
        # danach wieder wie vorher)
        thread, normal = _k32.GetCurrentThread(), _k32.GetThreadPriority(_k32.GetCurrentThread()) == 0
        if normal:
            _k32.SetThreadPriority(thread, THREAD_PRIORITY_BELOW_NORMAL)
        try:
            for base, size in self.regions():
                for off in range(0, size, CHUNK):
                    try:
                        data = self.read(base + off, min(CHUNK, size - off))
                    except OSError:
                        break
                    i = data.find(needle)
                    while i != -1:
                        if i % 8 == 0:
                            yield base + off + i
                        i = data.find(needle, i + 1)
        finally:
            if normal:
                _k32.SetThreadPriority(thread, 0)

    def find_pointers(self, value: int, limit: int = 5000) -> List[int]:
        hits = []
        for hit in self.iter_pointers(value):
            hits.append(hit)
            if len(hits) >= limit:
                break
        return hits


class Il2Cpp:
    """Klassen und Felder eines IL2CPP-Spiels über ihre Namen finden."""

    def __init__(self, reader: ProcessReader, metadata_path: str):
        self.r = reader
        with open(metadata_path, "rb") as f:
            raw = f.read()
        header = struct.unpack_from("<8i", raw, 0)
        if header[0] != -89056337:  # 0xFAB11BAF
            raise MemoryUnavailable("Unbekanntes Metadaten-Format (Spiel-Update?).")
        self.str_offset, self.strings = header[6], raw[header[6]:header[6] + header[7]]
        self.base = self._find_loaded(raw[:64])
        self._fields: Dict[int, Dict[str, int]] = {}

    def _find_loaded(self, head: bytes) -> int:
        for base, _ in self.r.regions(types=(MEM_MAPPED, MEM_PRIVATE)):
            try:
                if self.r.read(base, len(head)) == head:
                    return base
            except OSError:
                continue
        raise MemoryUnavailable("Metadaten von Master Duel nicht im Speicher gefunden.")

    def find_class(self, namespace: str, name: str) -> int:
        start = 0
        while True:
            idx = self.strings.find(b"\0" + name.encode() + b"\0", start)
            if idx < 0:
                raise MemoryUnavailable(f"Klasse {namespace}.{name} nicht gefunden (Spiel-Update?).")
            start = idx + 1
            for ref in self.r.iter_pointers(self.base + self.str_offset + idx + 1):  # erster Treffer genügt
                klass = ref - CLASS_NAME
                try:
                    if self.r.cstr(self.r.u64(klass + CLASS_NAMESPACE)) == namespace:
                        return klass
                except OSError:
                    continue

    def class_name(self, klass: int) -> str:
        return self.r.cstr(self.r.u64(klass + CLASS_NAME))

    def fields(self, klass: int) -> Dict[str, int]:
        """{Feldname: Offset} inkl. Basisklassen."""
        if klass not in self._fields:
            result, k = {}, klass
            while k:
                table = self.r.u64(k + CLASS_FIELDS)
                for i in range(1000):
                    entry = table + i * FIELD_INFO_SIZE
                    try:
                        if not table or self.r.u64(entry + 0x10) != k:
                            break
                        result.setdefault(self.r.cstr(self.r.u64(entry), 96), self.r.i32(entry + 0x18))
                    except OSError:
                        break
                k = self.r.u64(k + CLASS_PARENT)
            self._fields[klass] = result
        return self._fields[klass]

    def field(self, obj: int, name: str) -> int:
        """Offset eines Feldes im Objekt (über die Klasse des Objekts)."""
        offsets = self.fields(self.r.u64(obj))
        if name not in offsets:
            raise MemoryUnavailable(f"Feld {name} nicht gefunden (Spiel-Update?).")
        return offsets[name]

    def object_class(self, obj: int) -> str:
        return self.class_name(self.r.u64(obj)) if obj else ""


class DeckEditorMemory:
    """Liest im offenen Deck-Editor: angezeigte Karte, Suchergebnisse, Deck (Konami-IDs)."""

    def __init__(self, reader: ProcessReader, il: Il2Cpp, manager_klass: int = 0, klass: int = 0):
        self.r, self.il, self.manager_klass = reader, il, manager_klass
        self.klass = klass   # Klasse des Deck-Editors (aus dem gefundenen Objekt)
        self.editor: Optional[int] = None
        self.client_work_klass = 0           # erst beim ersten Lesen gesucht (einige Sekunden)
        self._class_names: Dict[int, str] = {}

    @classmethod
    def attach(cls) -> "DeckEditorMemory":
        """Master Duel finden und die Fenster-Verwaltung des Spiels auflösen (dauert einige Sekunden)."""
        pid = master_duel_pid()
        exe = win_api.process_image_name(pid) if pid else None
        if not exe:
            raise MemoryUnavailable("Master Duel läuft nicht.")
        metadata = os.path.join(os.path.dirname(exe), "masterduel_Data", "il2cpp_data", "Metadata",
                                "global-metadata.dat")
        if not os.path.exists(metadata):
            raise MemoryUnavailable("Metadaten-Datei von Master Duel nicht gefunden.")
        reader = ProcessReader(pid)
        try:
            il = Il2Cpp(reader, metadata)
            return cls(reader, il, il.find_class(*MANAGER_CLASS))
        except Exception:
            reader.close()
            raise

    def close(self) -> None:
        self.r.close()

    def valid(self) -> bool:
        """Verbindung noch brauchbar (Spiel läuft noch, Klasse unverändert)?"""
        try:
            return self.il.class_name(self.manager_klass) == MANAGER_CLASS[1]
        except (OSError, TypeError):
            return False

    # ── Offene Fenster ──
    def _view_stack(self) -> List[int]:
        """Objekte der offenen Fenster des Spiels, das unterste zuerst."""
        statics = self.r.u64(self.manager_klass + CLASS_STATIC_FIELDS)
        manager = self.r.u64(statics + self.il.fields(self.manager_klass)["Instance"]) if statics else 0
        if not manager:
            raise MemoryUnavailable("Fenster-Verwaltung von Master Duel nicht gefunden.")
        stack = self.r.u64(manager + self.il.field(manager, "viewStack"))
        items = self.r.u64(stack + self.il.field(stack, "_items"))
        size = self.r.i32(stack + self.il.field(stack, "_size"))
        return [self.r.u64(items + ARRAY_DATA + 8 * i) for i in range(max(0, min(size, 64)))]

    def open_views(self) -> List[str]:
        """Klassennamen der offenen Fenster (z.B. "ColosseumHistoryViewController"), das oberste zuletzt."""
        names = []
        for obj in self._view_stack():
            klass = self.r.u64(obj) if obj else 0
            if klass and self._unity_alive(obj):
                names.append(self.il.class_name(klass))
        return names

    # ── Deck-Editor-Objekt ──
    def find_editor(self) -> int:
        """Der gerade angezeigte Deck-Editor: oberster DeckEditViewController2 in den offenen Fenstern."""
        for obj in reversed(self._view_stack()):
            klass = self.r.u64(obj) if obj else 0
            if klass and self.il.class_name(klass) == EDITOR_CLASS[1] and self._unity_alive(obj):
                self.editor, self.klass = obj, klass
                return obj
        raise MemoryUnavailable("Deck-Editor ist nicht offen – bitte ein Deck zum Bearbeiten öffnen.")

    # ── Server-Daten (ClientWork) ──
    def client_work(self, *path: str):
        """
        Zwischengespeicherte Server-Antwort als dict/list/Wert, z.B. client_work("DuelHistory").
        None, wenn es den Pfad (noch) nicht gibt – das Spiel lädt viele Daten erst beim Öffnen des Menüs.
        """
        if not self.client_work_klass:
            self.client_work_klass = self.il.find_class(*CLIENT_WORK_CLASS)
        statics = self.r.u64(self.client_work_klass + CLASS_STATIC_FIELDS)
        if not statics:
            return None
        node = self.r.u64(statics + self.il.fields(self.client_work_klass)["s_data"])
        for key in path:
            node = dict(self._dict_entries(node)).get(key, 0) if node else 0
        return self._value(node) if node else None

    def _class_of(self, obj: int) -> str:
        klass = self.r.u64(obj)
        if klass not in self._class_names:
            self._class_names[klass] = self.il.class_name(klass)
        return self._class_names[klass]

    def _dict_entries(self, obj: int) -> List[Tuple[object, int]]:
        """Dictionary<string, object>: [(Schlüssel, Adresse des Werts)]; anderes Objekt → []."""
        if not self._class_of(obj).startswith("Dictionary"):
            return []
        entries = self.r.u64(obj + self.il.field(obj, "_entries"))
        count = self.r.i32(obj + self.il.field(obj, "_count"))
        if not entries or not 0 <= count <= MAX_ITEMS:
            return []
        data = self.r.read(entries + ARRAY_DATA, count * DICT_ENTRY_SIZE)
        result = []
        for i in range(count):
            hash_code, _, key, value = struct.unpack_from("<iiQQ", data, i * DICT_ENTRY_SIZE)
            if hash_code >= 0 and key:  # < 0: gelöschter Eintrag
                result.append((self._value(key), value))
        return result

    def _value(self, obj: int):
        """Objekt aus ClientWork → Python: Dictionary → dict, List → list, Zahl/Text/Wahrheitswert."""
        if not obj:
            return None
        name = self._class_of(obj)
        if name == "String":
            return self._string(obj)
        if name in ("Int64", "UInt64", "Int32", "Boolean", "Double", "Single"):
            fmt = {"Int64": "<q", "UInt64": "<Q", "Int32": "<i", "Boolean": "<?", "Double": "<d", "Single": "<f"}[name]
            return struct.unpack(fmt, self.r.read(obj + OBJECT_HEADER, struct.calcsize(fmt)))[0]
        if name.startswith("Dictionary"):
            return {key: self._value(value) for key, value in self._dict_entries(obj)}
        if name.startswith("List"):
            items = self.r.u64(obj + self.il.field(obj, "_items"))
            size = self.r.i32(obj + self.il.field(obj, "_size"))
            if not items or not 0 <= size <= MAX_ITEMS:
                return []
            data = self.r.read(items + ARRAY_DATA, 8 * size) if size else b""
            return [self._value(addr) for addr in struct.unpack(f"<{size}Q", data)]
        return None

    # ── Spielfeld (nur für Replays, siehe replay_recorder) ──
    def _member(self, obj: int, name: str) -> int:
        return self.r.u64(obj + self.il.field(obj, name)) if obj else 0

    def duel_worker(self) -> int:
        """RunEffectWorker des laufenden Duells/Replays (0 = keins)."""
        return self._member(self._duel_client(), "<effectWorker>k__BackingField")

    def _duel_client(self) -> int:
        klass = getattr(self, "_duel_client_klass", 0) or self.il.find_class(*DUEL_CLIENT_CLASS)
        self._duel_client_klass = klass
        statics = self.r.u64(klass + CLASS_STATIC_FIELDS)
        return self.r.u64(statics + self.il.fields(klass)["instance"]) if statics else 0

    def duel_board(self, worker: int) -> List[BoardCard]:
        """Alle Karten, die das Spiel gerade zeigt (Feld, Hand, oberste Karten von Deck/Friedhof …)."""
        roots = self._member(self._member(worker, "<goManager>k__BackingField"), "cardRoots")
        if not roots:
            return []
        items = self.r.u64(roots + self.il.field(roots, "_items"))
        size = self.r.i32(roots + self.il.field(roots, "_size"))
        if not items or not 0 <= size <= MAX_ITEMS:
            return []
        cards = []
        for addr in struct.unpack(f"<{size}Q", self.r.read(items + ARRAY_DATA, 8 * size)) if size else ():
            if not addr:
                continue
            offsets = self.il.fields(self.r.u64(addr))
            lo = min(offsets[n] for n in (*CARD_INTS.values(), *CARD_BYTES.values()))
            hi = max(offsets[n] for n in (*CARD_INTS.values(), *CARD_BYTES.values())) + 4
            block = self.r.read(addr + lo, hi - lo)  # ein Lesezugriff je Karte
            ints = {k: struct.unpack_from("<i", block, offsets[n] - lo)[0] for k, n in CARD_INTS.items()}
            flags = {k: block[offsets[n] - lo] for k, n in CARD_BYTES.items()}
            cards.append(BoardCard(ints["uid"], ints["team"], ints["zone"], ints["index"], ints["card"],
                                   flags["face"], flags["attack"], ints["atk"], ints["def"]))
        return cards

    def duel_lp(self, worker: int) -> Dict[int, int]:
        """Lebenspunkte {Spieler: LP}."""
        lp = self._member(worker, "<lp>k__BackingField")
        if not lp or not self._class_of(lp).startswith("Dictionary"):
            return {}
        entries = self.r.u64(lp + self.il.field(lp, "_entries"))
        count = self.r.i32(lp + self.il.field(lp, "_count"))
        if not entries or not 0 <= count <= 8:
            return {}
        data = self.r.read(entries + ARRAY_DATA, 16 * count)
        result = {}
        for i in range(count):
            hash_code, _, key, value = struct.unpack_from("<iiii", data, 16 * i)
            if hash_code >= 0:
                result[key] = value
        return result

    def duel_phase(self, worker: int) -> Tuple[str, str]:
        """(Zug, Phase) wie auf dem Phasen-Knopf, z.B. ("Turn 2", "Main 1"); "" wenn nicht lesbar."""
        button = self._member(worker, "<phaseSelect>k__BackingField")
        texts = []
        for name in ("m_TurnNum", "m_PhaseName"):
            label = self._member(button, name)
            text = ""
            if label:
                offsets = self.il.fields(self.r.u64(label))
                if "m_text" in offsets:
                    text = self._string(self.r.u64(label + offsets["m_text"]))
            texts.append(text)
        return texts[0], texts[1]

    def _log_controller_ok(self, ctl: int, host: int) -> bool:
        """Ist das der DuelLogController des laufenden Duells (Klasse, Zeitleiste, Wörterbuch, Host, lebendig)?"""
        try:
            if self.r.u64(ctl) != self._log_klass:
                return False
            f = self.il.fields(self._log_klass)
            lst, dic = self.r.u64(ctl + f["m_LogBaseItemList"]), self.r.u64(ctl + f["m_DataTypeNumDict"])
            return bool(lst and dic and self._class_of(lst).startswith("List")
                        and self._class_of(dic).startswith("Dictionary")
                        and self.r.u64(ctl + f["m_Host"]) == host and self._unity_alive(ctl))
        except (OSError, KeyError):
            return False

    def duel_log_controller(self, worker: int) -> int:
        """
        DuelLogController des laufenden Replays (0 = nicht gefunden). Kein statischer Weg: die Instanz wird im Speicher
        gesucht (~8 s) und gemerkt; nach einem Fehlschlag frühestens nach LOG_SEARCH_PAUSE wieder. Nur lesend.
        """
        if not worker:
            return 0
        host = self._duel_client()
        ctl = getattr(self, "_log_ctl", 0)
        if ctl and getattr(self, "_log_klass", 0) and self._log_controller_ok(ctl, host):
            return ctl
        self._log_ctl = 0
        now = time.monotonic()
        pause = getattr(self, "_log_pause", LOG_SEARCH_PAUSE)
        if getattr(self, "_log_search_at", 0.0) and now - self._log_search_at < pause:
            return 0
        self._log_search_at = now
        self._log_klass = getattr(self, "_log_klass", 0) or self.il.find_class(*LOG_CLASS)
        for addr in self.r.find_pointers(self._log_klass, limit=200):
            if self._log_controller_ok(addr, host):
                self._log_ctl, self._log_pause = addr, LOG_SEARCH_PAUSE
                return addr
        self._log_search_at = time.monotonic()  # die Pause zählt ab dem Ende der Suche
        self._log_pause = min(2 * pause, LOG_SEARCH_MAX_PAUSE)  # weiter erfolglos: seltener suchen
        return 0

    def reset_log_search(self) -> None:
        """Neues Replay: wieder zügig nach dem Controller suchen (Pause auf Anfang)."""
        self._log_search_at, self._log_pause = 0.0, LOG_SEARCH_PAUSE

    def _struct_list(self, lst: int) -> Tuple[int, int, int]:
        """List<Struct> → (Adresse des ersten Elements, Anzahl, Elementgröße); (0, 0, 0) bei unsinnigen Werten."""
        if not lst:
            return 0, 0, 0
        items = self.r.u64(lst + self.il.field(lst, "_items"))
        size = self.r.i32(lst + self.il.field(lst, "_size"))
        if not items or not 0 <= size <= LOG_MAX:
            return 0, 0, 0
        element = self.r.i32(self.r.u64(items) + ARRAY_ELEMENT_SIZE)
        return (items + ARRAY_DATA, size, element) if 0 < element <= 256 else (0, 0, 0)

    def duel_log(self, ctl: int, start: int = 0) -> DuelLog:
        """
        Protokollzeilen ab Nummer `start` als kompakte Einträge (siehe log_entry); .size = nächstes start. Nur lesend;
        bei jedem Lesefehler kommt zurück, was bis dahin gelesen war (leer, wenn nichts); später wird weitergelesen.
        """
        out = DuelLog(start)
        try:
            f = self.il.fields(self.r.u64(ctl))
            base, size, element = self._struct_list(self.r.u64(ctl + f["m_LogBaseItemList"]))
            if not base or element < LOG_BASE.size or start == size:
                return out
            if start > size:   # Zeitleiste kleiner als beim letzten Mal: neues Duell, von vorn
                start = out.size = 0
            raw = self.r.read(base + start * element, (size - start) * element)
            lists, labels = {}, {}
            for i in range(start, size):
                ptr, index = LOG_BASE.unpack_from(raw, (i - start) * element)
                if ptr not in labels:
                    labels[ptr] = self._string(ptr)
                label = labels[ptr]
                name = "m_DataList_" + label
                if label and name in f and index >= 0:
                    if name not in lists:
                        lists[name] = self._struct_list(self.r.u64(ctl + f[name]))
                    first, count, width = lists[name]
                    if first and index < count:
                        entry = log_entry(label, self.r.read(first + index * width, width))
                        if entry:
                            out.append(entry)
                out.size = i + 1
        except (OSError, KeyError, MemoryUnavailable, struct.error):
            pass
        return out

    def _unity_alive(self, obj: int) -> bool:
        """Geschlossene Fenster: Unity hat das Objekt zerstört (m_CachedPtr = 0), es liegt nur noch im Speicher."""
        offsets = self.il.fields(self.r.u64(obj))
        return "m_CachedPtr" not in offsets or bool(self.r.u64(obj + offsets["m_CachedPtr"]))

    def alive(self) -> bool:
        """Ist das gefundene Editor-Objekt noch der offene Editor?"""
        try:
            return bool(self.editor) and self.r.u64(self.editor) == self.klass and self._unity_alive(self.editor)
        except OSError:
            return False

    def _editor(self) -> int:
        if not self.alive():
            self.find_editor()
        return self.editor

    # ── Daten ──
    def shown_card(self) -> int:
        """Konami-ID der Karte im Detail-Panel (0 = keine)."""
        editor = self._editor()
        detail = self.r.u64(editor + self.il.fields(self.klass)["m_DetailView"])
        return self.r.i32(detail + self.il.field(detail, "<m_CardID>k__BackingField"))

    def shown_premium(self) -> int:
        """Ausführung der Karte im Detail-Panel (1 = normal, 2 = Shiny, 3 = Royal)."""
        editor = self._editor()
        detail = self.r.u64(editor + self.il.fields(self.klass)["m_DetailView"])
        return self.r.i32(detail + self.il.field(detail, "<m_Premium>k__BackingField"))

    def shown_name(self) -> str:
        """Name der Karte im Detail-Panel, wie das Spiel ihn anzeigt (Spielsprache) – auch für Artworks."""
        detail = self.r.u64(self._editor() + self.il.fields(self.klass)["m_DetailView"])
        title = self.r.u64(detail + self.il.field(detail, "m_TitleArea"))
        text = self.r.u64(title + self.il.field(title, "<m_CardName>k__BackingField"))
        return self._string(self.r.u64(text + self.il.field(text, "m_text")))

    def _string(self, addr: int) -> str:
        """System.String: Länge bei +0x10, UTF-16-Zeichen ab +0x14."""
        if not addr:
            return ""
        length = self.r.i32(addr + OBJECT_HEADER)
        if not 0 <= length <= 1000:
            return ""
        return self.r.read(addr + OBJECT_HEADER + 4, 2 * length).decode("utf-16-le", "replace")

    # Das Spiel filtert abwechselnd in zwei Listen und tauscht sie danach: m_CardCollection ist die angezeigte,
    # m_CardListBuff die vorige (hinkt eine Suche hinterher).
    SEARCH_LIST = "<m_CardCollection>k__BackingField"

    def search_results(self) -> List[int]:
        """Suchergebnisse in der Reihenfolge der Kartenliste (Konami-IDs)."""
        return [entry.kid for entry in self.search_entries()]

    def search_entries(self) -> List[SearchEntry]:
        """Suchergebnisse in der Reihenfolge der Kartenliste, mit Ausführung und Besitz."""
        return self._card_entries(self.SEARCH_LIST)

    def search_list_address(self) -> int:
        """Adresse der angezeigten Ergebnisliste – ändert sich, sobald das Spiel eine neue Suche fertig hat."""
        return self.r.u64(self._editor() + self.il.fields(self.klass)[self.SEARCH_LIST])

    def grid_count(self) -> int:
        """Wie viele Karten die Kartenliste gerade anzeigt (Anzahl der Suchergebnisse laut Anzeige)."""
        pool = self._list_pool()
        return self.r.i32(pool + self.il.field(pool, "m_DataCount"))

    def list_scroll(self) -> ListScroll:
        """Wie weit die Kartenliste gescrollt ist, und ihre Maße (ScrollRect + Raster der Kartenliste)."""
        pool = self._list_pool()
        rect = self.r.u64(pool + self.il.field(pool, "m_ScrollRect"))
        offsets = self.il.fields(self.r.u64(rect))
        # m_PrevPosition: Position des Inhalts im letzten Frame (Vector2), y wächst beim Runterscrollen
        position = struct.unpack("<f", self.r.read(rect + offsets["m_PrevPosition"] + 4, 4))[0]
        # Bounds = Mitte (3 floats) + halbe Größe (3 floats): scrollbar ist Inhalt minus Sichtbereich
        content = struct.unpack("<6f", self.r.read(rect + offsets["m_ContentBounds"], 24))
        view = struct.unpack("<6f", self.r.read(rect + offsets["m_ViewBounds"], 24))
        wheel = struct.unpack("<f", self.r.read(rect + offsets["m_ScrollSensitivity"], 4))[0]
        units = self.r.u64(pool + self.il.field(pool, "m_UnitSize"))  # List<Vector2>: Kartengröße mit Abstand
        items = self.r.u64(units + self.il.field(units, "_items"))
        row_height = struct.unpack("<f", self.r.read(items + ARRAY_DATA + 4, 4))[0]
        columns = self.r.i32(pool + self.il.field(pool, "m_ConstraintCount"))
        if not (row_height > 0 and columns > 0):
            raise MemoryUnavailable("Raster der Kartenliste nicht lesbar.")
        return ListScroll(position, max(0.0, 2 * (content[4] - view[4])), row_height, columns, wheel, 2 * view[4])

    def _list_pool(self) -> int:
        """EntityPoolController der Kartenliste (verwaltet die angezeigten Karten der Suchergebnisse)."""
        view = self.r.u64(self._editor() + self.il.fields(self.klass)["m_CollectionView"])
        area = self.r.u64(view + self.il.field(view, "<m_CardListArea>k__BackingField"))
        return self.r.u64(area + self.il.field(area, "entityPoolController"))

    def deck(self) -> Tuple[List[int], List[int]]:
        """
        (Main Deck, Extra Deck) als Konami-IDs, wie sie gerade angezeigt werden (Reihenfolge wie im Raster).
        Aus der Deck-Ansicht: m_MainDeckCards im Editor ist das Deck beim Öffnen und ändert sich beim
        Bearbeiten nicht.
        """
        view = self.r.u64(self._editor() + self.il.fields(self.klass)["m_DeckView"])
        if not view:
            raise MemoryUnavailable("Deck-Ansicht nicht gefunden.")
        return self._card_list("mainCardDataList", view), self._card_list("extraCardDataList", view)

    def _card_list(self, field: str, owner: Optional[int] = None) -> List[int]:
        """Konami-IDs einer List<CardBaseData> (Feld des Editors oder von `owner`)."""
        return [entry.kid for entry in self._card_entries(field, owner)]

    def _card_entries(self, field: str, owner: Optional[int] = None) -> List[SearchEntry]:
        """Einträge einer List<CardBaseData> (Feld des Editors oder von `owner`)."""
        if owner is None:
            lst = self.r.u64(self._editor() + self.il.fields(self.klass)[field])
        else:
            lst = self.r.u64(owner + self.il.field(owner, field))
        if not lst:
            return []
        items = self.r.u64(lst + self.il.field(lst, "_items"))
        size = self.r.i32(lst + self.il.field(lst, "_size"))
        if not 0 <= size <= 50000 or not items:
            return []
        element = self.r.u64(self.r.u64(items) + 0x40)  # element_class des Arrays (CardBaseData)
        offsets = self.il.fields(element)
        card_id = offsets["<CardID>k__BackingField"] - OBJECT_HEADER
        premium = offsets.get("<PremiumID>k__BackingField", -1) - OBJECT_HEADER
        owned = offsets.get("<Inventory>k__BackingField", -1) - OBJECT_HEADER
        stride = (max(offsets.values()) - OBJECT_HEADER + 1 + 3) // 4 * 4  # Struct-Größe (4-Byte-ausgerichtet)
        data = self.r.read(items + ARRAY_DATA, size * stride)

        def value(i, offset, default):
            return struct.unpack_from("<i", data, i * stride + offset)[0] if offset >= 0 else default

        return [SearchEntry(value(i, card_id, 0), value(i, premium, 1), value(i, owned, 0)) for i in range(size)]


def master_duel_pid() -> int:
    from window_automation import find_md_window
    hwnd = find_md_window()
    return win_api.window_pid(hwnd) if hwnd else 0


# ── Gemeinsame Verbindung: einmal pro Spielsitzung aufgebaut (im Hintergrund vorgeladen) ──
_shared: Optional[DeckEditorMemory] = None
_shared_lock = threading.Lock()


def shared() -> DeckEditorMemory:
    """Vorhandene Verbindung, solange dasselbe Master Duel läuft; sonst neu aufbauen (einige Sekunden)."""
    global _shared
    with _shared_lock:
        if _shared is not None and _shared.r.pid == master_duel_pid() and _shared.valid():
            return _shared
        if _shared is not None:
            _shared.close()
            _shared = None
        _shared = DeckEditorMemory.attach()
        return _shared


def is_ready() -> bool:
    """Ist die Verbindung schon aufgebaut (Import im Speicher-Modus startet dann ohne Wartezeit)?"""
    return _shared is not None


_preload_failures = 0              # Fehlversuche seit der letzten Verbindung (z.B. nach einem Spiel-Update)
_last_error: Optional[str] = None


def preload() -> Optional[str]:
    """Im Hintergrund aufrufen, sobald Master Duel läuft. Returns: Fehlergrund oder None."""
    global _preload_failures, _last_error
    try:
        shared()
        _preload_failures, _last_error = 0, None
        return None
    except Exception as e:  # Spiel noch im Ladebildschirm o.Ä. – später erneut versuchen
        _preload_failures, _last_error = _preload_failures + 1, str(e)
        return str(e)


def reset_preload_failures() -> None:
    """Master Duel wurde neu gestartet: Fehlversuche des alten Spiels zählen nicht mehr."""
    global _preload_failures
    _preload_failures = 0


def preload_failures() -> int:
    """Wie oft das Vorladen seit der letzten Verbindung gescheitert ist."""
    return _preload_failures


def last_error() -> Optional[str]:
    return _last_error


def wait_for(read, condition, timeout: float = 1.0, interval: float = 0.02):
    """read() wiederholen, bis condition(Wert) stimmt. Returns: letzter Wert."""
    end = time.monotonic() + timeout
    value = read()
    while not condition(value) and time.monotonic() < end:
        time.sleep(interval)
        value = read()
    return value

import base64
import struct
import requests
from typing import Dict, List, Tuple

class DeckParser:
    def __init__(self):
        self.api_url = "https://db.ygoprodeck.com/api/v7/cardinfo.php"

    def parse_ydk(self, file_path: str) -> Dict[str, int]:
        card_counts = {}
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or line.startswith('!') or line.startswith('$'):
                        continue
                    if line.isdigit():
                        card_counts[line] = card_counts.get(line, 0) + 1
        except Exception as e:
            print(f"[Fehler] Konnte YDK-Datei nicht lesen: {e}")
        return card_counts

    def parse_ydke(self, ydke_url: str) -> Dict[str, int]:
        card_counts = {}
        if ydke_url.startswith("ydke://"):
            ydke_url = ydke_url[7:]
        sections = ydke_url.split("!")
        for section in sections[:3]:
            if not section:
                continue
            try:
                padded_section = section + "=" * (-len(section) % 4)
                binary_data = base64.b64decode(padded_section)
                num_cards = len(binary_data) // 4
                card_ids = struct.unpack(f"<{num_cards}I", binary_data)
                for cid in card_ids:
                    if cid == 0:
                        continue
                    cid_str = str(cid)
                    card_counts[cid_str] = card_counts.get(cid_str, 0) + 1
            except Exception as e:
                print(f"[Fehler] Fehler beim Dekodieren der YDKE-Sektion: {e}")
        return card_counts

    def fetch_card_names(self, card_counts: Dict[str, int]) -> List[Tuple[str, int]]:
        if not card_counts:
            return []
        id_list_str = ",".join(card_counts.keys())
        payload = {"id": id_list_str}
        try:
            response = requests.get(self.api_url, params=payload, timeout=10)
            if response.status_code == 200:
                data = response.json()
                final_decklist = []
                for card_info in data.get("data", []):
                    api_id = str(card_info.get("id"))
                    card_name = card_info.get("name")
                    if api_id in card_counts:
                        count = card_counts[api_id]
                        final_decklist.append((card_name, count))
                return final_decklist
            else:
                print(f"[Fehler] API-Abfrage fehlgeschlagen (Status: {response.status_code})")
                return []
        except Exception as e:
            print(f"[Fehler] Netzwerkfehler bei API-Abfrage: {e}")
            return []
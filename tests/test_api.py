"""Kartennamen per YGOPRODeck-API (ohne Netzwerk: requests wird ersetzt)."""

import json
import os
import tempfile
import unittest
from unittest import mock

import utils

CARDS_EN = {
    "14558127": "Ash Blossom & Joyous Spring",
    "30271097": "The Fallen & The Virtuous",
    "89631140": "Blue-Eyes White Dragon",
}
CARDS_DE = {"14558127": "Aschenblüte & Freudiger Frühling"}  # The Fallen hat keinen deutschen Namen
ALT_ART = {"89631141": "89631140"}  # Alternativ-Artwork → Hauptkarte


class FakeApi:
    def __init__(self, fail_times=0):
        self.calls = []
        self.fail_times = fail_times

    def get(self, url, params, timeout):
        self.calls.append(dict(params))
        if self.fail_times:
            self.fail_times -= 1
            raise ConnectionError("kein Netz")
        names = CARDS_DE if params.get("language") == "de" else CARDS_EN
        data = []
        for cid in params["id"].split(","):
            main = ALT_ART.get(cid, cid)
            if main in names:
                data.append({"id": int(main), "name": names[main],
                             "card_images": [{"id": int(main)}] + [{"id": int(a)} for a, m in ALT_ART.items() if m == main]})
        response = mock.Mock(status_code=200 if data else 400)
        response.json.return_value = {"data": data} if data else {"error": "No card matching"}
        return response


class FetchCardNamesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cache_file = os.path.join(self.tmp, "cache.json")
        self.patches = [mock.patch.object(utils, "CACHE_FILE", self.cache_file),
                        mock.patch.object(utils.time, "sleep", lambda s: None)]
        for p in self.patches:
            p.start()
        utils._card_cache = None

    def tearDown(self):
        for p in self.patches:
            p.stop()
        utils._card_cache = None

    def fetch(self, ids, lang="en", api=None):
        api = api or FakeApi()
        with mock.patch.object(utils.requests, "get", api.get):
            return utils.fetch_card_names(ids, lang), api

    def test_one_request_for_all_cards(self):
        names, api = self.fetch(["14558127", "30271097", "89631141", "99999999"])
        self.assertEqual(len(api.calls), 1)
        self.assertEqual(names, {"14558127": CARDS_EN["14558127"], "30271097": CARDS_EN["30271097"],
                                 "89631141": "Blue-Eyes White Dragon"})  # Alt-Art aufgelöst, ungültige ID fehlt

    def test_cache_avoids_second_request(self):
        self.fetch(["14558127"])
        utils._card_cache = None  # zwingt Neuladen aus der Datei
        names, api = self.fetch(["14558127"])
        self.assertEqual(api.calls, [])
        self.assertEqual(names["14558127"], CARDS_EN["14558127"])
        with open(self.cache_file, encoding="utf-8") as f:
            self.assertIn("14558127", json.load(f))

    def test_german_with_english_fallback(self):
        names, api = self.fetch(["14558127", "30271097"], lang="de")
        self.assertEqual(names, {"14558127": "Aschenblüte & Freudiger Frühling",
                                 "30271097": "The Fallen & The Virtuous"})
        self.assertEqual([c.get("language", "en") for c in api.calls], ["de", "en"])
        self.assertIn("de:14558127", utils._card_cache)  # Sprache ist Teil des Cache-Schlüssels

    def test_language_switch_does_not_reuse_other_language(self):
        self.fetch(["14558127"], lang="en")
        names, _ = self.fetch(["14558127"], lang="de")
        self.assertEqual(names["14558127"], "Aschenblüte & Freudiger Frühling")

    def test_retries_on_network_error(self):
        names, api = self.fetch(["14558127"], api=FakeApi(fail_times=2))
        self.assertEqual(len(api.calls), 3)
        self.assertEqual(names["14558127"], CARDS_EN["14558127"])


if __name__ == "__main__":
    unittest.main()

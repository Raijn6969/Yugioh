"""Tägliche Sicherung der Datenbank: eine Kopie pro Tag, die letzten 7 bleiben."""

import datetime
import os
import sqlite3
import tempfile
import unittest

import db_backup


class BackupTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.db = os.path.join(self.folder, "md_card_stats.db")
        with sqlite3.connect(self.db) as con:
            con.execute("CREATE TABLE matches (did TEXT PRIMARY KEY)")
            con.execute("INSERT INTO matches VALUES ('1')")
        con.close()

    def test_one_backup_per_day_and_only_the_last_seven(self):
        start = datetime.date(2026, 10, 1)
        first = db_backup.backup(self.db, today=start)
        self.assertTrue(first.endswith("md_card_stats_2026-10-01.db"))
        self.assertIsNone(db_backup.backup(self.db, today=start))  # heute schon gesichert
        with sqlite3.connect(first) as con:
            self.assertEqual(con.execute("SELECT did FROM matches").fetchall(), [("1",)])
        con.close()
        for day in range(1, 10):
            db_backup.backup(self.db, today=start + datetime.timedelta(days=day))
        names = [os.path.basename(p) for p in db_backup.backups(db_backup.folder_for(self.db))]
        self.assertEqual(len(names), 7)
        self.assertEqual((names[0], names[-1]), ("md_card_stats_2026-10-04.db", "md_card_stats_2026-10-10.db"))
        self.assertFalse([n for n in os.listdir(db_backup.folder_for(self.db)) if n.endswith(".tmp")])

    def test_missing_database_is_ignored(self):
        self.assertIsNone(db_backup.backup(os.path.join(self.folder, "fehlt.db")))

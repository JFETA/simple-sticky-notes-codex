import os
import sqlite3
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "src"))


def make_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE NOTES ("
        "ID INTEGER,STATE INTEGER,CREATED FLOAT,UPDATED FLOAT,DELETED FLOAT,STARRED INTEGER,"
        "AOT INTEGER,MINIMIZE INTEGER,COLOR INTEGER,OPACITY INTEGER,LEFT INTEGER,TOP INTEGER,"
        "WIDTH INTEGER,HEIGHT INTEGER,LOCKED INTEGER,ZORDER INTEGER,ALARM FLOAT,ALARM_CURRENT FLOAT,"
        "ALARM_PERIOD INTEGER,ALARM_DAY INTEGER,ALARM_SNOOZE INTEGER,ALARM_SOUND TEXT,NOTEBOOK TEXT,"
        "TITLE TEXT,TYPE INTEGER,DATA BLOB,TEXT TEXT)"
    )
    conn.execute("CREATE TABLE NOTEBOOKS (ID INTEGER,NAME TEXT)")
    conn.execute("INSERT INTO NOTEBOOKS VALUES (1,'Pruebas')")
    conn.commit()
    conn.close()


class ConnectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.db = cls.root / "Notes.db"
        make_database(cls.db)
        os.environ["SSN_DB_PATH"] = str(cls.db)
        os.environ["SSN_EXE_PATH"] = str(cls.root / "missing-ssn.exe")
        os.environ["LOCALAPPDATA"] = str(cls.root / "local")
        os.environ["SSN_TEST_MODE"] = "1"
        from simple_sticky_notes_mcp import server

        cls.server = server

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def call(self, name, *args, **kwargs):
        fn = getattr(self.server, name)
        return fn.fn(*args, **kwargs) if hasattr(fn, "fn") else fn(*args, **kwargs)

    def test_create_search_move_star_and_idempotency(self):
        request_id = str(uuid.uuid4())
        created = self.call("create_note", "Título ágil", "Línea 1\nNiño 😀", "Pruebas", False, request_id)
        self.assertTrue(created["created"])
        replay = self.call("create_note", "Título ágil", "Línea 1\nNiño 😀", "Pruebas", False, request_id)
        self.assertFalse(replay["created"])
        found = self.call("search_notes", "Niño", "Pruebas", False, 10)
        self.assertEqual(found["count"], 1)
        note_id = created["note_id"]
        self.call("create_notebook", "Archivo")
        self.call("move_notes", [note_id], "Archivo")
        self.call("set_starred", [note_id], True)
        note = self.call("get_note", note_id)
        self.assertEqual(note["notebook"], "Archivo")
        self.assertTrue(note["starred"])
        conn = sqlite3.connect(self.db)
        try:
            data, text = conn.execute("SELECT DATA,TEXT FROM NOTES WHERE ID=?", (note_id,)).fetchone()
            self.assertEqual(text, "Línea 1\nNiño 😀")
            self.assertIn(b"\\u", data)
            self.assertEqual(conn.execute("PRAGMA quick_check").fetchone()[0], "ok")
        finally:
            conn.close()

    def test_rejects_missing_notes_without_partial_update(self):
        with self.assertRaises(ValueError):
            self.call("set_starred", [9999], True)

    def test_markdown_is_rendered_and_existing_note_can_be_formatted(self):
        original = "**Título** 😀\n- [ ] **Paso** [guía](https://example.com/a)\n1. `Código`"
        created = self.call("create_note", "Formato", original, "Pruebas", True, str(uuid.uuid4()), "plain")
        note_id = created["note_id"]
        updated = self.call("format_note", note_id, original)
        self.assertTrue(updated["changed"])
        self.assertEqual(updated["notebook"], "Pruebas")
        self.assertTrue(updated["starred"])
        conn = sqlite3.connect(self.db)
        try:
            data, visible = conn.execute("SELECT DATA,TEXT FROM NOTES WHERE ID=?", (note_id,)).fetchone()
            self.assertEqual(visible, "Título 😀\n• ☐ Paso guía (https://example.com/a)\n1. Código")
            self.assertIn(b"\\b T", data)
            self.assertIn(b"\\f1 C", data)
            self.assertIn(b"\\u-10179?\\u-8704?", data)
        finally:
            conn.close()
        replay = self.call("format_note", note_id, original)
        self.assertFalse(replay["changed"])
        with self.assertRaises(ValueError):
            self.call("format_note", note_id, "otro texto")


if __name__ == "__main__":
    unittest.main(verbosity=2)

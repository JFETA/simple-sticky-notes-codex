from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
import uuid
from collections.abc import Callable, Iterable
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP

APP_VERSION = "6.9.0.0"
DEFAULT_DB = Path.home() / "Documents" / "Simple Sticky Notes" / "Notes.db"
DEFAULT_EXE = (
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Simnet" / "Simple Sticky Notes" / "ssn.exe"
)
DB_PATH = Path(os.environ.get("SSN_DB_PATH", str(DEFAULT_DB))).expanduser().resolve()
EXE_PATH = Path(os.environ.get("SSN_EXE_PATH", str(DEFAULT_EXE))).expanduser().resolve()
DATA_DIR = (
    Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Codex" / "simple-sticky-notes"
)
BACKUP_DIR = DATA_DIR / "backups"
LEDGER_PATH = DATA_DIR / "idempotency.json"
LOCK_PATH = DATA_DIR / "write.lock"

NOTE_COLUMNS = [
    "ID",
    "STATE",
    "CREATED",
    "UPDATED",
    "DELETED",
    "STARRED",
    "AOT",
    "MINIMIZE",
    "COLOR",
    "OPACITY",
    "LEFT",
    "TOP",
    "WIDTH",
    "HEIGHT",
    "LOCKED",
    "ZORDER",
    "ALARM",
    "ALARM_CURRENT",
    "ALARM_PERIOD",
    "ALARM_DAY",
    "ALARM_SNOOZE",
    "ALARM_SOUND",
    "NOTEBOOK",
    "TITLE",
    "TYPE",
    "DATA",
    "TEXT",
]
NOTEBOOK_COLUMNS = ["ID", "NAME"]

mcp = FastMCP(
    "simple-sticky-notes",
    instructions=(
        "Search before creating possible duplicates. For create_note, use a stable request_id and reuse it on retry. "
        "Writes close Simple Sticky Notes briefly, back up Notes.db, use one transaction, and reopen it."
    ),
)


def _connect_readonly() -> sqlite3.Connection:
    if not DB_PATH.is_file():
        raise RuntimeError(f"No se encontró la base de datos: {DB_PATH}")
    uri = f"file:{quote(DB_PATH.as_posix(), safe='/:')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    _validate_schema(conn)
    return conn


@contextmanager
def _readonly() -> Iterable[sqlite3.Connection]:
    conn = _connect_readonly()
    try:
        yield conn
    finally:
        conn.close()


def _validate_schema(conn: sqlite3.Connection) -> None:
    notes = [row[1] for row in conn.execute("PRAGMA table_info(NOTES)")]
    notebooks = [row[1] for row in conn.execute("PRAGMA table_info(NOTEBOOKS)")]
    if notes != NOTE_COLUMNS or notebooks != NOTEBOOK_COLUMNS:
        raise RuntimeError("Esquema no compatible. Este conector admite exclusivamente Simple Sticky Notes 6.9.0.0.")
    result = conn.execute("PRAGMA quick_check").fetchone()[0]
    if result != "ok":
        raise RuntimeError(f"La base de datos no superó PRAGMA quick_check: {result}")


def _ole_now() -> float:
    return time.time() / 86400.0 + 25569.0


def _validate_app_version() -> None:
    if os.environ.get("SSN_TEST_MODE") == "1":
        return
    if not EXE_PATH.is_file():
        raise RuntimeError(f"No se encontró el ejecutable: {EXE_PATH}")
    import win32api

    info = win32api.GetFileVersionInfo(str(EXE_PATH), "\\")
    ms, ls = info["FileVersionMS"], info["FileVersionLS"]
    version = f"{win32api.HIWORD(ms)}.{win32api.LOWORD(ms)}.{win32api.HIWORD(ls)}.{win32api.LOWORD(ls)}"
    if version != APP_VERSION:
        raise RuntimeError(f"Versión no compatible: {version}. Se requiere {APP_VERSION}.")


def _rtf_escape(text: str) -> str:
    out: list[str] = []
    for char in text:
        if char in "\\{}":
            out.append("\\" + char)
        else:
            code = ord(char)
            if 32 <= code <= 126:
                out.append(char)
            elif code <= 0xFFFF:
                signed = code if code < 0x8000 else code - 0x10000
                out.append(f"\\u{signed}?")
            else:
                value = code - 0x10000
                for unit in (0xD800 + (value >> 10), 0xDC00 + (value & 0x3FF)):
                    out.append(f"\\u{unit - 0x10000}?")
    return "".join(out)


INLINE_MARKUP = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)|\*\*(.+?)\*\*|`([^`\n]+)`|(?<!\*)\*([^*\n]+)\*(?!\*)")
LIST_MARKUP = re.compile(r"^(\s*)([-*+] |\d+[.)] )(.*)$")


def _render_inline(text: str, markdown: bool) -> tuple[str, str]:
    if not markdown:
        return _rtf_escape(text), text
    rtf: list[str] = []
    plain: list[str] = []
    start = 0
    for match in INLINE_MARKUP.finditer(text):
        before = text[start : match.start()]
        rtf.append(_rtf_escape(before))
        plain.append(before)
        if match.group(1) is not None:
            label, url = match.group(1), match.group(2)
            # Keep the URL visible and recognizable by Simple Sticky Notes.
            display = f"{label} ({url})"
            rtf.append(_rtf_escape(display))
            plain.append(display)
        elif match.group(3) is not None:
            value = match.group(3)
            rtf.append(r"\b " + _rtf_escape(value) + r"\b0 ")
            plain.append(value)
        elif match.group(4) is not None:
            value = match.group(4)
            rtf.append(r"\f1 " + _rtf_escape(value) + r"\f0 ")
            plain.append(value)
        else:
            value = match.group(5)
            rtf.append(r"\i " + _rtf_escape(value) + r"\i0 ")
            plain.append(value)
        start = match.end()
    tail = text[start:]
    rtf.append(_rtf_escape(tail))
    plain.append(tail)
    return "".join(rtf), "".join(plain)


def _render_note(text: str, format: str = "markdown") -> tuple[bytes, str]:
    if format == "rich_json":
        return _render_rich_document(json.loads(text))
    if format not in {"markdown", "plain"}:
        raise ValueError("format debe ser 'markdown', 'plain' o 'rich_json'.")
    markdown = format == "markdown"
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    rtf_lines: list[str] = []
    plain_lines: list[str] = []
    for line in lines:
        prefix = ""
        if markdown:
            match = LIST_MARKUP.match(line)
            if match:
                marker = match.group(2).strip()
                line = match.group(3)
                prefix = match.group(1) + ("• " if marker in {"-", "*", "+"} else marker + " ")
                if line.startswith("[ ] "):
                    prefix += "☐ "
                    line = line[4:]
                elif line.startswith(("[x] ", "[X] ")):
                    prefix += "☑ "
                    line = line[4:]
        rich, visible = _render_inline(line, markdown)
        rtf_lines.append(_rtf_escape(prefix) + rich)
        plain_lines.append(prefix + visible)
    body = (r"\par" + "\r\n").join(rtf_lines)
    rtf = (
        r"{\rtf1\ansi\ansicpg1252\deff0\nouicompat\deflang2058"
        r"{\fonttbl{\f0\fnil\fcharset0 Segoe UI;}{\f1\fnil\fcharset0 Consolas;}}"
        "\r\n"
        r"{\*\generator Codex Simple Sticky Notes Connector;}\viewkind4\uc1 "
        "\r\n"
        r"\pard\f0\fs24 " + body + r"\par"
        "\r\n}"
    )
    return rtf.encode("ascii"), "\n".join(plain_lines)


def _render_rich_document(document: dict[str, Any]) -> tuple[bytes, str]:
    """Render explicit paragraph/run styling supported by RichEdit RTF."""
    if not isinstance(document, dict) or not isinstance(document.get("paragraphs"), list):
        raise ValueError("rich_json requiere un objeto con paragraphs.")
    fonts = {"Segoe UI": 0, "Consolas": 1, "Arial": 2, "Times New Roman": 3}
    colors = {
        "black": 1, "red": 2, "blue": 3, "green": 4, "yellow": 5,
        "cyan": 6, "magenta": 7, "white": 8, "orange": 9,
    }
    color_table = (r"{\colortbl ;\red0\green0\blue0;\red255\green75\blue85;"
                   r"\red90\green170\blue255;\red80\green220\blue120;"
                   r"\red255\green235\blue0;\red0\green205\blue220;"
                   r"\red220\green40\blue190;\red255\green255\blue255;"
                   r"\red255\green145\blue0;}")
    align = {"left": "ql", "center": "qc", "right": "qr"}
    body: list[str] = []
    visible_lines: list[str] = []
    for paragraph in document["paragraphs"]:
        if not isinstance(paragraph, dict) or not isinstance(paragraph.get("runs"), list):
            raise ValueError("Cada párrafo requiere runs.")
        alignment = paragraph.get("align", "left")
        spacing = paragraph.get("spacing", 1)
        if alignment not in align or spacing not in (1, 1.5, 2):
            raise ValueError("Alineación o interlineado no admitido.")
        prefix = paragraph.get("prefix", "")
        if not isinstance(prefix, str):
            raise ValueError("prefix debe ser texto.")
        rich_runs = []
        visible = prefix
        for run in paragraph["runs"]:
            if not isinstance(run, dict) or not isinstance(run.get("text"), str):
                raise ValueError("Cada run requiere text.")
            font = run.get("font", "Segoe UI")
            size = run.get("size", 12)
            color = run.get("color", "white")
            highlight = run.get("highlight")
            if font not in fonts or not isinstance(size, (int, float)) or not 6 <= size <= 72:
                raise ValueError("Fuente o tamaño no admitido.")
            if color not in colors or (highlight is not None and highlight not in colors):
                raise ValueError("Color no admitido.")
            value = run["text"]
            visible += value
            flags = "".join("\\" + code + (" " if run.get(key) else "0 ") for key, code in (
                ("bold", "b"), ("italic", "i"), ("underline", "ul"), ("strike", "strike")))
            rich_runs.append("{" + f"\\f{fonts[font]}\\fs{round(size * 2)}\\cf{colors[color]} "
                             + (f"\\highlight{colors[highlight]} " if highlight else r"\highlight0 ")
                             + flags + _rtf_escape(value) + "}")
        leading = f"\\pard\\{align[alignment]}\\sl{int(240 * spacing)}\\slmult1 "
        body.append(leading + _rtf_escape(prefix) + "".join(rich_runs) + r"\par")
        visible_lines.append(visible)
    header = (r"{\rtf1\ansi\ansicpg1252\deff0\nouicompat\deflang2058"
              r"{\fonttbl{\f0 Segoe UI;}{\f1 Consolas;}{\f2 Arial;}{\f3 Times New Roman;}}"
              + color_table + r"\viewkind4\uc1 ")
    return (header + "\r\n".join(body) + "}").encode("ascii"), "\n".join(visible_lines)


def _row_note(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["ID"],
        "title": row["TITLE"] or "",
        "text": row["TEXT"] or "",
        "notebook": row["NOTEBOOK"] or "",
        "starred": bool(row["STARRED"]),
        "active": row["STATE"] == 1,
        "created": row["CREATED"],
        "updated": row["UPDATED"],
    }


def _process_ids() -> list[int]:
    if os.environ.get("SSN_TEST_MODE") == "1":
        return []
    if os.name != "nt":
        return []
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID = ctypes.c_void_p(-1).value

    class Entry(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong),
            ("cntUsage", ctypes.c_ulong),
            ("th32ProcessID", ctypes.c_ulong),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", ctypes.c_ulong),
            ("cntThreads", ctypes.c_ulong),
            ("th32ParentProcessID", ctypes.c_ulong),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel = ctypes.windll.kernel32
    snapshot = kernel.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID:
        return []
    ids: list[int] = []
    entry = Entry()
    entry.dwSize = ctypes.sizeof(Entry)
    try:
        ok = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == EXE_PATH.name.lower():
                ids.append(int(entry.th32ProcessID))
            ok = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return ids


def _close_gracefully(timeout: float = 12.0) -> bool:
    pids = set(_process_ids())
    if not pids:
        return False
    user32 = ctypes.windll.user32
    WM_CLOSE = 0x0010
    main_windows: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @callback_type
    def callback(hwnd: int, _lparam: int) -> bool:
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            class_name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_name, 256)
            if class_name.value == "CSSN_MainForm_WndClass":
                main_windows.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    if len(main_windows) != len(pids):
        raise RuntimeError(
            "No se encontró la ventana principal de Simple Sticky Notes; se abortó sin forzar el proceso."
        )
    for hwnd in main_windows:
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not (pids & set(_process_ids())):
            return True
        time.sleep(0.2)
    raise RuntimeError("Simple Sticky Notes no terminó tras solicitar un cierre normal; no se modificó la base.")


def _reopen() -> None:
    if not EXE_PATH.is_file():
        raise RuntimeError(f"No se encontró el ejecutable: {EXE_PATH}")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(EXE_PATH)], cwd=str(EXE_PATH.parent), close_fds=True, creationflags=flags)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def _exclusive_lock() -> Iterable[None]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError("Ya hay una operación de escritura de Simple Sticky Notes en curso.") from exc
    try:
        os.write(fd, f"pid={os.getpid()} time={time.time()}".encode())
        os.close(fd)
        yield
    finally:
        LOCK_PATH.unlink(missing_ok=True)


def _mutate(action: Callable[[sqlite3.Connection], dict[str, Any]]) -> dict[str, Any]:
    with _exclusive_lock():
        _validate_app_version()
        was_running = bool(_process_ids())
        closed = False
        backup: Path | None = None
        result: dict[str, Any] | None = None
        reopen_error = ""
        try:
            if was_running:
                closed = _close_gracefully()
            with closing(sqlite3.connect(DB_PATH, timeout=5)) as check:
                _validate_schema(check)
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            backup = BACKUP_DIR / f"Notes-{stamp}.db"
            shutil.copy2(DB_PATH, backup)
            backup_hash = _sha256(backup)
            conn = sqlite3.connect(DB_PATH, timeout=5)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("BEGIN IMMEDIATE")
                _validate_schema(conn)
                result = action(conn)
                conn.commit()
                _validate_schema(conn)
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
            result.update(
                {
                    "backup_path": str(backup),
                    "backup_sha256": backup_hash,
                    "application_was_running": was_running,
                    "application_reopened": False,
                }
            )
        except Exception:
            if backup and backup.is_file():
                try:
                    with closing(sqlite3.connect(DB_PATH)) as current:
                        integrity = current.execute("PRAGMA quick_check").fetchone()[0]
                    if integrity != "ok":
                        shutil.copy2(backup, DB_PATH)
                except Exception:
                    shutil.copy2(backup, DB_PATH)
            raise
        finally:
            if was_running and closed:
                try:
                    _reopen()
                except Exception as exc:
                    reopen_error = str(exc)
        if result is None:
            raise RuntimeError("La operación terminó sin resultado.")
        result["application_reopened"] = bool(was_running and closed and not reopen_error)
        if reopen_error:
            result["reopen_warning"] = reopen_error
        return result


def _resolve_notebook(conn: sqlite3.Connection, name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("El nombre de la libreta no puede estar vacío.")
    rows = conn.execute("SELECT NAME FROM NOTEBOOKS WHERE NAME = ? COLLATE NOCASE", (cleaned,)).fetchall()
    if len(rows) != 1:
        raise ValueError(f"No existe una libreta única llamada '{cleaned}'.")
    return rows[0][0]


def _load_ledger() -> dict[str, Any]:
    try:
        return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _save_ledger(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp = LEDGER_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, LEDGER_PATH)


@mcp.tool()
def list_notebooks() -> dict[str, Any]:
    """List notebooks and active-note counts from the local Simple Sticky Notes library."""
    with _readonly() as conn:
        rows = conn.execute(
            "SELECT b.ID,b.NAME,COUNT(n.ID) active_notes FROM NOTEBOOKS b "
            "LEFT JOIN NOTES n ON n.NOTEBOOK=b.NAME AND n.STATE=1 GROUP BY b.ID,b.NAME ORDER BY b.ID"
        ).fetchall()
        return {"database": str(DB_PATH), "notebooks": [dict(row) for row in rows]}


@mcp.tool()
def search_notes(query: str = "", notebook: str = "", starred_only: bool = False, limit: int = 50) -> dict[str, Any]:
    """Search active notes by title or plain text, optionally filtering by notebook and starred status."""
    limit = max(1, min(int(limit), 200))
    clauses = ["STATE=1"]
    params: list[Any] = []
    if query.strip():
        clauses.append("(TITLE LIKE ? ESCAPE '\\' OR TEXT LIKE ? ESCAPE '\\')")
        escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.extend([f"%{escaped}%", f"%{escaped}%"])
    if notebook.strip():
        clauses.append("NOTEBOOK = ? COLLATE NOCASE")
        params.append(notebook.strip())
    if starred_only:
        clauses.append("STARRED=1")
    params.append(limit)
    with _readonly() as conn:
        rows = conn.execute(
            "SELECT ID,TITLE,TEXT,NOTEBOOK,STARRED,STATE,CREATED,UPDATED FROM NOTES WHERE "
            + " AND ".join(clauses)
            + " ORDER BY UPDATED DESC LIMIT ?",
            params,
        ).fetchall()
        return {"count": len(rows), "notes": [_row_note(row) for row in rows]}


@mcp.tool()
def get_note(note_id: int) -> dict[str, Any]:
    """Get one note by numeric ID, including whether it is active or in trash."""
    with _readonly() as conn:
        row = conn.execute(
            "SELECT ID,TITLE,TEXT,NOTEBOOK,STARRED,STATE,CREATED,UPDATED FROM NOTES WHERE ID=?", (int(note_id),)
        ).fetchone()
        if row is None:
            raise ValueError(f"No existe la nota {note_id}.")
        return _row_note(row)


@mcp.tool()
def create_notebook(name: str) -> dict[str, Any]:
    """Create a notebook. The operation is idempotent by case-insensitive notebook name."""
    cleaned = name.strip()
    if not cleaned or len(cleaned) > 120:
        raise ValueError("El nombre debe contener entre 1 y 120 caracteres.")

    def action(conn: sqlite3.Connection) -> dict[str, Any]:
        existing = conn.execute("SELECT ID,NAME FROM NOTEBOOKS WHERE NAME=? COLLATE NOCASE", (cleaned,)).fetchone()
        if existing:
            return {"created": False, "notebook": {"id": existing[0], "name": existing[1]}}
        new_id = conn.execute("SELECT COALESCE(MAX(ID),0)+1 FROM NOTEBOOKS").fetchone()[0]
        conn.execute("INSERT INTO NOTEBOOKS(ID,NAME) VALUES (?,?)", (new_id, cleaned))
        return {"created": True, "notebook": {"id": new_id, "name": cleaned}}

    return _mutate(action)


@mcp.tool()
def create_note(
    title: str, text: str, notebook: str, starred: bool = False, request_id: str = "", format: str = "markdown"
) -> dict[str, Any]:
    """Create a note with native rich text.

    Use format='plain' for literal text and a stable request_id for retries.
    """
    title = title.strip()
    if not title or len(title) > 250:
        raise ValueError("El título debe contener entre 1 y 250 caracteres.")
    if len(text) > 200_000:
        raise ValueError("El texto supera el límite de 200.000 caracteres.")
    data, visible_text = _render_note(text, format)
    key = request_id.strip()
    if key:
        try:
            uuid.UUID(key)
        except ValueError as exc:
            raise ValueError("request_id debe ser un UUID válido.") from exc
        prior = _load_ledger().get(key)
        if prior:
            with _readonly() as conn:
                row = conn.execute("SELECT ID FROM NOTES WHERE ID=?", (prior["note_id"],)).fetchone()
                if row:
                    return {"created": False, "idempotent_replay": True, **prior}

    def action(conn: sqlite3.Connection) -> dict[str, Any]:
        target = _resolve_notebook(conn, notebook)
        new_id = conn.execute("SELECT COALESCE(MAX(ID),0)+1 FROM NOTES").fetchone()[0]
        zorder = conn.execute("SELECT COALESCE(MAX(ZORDER),0)+1 FROM NOTES").fetchone()[0]
        now = _ole_now()
        values = (
            new_id,
            1,
            now,
            now,
            0.0,
            int(bool(starred)),
            1,
            0,
            16764057,
            100,
            100,
            100,
            300,
            300,
            0,
            zorder,
            0.0,
            0.0,
            0,
            0,
            0,
            "",
            target,
            title,
            0,
            sqlite3.Binary(data),
            visible_text,
        )
        marks = ",".join("?" for _ in NOTE_COLUMNS)
        conn.execute(f"INSERT INTO NOTES ({','.join(NOTE_COLUMNS)}) VALUES ({marks})", values)
        return {
            "created": True,
            "idempotent_replay": False,
            "note_id": new_id,
            "title": title,
            "notebook": target,
            "starred": bool(starred),
        }

    result = _mutate(action)
    if key and result.get("created"):
        ledger = _load_ledger()
        ledger[key] = {"note_id": result["note_id"], "title": title, "notebook": result["notebook"], "request_id": key}
        _save_ledger(ledger)
        result["request_id"] = key
    return result


@mcp.tool()
def format_note(note_id: int, expected_text: str) -> dict[str, Any]:
    """Render existing Markdown as rich text when its body exactly matches expected_text."""
    data, visible_text = _render_note(expected_text, "markdown")

    def action(conn: sqlite3.Connection) -> dict[str, Any]:
        row = conn.execute(
            "SELECT ID,STATE,TEXT,DATA,NOTEBOOK,STARRED FROM NOTES WHERE ID=?", (int(note_id),)
        ).fetchone()
        if row is None or row["STATE"] != 1:
            raise ValueError(f"No existe la nota activa {note_id}.")
        if row["TEXT"] == visible_text and row["DATA"] == data:
            return {
                "note_id": int(note_id),
                "changed": False,
                "notebook": row["NOTEBOOK"],
                "starred": bool(row["STARRED"]),
            }
        if row["TEXT"] != expected_text:
            raise ValueError("El contenido cambió desde la lectura; se canceló el formato.")
        conn.execute(
            "UPDATE NOTES SET DATA=?,TEXT=?,UPDATED=? WHERE ID=?",
            (sqlite3.Binary(data), visible_text, _ole_now(), int(note_id)),
        )
        return {"note_id": int(note_id), "changed": True, "notebook": row["NOTEBOOK"], "starred": bool(row["STARRED"])}

    return _mutate(action)


@mcp.tool()
def format_rich_note(note_id: int, expected_text: str, document_json: str) -> dict[str, Any]:
    """Apply explicit rich formatting to an existing note, guarded by its current visible text."""
    data, visible_text = _render_note(document_json, "rich_json")
    if visible_text != expected_text:
        raise ValueError("El formato debe conservar exactamente el texto visible de la nota.")

    def action(conn: sqlite3.Connection) -> dict[str, Any]:
        row = conn.execute(
            "SELECT ID,STATE,TEXT,DATA,NOTEBOOK,STARRED FROM NOTES WHERE ID=?", (int(note_id),)
        ).fetchone()
        if row is None or row["STATE"] != 1:
            raise ValueError(f"No existe la nota activa {note_id}.")
        if row["TEXT"] != expected_text:
            raise ValueError("El contenido cambió desde la lectura; se canceló el formato.")
        if row["DATA"] == data:
            return {
                "note_id": int(note_id), "changed": False,
                "notebook": row["NOTEBOOK"], "starred": bool(row["STARRED"]),
            }
        conn.execute(
            "UPDATE NOTES SET DATA=?,UPDATED=? WHERE ID=?", (sqlite3.Binary(data), _ole_now(), int(note_id))
        )
        return {"note_id": int(note_id), "changed": True, "notebook": row["NOTEBOOK"], "starred": bool(row["STARRED"])}

    return _mutate(action)


def _normalize_ids(note_ids: list[int]) -> list[int]:
    ids = sorted({int(value) for value in note_ids})
    if not ids:
        raise ValueError("Debe indicarse al menos un ID de nota.")
    return ids


@mcp.tool()
def move_notes(note_ids: list[int], notebook: str) -> dict[str, Any]:
    """Move one or more existing active notes to an existing notebook."""
    ids = _normalize_ids(note_ids)

    def action(conn: sqlite3.Connection) -> dict[str, Any]:
        target = _resolve_notebook(conn, notebook)
        marks = ",".join("?" for _ in ids)
        found = [row[0] for row in conn.execute(f"SELECT ID FROM NOTES WHERE STATE=1 AND ID IN ({marks})", ids)]
        missing = sorted(set(ids) - set(found))
        if missing:
            raise ValueError(f"No son notas activas válidas: {missing}")
        now = _ole_now()
        conn.execute(f"UPDATE NOTES SET NOTEBOOK=?,UPDATED=? WHERE ID IN ({marks})", [target, now, *ids])
        return {"moved_note_ids": ids, "notebook": target}

    return _mutate(action)


@mcp.tool()
def set_starred(note_ids: list[int], starred: bool = True) -> dict[str, Any]:
    """Mark or unmark one or more existing active notes as starred."""
    ids = _normalize_ids(note_ids)

    def action(conn: sqlite3.Connection) -> dict[str, Any]:
        marks = ",".join("?" for _ in ids)
        found = [row[0] for row in conn.execute(f"SELECT ID FROM NOTES WHERE STATE=1 AND ID IN ({marks})", ids)]
        missing = sorted(set(ids) - set(found))
        if missing:
            raise ValueError(f"No son notas activas válidas: {missing}")
        conn.execute(
            f"UPDATE NOTES SET STARRED=?,UPDATED=? WHERE ID IN ({marks})", [int(bool(starred)), _ole_now(), *ids]
        )
        return {"note_ids": ids, "starred": bool(starred)}

    return _mutate(action)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

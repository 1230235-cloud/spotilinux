#!/usr/bin/env python3
"""
Thread-safe, persistent SQLite download queue and queue runner for MaoMusic.
Operates without PyQt6 dependencies for maximum testability and portability.
"""

import contextlib
import json
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent
from gdrive_sync import normalize_track_key


@dataclass
class QueueItem:
    id: int
    query: str
    query_key: str
    status: str  # pending | running | done | error | cancelled
    stage: str
    progress: float
    attempts: int
    max_attempts: int
    next_retry_at: float
    keep_local: bool
    title: str
    error: str
    result_json: str
    created_at: float
    started_at: float
    finished_at: float
    target_playlist: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def result(self) -> Dict[str, Any]:
        if not self.result_json:
            return {}
        try:
            data = json.loads(self.result_json)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def parse_playlist_txt(content: str, default_playlist_name: str = "") -> List[Tuple[str, str]]:
    """
    Analiza el contenido de un archivo .txt y devuelve una lista de tuplas (query, playlist_name).
    - Si encuentra líneas '# Playlist: Mi Lista' o '[Mi Lista]', cambia la playlist activa para las siguientes canciones.
    - Si no hay encabezados, asigna `default_playlist_name` (ej. el nombre del archivo .txt).
    """
    current_pl = default_playlist_name.strip()
    entries: List[Tuple[str, str]] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            low = line[1:].strip()
            if low.lower().startswith("playlist:"):
                pl_candidate = low.split(":", 1)[1].strip()
                if pl_candidate:
                    current_pl = pl_candidate
            continue
        if line.startswith("[") and line.endswith("]") and len(line) > 2:
            current_pl = line[1:-1].strip()
            continue
        entries.append((line, current_pl))
    return entries


class DownloadQueue:
    """Manages persistent SQLite storage of download queue items."""

    RETRY_DELAYS = [15.0, 60.0, 180.0]

    def __init__(self, db_path: Optional[Path] = None) -> None:
        if db_path is None:
            data_dir = BASE_DIR / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            self.db_path = data_dir / "download_queue.sqlite"
        else:
            self.db_path = Path(db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._init_db()

    @contextlib.contextmanager
    def _connection(self):
        conn = sqlite3.connect(str(self.db_path), timeout=20.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock, self._connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS queue_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query TEXT NOT NULL,
                    query_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT DEFAULT '',
                    progress REAL DEFAULT 0,
                    attempts INTEGER DEFAULT 0,
                    max_attempts INTEGER DEFAULT 3,
                    next_retry_at REAL DEFAULT 0,
                    keep_local INTEGER DEFAULT 1,
                    title TEXT DEFAULT '',
                    error TEXT DEFAULT '',
                    result_json TEXT DEFAULT '',
                    created_at REAL NOT NULL,
                    started_at REAL DEFAULT 0,
                    finished_at REAL DEFAULT 0,
                    target_playlist TEXT DEFAULT ''
                );
            """)
            # Migración idempotente si la tabla ya existía sin target_playlist
            cur = conn.execute("PRAGMA table_info(queue_items);")
            cols = {row["name"] for row in cur.fetchall()}
            if "target_playlist" not in cols:
                conn.execute("ALTER TABLE queue_items ADD COLUMN target_playlist TEXT DEFAULT '';")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status_retry ON queue_items (status, next_retry_at);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_query_key ON queue_items (query_key);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_target_playlist ON queue_items (target_playlist);")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS queue_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
            """)

    def _row_to_item(self, row: sqlite3.Row) -> QueueItem:
        target_pl = ""
        try:
            target_pl = str(row["target_playlist"] or "")
        except (IndexError, KeyError):
            pass

        return QueueItem(
            id=int(row["id"]),
            query=str(row["query"]),
            query_key=str(row["query_key"]),
            status=str(row["status"]),
            stage=str(row["stage"]),
            progress=float(row["progress"]),
            attempts=int(row["attempts"]),
            max_attempts=int(row["max_attempts"]),
            next_retry_at=float(row["next_retry_at"]),
            keep_local=bool(row["keep_local"]),
            title=str(row["title"]),
            error=str(row["error"]),
            result_json=str(row["result_json"]),
            created_at=float(row["created_at"]),
            started_at=float(row["started_at"]),
            finished_at=float(row["finished_at"]),
            target_playlist=target_pl,
        )

    def add(
        self,
        queries: List[str],
        keep_local: bool = True,
        target_playlist: str = "",
    ) -> Tuple[int, int]:
        """
        Adds multiple queries to the queue.
        Deduplicates against pending, running or already done items.
        Returns: (added_count, skipped_count)
        """
        added = 0
        skipped = 0
        now = time.time()
        tgt_pl = target_playlist.strip()

        with self._lock, self._connection() as conn:
            for raw_q in queries:
                q = raw_q.strip()
                if not q or q.startswith("#"):
                    continue

                q_key = normalize_track_key(q)
                if not q_key:
                    continue

                # Check if already present in non-error/non-cancelled state
                cur = conn.execute(
                    "SELECT id, status, target_playlist FROM queue_items WHERE query_key = ? AND status IN ('pending', 'running', 'done') LIMIT 1",
                    (q_key,),
                )
                existing = cur.fetchone()
                if existing:
                    skipped += 1
                    # Si se indica una playlist destino, actualizarla en el ítem existente para agruparlo
                    if tgt_pl and str(existing["target_playlist"] or "").strip() != tgt_pl:
                        conn.execute(
                            "UPDATE queue_items SET target_playlist = ? WHERE id = ?",
                            (tgt_pl, existing["id"]),
                        )
                    continue

                conn.execute(
                    """
                    INSERT INTO queue_items (
                        query, query_key, status, stage, progress, attempts, max_attempts,
                        next_retry_at, keep_local, title, error, result_json, created_at, target_playlist
                    ) VALUES (?, ?, 'pending', 'queued', 0, 0, 3, 0, ?, '', '', '', ?, ?)
                    """,
                    (q, q_key, 1 if keep_local else 0, now, tgt_pl),
                )
                added += 1

        return added, skipped

    def list(self, status: Optional[str] = None) -> List[QueueItem]:
        with self._lock, self._connection() as conn:
            if status:
                cur = conn.execute("SELECT * FROM queue_items WHERE status = ? ORDER BY id ASC", (status,))
            else:
                cur = conn.execute("SELECT * FROM queue_items ORDER BY id ASC")
            return [self._row_to_item(r) for r in cur.fetchall()]

    def get(self, item_id: int) -> Optional[QueueItem]:
        with self._lock, self._connection() as conn:
            cur = conn.execute("SELECT * FROM queue_items WHERE id = ? LIMIT 1", (item_id,))
            row = cur.fetchone()
            return self._row_to_item(row) if row else None

    def next_runnable(self, now: Optional[float] = None) -> Optional[QueueItem]:
        t = now if now is not None else time.time()
        with self._lock, self._connection() as conn:
            cur = conn.execute(
                """
                SELECT * FROM queue_items
                WHERE status = 'pending' AND next_retry_at <= ?
                ORDER BY id ASC LIMIT 1
                """,
                (t,),
            )
            row = cur.fetchone()
            return self._row_to_item(row) if row else None

    def mark_running(self, item_id: int, now: Optional[float] = None) -> None:
        t = now if now is not None else time.time()
        with self._lock, self._connection() as conn:
            conn.execute(
                """
                UPDATE queue_items
                SET status = 'running', stage = 'starting', attempts = attempts + 1,
                    started_at = ?, progress = 0
                WHERE id = ?
                """,
                (t, item_id),
            )

    def update_progress(self, item_id: int, stage: str, progress: float) -> None:
        with self._lock, self._connection() as conn:
            conn.execute(
                "UPDATE queue_items SET stage = ?, progress = ? WHERE id = ?",
                (stage, max(0.0, min(100.0, progress)), item_id),
            )

    def mark_done(self, item_id: int, title: str, result_data: Dict[str, Any], now: Optional[float] = None) -> None:
        t = now if now is not None else time.time()
        with self._lock, self._connection() as conn:
            conn.execute(
                """
                UPDATE queue_items
                SET status = 'done', stage = 'completed', progress = 100.0,
                    title = ?, result_json = ?, finished_at = ?, error = ''
                WHERE id = ?
                """,
                (title, json.dumps(result_data, ensure_ascii=False), t, item_id),
            )

    def mark_error(
        self,
        item_id: int,
        error_msg: str,
        is_transient: bool = False,
        now: Optional[float] = None,
    ) -> None:
        t = now if now is not None else time.time()
        with self._lock, self._connection() as conn:
            cur = conn.execute("SELECT attempts, max_attempts FROM queue_items WHERE id = ?", (item_id,))
            row = cur.fetchone()
            if not row:
                return

            attempts = int(row["attempts"])
            max_attempts = int(row["max_attempts"])

            if is_transient and attempts < max_attempts:
                delay_idx = min(attempts - 1, len(self.RETRY_DELAYS) - 1)
                retry_delay = self.RETRY_DELAYS[max(0, delay_idx)]
                next_t = t + retry_delay
                conn.execute(
                    """
                    UPDATE queue_items
                    SET status = 'pending', stage = 'waiting_retry', error = ?,
                        next_retry_at = ?, progress = 0
                    WHERE id = ?
                    """,
                    (f"Reintento pendiente: {error_msg}", next_t, item_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE queue_items
                    SET status = 'error', stage = 'failed', error = ?,
                        finished_at = ?
                    WHERE id = ?
                    """,
                    (error_msg, t, item_id),
                )

    def mark_cancelled(self, item_id: int, now: Optional[float] = None) -> None:
        t = now if now is not None else time.time()
        with self._lock, self._connection() as conn:
            conn.execute(
                """
                UPDATE queue_items
                SET status = 'cancelled', stage = 'cancelled', finished_at = ?
                WHERE id = ?
                """,
                (t, item_id),
            )

    def retry(self, item_id: int, now: Optional[float] = None) -> bool:
        t = now if now is not None else time.time()
        with self._lock, self._connection() as conn:
            cur = conn.execute(
                """
                UPDATE queue_items
                SET status = 'pending', stage = 'queued', progress = 0,
                    attempts = 0, next_retry_at = 0, error = ''
                WHERE id = ?
                """,
                (item_id,),
            )
            return cur.rowcount > 0

    def retry_failed(self, now: Optional[float] = None) -> int:
        with self._lock, self._connection() as conn:
            cur = conn.execute(
                """
                UPDATE queue_items
                SET status = 'pending', stage = 'queued', progress = 0,
                    attempts = 0, next_retry_at = 0, error = ''
                WHERE status IN ('error', 'cancelled')
                """
            )
            return cur.rowcount

    def remove(self, item_id: int) -> bool:
        with self._lock, self._connection() as conn:
            cur = conn.execute("DELETE FROM queue_items WHERE id = ?", (item_id,))
            return cur.rowcount > 0

    def set_target_playlist(self, item_id: int, target_playlist: str) -> bool:
        """Actualiza la playlist destino de un ítem en la cola."""
        tgt = str(target_playlist or "").strip()
        with self._lock, self._connection() as conn:
            cur = conn.execute(
                "UPDATE queue_items SET target_playlist = ? WHERE id = ?",
                (tgt, item_id),
            )
            return cur.rowcount > 0

    def clear_finished(self) -> int:
        with self._lock, self._connection() as conn:
            cur = conn.execute("DELETE FROM queue_items WHERE status IN ('done', 'cancelled')")
            return cur.rowcount

    def recover_after_crash(self) -> int:
        """Resets any stuck 'running' tasks back to 'pending' on startup."""
        with self._lock, self._connection() as conn:
            cur = conn.execute(
                """
                UPDATE queue_items
                SET status = 'pending', stage = 'recovered', progress = 0,
                    attempts = max(0, attempts - 1)
                WHERE status = 'running'
                """
            )
            return cur.rowcount

    def get_meta(self, key: str, default: str = "") -> str:
        with self._lock, self._connection() as conn:
            cur = conn.execute("SELECT value FROM queue_meta WHERE key = ? LIMIT 1", (key,))
            row = cur.fetchone()
            return str(row["value"]) if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._connection() as conn:
            conn.execute(
                "INSERT INTO queue_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )


class QueueRunner:
    """
    Background worker manager that pulls runnable items and processes them.
    Supports pause, concurrency limits (1 to 3), and thread-safe cancellation.
    """

    def __init__(
        self,
        queue: DownloadQueue,
        downloader_factory: Optional[Callable[[], Any]] = None,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
        max_parallel: int = 1,
    ) -> None:
        self.queue = queue
        self.downloader_factory = downloader_factory
        self.on_event = on_event or (lambda ev: None)
        self.max_parallel = max(1, min(3, max_parallel))

        self._running_threads: Dict[int, threading.Thread] = {}
        self._cancel_events: Dict[int, threading.Event] = {}
        self._lock = threading.Lock()
        self._stop_requested = threading.Event()
        self._paused = threading.Event()
        self._loop_thread: Optional[threading.Thread] = None

        # Reset any crashed tasks from previous session
        self.queue.recover_after_crash()

    @property
    def is_paused(self) -> bool:
        return self._paused.is_set()

    def pause(self) -> None:
        self._paused.set()
        self.on_event({"type": "state_changed", "action": "paused"})

    def resume(self) -> None:
        self._paused.clear()
        self.on_event({"type": "state_changed", "action": "resumed"})

    def start(self) -> None:
        if self._loop_thread and self._loop_thread.is_alive():
            return
        self._stop_requested.clear()
        self._loop_thread = threading.Thread(target=self._scheduler_loop, name="DownloadQueueScheduler", daemon=True)
        self._loop_thread.start()

    def stop(self) -> None:
        self._stop_requested.set()
        with self._lock:
            for ev in self._cancel_events.values():
                ev.set()
        if self._loop_thread and self._loop_thread.is_alive():
            self._loop_thread.join(timeout=2.0)

    def cancel_item(self, item_id: int) -> None:
        with self._lock:
            ev = self._cancel_events.get(item_id)
            if ev:
                ev.set()
        self.queue.mark_cancelled(item_id)
        self.on_event({"type": "item_cancelled", "item_id": item_id})

    def _scheduler_loop(self) -> None:
        while not self._stop_requested.is_set():
            if self._paused.is_set():
                time.sleep(0.5)
                continue

            with self._lock:
                active_count = len(self._running_threads)

            if active_count < self.max_parallel:
                item = self.queue.next_runnable()
                if item:
                    cancel_ev = threading.Event()
                    with self._lock:
                        self._cancel_events[item.id] = cancel_ev

                    t = threading.Thread(
                        target=self._execute_item,
                        args=(item.id, item.query, item.target_playlist, item.keep_local, cancel_ev),
                        name=f"QueueTask-{item.id}",
                        daemon=True,
                    )
                    with self._lock:
                        self._running_threads[item.id] = t
                    t.start()
                    continue

            time.sleep(0.5)

    def _execute_item(
        self,
        item_id: int,
        query: str,
        target_playlist: str,
        keep_local: bool,
        cancel_ev: threading.Event,
    ) -> None:
        self.queue.mark_running(item_id)
        self.on_event({
            "type": "item_started",
            "item_id": item_id,
            "query": query,
            "target_playlist": target_playlist,
        })

        def progress_callback(stage: str, pct: float, msg: str) -> None:
            if cancel_ev.is_set():
                return
            self.queue.update_progress(item_id, stage, pct)
            self.on_event({
                "type": "item_progress",
                "item_id": item_id,
                "stage": stage,
                "progress": pct,
                "message": msg,
                "target_playlist": target_playlist,
            })

        try:
            if self.downloader_factory:
                downloader = self.downloader_factory()
            else:
                from media_sync_worker import MediaDownloaderWorker
                downloader = MediaDownloaderWorker()

            if cancel_ev.is_set():
                self.queue.mark_cancelled(item_id)
                self.on_event({"type": "item_cancelled", "item_id": item_id, "target_playlist": target_playlist})
                return

            res = downloader.process_query(
                query,
                keep_local=keep_local,
                progress_cb=progress_callback,
                cancel_event=cancel_ev,
            )

            if cancel_ev.is_set():
                self.queue.mark_cancelled(item_id)
                self.on_event({"type": "item_cancelled", "item_id": item_id, "target_playlist": target_playlist})
                return

            if res.get("status") == "success":
                title = res.get("title", query)
                self.queue.mark_done(item_id, title, res)
                self.on_event({
                    "type": "item_done",
                    "item_id": item_id,
                    "query": query,
                    "target_playlist": target_playlist,
                    "result": res,
                })
            else:
                err_msg = res.get("error", "Error desconocido")
                is_transient = res.get("error_kind") == "transient"
                self.queue.mark_error(item_id, err_msg, is_transient=is_transient)
                self.on_event({
                    "type": "item_error",
                    "item_id": item_id,
                    "query": query,
                    "target_playlist": target_playlist,
                    "error": err_msg,
                    "transient": is_transient,
                })
        except Exception as exc:
            if cancel_ev.is_set():
                self.queue.mark_cancelled(item_id)
                self.on_event({"type": "item_cancelled", "item_id": item_id, "target_playlist": target_playlist})
            else:
                self.queue.mark_error(item_id, str(exc), is_transient=False)
                self.on_event({
                    "type": "item_error",
                    "item_id": item_id,
                    "query": query,
                    "target_playlist": target_playlist,
                    "error": str(exc),
                    "transient": False,
                })
        finally:
            with self._lock:
                self._running_threads.pop(item_id, None)
                self._cancel_events.pop(item_id, None)
            self.on_event({"type": "summary_changed"})

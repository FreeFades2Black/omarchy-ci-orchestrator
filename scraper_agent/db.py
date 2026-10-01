import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

DB_PATH = Path.home() / "agents" / "cache" / "intel_cache.db"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode = WAL;")  # Concurrent reads/writes
        cursor = conn.cursor()

        # Action versions cache
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS action_versions (
                repo TEXT PRIMARY KEY,
                version TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
        """)

        # CISA KEV signatures
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cisa_kev (
                cve_id TEXT PRIMARY KEY,
                ingested_at TEXT NOT NULL
            );
        """)

        # Academic research citations
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS academic_citations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                title TEXT NOT NULL UNIQUE,
                link TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)

        # Feed sync metadata
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS feed_sync_meta (
                feed_name TEXT PRIMARY KEY,
                last_synced TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
        """)
        conn.commit()


class IntelDB:
    def __init__(self):
        init_db()

    def get_action_version(self, action_repo: str) -> Optional[str]:
        now_str = _iso_now()
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT version FROM action_versions WHERE repo = ? AND expires_at > ?",
                (action_repo, now_str)
            )
            row = cursor.fetchone()
            return row[0] if row else None

    def set_action_version(self, action_repo: str, version: str, ttl_hours: int = 24):
        expires_at = (_utc_now() + timedelta(hours=ttl_hours)).isoformat()
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO action_versions (repo, version, expires_at)
                VALUES (?, ?, ?)
                ON CONFLICT(repo) DO UPDATE SET
                    version = excluded.version,
                    expires_at = excluded.expires_at;
                """,
                (action_repo, version, expires_at)
            )
            conn.commit()

    def is_feed_expired(self, feed_name: str) -> bool:
        now_str = _iso_now()
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT expires_at FROM feed_sync_meta WHERE feed_name = ? AND expires_at > ?",
                (feed_name, now_str)
            )
            return cursor.fetchone() is None

    def update_feed_sync(self, feed_name: str, ttl_hours: int = 12):
        now = _utc_now()
        expires_at = (now + timedelta(hours=ttl_hours)).isoformat()
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO feed_sync_meta (feed_name, last_synced, expires_at)
                VALUES (?, ?, ?)
                ON CONFLICT(feed_name) DO UPDATE SET
                    last_synced = excluded.last_synced,
                    expires_at = excluded.expires_at;
                """,
                (feed_name, now.isoformat(), expires_at)
            )
            conn.commit()

    def store_cisa_cves(self, cve_ids: Set[str]):
        now_str = _iso_now()
        records = [(cve, now_str) for cve in cve_ids]
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO cisa_kev (cve_id, ingested_at) VALUES (?, ?);",
                records
            )
            conn.commit()
        self.update_feed_sync("cisa_kev", ttl_hours=12)

    def store_citations(self, citations: List[Dict[str, str]]):
        now_str = _iso_now()
        with sqlite3.connect(DB_PATH) as conn:
            for item in citations:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO academic_citations (source, title, link, created_at)
                    VALUES (?, ?, ?, ?);
                    """,
                    (item["source"], item["title"], item["link"], now_str)
                )
            conn.commit()
        self.update_feed_sync("academic_citations", ttl_hours=24)

    def get_latest_citation(self) -> Optional[Dict[str, str]]:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT source, title, link FROM academic_citations ORDER BY id DESC LIMIT 1;"
            )
            row = cursor.fetchone()
            if row:
                return {"source": row[0], "title": row[1], "link": row[2]}
            return None

    def get_all_citations(self, limit: int = 5) -> List[Dict[str, str]]:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT source, title, link FROM academic_citations ORDER BY id DESC LIMIT ?;",
                (limit,)
            )
            rows = cursor.fetchall()
            return [{"source": r[0], "title": r[1], "link": r[2]} for r in rows]

    def get_stats(self) -> Dict[str, Any]:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT count(*) FROM cisa_kev;")
            cisa_count = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM academic_citations;")
            citations_count = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM action_versions;")
            cached_tags = cursor.fetchone()[0]
            cursor.execute("SELECT last_synced FROM feed_sync_meta WHERE feed_name = 'cisa_kev';")
            sync_row = cursor.fetchone()
            last_synced = sync_row[0] if sync_row else "Never"

        return {
            "cisa_kev_cves": cisa_count,
            "academic_citations": citations_count,
            "cached_action_tags": cached_tags,
            "last_synced": last_synced
        }

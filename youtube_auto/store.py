"""이미 넣은 영상을 기록해 두 번 넣지 않게 한다.

키는 영상 하나가 아니라 (영상, 재생목록) 짝이다. 같은 영상이 채널 설정 변경으로 다른
재생목록에 들어갈 수 있고, 그건 중복이 아니기 때문이다.

seen_channels 는 그 채널을 한 번이라도 훑었는지만 본다. 처음 보는 채널에서도 최근
영상 열다섯 개를 한꺼번에 받으므로, 그걸 그대로 넣으면 첫 회차에 재생목록이 과거 영상으로
덮인다. 첫 회차를 기록만 하고 넘기는 판단(seed_on_first_run)이 이 표를 쓴다.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

SCHEMA = """
CREATE TABLE IF NOT EXISTS added_videos (
    video_id     TEXT NOT NULL,
    playlist_id  TEXT NOT NULL,
    channel_id   TEXT NOT NULL,
    title        TEXT,
    published_at TEXT,
    added_at     TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'api',
    PRIMARY KEY (video_id, playlist_id)
);
CREATE INDEX IF NOT EXISTS idx_added_videos_channel ON added_videos (channel_id);
CREATE TABLE IF NOT EXISTS seen_channels (
    channel_id    TEXT PRIMARY KEY,
    first_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS channel_health (
    channel_id        TEXT PRIMARY KEY,
    last_ok_at        TEXT,
    last_error_at     TEXT,
    last_error        TEXT,
    consecutive_fails INTEGER NOT NULL DEFAULT 0
);
"""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Store:
    """중복 방지 기록. 파일 경로에 ':memory:' 를 주면 메모리에서 돈다."""

    def __init__(self, path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def is_added(self, video_id: str, playlist_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM added_videos WHERE video_id = ? AND playlist_id = ?",
            (video_id, playlist_id),
        ).fetchone()
        return row is not None

    def mark_added(
        self,
        video_id: str,
        playlist_id: str,
        channel_id: str,
        title: Optional[str] = None,
        published_at: Optional[str] = None,
        source: str = "api",
    ) -> None:
        """기록한다. 같은 짝을 다시 기록해도 덮어쓰지 않는다 — 처음 넣은 시각을 지킨다."""
        self.conn.execute(
            "INSERT OR IGNORE INTO added_videos "
            "(video_id, playlist_id, channel_id, title, published_at, added_at, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (video_id, playlist_id, channel_id, title, published_at, _now(), source),
        )
        self.conn.commit()

    def seen_channel(self, channel_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM seen_channels WHERE channel_id = ?", (channel_id,)
        ).fetchone()
        return row is not None

    def mark_channel_seen(self, channel_id: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO seen_channels (channel_id, first_seen_at) VALUES (?, ?)",
            (channel_id, _now()),
        )
        self.conn.commit()

    def seed_channel(self, channel_id: str, playlist_id: str, videos) -> int:
        """처음 보는 채널의 지금 목록을 기록하고 그 채널을 '본 채널' 로 표시한다.

        둘을 한 트랜잭션으로 묶는다. 따로 쓰면 기록이 중간에 끊겼는데 '본 채널' 표시만
        남을 수 있고, 그러면 다음 회차가 그 채널을 아는 채널로 보고 안 넣힌 과거 영상을
        새 영상으로 재생목록에 쏟는다.
        """
        now = _now()
        try:
            rows = [
                (v.video_id, playlist_id, channel_id, v.title, v.published, now, "seed")
                for v in videos
            ]
            self.conn.executemany(
                "INSERT OR IGNORE INTO added_videos "
                "(video_id, playlist_id, channel_id, title, published_at, added_at, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO seen_channels (channel_id, first_seen_at) VALUES (?, ?)",
                (channel_id, now),
            )
        except Exception:
            self.conn.rollback()
            raise
        self.conn.commit()
        return len(rows)

    def record_lookup_ok(self, channel_id: str) -> None:
        self.conn.execute(
            "INSERT INTO channel_health (channel_id, last_ok_at, consecutive_fails) "
            "VALUES (?, ?, 0) "
            "ON CONFLICT(channel_id) DO UPDATE SET last_ok_at = excluded.last_ok_at, "
            "consecutive_fails = 0",
            (channel_id, _now()),
        )
        self.conn.commit()

    def record_lookup_failure(self, channel_id: str, message: str) -> int:
        """실패를 세고 연속 몇 번째인지 돌려준다."""
        self.conn.execute(
            "INSERT INTO channel_health "
            "(channel_id, last_error_at, last_error, consecutive_fails) VALUES (?, ?, ?, 1) "
            "ON CONFLICT(channel_id) DO UPDATE SET last_error_at = excluded.last_error_at, "
            "last_error = excluded.last_error, "
            "consecutive_fails = channel_health.consecutive_fails + 1",
            (channel_id, _now(), message[:500]),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT consecutive_fails FROM channel_health WHERE channel_id = ?", (channel_id,)
        ).fetchone()
        return int(row[0]) if row else 1

    def health(self, channel_id: str) -> Optional[Tuple]:
        row = self.conn.execute(
            "SELECT last_ok_at, last_error_at, last_error, consecutive_fails "
            "FROM channel_health WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()
        return tuple(row) if row else None

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM added_videos").fetchone()[0]

    def recent(self, limit: int = 20) -> List[Tuple]:
        rows = self.conn.execute(
            "SELECT video_id, playlist_id, channel_id, title, added_at, source "
            "FROM added_videos ORDER BY added_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [tuple(row) for row in rows]


class ReadOnlyStore:
    """시험 실행(--dry-run)에서 쓴다. 읽기는 진짜 기록을 보고 쓰기는 버린다.

    시험 실행이 기록을 남기면 그 채널이 '이미 본 채널' 이 돼서, 정작 진짜로 돌릴 때
    첫 회차가 통째로 건너뛰어진다. 그 영상들은 목록에서 밀려날 때까지 한 번도 안 들어간다.
    """

    def __init__(self, store: Store):
        self._store = store

    def is_added(self, video_id: str, playlist_id: str) -> bool:
        return self._store.is_added(video_id, playlist_id)

    def seen_channel(self, channel_id: str) -> bool:
        return self._store.seen_channel(channel_id)

    def mark_added(self, *args, **kwargs) -> None:
        return None

    def mark_channel_seen(self, *args, **kwargs) -> None:
        return None

    def seed_channel(self, channel_id: str, playlist_id: str, videos) -> int:
        return len(list(videos))

    def record_lookup_ok(self, *args, **kwargs) -> None:
        return None

    def record_lookup_failure(self, *args, **kwargs) -> int:
        return 0

    def health(self, channel_id: str) -> Optional[Tuple]:
        return self._store.health(channel_id)

    def count(self) -> int:
        return self._store.count()

    def close(self) -> None:
        self._store.close()

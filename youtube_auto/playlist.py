"""재생목록에 영상을 넣는다 (playlistItems.insert).

오류를 한 덩어리로 보지 않고 네 갈래로 가른다. 갈래마다 다음에 할 일이 다르기 때문이다.

- 할당량 소진: 오늘은 더 넣을 수 없다. 회차를 통째로 멈추고 한참 뒤에 다시 온다.
- 일시적 오류(5xx·초당 호출 과다): 몇 초 쉬고 다시 걸어 본다.
- 재생목록 문제(없음·권한 없음·가득 참): 그 채널만 건너뛴다. 다시 걸어도 같다.
- 영상 문제(비공개·삭제됨): 그 영상만 건너뛴다.

이미 들어 있는 영상은 오류가 아니라 성공으로 센다 — 우리 기록이 지워졌을 뿐이고,
다음 회차에 또 시도하지 않도록 기록해 두는 것이 맞다.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from googleapiclient.errors import HttpError

from .errors import (
    QUOTA_REASONS,
    RETRY_REASONS,
    QuotaExceeded,
    TransientError,
    error_reason,
    status_of,
)

log = logging.getLogger(__name__)

ADDED = "added"
DUPLICATE = "duplicate"
DRY_RUN = "dry-run"

DUPLICATE_REASONS = {"videoAlreadyInPlaylist"}
PLAYLIST_REASONS = {
    "playlistNotFound",
    "playlistIdRequired",
    "playlistItemsNotAccessible",
    "playlistContainsMaximumNumberOfVideos",
    "playlistOperationUnsupported",
    "forbidden",
    "insufficientPermissions",
}
VIDEO_REASONS = {"videoNotFound", "invalidVideoId", "failedPrecondition", "notFound"}


class PlaylistNotUsable(Exception):
    """그 재생목록에는 넣을 수 없다 — 설정이나 권한 문제라 다시 걸어도 같다."""


class VideoNotAddable(Exception):
    """그 영상은 넣을 수 없다 — 비공개이거나 지워졌다."""


class PlaylistClient:
    """playlistItems.insert 한 번을 거는 얇은 겹. youtube 는 build() 가 돌려준 객체다."""

    def __init__(self, youtube, max_retries: int = 3, sleep: Callable[[float], None] = time.sleep):
        self.youtube = youtube
        self.max_retries = max_retries
        self.sleep = sleep

    def add(self, video_id: str, playlist_id: str) -> str:
        body = {
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        }
        attempt = 0
        while True:
            try:
                self.youtube.playlistItems().insert(part="snippet", body=body).execute()
                return ADDED
            except HttpError as exc:
                reason = error_reason(exc)
                status = status_of(exc)

                if reason in QUOTA_REASONS:
                    raise QuotaExceeded(
                        f"할당량 소진 ({reason}). 태평양 시간 자정까지 더 넣을 수 없다."
                    ) from exc
                if reason in DUPLICATE_REASONS:
                    return DUPLICATE
                if reason in PLAYLIST_REASONS:
                    raise PlaylistNotUsable(
                        f"재생목록 {playlist_id} 에 넣을 수 없다 ({reason}). "
                        "아이디가 맞는지, 로그인한 계정의 재생목록이 맞는지 본다."
                    ) from exc
                if reason in VIDEO_REASONS:
                    raise VideoNotAddable(f"영상 {video_id} 를 넣을 수 없다 ({reason}).") from exc

                transient = reason in RETRY_REASONS or status >= 500
                attempt += 1
                if not transient or attempt > self.max_retries:
                    raise TransientError(
                        f"영상 {video_id} 를 넣지 못했다 (상태 {status}, 사유 {reason or '알 수 없음'})."
                    ) from exc

                delay = 2 ** (attempt - 1)
                log.warning(
                    "일시적 오류라 %s초 뒤 다시 건다 (%s/%s, 사유 %s)",
                    delay, attempt, self.max_retries, reason or status,
                )
                self.sleep(delay)


class DryRunClient:
    """실제로는 안 넣고 무엇을 넣을지만 알린다. --dry-run 에서 쓴다."""

    def __init__(self):
        self.calls = []

    def add(self, video_id: str, playlist_id: str) -> str:
        self.calls.append((video_id, playlist_id))
        return DRY_RUN

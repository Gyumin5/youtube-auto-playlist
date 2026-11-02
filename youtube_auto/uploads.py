"""채널의 새 영상을 공개 API 의 업로드 재생목록에서 읽는다.

채널마다 "업로드" 라는 재생목록이 하나 자동으로 있고, 그 아이디는 채널 아이디의 앞 두
글자(UC)를 UU 로 바꾼 값이다. 거기서 최근 열다섯 개를 읽는다 — 호출 하나가 할당량
1단위다(search.list 는 100단위).

열다섯으로 묶어 두는 이유는 한 회차에 넣을 수 있는 양을 넘기지 않기 위해서다. 더 받으면
처음 보는 채널이 아닌데 기록에 없는 과거 영상이 한꺼번에 새 영상으로 보인다 — 새 영상
판정이 공개 시각이 아니라 "기록에 있나" 하나로 돌기 때문이다. 쪽을 넘겨 더 받는 것은
그 판정을 먼저 고친 뒤에 볼 일이다.

공개 시각은 contentDetails.videoPublishedAt 을 쓴다. snippet.publishedAt 은 그 영상이
재생목록에 담긴 시각이라 업로드 재생목록에서는 대개 같지만 뜻이 다르다.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

from googleapiclient.errors import HttpError

from .errors import (
    QUOTA_REASONS,
    RETRY_REASONS,
    QuotaExceeded,
    error_reason,
    status_of,
)

log = logging.getLogger(__name__)

# 한 회차에 채널마다 받는 개수. 위 설명대로 늘리지 않는다.
PAGE_SIZE = 15

# videos.list 는 한 번에 50개까지 묻는다. 우리는 15개라 늘 한 번에 끝난다.
DURATION_BATCH = 50

# 유튜브가 주는 길이 표기. 예: PT1H24M9S, PT47S, P1DT2H (라이브 다시보기가 하루를 넘기면 D 가 붙는다)
DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


def parse_duration(text: str) -> Optional[int]:
    """길이 표기를 초로 바꾼다. 모양이 아니면 None — 길이를 모르는 것과 0초는 다르다."""
    if not text:
        return None
    match = DURATION_RE.match(text)
    if not match:
        return None
    parts = {k: int(v) for k, v in match.groupdict(default="0").items()}
    return (
        parts["days"] * 86400
        + parts["hours"] * 3600
        + parts["minutes"] * 60
        + parts["seconds"]
    )

NOT_ACCESSIBLE_REASONS = {
    "playlistNotFound",
    "playlistItemsNotAccessible",
    "forbidden",
    "insufficientPermissions",
    "channelNotFound",
    "notFound",
}


class ChannelLookupError(Exception):
    """그 채널만 이번 회차에서 건너뛴다."""


class UploadsNotAccessible(ChannelLookupError):
    """업로드 목록을 읽을 수 없다 — 채널 아이디나 권한 문제라 다시 걸어도 같다."""


class LookupTransientError(ChannelLookupError):
    """몇 번 다시 걸어도 못 읽었다. 다음 회차에 다시 본다."""


@dataclass(frozen=True)
class Video:
    video_id: str
    title: str
    published: str
    channel_id: str

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


def uploads_playlist_id(channel_id: str) -> str:
    if not channel_id.startswith("UC") or len(channel_id) < 3:
        raise UploadsNotAccessible(
            f"{channel_id}: 채널 아이디가 UC 로 시작하지 않는다. 핸들(@이름)이 아니라 "
            "채널 아이디를 적어야 한다."
        )
    return "UU" + channel_id[2:]


class UploadsClient:
    """playlistItems.list 한 번을 거는 얇은 겹. youtube 는 build() 가 돌려준 객체다."""

    def __init__(self, youtube, max_retries: int = 3, sleep: Callable[[float], None] = time.sleep):
        self.youtube = youtube
        self.max_retries = max_retries
        self.sleep = sleep

    def fetch(self, channel_id: str, timeout: int = 20) -> List[Video]:
        """그 채널의 최근 영상을 오래된 것부터 돌려준다.

        timeout 은 지금 쓰지 않는다 — 구글 클라이언트가 자기 제한시간을 쓴다. 받는 자리를
        남겨 두는 것은 회차를 도는 쪽이 통로마다 다른 인자를 알 필요가 없게 하려는 것이다.
        """
        playlist_id = uploads_playlist_id(channel_id)
        response = self._execute(
            lambda: self.youtube.playlistItems().list(
                part="snippet,contentDetails", playlistId=playlist_id, maxResults=PAGE_SIZE
            ),
            label=channel_id,
            not_accessible=(
                f"{channel_id}: 업로드 목록 {playlist_id} 를 읽을 수 없다. "
                "채널 아이디가 맞는지 본다."
            ),
        )
        return _to_videos(response, channel_id)

    def durations(self, video_ids: Sequence[str], timeout: int = 20) -> Dict[str, int]:
        """영상마다 길이를 초로 돌려준다. 길이를 못 받은 영상은 빠진다.

        길이는 영상 목록에 안 들어 있어서 따로 묻는다. 한 번에 쉰 개까지라 열다섯 개는
        한 호출(1단위)로 끝난다. timeout 은 fetch 와 같은 이유로 받기만 한다.
        """
        ids = list(video_ids)
        if not ids:
            return {}

        lengths: Dict[str, int] = {}
        for start in range(0, len(ids), DURATION_BATCH):
            chunk = ids[start:start + DURATION_BATCH]
            response = self._execute(
                lambda c=chunk: self.youtube.videos().list(
                    part="contentDetails", id=",".join(c), maxResults=DURATION_BATCH
                ),
                label="영상 길이",
                not_accessible="영상 길이를 물을 수 없다. 권한을 본다.",
            )
            for item in response.get("items", []):
                seconds = parse_duration((item.get("contentDetails") or {}).get("duration", ""))
                if seconds is None:
                    log.warning("영상 %s 의 길이 표기를 못 읽었다.", item.get("id"))
                    continue
                lengths[item["id"]] = seconds
        return lengths

    def _execute(self, make_request: Callable, label: str, not_accessible: str):
        attempt = 0
        while True:
            try:
                return make_request().execute()
            except HttpError as exc:
                reason = error_reason(exc)
                status = status_of(exc)

                if reason in QUOTA_REASONS:
                    raise QuotaExceeded(
                        f"할당량 소진 ({reason}). 태평양 시간 자정까지 더 조회할 수 없다."
                    ) from exc
                if reason in NOT_ACCESSIBLE_REASONS or status == 404:
                    raise UploadsNotAccessible(
                        f"{not_accessible} (사유 {reason or status})"
                    ) from exc

                transient = reason in RETRY_REASONS or status >= 500
                attempt += 1
                if not transient or attempt > self.max_retries:
                    raise LookupTransientError(
                        f"{label}: 못 읽었다 (상태 {status}, 사유 {reason or '알 수 없음'})."
                    ) from exc

                delay = 2 ** (attempt - 1)
                log.warning(
                    "%s: 일시적 오류라 %s초 뒤 다시 건다 (%s/%s, 사유 %s)",
                    label, delay, attempt, self.max_retries, reason or status,
                )
                self.sleep(delay)


def _to_videos(response, channel_id: str) -> List[Video]:
    videos = []
    for item in response.get("items", []):
        details = item.get("contentDetails") or {}
        snippet = item.get("snippet") or {}
        video_id = details.get("videoId")
        if not video_id:
            log.warning("%s: 영상 아이디가 없는 항목을 건너뛴다 — %s",
                        channel_id, snippet.get("title"))
            continue
        videos.append(
            Video(
                video_id=video_id,
                title=snippet.get("title") or "",
                published=details.get("videoPublishedAt") or "",
                channel_id=channel_id,
            )
        )
    # 목록은 최신이 앞이다. 재생목록에는 올라온 순서대로 쌓이는 쪽이 자연스럽다.
    videos.reverse()
    return videos

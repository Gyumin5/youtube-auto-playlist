"""채널의 RSS 피드를 읽어 새 영상 목록을 만든다.

공개 API 대신 RSS 를 쓰는 이유는 하루 할당량이다. search.list 로 새 영상을 찾으면 호출
하나에 100단위가 나가는데, RSS 는 할당량을 아예 쓰지 않는다. 대신 피드에는 최근 열다섯
개만 들어 있고 몇 분 늦게 갱신된다 — 그래서 확인 주기를 분 단위로 줄여도 의미가 없다.

내려받기는 feedparser 에 맡기지 않고 직접 한다. feedparser.parse(url) 에는 제한시간을
줄 수가 없어서, 상대가 응답을 끊지 않으면 그 회차가 통째로 멈춘다.
"""
from __future__ import annotations

import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import List, Optional

import feedparser

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
USER_AGENT = "youtube-auto-playlist/1.0 (+personal use)"

log = logging.getLogger(__name__)


class FeedError(Exception):
    """피드를 못 읽었다. 그 채널만 이번 회차에서 건너뛴다."""


@dataclass(frozen=True)
class Video:
    video_id: str
    title: str
    published: str
    channel_id: str

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


def fetch_feed_bytes(channel_id: str, timeout: int = 20) -> bytes:
    url = FEED_URL.format(channel_id=channel_id)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise FeedError(f"{channel_id}: 피드 응답 코드 {response.status}")
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise FeedError(
                f"{channel_id}: 그런 채널이 없다(404). 채널 아이디를 다시 본다."
            ) from exc
        raise FeedError(f"{channel_id}: 피드 응답 코드 {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise FeedError(f"{channel_id}: 피드에 닿지 못했다 — {exc.reason}") from exc
    except OSError as exc:  # 제한시간 초과를 포함한다
        raise FeedError(f"{channel_id}: 피드를 읽는 중 오류 — {exc}") from exc


def parse_feed(raw: bytes, channel_id: str) -> List[Video]:
    """피드 본문을 영상 목록으로 바꾼다. 오래된 것이 앞에 오도록 뒤집는다."""
    parsed = feedparser.parse(raw)
    if getattr(parsed, "bozo", 0) and not parsed.entries:
        raise FeedError(f"{channel_id}: 피드를 해석하지 못했다 — {parsed.get('bozo_exception')}")

    videos = []
    for entry in parsed.entries:
        video_id: Optional[str] = entry.get("yt_videoid") or _video_id_from_link(entry.get("link"))
        if not video_id:
            log.warning("%s: 영상 아이디가 없는 항목을 건너뛴다 — %s", channel_id, entry.get("title"))
            continue
        videos.append(
            Video(
                video_id=video_id,
                title=entry.get("title") or "",
                published=entry.get("published") or "",
                channel_id=entry.get("yt_channelid") or channel_id,
            )
        )

    # 피드는 최신이 앞이다. 재생목록에는 올라온 순서대로 쌓이는 쪽이 자연스럽다.
    videos.reverse()
    return videos


def _video_id_from_link(link: Optional[str]) -> Optional[str]:
    if not link or "watch?v=" not in link:
        return None
    return link.split("watch?v=", 1)[1].split("&", 1)[0] or None


def fetch_videos(channel_id: str, timeout: int = 20) -> List[Video]:
    return parse_feed(fetch_feed_bytes(channel_id, timeout=timeout), channel_id)

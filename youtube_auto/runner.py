"""한 회차를 돈다 — 채널마다 피드를 보고, 새 영상을 그 채널의 재생목록에 넣는다.

채널과 재생목록의 짝은 설정이 정한다. 이 파일은 그 짝을 바꾸지 않는다 — 어느 채널의
영상이든 config 에 적힌 그 채널의 재생목록으로만 간다.

한 채널에서 터진 문제가 다음 채널을 막지 않는다. 예외는 할당량 소진 하나다 — 그건
계정 단위라 다음 채널도 똑같이 막히므로 회차를 그 자리에서 끝낸다.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .feeds import FeedError, fetch_videos
from .playlist import (
    ADDED,
    DRY_RUN,
    DUPLICATE,
    PlaylistNotUsable,
    QuotaExceeded,
    TransientError,
    VideoNotAddable,
)

log = logging.getLogger(__name__)


@dataclass
class RunStats:
    channels: int = 0
    feed_errors: int = 0
    new_videos: int = 0
    added: int = 0
    duplicates: int = 0
    seeded: int = 0
    skipped_videos: int = 0
    playlist_errors: int = 0
    quota_exceeded: bool = False

    def summary(self) -> str:
        return (
            f"채널 {self.channels}개 · 새 영상 {self.new_videos}개 · 넣음 {self.added}개 · "
            f"이미 있음 {self.duplicates}개 · 첫 회차 기록만 {self.seeded}개 · "
            f"건너뜀 {self.skipped_videos}개 · 피드 오류 {self.feed_errors}개 · "
            f"재생목록 오류 {self.playlist_errors}개"
            + (" · 할당량 소진" if self.quota_exceeded else "")
        )


def run_once(config, store, client, fetch: Optional[Callable] = None) -> RunStats:
    fetch = fetch or fetch_videos
    stats = RunStats()

    for rule in config.channels:
        stats.channels += 1
        try:
            videos = fetch(rule.channel_id, timeout=config.request_timeout_seconds)
        except FeedError as exc:
            log.warning("%s: 피드를 못 읽어 이번 회차는 건너뛴다 — %s", rule.name, exc)
            stats.feed_errors += 1
            continue

        first_time = not store.seen_channel(rule.channel_id)
        store.mark_channel_seen(rule.channel_id)

        fresh = [v for v in videos if not store.is_added(v.video_id, rule.playlist_id)]
        if not fresh:
            log.debug("%s: 새 영상 없음", rule.name)
            continue

        if first_time and config.seed_on_first_run:
            # 피드에는 최근 열다섯 개가 들어 있다. 처음 보는 채널의 그것을 그대로 넣으면
            # 재생목록이 과거 영상으로 덮인다. 기록만 하고 다음 회차부터 진짜 새것을 넣는다.
            for video in fresh:
                store.mark_added(
                    video.video_id, rule.playlist_id, rule.channel_id,
                    title=video.title, published_at=video.published, source="seed",
                )
            stats.seeded += len(fresh)
            log.info(
                "%s: 처음 보는 채널이라 지금 올라와 있는 %d개는 넣지 않고 넘긴다. "
                "다음 회차부터 새로 올라오는 영상을 넣는다.",
                rule.name, len(fresh),
            )
            continue

        stats.new_videos += len(fresh)
        batch = fresh[: config.max_videos_per_channel_per_run]
        if len(batch) < len(fresh):
            log.info(
                "%s: 새 영상 %d개 중 %d개만 이번에 넣는다. 나머지는 다음 회차로 넘긴다.",
                rule.name, len(fresh), len(batch),
            )

        for video in batch:
            try:
                result = client.add(video.video_id, rule.playlist_id)
            except QuotaExceeded as exc:
                log.error("할당량 소진이라 이번 회차를 여기서 끝낸다 — %s", exc)
                stats.quota_exceeded = True
                return stats
            except PlaylistNotUsable as exc:
                log.error("%s: %s", rule.name, exc)
                stats.playlist_errors += 1
                break
            except VideoNotAddable as exc:
                log.warning("%s: %s", rule.name, exc)
                stats.skipped_videos += 1
                # 다시 걸어도 같은 답이 온다. 기록해 두지 않으면 피드에 남아 있는 동안
                # 회차마다 할당량을 쓴다.
                store.mark_added(
                    video.video_id, rule.playlist_id, rule.channel_id,
                    title=video.title, published_at=video.published, source="skipped",
                )
                continue
            except TransientError as exc:
                # 기록하지 않는다 — 다음 회차에 다시 걸어 본다.
                log.warning("%s: %s", rule.name, exc)
                stats.skipped_videos += 1
                continue

            if result == DRY_RUN:
                log.info(
                    "[시험] %s → 재생목록 %s 에 넣을 것: %s (%s)",
                    rule.name, rule.playlist_id, video.title, video.url,
                )
                continue

            store.mark_added(
                video.video_id, rule.playlist_id, rule.channel_id,
                title=video.title, published_at=video.published,
                source="api" if result == ADDED else "duplicate",
            )
            if result == DUPLICATE:
                stats.duplicates += 1
                log.info("%s: 이미 재생목록에 있다 — %s", rule.name, video.title)
            else:
                stats.added += 1
                log.info(
                    "%s → 재생목록 %s 에 넣었다: %s (%s)",
                    rule.name, rule.playlist_id, video.title, video.url,
                )

    return stats


def next_delay(config, stats: RunStats) -> int:
    """다음 회차까지 쉴 시간. 할당량이 막힌 회차는 평소 주기로 돌아봐야 소용이 없다."""
    return config.quota_backoff_seconds if stats.quota_exceeded else config.check_interval_seconds


def run_forever(config, store, client, fetch: Optional[Callable] = None, stop=None) -> None:
    while True:
        if stop is not None and stop.is_set():
            break
        try:
            stats = run_once(config, store, client, fetch=fetch)
            log.info("회차 끝 — %s", stats.summary())
            delay = next_delay(config, stats)
        except Exception:
            # 한 회차가 통째로 터져도 데몬은 선다. 다음 회차에 다시 본다.
            log.exception("회차 중 예상 못 한 오류. 다음 회차에 다시 본다.")
            delay = config.check_interval_seconds

        log.info("다음 회차까지 %d초 쉰다.", delay)
        if stop is not None:
            if stop.wait(delay):
                break
        else:
            time.sleep(delay)

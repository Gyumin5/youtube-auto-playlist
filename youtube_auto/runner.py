"""한 회차를 돈다 — 채널마다 올라온 영상을 보고, 새 영상을 그 채널의 재생목록에 넣는다.

채널과 재생목록의 짝은 설정이 정한다. 이 파일은 그 짝을 바꾸지 않는다 — 어느 채널의
영상이든 config 에 적힌 그 채널의 재생목록으로만 간다.

한 채널에서 터진 문제가 다음 채널을 막지 않는다. 예외는 할당량 소진 하나다 — 그건
계정 단위라 다음 채널도 똑같이 막히므로 회차를 그 자리에서 끝낸다. 조회도 넣기도 같은
할당량을 쓰므로 어느 쪽에서 소진이 나든 똑같이 멈춘다.

새 영상 판정은 공개 시각이 아니라 "기록에 있나" 하나로 돈다. 그래서 한 번에 받는 개수를
늘리면 과거 영상이 새 영상으로 보인다 — 그 판정을 먼저 고치기 전에는 열다섯을 유지한다.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .errors import QuotaExceeded, TransientError
from .playlist import ADDED, DRY_RUN, DUPLICATE, PlaylistNotUsable, VideoNotAddable
from .uploads import (
    SHORTS_MAX_SECONDS,
    ChannelLookupError,
    ShortsCheckFailed,
    UploadsNotAccessible,
    is_short,
)

log = logging.getLogger(__name__)


@dataclass
class RunStats:
    channels: int = 0
    lookup_errors: int = 0
    lookup_ok: int = 0
    new_videos: int = 0
    added: int = 0
    duplicates: int = 0
    seeded: int = 0
    too_short: int = 0
    shorts: int = 0
    live: int = 0
    skipped_videos: int = 0
    playlist_errors: int = 0
    quota_exceeded: bool = False

    @property
    def all_lookups_failed(self) -> bool:
        return self.channels > 0 and self.lookup_ok == 0

    def summary(self) -> str:
        return (
            f"채널 {self.channels}개 · 새 영상 {self.new_videos}개 · 넣음 {self.added}개 · "
            f"이미 있음 {self.duplicates}개 · 첫 회차 기록만 {self.seeded}개 · "
            f"쇼츠 {self.shorts}개 · 라이브 {self.live}개 · 짧아서 거름 {self.too_short}개 · "
            f"건너뜀 {self.skipped_videos}개 · 조회 오류 {self.lookup_errors}개 · "
            f"재생목록 오류 {self.playlist_errors}개"
            + (" · 할당량 소진" if self.quota_exceeded else "")
        )


def run_once(
    config,
    store,
    client,
    fetch: Optional[Callable] = None,
    video_meta: Optional[Callable] = None,
    shorts_check: Callable = is_short,
) -> RunStats:
    """한 회차를 돈다.

    fetch 는 채널 아이디를 받아 영상 목록을 돌려준다 (UploadsClient.fetch).
    video_meta 는 영상 아이디 목록을 받아 길이와 라이브 여부를 돌려준다
    (UploadsClient.video_meta). shorts_check 는 영상 하나가 쇼츠인지 묻는다.
    """
    if fetch is None:
        raise TypeError("fetch 를 넘겨야 한다 — 조회에도 로그인한 계정이 필요하다.")
    if video_meta is None:
        raise TypeError(
            "video_meta 를 넘겨야 한다 — 쇼츠·라이브를 가리려면 영상 정보가 있어야 한다."
        )
    stats = RunStats()

    for rule in config.channels:
        stats.channels += 1
        try:
            videos = fetch(rule.channel_id, timeout=config.request_timeout_seconds)
        except QuotaExceeded as exc:
            # 조회와 넣기가 같은 할당량을 쓴다. 다음 채널도 똑같이 막히므로 여기서 끝낸다.
            log.error("할당량 소진이라 이번 회차를 여기서 끝낸다 — %s", exc)
            stats.quota_exceeded = True
            return stats
        except ChannelLookupError as exc:
            fails = store.record_lookup_failure(rule.channel_id, str(exc))
            stats.lookup_errors += 1
            if isinstance(exc, UploadsNotAccessible):
                # 설정이나 권한 문제라 다음 회차에도 같다. 사람이 고쳐야 한다.
                log.error("%s: %s (연속 %d회)", rule.name, exc, fails)
            else:
                log.warning("%s: %s 다음 회차에 다시 본다. (연속 %d회)", rule.name, exc, fails)
            continue

        store.record_lookup_ok(rule.channel_id)
        stats.lookup_ok += 1

        first_time = not store.seen_channel(rule.channel_id)
        fresh = [v for v in videos if not store.is_added(v.video_id, rule.playlist_id)]

        if first_time and config.seed_on_first_run:
            # 조회에는 최근 열다섯 개가 들어 있다. 처음 보는 채널의 그것을 그대로 넣으면
            # 재생목록이 과거 영상으로 덮인다. 기록만 하고 다음 회차부터 진짜 새것을 넣는다.
            # 기록과 '본 채널' 표시는 한 트랜잭션이다 — 표시만 남으면 다음 회차가 이
            # 영상들을 새 영상으로 보고 쏟아 넣는다.
            store.seed_channel(rule.channel_id, rule.playlist_id, fresh)
            stats.seeded += len(fresh)
            log.info(
                "%s: 처음 보는 채널이라 지금 올라와 있는 %d개는 넣지 않고 넘긴다. "
                "다음 회차부터 새로 올라오는 영상을 넣는다.",
                rule.name, len(fresh),
            )
            continue

        store.mark_channel_seen(rule.channel_id)

        if not fresh:
            log.debug("%s: 새 영상 없음", rule.name)
            continue

        try:
            meta = video_meta(
                [v.video_id for v in fresh], timeout=config.request_timeout_seconds
            )
        except QuotaExceeded as exc:
            log.error("할당량 소진이라 이번 회차를 여기서 끝낸다 — %s", exc)
            stats.quota_exceeded = True
            return stats
        except ChannelLookupError as exc:
            # 영상 정보를 모르면 쇼츠·라이브를 가릴 수 없다. 넣느니 다음 회차에 다시 본다.
            store.record_lookup_failure(rule.channel_id, str(exc))
            stats.lookup_errors += 1
            log.warning("%s: 영상 정보를 못 물었다 — %s", rule.name, exc)
            continue

        kept = []
        dropped = {"쇼츠": 0, "라이브": 0, "짧은 것": 0}
        for video in fresh:
            info = meta.get(video.video_id)
            if info is None:
                # 정보를 못 받은 영상만 보류한다. 기록하지 않으니 다음 회차에 다시 본다.
                log.warning("%s: 정보를 모르는 영상은 다음 회차에 다시 본다 — %s",
                            rule.name, video.title)
                continue

            if info.is_live:
                # 예약도 진행 중도 끝난 다시보기도 안 넣는다. 기록해 두지 않으면 예약이
                # 끝나 다시보기가 된 뒤에 들어간다.
                store.mark_added(
                    video.video_id, rule.playlist_id, rule.channel_id,
                    title=video.title, published_at=video.published, source="live",
                )
                stats.live += 1
                dropped["라이브"] += 1
                continue

            if info.duration_seconds <= SHORTS_MAX_SECONDS:
                try:
                    short = shorts_check(
                        video.video_id, timeout=config.request_timeout_seconds
                    )
                except ShortsCheckFailed as exc:
                    # 쇼츠인지 모르는 채로 넣으면 거르는 뜻이 없어진다. 다음 회차에 다시 본다.
                    log.warning("%s: %s 다음 회차에 다시 본다.", rule.name, exc)
                    continue
                if short:
                    store.mark_added(
                        video.video_id, rule.playlist_id, rule.channel_id,
                        title=video.title, published_at=video.published, source="shorts",
                    )
                    stats.shorts += 1
                    dropped["쇼츠"] += 1
                    continue

            if rule.min_duration_seconds and info.duration_seconds < rule.min_duration_seconds:
                # 기록해 둔다. 안 그러면 목록에 남아 있는 동안 회차마다 다시 묻는다.
                store.mark_added(
                    video.video_id, rule.playlist_id, rule.channel_id,
                    title=video.title, published_at=video.published, source="too_short",
                )
                stats.too_short += 1
                dropped["짧은 것"] += 1
                continue

            kept.append(video)

        if any(dropped.values()):
            reasons = ", ".join(f"{k} {v}개" for k, v in dropped.items() if v)
            log.info(
                "%s: %d개 중 %d개를 넣는다 — 거른 것: %s.",
                rule.name, len(fresh), len(kept), reasons,
            )
        fresh = kept
        if not fresh:
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
                # 다시 걸어도 같은 답이 온다. 기록해 두지 않으면 목록에 남아 있는 동안
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


def run_forever(
    config,
    store,
    client,
    fetch: Optional[Callable] = None,
    video_meta: Optional[Callable] = None,
    shorts_check: Callable = is_short,
    stop=None,
) -> None:
    blackout = False  # 직전 회차에 어느 채널도 못 읽었나
    while True:
        if stop is not None and stop.is_set():
            break
        try:
            stats = run_once(
                config, store, client,
                fetch=fetch, video_meta=video_meta, shorts_check=shorts_check,
            )
            log.info("회차 끝 — %s", stats.summary())
            if stats.all_lookups_failed and not stats.quota_exceeded:
                if not blackout:
                    log.error(
                        "채널 %d개를 하나도 못 읽었다. 로그인이 끊겼거나 유튜브 쪽이 막힌 것이다.",
                        stats.channels,
                    )
                blackout = True
            elif blackout and stats.lookup_ok > 0:
                log.info("조회가 다시 된다 — 이번 회차에 채널 %d개를 읽었다.", stats.lookup_ok)
                blackout = False
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

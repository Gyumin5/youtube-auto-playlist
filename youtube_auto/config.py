"""config.yaml 과 .env 를 읽어 검증된 설정 하나로 만든다.

잘못된 설정은 돌기 전에 거절한다. 채널 아이디나 재생목록 아이디가 틀리면 도는 동안에는
로그 한 줄로만 보이고, 그게 몇 시간 뒤에야 눈에 띈다. 특히 채널에 재생목록을 안 적은
경우를 조용히 넘기지 않는다 — 기본 재생목록으로 떨어뜨리면 엉뚱한 곳에 쌓인다.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

import yaml

# 채널 아이디는 UC 로 시작하는 24자다. 주소창에 보이는 @핸들이나 사용자 이름은 다른 값이라
# 그대로 넣으면 조회가 404 를 준다.
CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
PLAYLIST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{12,64}$")

# 공개 API 로 항목을 넣을 수 없는 재생목록. 넣으려 하면 권한 오류만 나오고 이유가 안 드러난다.
UNWRITABLE_PLAYLISTS = {
    "WL": "나중에 볼 동영상",
    "LL": "좋아요 표시한 동영상",
    "HL": "시청 기록",
}

MIN_INTERVAL_SECONDS = 60

# 공개 API 가 하루에 주는 양. 조회(playlistItems.list)는 한 번에 1단위, 넣기
# (playlistItems.insert)는 50단위다. 조회가 하루 치의 절반을 넘으면 거절한다 — 나머지는
# 넣기와 재시도 몫이고, 조회로 다 쓰면 정작 새 영상을 못 넣는다.
DAILY_QUOTA_UNITS = 10_000
LOOKUP_UNITS_PER_CALL = 1
# 채널 하나에 드는 조회 호출 수: 올라온 목록 받기 + 그 영상들의 길이·라이브 여부 묻기.
CALLS_PER_CHANNEL = 2
INSERT_UNITS_PER_CALL = 50
LOOKUP_BUDGET_FRACTION = 0.5
SECONDS_PER_DAY = 86_400

DEFAULTS = {
    "check_interval_seconds": 3600,
    "quota_backoff_seconds": 3600,
    "seed_on_first_run": True,
    "max_videos_per_channel_per_run": 10,
    "request_timeout_seconds": 20,
}

ENV_DEFAULTS = {
    "YTAP_CLIENT_SECRET_FILE": "client_secret.json",
    "YTAP_TOKEN_FILE": "token.json",
    "YTAP_DB_FILE": "state/added_videos.sqlite3",
    "YTAP_LOG_FILE": "youtube_auto_add.log",
}


class ConfigError(Exception):
    """설정이 틀려서 시작할 수 없다."""


@dataclass(frozen=True)
class ChannelRule:
    """채널 하나와 그 채널의 새 영상을 넣을 재생목록 하나.

    min_duration_seconds 가 0 이 아니면 그보다 짧은 영상은 안 넣는다. 예고·하이라이트가
    본편과 같은 채널에서 나오는 경우를 위한 칸이다.

    include_shorts 가 참이면 그 채널만 쇼츠도 넣는다. 올리는 것이 거의 다 쇼츠인 채널을
    전용 재생목록으로 통째로 받을 때 쓴다. 라이브는 이 칸과 상관없이 전 채널에서 거른다.

    title_exclude 에 적은 말이 제목에 들어 있으면 그 영상은 안 넣는다. 한 채널이 본편과
    다른 꼭지를 같이 올리는데 길이로는 안 갈릴 때 쓴다. 대소문자는 안 가린다.
    """

    name: str
    channel_id: str
    playlist_id: str
    min_duration_seconds: int = 0
    include_shorts: bool = False
    title_exclude: Tuple[str, ...] = ()


def daily_lookup_units(channel_count: int, interval_seconds: int) -> int:
    """하루에 조회로만 쓰는 할당량. 주기가 짧을수록 커진다.

    채널 하나에 호출이 둘 든다 — 올라온 목록 받기와 그 영상들의 길이·라이브 여부 묻기다.
    둘째 호출은 쇼츠와 라이브를 전 채널에서 거르기 때문에 늘 든다.
    """
    runs_per_day = math.ceil(SECONDS_PER_DAY / interval_seconds)
    return runs_per_day * channel_count * CALLS_PER_CHANNEL * LOOKUP_UNITS_PER_CALL


def min_interval_for_budget(channel_count: int) -> int:
    """조회 예산 안에 들어오는 가장 짧은 주기(초)."""
    budget = int(DAILY_QUOTA_UNITS * LOOKUP_BUDGET_FRACTION)
    calls_per_run = channel_count * CALLS_PER_CHANNEL * LOOKUP_UNITS_PER_CALL
    runs = budget // max(calls_per_run, 1)
    if runs <= 0:
        raise ConfigError(
            f"채널 {channel_count}개는 하루 한 번만 봐도 조회 예산 {budget}단위를 넘는다. "
            "채널 수를 줄인다."
        )
    return math.ceil(SECONDS_PER_DAY / runs)


@dataclass(frozen=True)
class Config:
    channels: Tuple[ChannelRule, ...]
    check_interval_seconds: int
    quota_backoff_seconds: int
    seed_on_first_run: bool
    max_videos_per_channel_per_run: int
    request_timeout_seconds: int
    client_secret_file: Path
    token_file: Path
    db_file: Path
    log_file: Path

    @property
    def daily_lookup_units(self) -> int:
        return daily_lookup_units(len(self.channels), self.check_interval_seconds)

    def playlist_for(self, channel_id: str) -> Optional[str]:
        for rule in self.channels:
            if rule.channel_id == channel_id:
                return rule.playlist_id
        return None


def _positive_int(raw, field: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ConfigError(f"{field} 는 정수여야 한다 (받은 값: {raw!r})")
    if raw <= 0:
        raise ConfigError(f"{field} 는 0보다 커야 한다 (받은 값: {raw})")
    return raw


def _check_channel_id(value: str, where: str) -> str:
    if not CHANNEL_ID_RE.match(value):
        raise ConfigError(
            f"{where}: 채널 아이디 모양이 아니다 — {value!r}. "
            "UC 로 시작하는 24자여야 한다. 주소창의 @핸들이나 사용자 이름은 다른 값이고, "
            "채널 페이지 소스에서 channelId 로 찾거나 주소가 /channel/UC... 인 쪽을 쓴다."
        )
    return value


def _check_playlist_id(value: str, where: str) -> str:
    if value in UNWRITABLE_PLAYLISTS:
        raise ConfigError(
            f"{where}: {UNWRITABLE_PLAYLISTS[value]}({value}) 에는 공개 API 로 영상을 넣을 수 없다. "
            "직접 만든 재생목록의 아이디를 쓴다."
        )
    if not PLAYLIST_ID_RE.match(value):
        raise ConfigError(
            f"{where}: 재생목록 아이디 모양이 아니다 — {value!r}. "
            "재생목록 주소의 list= 뒤에 붙는 값이다."
        )
    return value


def _title_exclude(raw, where: str) -> Tuple[str, ...]:
    """제목에 들어 있으면 안 넣을 말들. 비교는 대소문자를 안 가린다.

    빈 문자열을 거절하는 이유는 그게 모든 제목에 들어 있다고 판정돼 그 채널을 통째로
    막기 때문이다. 조용히 지나가면 왜 아무것도 안 들어오는지 설정만 봐서는 안 보인다.
    """
    if raw is None:
        return ()
    if isinstance(raw, str):
        raise ConfigError(
            f"{where}.title_exclude 는 목록이어야 한다 — 한 개라도 [\"{raw}\"] 로 적는다."
        )
    if not isinstance(raw, list):
        raise ConfigError(f"{where}.title_exclude 는 목록이어야 한다 (받은 값: {raw!r})")
    out = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(
                f"{where}.title_exclude 의 각 항목은 빈칸이 아닌 문자열이어야 한다 "
                f"(받은 값: {item!r})"
            )
        out.append(item.strip().casefold())
    return tuple(out)


def _parse_channels(raw) -> Tuple[ChannelRule, ...]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError("channels 에 채널을 최소 하나 적어야 한다.")

    rules = []
    seen_channels: Dict[str, str] = {}
    for index, item in enumerate(raw, start=1):
        where = f"channels[{index}]"
        if not isinstance(item, dict):
            raise ConfigError(f"{where}: 항목은 channel_id·playlist_id 를 가진 묶음이어야 한다.")

        unknown_keys = set(item) - {
            "name", "channel_id", "playlist_id", "min_duration_minutes", "include_shorts",
            "title_exclude",
        }
        if unknown_keys:
            raise ConfigError(
                f"{where}: 모르는 항목이 있다 — {', '.join(sorted(unknown_keys))}. 오타를 본다."
            )

        channel_id = item.get("channel_id")
        playlist_id = item.get("playlist_id")
        name = item.get("name") or channel_id or where

        if not channel_id:
            raise ConfigError(f"{where}: channel_id 가 없다.")
        if not playlist_id:
            raise ConfigError(
                f"{where}({name}): playlist_id 가 없다. "
                "채널마다 넣을 재생목록을 직접 적는다 — 기본 재생목록으로 떨어뜨리지 않는다."
            )
        if not isinstance(channel_id, str) or not isinstance(playlist_id, str):
            raise ConfigError(f"{where}: channel_id·playlist_id 는 문자열이어야 한다.")

        channel_id = _check_channel_id(channel_id.strip(), f"{where}({name})")
        playlist_id = _check_playlist_id(playlist_id.strip(), f"{where}({name})")

        if channel_id in seen_channels:
            raise ConfigError(
                f"{where}({name}): 채널 {channel_id} 가 {seen_channels[channel_id]} 에도 있다. "
                "한 채널은 재생목록 하나에만 짝짓는다."
            )
        raw_minutes = item.get("min_duration_minutes")
        if raw_minutes is None:
            min_seconds = 0
        else:
            min_seconds = _positive_int(raw_minutes, f"{where}({name}).min_duration_minutes") * 60

        include_shorts = item.get("include_shorts", False)
        if not isinstance(include_shorts, bool):
            raise ConfigError(
                f"{where}({name}).include_shorts 는 true 또는 false 여야 한다 "
                f"(받은 값: {include_shorts!r})"
            )
        if include_shorts and min_seconds:
            raise ConfigError(
                f"{where}({name}): include_shorts 와 min_duration_minutes 를 같이 쓸 수 없다. "
                "최소 길이가 쇼츠를 전부 걸러서 include_shorts 가 아무 일도 안 한다."
            )

        title_exclude = _title_exclude(item.get("title_exclude"), f"{where}({name})")

        seen_channels[channel_id] = where
        rules.append(
            ChannelRule(
                name=str(name),
                channel_id=channel_id,
                playlist_id=playlist_id,
                min_duration_seconds=min_seconds,
                include_shorts=include_shorts,
                title_exclude=title_exclude,
            )
        )

    return tuple(rules)


def _resolve(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (root / path)


def resolve_log_file(config_path, env: Optional[Dict[str, str]] = None) -> Path:
    """설정 파일을 아직 못 읽는 동안에도 기록은 같은 자리에 남긴다.

    로그 경로는 config.yaml 안이 아니라 환경 변수와 기본값에서 오므로, 설정이 없거나
    틀린 상태에서도 정할 수 있다. 상주로 돌 때 준비를 기다리는 동안 쓴다.
    """
    env = dict(env or {})
    root = Path(config_path).expanduser().resolve().parent
    return _resolve(root, env.get("YTAP_LOG_FILE") or ENV_DEFAULTS["YTAP_LOG_FILE"])


def load_config(config_path, env: Optional[Dict[str, str]] = None) -> Config:
    """config.yaml 을 읽어 Config 를 만든다. env 는 .env 를 읽어 넣은 환경 변수다."""
    env = dict(env or {})
    path = Path(config_path).expanduser().resolve()
    if not path.exists():
        raise ConfigError(
            f"설정 파일이 없다: {path}. config.yaml.example 을 config.yaml 로 복사해서 채운다."
        )

    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if raw is None:
        raise ConfigError(f"설정 파일이 비어 있다: {path}")
    if not isinstance(raw, dict):
        raise ConfigError(f"설정 파일의 맨 바깥은 묶음이어야 한다: {path}")

    unknown = set(raw) - set(DEFAULTS) - {"channels"}
    if unknown:
        raise ConfigError(
            f"모르는 설정 항목이 있다: {', '.join(sorted(unknown))}. 오타이거나 지워진 항목이다."
        )

    channels = _parse_channels(raw.get("channels"))

    values = dict(DEFAULTS)
    values.update({k: v for k, v in raw.items() if k in DEFAULTS})

    interval = _positive_int(values["check_interval_seconds"], "check_interval_seconds")
    if interval < MIN_INTERVAL_SECONDS:
        raise ConfigError(
            f"check_interval_seconds 는 {MIN_INTERVAL_SECONDS} 이상이어야 한다 (받은 값: {interval}). "
            "더 자주 봐도 새 영상이 빨리 보이지 않는다."
        )

    lookup_units = daily_lookup_units(len(channels), interval)
    budget = int(DAILY_QUOTA_UNITS * LOOKUP_BUDGET_FRACTION)
    if lookup_units > budget:
        needed = min_interval_for_budget(len(channels))
        raise ConfigError(
            f"주기 {interval}초로 채널 {len(channels)}개를 보면 조회에만 하루 "
            f"{lookup_units}단위를 쓴다. 하루 치가 {DAILY_QUOTA_UNITS}단위이고 조회 몫은 "
            f"{budget}단위까지다 — 나머지는 영상을 넣는 데 쓴다(한 개에 "
            f"{INSERT_UNITS_PER_CALL}단위). check_interval_seconds 를 {needed} 이상으로 "
            "올리거나 채널 수를 줄인다."
        )

    seed = values["seed_on_first_run"]
    if not isinstance(seed, bool):
        raise ConfigError(f"seed_on_first_run 은 true 또는 false 여야 한다 (받은 값: {seed!r})")

    root = path.parent
    return Config(
        channels=channels,
        check_interval_seconds=interval,
        quota_backoff_seconds=_positive_int(values["quota_backoff_seconds"], "quota_backoff_seconds"),
        seed_on_first_run=seed,
        max_videos_per_channel_per_run=_positive_int(
            values["max_videos_per_channel_per_run"], "max_videos_per_channel_per_run"
        ),
        request_timeout_seconds=_positive_int(
            values["request_timeout_seconds"], "request_timeout_seconds"
        ),
        client_secret_file=_resolve(root, env.get("YTAP_CLIENT_SECRET_FILE") or ENV_DEFAULTS["YTAP_CLIENT_SECRET_FILE"]),
        token_file=_resolve(root, env.get("YTAP_TOKEN_FILE") or ENV_DEFAULTS["YTAP_TOKEN_FILE"]),
        db_file=_resolve(root, env.get("YTAP_DB_FILE") or ENV_DEFAULTS["YTAP_DB_FILE"]),
        log_file=_resolve(root, env.get("YTAP_LOG_FILE") or ENV_DEFAULTS["YTAP_LOG_FILE"]),
    )

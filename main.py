#!/usr/bin/env python3
"""구독 채널의 새 영상을 채널마다 정해 둔 재생목록에 넣는다.

  python main.py --list        설정에 적힌 채널과 재생목록 짝만 확인한다 (연결 없음)
  python main.py --dry-run     피드를 읽고 무엇을 넣을지만 보여준다 (로그인 없이 된다)
  python main.py --auth-only   브라우저로 한 번 로그인해 token.json 을 만든다
  python main.py --once        한 회차만 돌고 끝낸다
  python main.py               주기마다 계속 돈다 (백그라운드 상주)
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

from youtube_auto.auth import AuthError, build_youtube, load_credentials
from youtube_auto.config import ConfigError, load_config
from youtube_auto.playlist import DryRunClient, PlaylistClient
from youtube_auto.runner import run_forever, run_once
from youtube_auto.store import ReadOnlyStore, Store

ROOT = Path(__file__).resolve().parent
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"

log = logging.getLogger("youtube_auto.main")


def setup_logging(log_file: Path, verbose: bool) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(LOG_FORMAT)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    # 파일은 5MB 씩 세 벌까지만 둔다 — 상주로 돌기 때문에 안 자르면 계속 자란다.
    rotating = RotatingFileHandler(
        str(log_file), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    rotating.setFormatter(formatter)
    root.addHandler(rotating)

    # 라이브러리 쪽 수다는 줄인다. 우리 기록이 묻힌다.
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
    logging.getLogger("googleapiclient.discovery").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="구독 채널의 새 영상을 채널별 재생목록에 자동으로 넣는다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--config", default=None, help="설정 파일 경로 (기본: config.yaml)")
    parser.add_argument("--once", action="store_true", help="한 회차만 돌고 끝낸다")
    parser.add_argument(
        "--dry-run", action="store_true", help="실제로 넣지 않고 무엇을 넣을지만 보여준다"
    )
    parser.add_argument(
        "--auth-only", action="store_true", help="브라우저로 로그인만 하고 끝낸다"
    )
    parser.add_argument(
        "--list", action="store_true", help="채널과 재생목록 짝만 확인한다 (연결 없음)"
    )
    parser.add_argument("--verbose", action="store_true", help="자세한 기록")
    return parser.parse_args(argv)


def print_rules(config) -> None:
    print(f"채널 {len(config.channels)}개 — 각 채널의 새 영상은 아래 재생목록으로 간다.\n")
    width = max(len(rule.name) for rule in config.channels)
    for index, rule in enumerate(config.channels, start=1):
        print(f"{index:>2}. {rule.name:<{width}}  {rule.channel_id}  →  {rule.playlist_id}")
    print(
        f"\n확인 주기 {config.check_interval_seconds}초 · "
        f"회차당 채널마다 최대 {config.max_videos_per_channel_per_run}개 · "
        f"첫 회차 기록만 하기 {'켬' if config.seed_on_first_run else '끔'}"
    )


def main(argv=None) -> int:
    args = parse_args(argv)
    load_dotenv(ROOT / ".env")

    config_path = args.config or os.environ.get("YTAP_CONFIG_FILE") or (ROOT / "config.yaml")
    try:
        config = load_config(config_path, env=dict(os.environ))
    except ConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 2

    if args.list:
        print_rules(config)
        return 0

    setup_logging(config.log_file, args.verbose)

    if args.auth_only:
        try:
            load_credentials(config.client_secret_file, config.token_file, allow_browser=True)
        except AuthError as exc:
            log.error("%s", exc)
            return 1
        log.info("로그인이 끝났다. 이제 백그라운드로 돌려도 된다.")
        return 0

    if args.dry_run:
        # 넣지 않으니 로그인도 필요 없다. 설정과 피드만 확인하는 자리다.
        client = DryRunClient()
        log.info("시험 실행 — 실제로 넣지 않는다.")
    else:
        try:
            # 터미널에서 직접 돌릴 때만 브라우저를 연다. 상주로 돌 때는 열지 않는다.
            credentials = load_credentials(
                config.client_secret_file,
                config.token_file,
                allow_browser=sys.stdin.isatty(),
            )
        except AuthError as exc:
            log.error("%s", exc)
            return 1
        client = PlaylistClient(build_youtube(credentials))

    # 시험 실행은 기록을 남기지 않는다 — 남기면 그 채널이 '이미 본 채널' 이 돼서
    # 진짜로 돌릴 때 첫 회차가 통째로 건너뛰어진다.
    store = ReadOnlyStore(Store(config.db_file)) if args.dry_run else Store(config.db_file)
    try:
        if args.once or args.dry_run:
            stats = run_once(config, store, client)
            log.info("회차 끝 — %s", stats.summary())
            return 0

        stop = threading.Event()

        def handle_signal(signum, _frame):
            log.info("신호 %s 를 받아 이번 대기 뒤에 멈춘다.", signum)
            stop.set()

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        log.info(
            "채널 %d개를 %d초마다 본다. 기록 파일 %s",
            len(config.channels), config.check_interval_seconds, config.db_file,
        )
        run_forever(config, store, client, stop=stop)
        log.info("멈췄다.")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())

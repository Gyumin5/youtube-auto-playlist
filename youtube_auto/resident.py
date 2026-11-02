"""상주로 돌 때 준비가 갖춰지기를 기다린다.

설정 파일과 로그인 결과는 사람이 채워 넣는 것이라, 처음 켠 시점에는 대개 둘 다 없다.
없다고 그냥 죽으면 서비스가 재시작만 되풀이하고 그 사이 같은 말이 로그를 덮는다.
여기서 기다리면 파일이 채워진 다음 회차에 그대로 이어서 돈다 — 사람이 다시 켤 일이 없다.
"""
from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

from .auth import AuthError, load_credentials
from .config import Config, ConfigError, load_config

# 준비를 기다리는 동안 다시 보는 간격. 사람이 파일을 채우는 속도에 맞춘 값이라
# 영상을 보는 주기(config 의 check_interval_seconds)와는 다른 축이다.
SETUP_RETRY_SECONDS = 300

log = logging.getLogger(__name__)


def _say(last: Optional[str], message: str) -> str:
    # 몇 분마다 같은 줄을 쌓으면 기록이 그 말로 덮인다. 처음과 바뀔 때만 적는다.
    if message != last:
        log.warning("%s", message)
    return message


def wait_until_ready(
    config_path,
    stop,
    env=None,
    retry_seconds: int = SETUP_RETRY_SECONDS,
    load_config_fn=load_config,
    load_credentials_fn=load_credentials,
) -> Optional[Tuple[Config, object]]:
    """설정과 로그인이 다 갖춰지면 둘을 돌려준다.

    기다리는 동안 멈추라는 신호를 받으면 None 을 돌려준다.
    """
    env = dict(os.environ if env is None else env)
    last_message = None
    waited = False

    while not stop.is_set():
        try:
            config = load_config_fn(config_path, env=env)
        except ConfigError as exc:
            last_message = _say(last_message, "설정을 기다린다 — %s" % exc)
            waited = True
            stop.wait(retry_seconds)
            continue

        try:
            # 상주로 돌 때는 브라우저를 열지 않는다 — 열 화면이 없다.
            credentials = load_credentials_fn(
                config.client_secret_file, config.token_file, allow_browser=False
            )
        except AuthError as exc:
            last_message = _say(last_message, "로그인을 기다린다 — %s" % exc)
            waited = True
            stop.wait(retry_seconds)
            continue

        if waited:
            log.info("준비가 끝났다. 이어서 돈다.")
        return config, credentials

    return None

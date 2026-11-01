"""구글 OAuth 2.0 인증. 최초 한 번만 브라우저를 열고, 그 뒤에는 token.json 을 재사용한다.

갱신 토큰이 살아 있으면 라이브러리가 만료된 접근 토큰을 알아서 바꿔 온다. 갱신까지
거절당하는 경우(비밀번호 변경·접근 권한 회수·테스트 모드 앱의 7일 만료)는 자동으로
풀 수 없다 — 그때는 낡은 토큰을 옆으로 치우고 사람이 다시 로그인해야 한다는 것을
분명히 말한다. 백그라운드로 도는 동안 브라우저를 여는 일은 하지 않는다.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/youtube"]

log = logging.getLogger(__name__)


class AuthError(Exception):
    """인증을 세울 수 없다. 대개 사람이 한 번 로그인해 주면 풀린다."""


def _save(credentials: Credentials, token_file: Path) -> None:
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(credentials.to_json(), encoding="utf-8")
    os.chmod(token_file, 0o600)


def _retire(token_file: Path) -> Path:
    """못 쓰게 된 토큰을 지우지 않고 옆으로 치운다 — 무엇이 있었는지 남겨 둔다."""
    retired = token_file.with_suffix(token_file.suffix + ".invalid")
    token_file.replace(retired)
    return retired


def load_credentials(
    client_secret_file,
    token_file,
    allow_browser: bool = False,
    scopes: Optional[list] = None,
) -> Credentials:
    client_secret_file = Path(client_secret_file)
    token_file = Path(token_file)
    scopes = scopes or SCOPES

    credentials: Optional[Credentials] = None
    if token_file.exists():
        try:
            credentials = Credentials.from_authorized_user_file(str(token_file), scopes)
        except ValueError as exc:
            raise AuthError(
                f"토큰 파일을 읽지 못했다: {token_file} — {exc}. "
                "파일을 지우고 다시 로그인한다: python main.py --auth-only"
            ) from exc

    if credentials and credentials.valid:
        return credentials

    if credentials and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
            _save(credentials, token_file)
            log.info("접근 토큰을 갱신했다.")
            return credentials
        except RefreshError as exc:
            retired = _retire(token_file)
            log.warning("토큰 갱신이 거절됐다 — %s. 낡은 토큰은 %s 로 치웠다.", exc, retired.name)
            credentials = None

    if not allow_browser:
        raise AuthError(
            "다시 로그인해야 한다. 화면이 있는 자리에서 한 번 실행한다: python main.py --auth-only"
        )

    if not client_secret_file.exists():
        raise AuthError(
            f"클라이언트 비밀 파일이 없다: {client_secret_file}. "
            "구글 클라우드 콘솔에서 데스크톱 앱 자격을 만들어 내려받고 그 경로에 둔다 (README 참고)."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_file), scopes)
    credentials = flow.run_local_server(port=0, prompt="consent")
    _save(credentials, token_file)
    log.info("로그인했다. 토큰을 저장했다: %s", token_file)
    return credentials


def build_youtube(credentials: Credentials):
    # cache_discovery 를 끄는 것은 oauth2client 가 없는 환경에서 나오는 경고를 없애기 위해서다.
    return build("youtube", "v3", credentials=credentials, cache_discovery=False)

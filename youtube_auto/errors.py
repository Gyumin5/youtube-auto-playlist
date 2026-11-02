"""구글 API 오류를 갈래로 가른다. 조회(uploads)와 넣기(playlist)가 같이 쓴다.

상태 코드만으로는 갈래를 못 가른다 — 403 하나에 할당량 소진과 권한 없음이 같이 들어
있어서, 본문의 reason 을 봐야 다음에 할 일이 갈린다.
"""
from __future__ import annotations

import json

from googleapiclient.errors import HttpError

QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded"}
RETRY_REASONS = {"rateLimitExceeded", "userRateLimitExceeded", "backendError", "internalError"}


class QuotaExceeded(Exception):
    """오늘 치 할당량을 다 썼다. 태평양 시간 자정에 초기화된다.

    회차를 통째로 멈춘다 — 계정 단위라 다음 채널도 똑같이 막힌다.
    """


class TransientError(Exception):
    """몇 번 다시 걸어도 안 됐다. 다음 회차에 다시 본다."""


def error_reason(error: HttpError) -> str:
    """구글 오류 본문에서 reason 을 꺼낸다. 본문이 JSON 이 아니면 빈 문자열."""
    try:
        content = error.content
        if isinstance(content, bytes):
            content = content.decode("utf-8", "replace")
        payload = json.loads(content)
    except Exception:
        return ""
    block = payload.get("error", {})
    errors = block.get("errors") or []
    if errors and isinstance(errors[0], dict):
        return errors[0].get("reason", "") or ""
    return block.get("status", "") or ""


def status_of(error: HttpError) -> int:
    resp = getattr(error, "resp", None)
    return int(getattr(resp, "status", 0) or 0)

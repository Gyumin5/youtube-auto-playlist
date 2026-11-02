"""상주로 돌 때 준비를 기다리는 동작."""
from __future__ import annotations

import sys
import tempfile
import unittest
import pathlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from youtube_auto.auth import AuthError  # noqa: E402
from youtube_auto.config import ConfigError, resolve_log_file  # noqa: E402
from youtube_auto.resident import wait_until_ready  # noqa: E402


class 가짜설정:
    """resident 가 읽는 두 경로만 가진 설정 대역."""

    def __init__(self, 이름="설정"):
        self.이름 = 이름
        self.client_secret_file = pathlib.Path("client_secret.json")
        self.token_file = pathlib.Path("token.json")

    def __eq__(self, other):
        return isinstance(other, 가짜설정) and other.이름 == self.이름


class FakeStop:
    """멈춤 신호 대역. 기다린 횟수를 세고, 정해진 횟수가 되면 멈춘다."""

    def __init__(self, stop_after=None):
        self.waits = []
        self.stop_after = stop_after
        self._set = False

    def is_set(self):
        return self._set

    def set(self):
        self._set = True

    def wait(self, seconds):
        self.waits.append(seconds)
        if self.stop_after is not None and len(self.waits) >= self.stop_after:
            self._set = True


class 준비를기다린다(unittest.TestCase):
    def test_설정과_로그인이_다_있으면_바로_돌려준다(self):
        stop = FakeStop()
        결과 = wait_until_ready(
            "config.yaml", stop,
            env={},
            load_config_fn=lambda path, env=None: 가짜설정(),
            load_credentials_fn=lambda *a, **k: "자격",
        )
        self.assertEqual(결과, (가짜설정(), "자격"))
        self.assertEqual(stop.waits, [], "준비돼 있으면 한 번도 기다리지 않는다")

    def test_설정이_없으면_기다렸다가_생기면_집어든다(self):
        stop = FakeStop()
        호출 = {"n": 0}

        def 설정읽기(path, env=None):
            호출["n"] += 1
            if 호출["n"] < 3:
                raise ConfigError("설정 파일이 없다")
            return 가짜설정()

        결과 = wait_until_ready(
            "config.yaml", stop, env={}, retry_seconds=7,
            load_config_fn=설정읽기,
            load_credentials_fn=lambda *a, **k: "자격",
        )
        self.assertEqual(결과, (가짜설정(), "자격"))
        self.assertEqual(stop.waits, [7, 7], "생길 때까지 정해진 간격으로 다시 본다")

    def test_로그인이_없으면_기다렸다가_생기면_집어든다(self):
        stop = FakeStop()
        호출 = {"n": 0}

        def 자격읽기(*_a, **_k):
            호출["n"] += 1
            if 호출["n"] < 2:
                raise AuthError("토큰이 없다")
            return "자격"

        결과 = wait_until_ready(
            "config.yaml", stop, env={}, retry_seconds=5,
            load_config_fn=lambda path, env=None: 가짜설정(),
            load_credentials_fn=자격읽기,
        )
        self.assertEqual(결과, (가짜설정(), "자격"))
        self.assertEqual(stop.waits, [5])

    def test_설정_오류는_상주를_죽이지_않는다(self):
        # 서비스가 죽으면 재시작만 되풀이한다. 예외가 밖으로 새지 않아야 한다.
        stop = FakeStop(stop_after=2)
        결과 = wait_until_ready(
            "config.yaml", stop, env={}, retry_seconds=1,
            load_config_fn=lambda path, env=None: (_ for _ in ()).throw(ConfigError("틀렸다")),
            load_credentials_fn=lambda *a, **k: "자격",
        )
        self.assertIsNone(결과, "멈추라는 신호를 받으면 None 으로 끝난다")
        self.assertEqual(len(stop.waits), 2)

    def test_멈추라는_신호가_먼저_와_있으면_아무것도_안_한다(self):
        stop = FakeStop()
        stop.set()
        호출 = {"n": 0}

        def 설정읽기(path, env=None):
            호출["n"] += 1
            return 가짜설정()

        결과 = wait_until_ready("config.yaml", stop, env={}, load_config_fn=설정읽기)
        self.assertIsNone(결과)
        self.assertEqual(호출["n"], 0)

    def test_같은_사유를_되풀이해_적지_않는다(self):
        import logging

        stop = FakeStop(stop_after=4)
        적힌것 = []

        class 모으기(logging.Handler):
            def emit(self, record):
                적힌것.append(record.getMessage())

        손잡이 = 모으기()
        기록기 = logging.getLogger("youtube_auto.resident")
        기록기.addHandler(손잡이)
        이전수준 = 기록기.level
        기록기.setLevel(logging.WARNING)
        try:
            wait_until_ready(
                "config.yaml", stop, env={}, retry_seconds=1,
                load_config_fn=lambda path, env=None: (_ for _ in ()).throw(ConfigError("없다")),
            )
        finally:
            기록기.removeHandler(손잡이)
            기록기.setLevel(이전수준)

        self.assertEqual(len(적힌것), 1, "네 번 돌아도 같은 말은 한 번만 적는다")


class 설정없이로그경로(unittest.TestCase):
    def test_설정_파일이_없어도_경로가_나온다(self):
        with tempfile.TemporaryDirectory() as 임시:
            경로 = resolve_log_file(Path(임시) / "config.yaml", env={})
            self.assertEqual(경로, Path(임시).resolve() / "youtube_auto_add.log")

    def test_환경변수가_기본값을_이긴다(self):
        with tempfile.TemporaryDirectory() as 임시:
            경로 = resolve_log_file(
                Path(임시) / "config.yaml", env={"YTAP_LOG_FILE": "logs/다른.log"}
            )
            self.assertEqual(경로, Path(임시).resolve() / "logs" / "다른.log")

    def test_절대경로는_그대로_쓴다(self):
        with tempfile.TemporaryDirectory() as 임시:
            경로 = resolve_log_file(
                Path(임시) / "config.yaml", env={"YTAP_LOG_FILE": "/var/tmp/어디든.log"}
            )
            self.assertEqual(경로, Path("/var/tmp/어디든.log"))


if __name__ == "__main__":
    unittest.main()

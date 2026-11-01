"""재생목록에 넣는 호출과 오류 갈래. 실제 API 는 부르지 않는다 — 가짜 객체로 인자와 갈래만 본다."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from googleapiclient.errors import HttpError  # noqa: E402

from youtube_auto.playlist import (  # noqa: E402
    ADDED,
    DRY_RUN,
    DUPLICATE,
    DryRunClient,
    PlaylistClient,
    PlaylistNotUsable,
    QuotaExceeded,
    TransientError,
    VideoNotAddable,
    error_reason,
)


class FakeResponse:
    def __init__(self, status):
        self.status = status
        self.reason = "fake"


def http_error(status, reason):
    body = json.dumps(
        {"error": {"code": status, "message": "nope", "errors": [{"reason": reason}]}}
    ).encode("utf-8")
    return HttpError(FakeResponse(status), body)


class FakeYouTube:
    """playlistItems().insert(...).execute() 만 흉내 낸다."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def playlistItems(self):
        return self

    def insert(self, part, body):
        self.calls.append({"part": part, "body": body})
        return self

    def execute(self):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class PlaylistClientTest(unittest.TestCase):
    def client(self, outcomes, max_retries=3):
        self.youtube = FakeYouTube(outcomes)
        self.slept = []
        return PlaylistClient(self.youtube, max_retries=max_retries, sleep=self.slept.append)

    def test_넣으면_성공을_돌려준다(self):
        client = self.client([{"id": "item1"}])
        self.assertEqual(client.add("vid1", "PL_A"), ADDED)

    def test_지정한_재생목록과_영상으로_부른다(self):
        client = self.client([{"id": "item1"}])
        client.add("vid1", "PL_A")
        call = self.youtube.calls[0]
        self.assertEqual(call["part"], "snippet")
        self.assertEqual(call["body"]["snippet"]["playlistId"], "PL_A")
        self.assertEqual(call["body"]["snippet"]["resourceId"]["videoId"], "vid1")
        self.assertEqual(call["body"]["snippet"]["resourceId"]["kind"], "youtube#video")

    def test_재생목록마다_다른_값으로_부른다(self):
        client = self.client([{"id": "1"}, {"id": "2"}])
        client.add("vid1", "PL_A")
        client.add("vid2", "PL_B")
        self.assertEqual(
            [c["body"]["snippet"]["playlistId"] for c in self.youtube.calls], ["PL_A", "PL_B"]
        )

    def test_할당량_소진은_따로_올린다(self):
        client = self.client([http_error(403, "quotaExceeded")])
        with self.assertRaises(QuotaExceeded):
            client.add("vid1", "PL_A")

    def test_하루_한도_초과도_할당량으로_본다(self):
        client = self.client([http_error(403, "dailyLimitExceeded")])
        with self.assertRaises(QuotaExceeded):
            client.add("vid1", "PL_A")

    def test_이미_들어_있으면_성공으로_센다(self):
        client = self.client([http_error(409, "videoAlreadyInPlaylist")])
        self.assertEqual(client.add("vid1", "PL_A"), DUPLICATE)

    def test_없는_재생목록은_설정_오류로_올린다(self):
        client = self.client([http_error(404, "playlistNotFound")])
        with self.assertRaises(PlaylistNotUsable):
            client.add("vid1", "PL_A")

    def test_권한이_없는_재생목록도_설정_오류다(self):
        client = self.client([http_error(403, "playlistItemsNotAccessible")])
        with self.assertRaises(PlaylistNotUsable):
            client.add("vid1", "PL_A")

    def test_가득_찬_재생목록도_설정_오류다(self):
        client = self.client([http_error(403, "playlistContainsMaximumNumberOfVideos")])
        with self.assertRaises(PlaylistNotUsable):
            client.add("vid1", "PL_A")

    def test_못_넣는_영상은_영상_오류로_올린다(self):
        client = self.client([http_error(404, "videoNotFound")])
        with self.assertRaises(VideoNotAddable):
            client.add("vid1", "PL_A")

    def test_일시적_오류는_다시_걸어_성공한다(self):
        client = self.client([http_error(503, "backendError"), {"id": "item1"}])
        self.assertEqual(client.add("vid1", "PL_A"), ADDED)
        self.assertEqual(self.slept, [1])

    def test_초당_호출_과다도_다시_건다(self):
        client = self.client([http_error(403, "rateLimitExceeded"), {"id": "item1"}])
        self.assertEqual(client.add("vid1", "PL_A"), ADDED)

    def test_다시_걸_때마다_기다리는_시간이_는다(self):
        outcomes = [http_error(500, "internalError")] * 3 + [{"id": "item1"}]
        client = self.client(outcomes)
        self.assertEqual(client.add("vid1", "PL_A"), ADDED)
        self.assertEqual(self.slept, [1, 2, 4])

    def test_다시_걸어도_안_되면_포기한다(self):
        outcomes = [http_error(500, "internalError")] * 5
        client = self.client(outcomes, max_retries=2)
        with self.assertRaises(TransientError):
            client.add("vid1", "PL_A")
        self.assertEqual(len(self.slept), 2)

    def test_모르는_오류는_다시_걸지_않는다(self):
        client = self.client([http_error(400, "badRequest")])
        with self.assertRaises(TransientError):
            client.add("vid1", "PL_A")
        self.assertEqual(self.slept, [])


class ErrorReasonTest(unittest.TestCase):
    def test_본문에서_사유를_꺼낸다(self):
        self.assertEqual(error_reason(http_error(403, "quotaExceeded")), "quotaExceeded")

    def test_본문이_json_이_아니면_빈_값이다(self):
        self.assertEqual(error_reason(HttpError(FakeResponse(500), b"<html>oops</html>")), "")

    def test_사유_목록이_없으면_상태를_쓴다(self):
        body = json.dumps({"error": {"status": "RESOURCE_EXHAUSTED"}}).encode("utf-8")
        self.assertEqual(error_reason(HttpError(FakeResponse(429), body)), "RESOURCE_EXHAUSTED")


class DryRunTest(unittest.TestCase):
    def test_실제로_넣지_않고_기록만_한다(self):
        client = DryRunClient()
        self.assertEqual(client.add("vid1", "PL_A"), DRY_RUN)
        self.assertEqual(client.calls, [("vid1", "PL_A")])


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""업로드 재생목록 조회. 실제 API 는 부르지 않는다 — 가짜 객체로 인자와 갈래만 본다."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from googleapiclient.errors import HttpError  # noqa: E402

from youtube_auto.errors import QuotaExceeded  # noqa: E402
from youtube_auto.uploads import (  # noqa: E402
    PAGE_SIZE,
    LookupTransientError,
    ShortsCheckFailed,
    UploadsClient,
    UploadsNotAccessible,
    VideoMeta,
    is_short,
    parse_duration,
    uploads_playlist_id,
)

CHANNEL = "UCsXVk37bltHxD1rDPwtNM8Q"


class FakeResponse:
    def __init__(self, status):
        self.status = status
        self.reason = "fake"


def http_error(status, reason):
    body = json.dumps(
        {"error": {"code": status, "message": "nope", "errors": [{"reason": reason}]}}
    ).encode("utf-8")
    return HttpError(FakeResponse(status), body)


def item(video_id, title="제목", published="2026-11-01T09:00:00Z"):
    return {
        "snippet": {"title": title, "publishedAt": "2026-01-01T00:00:00Z"},
        "contentDetails": {"videoId": video_id, "videoPublishedAt": published},
    }


class FakeYouTube:
    """playlistItems().list(...) 와 videos().list(...) 를 흉내 낸다."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.endpoints = []

    def playlistItems(self):
        self.endpoints.append("playlistItems")
        return self

    def videos(self):
        self.endpoints.append("videos")
        return self

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return self

    def execute(self):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class UploadsPlaylistIdTest(unittest.TestCase):
    def test_채널_아이디의_앞_두_글자를_바꾼다(self):
        self.assertEqual(uploads_playlist_id(CHANNEL), "UU" + CHANNEL[2:])

    def test_핸들을_넣으면_거절한다(self):
        with self.assertRaises(UploadsNotAccessible):
            uploads_playlist_id("@kurzgesagt")


class UploadsClientTest(unittest.TestCase):
    def client(self, outcomes, max_retries=3):
        youtube = FakeYouTube(outcomes)
        slept = []
        return UploadsClient(youtube, max_retries=max_retries, sleep=slept.append), youtube, slept

    def test_열다섯_개만_묻는다(self):
        client, youtube, _ = self.client([{"items": []}])
        client.fetch(CHANNEL)

        self.assertEqual(len(youtube.calls), 1)
        call = youtube.calls[0]
        self.assertEqual(call["maxResults"], PAGE_SIZE)
        self.assertEqual(PAGE_SIZE, 15)
        self.assertEqual(call["playlistId"], "UU" + CHANNEL[2:])
        self.assertIn("contentDetails", call["part"])

    def test_쪽을_더_넘기지_않는다(self):
        # 다음 쪽이 있다고 해도 한 번만 묻는다. 더 받으면 과거 영상이 새 영상으로 보인다.
        client, youtube, _ = self.client(
            [{"items": [item("v1")], "nextPageToken": "더있음"}]
        )
        client.fetch(CHANNEL)

        self.assertEqual(len(youtube.calls), 1)
        self.assertNotIn("pageToken", youtube.calls[0])

    def test_오래된_것부터_돌려준다(self):
        # API 는 최신을 앞에 준다. 재생목록에는 올라온 순서대로 쌓이는 쪽이 자연스럽다.
        client, _, _ = self.client(
            [{"items": [item("새것"), item("중간"), item("옛것")]}]
        )
        videos = client.fetch(CHANNEL)

        self.assertEqual([v.video_id for v in videos], ["옛것", "중간", "새것"])

    def test_공개_시각은_영상_쪽_값을_쓴다(self):
        client, _, _ = self.client([{"items": [item("v1", published="2026-10-31T12:00:00Z")]}])
        videos = client.fetch(CHANNEL)

        # snippet.publishedAt 은 재생목록에 담긴 시각이라 뜻이 다르다.
        self.assertEqual(videos[0].published, "2026-10-31T12:00:00Z")
        self.assertEqual(videos[0].channel_id, CHANNEL)
        self.assertIn("v1", videos[0].url)

    def test_영상_아이디가_없는_항목은_건너뛴다(self):
        broken = {"snippet": {"title": "지워진 영상"}, "contentDetails": {}}
        client, _, _ = self.client([{"items": [item("v1"), broken]}])
        videos = client.fetch(CHANNEL)

        self.assertEqual([v.video_id for v in videos], ["v1"])

    def test_할당량_소진은_회차를_멈추라고_알린다(self):
        client, _, _ = self.client([http_error(403, "quotaExceeded")])
        with self.assertRaises(QuotaExceeded):
            client.fetch(CHANNEL)

    def test_없는_재생목록은_그_채널만_건너뛴다(self):
        client, _, _ = self.client([http_error(404, "playlistNotFound")])
        with self.assertRaises(UploadsNotAccessible):
            client.fetch(CHANNEL)

    def test_사유를_모르는_404_도_접근_불가로_본다(self):
        client, _, _ = self.client([http_error(404, "")])
        with self.assertRaises(UploadsNotAccessible):
            client.fetch(CHANNEL)

    def test_권한_오류는_다시_걸지_않는다(self):
        client, youtube, _ = self.client([http_error(403, "insufficientPermissions")])
        with self.assertRaises(UploadsNotAccessible):
            client.fetch(CHANNEL)
        self.assertEqual(len(youtube.calls), 1)

    def test_일시적_오류는_몇_번_다시_건다(self):
        client, youtube, slept = self.client(
            [http_error(503, "backendError"), http_error(503, "backendError"),
             {"items": [item("v1")]}]
        )
        videos = client.fetch(CHANNEL)

        self.assertEqual([v.video_id for v in videos], ["v1"])
        self.assertEqual(len(youtube.calls), 3)
        self.assertEqual(slept, [1, 2])

    def test_계속_실패하면_그_채널만_포기한다(self):
        client, youtube, _ = self.client([http_error(503, "backendError")] * 5, max_retries=2)
        with self.assertRaises(LookupTransientError):
            client.fetch(CHANNEL)
        self.assertEqual(len(youtube.calls), 3)

    def test_모르는_오류는_다시_걸지_않는다(self):
        client, youtube, _ = self.client([http_error(400, "badRequest")])
        with self.assertRaises(LookupTransientError):
            client.fetch(CHANNEL)
        self.assertEqual(len(youtube.calls), 1)


class ParseDurationTest(unittest.TestCase):
    def test_시분초를_초로_바꾼다(self):
        self.assertEqual(parse_duration("PT1H24M9S"), 5049)
        self.assertEqual(parse_duration("PT46M52S"), 2812)
        self.assertEqual(parse_duration("PT47S"), 47)
        self.assertEqual(parse_duration("PT1M"), 60)

    def test_하루를_넘기는_다시보기도_읽는다(self):
        self.assertEqual(parse_duration("P1DT2H"), 93600)

    def test_길이를_모르는_것과_0초는_다르다(self):
        # 라이브 진행 중인 영상은 길이가 P0D 로 온다.
        self.assertEqual(parse_duration("P0D"), 0)
        self.assertIsNone(parse_duration(""))
        self.assertIsNone(parse_duration("몰라"))


class VideoMetaTest(unittest.TestCase):
    def client(self, outcomes):
        youtube = FakeYouTube(outcomes)
        return UploadsClient(youtube, sleep=lambda _s: None), youtube

    def test_영상마다_길이와_라이브_여부를_돌려준다(self):
        client, youtube = self.client([
            {"items": [
                {"id": "v1", "contentDetails": {"duration": "PT46M52S"}},
                {"id": "v2", "contentDetails": {"duration": "PT47S"}},
            ]}
        ])
        meta = client.video_meta(["v1", "v2"])

        self.assertEqual(meta["v1"], VideoMeta(duration_seconds=2812, is_live=False))
        self.assertEqual(meta["v2"], VideoMeta(duration_seconds=47, is_live=False))
        self.assertEqual(youtube.endpoints, ["videos"])
        self.assertEqual(youtube.calls[0]["id"], "v1,v2")
        self.assertEqual(youtube.calls[0]["part"], "contentDetails,liveStreamingDetails")

    def test_라이브_칸이_있으면_라이브로_본다(self):
        # 예약도 진행 중도 끝난 다시보기도 이 칸을 들고 있다.
        client, _ = self.client([
            {"items": [{
                "id": "v1",
                "contentDetails": {"duration": "P0D"},
                "liveStreamingDetails": {"scheduledStartTime": "2026-11-02T12:00:00Z"},
            }]}
        ])
        meta = client.video_meta(["v1"])

        self.assertTrue(meta["v1"].is_live)
        self.assertEqual(meta["v1"].duration_seconds, 0)
        self.assertFalse(meta["v1"].live_ended)

    def test_끝난_라이브는_다시보기로_본다(self):
        client, _ = self.client([
            {"items": [{
                "id": "v1",
                "contentDetails": {"duration": "PT1H30M"},
                "liveStreamingDetails": {"actualStartTime": "2026-11-02T12:00:00Z",
                                         "actualEndTime": "2026-11-02T13:30:00Z"},
            }]}
        ])
        meta = client.video_meta(["v1"])

        self.assertTrue(meta["v1"].is_live)
        self.assertTrue(meta["v1"].live_ended)

    def test_한_번에_묻는다(self):
        # 열다섯 개짜리 목록이 호출 하나(1단위)로 끝나야 한다.
        ids = [f"v{i}" for i in range(15)]
        client, youtube = self.client([
            {"items": [{"id": i, "contentDetails": {"duration": "PT10M"}} for i in ids]}
        ])
        client.video_meta(ids)

        self.assertEqual(len(youtube.calls), 1)

    def test_길이를_못_읽은_영상은_빠진다(self):
        client, _ = self.client([
            {"items": [
                {"id": "v1", "contentDetails": {"duration": "PT10M"}},
                {"id": "v2", "contentDetails": {}},
            ]}
        ])
        meta = client.video_meta(["v1", "v2"])

        self.assertEqual(list(meta), ["v1"])

    def test_물을_것이_없으면_부르지도_않는다(self):
        client, youtube = self.client([])
        self.assertEqual(client.video_meta([]), {})
        self.assertEqual(youtube.calls, [])

    def test_할당량_소진은_그대로_올린다(self):
        client, _ = self.client([http_error(403, "quotaExceeded")])
        with self.assertRaises(QuotaExceeded):
            client.video_meta(["v1"])


class IsShortTest(unittest.TestCase):
    """쇼츠 주소가 그대로 열리면 쇼츠, 일반 보기로 넘기면 아니다."""

    def call(self, outcome):
        import youtube_auto.uploads as uploads

        class FakeOpener:
            def open(self, request, timeout=None):
                if isinstance(outcome, Exception):
                    raise outcome
                return FakeHead(outcome)

        original = uploads._no_redirect_opener
        uploads._no_redirect_opener = FakeOpener()
        try:
            return is_short("abc123")
        finally:
            uploads._no_redirect_opener = original

    def test_200이면_쇼츠다(self):
        self.assertTrue(self.call(200))

    def test_303이면_쇼츠가_아니다(self):
        self.assertFalse(self.call(http_redirect(303)))

    def test_물어보지_못하면_올린다(self):
        with self.assertRaises(ShortsCheckFailed):
            self.call(OSError("연결 안 됨"))

    def test_뜻밖의_상태도_올린다(self):
        # 모르는 답을 쇼츠가 아닌 것으로 치면 쇼츠가 조용히 들어간다.
        with self.assertRaises(ShortsCheckFailed):
            self.call(http_redirect(500))


class FakeHead:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_redirect(code):
    import urllib.error
    return urllib.error.HTTPError(
        "https://www.youtube.com/shorts/abc123", code, "redirect", {}, None
    )


if __name__ == "__main__":
    unittest.main(verbosity=2)

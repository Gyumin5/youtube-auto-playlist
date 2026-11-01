"""한 회차의 동작. 가장 중요한 것은 어느 채널의 영상이 어느 재생목록으로 가느냐다."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from youtube_auto.config import ChannelRule, Config  # noqa: E402
from youtube_auto.feeds import FeedError, Video  # noqa: E402
from youtube_auto.playlist import (  # noqa: E402
    ADDED,
    DUPLICATE,
    DryRunClient,
    PlaylistNotUsable,
    QuotaExceeded,
    TransientError,
    VideoNotAddable,
)
from youtube_auto.runner import next_delay, run_once  # noqa: E402
from youtube_auto.store import ReadOnlyStore, Store  # noqa: E402

CH_A = "UC_x5XG1OV2P6uZZ5FSM9Ttw"
CH_B = "UCBJycsmduvYEL83R_U4JriQ"
PL_A = "PLaaaaaaaaaaaaaaaaaaaa"
PL_B = "PLbbbbbbbbbbbbbbbbbbbb"

RULES = (
    ChannelRule(name="가 채널", channel_id=CH_A, playlist_id=PL_A),
    ChannelRule(name="나 채널", channel_id=CH_B, playlist_id=PL_B),
)


def make_config(channels=RULES, **kw):
    values = dict(
        check_interval_seconds=900,
        quota_backoff_seconds=3600,
        seed_on_first_run=False,
        max_videos_per_channel_per_run=10,
        request_timeout_seconds=20,
        client_secret_file=Path("client_secret.json"),
        token_file=Path("token.json"),
        db_file=Path("db.sqlite3"),
        log_file=Path("run.log"),
    )
    values.update(kw)
    return Config(channels=tuple(channels), **values)


def video(video_id, channel_id, title=None):
    return Video(
        video_id=video_id,
        title=title or f"{video_id} 제목",
        published="2026-11-01T09:00:00+00:00",
        channel_id=channel_id,
    )


def make_fetch(mapping):
    def fetch(channel_id, timeout=20):
        result = mapping[channel_id]
        if isinstance(result, Exception):
            raise result
        return result

    return fetch


class RecordingClient:
    """add 호출을 받아 적는다. results 로 영상마다 다른 답을 시킬 수 있다."""

    def __init__(self, results=None):
        self.calls = []
        self.results = results or {}

    def add(self, video_id, playlist_id):
        self.calls.append((video_id, playlist_id))
        outcome = self.results.get(video_id, ADDED)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class RunOnceTest(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_채널마다_자기_재생목록으로_간다(self):
        fetch = make_fetch({CH_A: [video("a1", CH_A)], CH_B: [video("b1", CH_B)]})
        client = RecordingClient()
        stats = run_once(make_config(), self.store, client, fetch=fetch)

        self.assertEqual(sorted(client.calls), [("a1", PL_A), ("b1", PL_B)])
        self.assertEqual(stats.added, 2)

    def test_한_채널의_영상이_다른_재생목록으로_새지_않는다(self):
        fetch = make_fetch(
            {CH_A: [video("a1", CH_A), video("a2", CH_A)], CH_B: [video("b1", CH_B)]}
        )
        client = RecordingClient()
        run_once(make_config(), self.store, client, fetch=fetch)

        for video_id, playlist_id in client.calls:
            expected = PL_A if video_id.startswith("a") else PL_B
            self.assertEqual(playlist_id, expected, f"{video_id} 가 엉뚱한 재생목록으로 갔다")

    def test_넣은_영상은_기록해_다음_회차에_다시_넣지_않는다(self):
        fetch = make_fetch({CH_A: [video("a1", CH_A)], CH_B: []})
        client = RecordingClient()
        run_once(make_config(), self.store, client, fetch=fetch)
        run_once(make_config(), self.store, client, fetch=fetch)

        self.assertEqual(client.calls, [("a1", PL_A)])

    def test_두_번째_회차에는_새_영상만_넣는다(self):
        config = make_config()
        client = RecordingClient()
        run_once(config, self.store, client,
                 fetch=make_fetch({CH_A: [video("a1", CH_A)], CH_B: []}))
        run_once(config, self.store, client,
                 fetch=make_fetch({CH_A: [video("a1", CH_A), video("a2", CH_A)], CH_B: []}))

        self.assertEqual(client.calls, [("a1", PL_A), ("a2", PL_A)])

    def test_처음_보는_채널은_기록만_하고_넘긴다(self):
        config = make_config(seed_on_first_run=True)
        fetch = make_fetch({CH_A: [video("a1", CH_A), video("a2", CH_A)], CH_B: []})
        client = RecordingClient()
        stats = run_once(config, self.store, client, fetch=fetch)

        self.assertEqual(client.calls, [])
        self.assertEqual(stats.seeded, 2)
        self.assertTrue(self.store.is_added("a1", PL_A))

    def test_기록만_한_뒤_회차부터는_새_영상을_넣는다(self):
        config = make_config(seed_on_first_run=True)
        client = RecordingClient()
        run_once(config, self.store, client,
                 fetch=make_fetch({CH_A: [video("a1", CH_A)], CH_B: []}))
        run_once(config, self.store, client,
                 fetch=make_fetch({CH_A: [video("a2", CH_A), video("a1", CH_A)], CH_B: []}))

        self.assertEqual(client.calls, [("a2", PL_A)])

    def test_회차당_넣는_개수에_상한이_있다(self):
        videos = [video(f"a{i}", CH_A) for i in range(5)]
        fetch = make_fetch({CH_A: videos, CH_B: []})
        client = RecordingClient()
        stats = run_once(make_config(max_videos_per_channel_per_run=2),
                         self.store, client, fetch=fetch)

        self.assertEqual(len(client.calls), 2)
        self.assertEqual(stats.new_videos, 5)
        self.assertEqual(stats.added, 2)

    def test_상한에_걸려_남은_영상은_다음_회차에_넣는다(self):
        videos = [video(f"a{i}", CH_A) for i in range(3)]
        config = make_config(max_videos_per_channel_per_run=2)
        fetch = make_fetch({CH_A: videos, CH_B: []})
        client = RecordingClient()
        run_once(config, self.store, client, fetch=fetch)
        run_once(config, self.store, client, fetch=fetch)

        self.assertEqual([c[0] for c in client.calls], ["a0", "a1", "a2"])

    def test_피드_오류는_그_채널만_건너뛴다(self):
        fetch = make_fetch({CH_A: FeedError("못 읽음"), CH_B: [video("b1", CH_B)]})
        client = RecordingClient()
        stats = run_once(make_config(), self.store, client, fetch=fetch)

        self.assertEqual(client.calls, [("b1", PL_B)])
        self.assertEqual(stats.feed_errors, 1)
        self.assertEqual(stats.added, 1)

    def test_재생목록_오류는_그_채널만_멈춘다(self):
        fetch = make_fetch(
            {CH_A: [video("a1", CH_A), video("a2", CH_A)], CH_B: [video("b1", CH_B)]}
        )
        client = RecordingClient({"a1": PlaylistNotUsable("없는 재생목록")})
        stats = run_once(make_config(), self.store, client, fetch=fetch)

        self.assertEqual(client.calls, [("a1", PL_A), ("b1", PL_B)])
        self.assertEqual(stats.playlist_errors, 1)
        self.assertEqual(stats.added, 1)

    def test_재생목록_오류가_난_영상은_기록하지_않는다(self):
        fetch = make_fetch({CH_A: [video("a1", CH_A)], CH_B: []})
        client = RecordingClient({"a1": PlaylistNotUsable("없는 재생목록")})
        run_once(make_config(), self.store, client, fetch=fetch)

        self.assertFalse(self.store.is_added("a1", PL_A))

    def test_할당량_소진이면_회차를_그_자리에서_끝낸다(self):
        fetch = make_fetch({CH_A: [video("a1", CH_A)], CH_B: [video("b1", CH_B)]})
        client = RecordingClient({"a1": QuotaExceeded("오늘 치 끝")})
        stats = run_once(make_config(), self.store, client, fetch=fetch)

        self.assertEqual(client.calls, [("a1", PL_A)])
        self.assertTrue(stats.quota_exceeded)
        self.assertFalse(self.store.is_added("a1", PL_A))

    def test_못_넣는_영상은_기록해_다시_시도하지_않는다(self):
        config = make_config()
        fetch = make_fetch({CH_A: [video("a1", CH_A)], CH_B: []})
        client = RecordingClient({"a1": VideoNotAddable("비공개")})
        stats = run_once(config, self.store, client, fetch=fetch)
        run_once(config, self.store, client, fetch=fetch)

        self.assertEqual(client.calls, [("a1", PL_A)])
        self.assertEqual(stats.skipped_videos, 1)

    def test_일시적_오류는_기록하지_않아_다음_회차에_다시_건다(self):
        config = make_config()
        fetch = make_fetch({CH_A: [video("a1", CH_A)], CH_B: []})
        client = RecordingClient({"a1": TransientError("잠깐 실패")})
        run_once(config, self.store, client, fetch=fetch)
        client.results = {}
        run_once(config, self.store, client, fetch=fetch)

        self.assertEqual(client.calls, [("a1", PL_A), ("a1", PL_A)])
        self.assertTrue(self.store.is_added("a1", PL_A))

    def test_이미_재생목록에_있는_영상도_기록한다(self):
        fetch = make_fetch({CH_A: [video("a1", CH_A)], CH_B: []})
        client = RecordingClient({"a1": DUPLICATE})
        stats = run_once(make_config(), self.store, client, fetch=fetch)

        self.assertEqual(stats.duplicates, 1)
        self.assertTrue(self.store.is_added("a1", PL_A))

    def test_새_영상이_없으면_아무것도_넣지_않는다(self):
        fetch = make_fetch({CH_A: [], CH_B: []})
        client = RecordingClient()
        stats = run_once(make_config(), self.store, client, fetch=fetch)

        self.assertEqual(client.calls, [])
        self.assertEqual(stats.channels, 2)
        self.assertEqual(stats.added, 0)

    def test_같은_영상을_두_채널이_올려도_각_재생목록에_하나씩_간다(self):
        # 재방송·합작처럼 같은 영상 아이디가 두 피드에 나오는 경우다.
        fetch = make_fetch({CH_A: [video("same1", CH_A)], CH_B: [video("same1", CH_B)]})
        client = RecordingClient()
        run_once(make_config(), self.store, client, fetch=fetch)

        self.assertEqual(sorted(client.calls), [("same1", PL_A), ("same1", PL_B)])

    def test_시험_실행은_넣지_않고_어디로_갈지만_알린다(self):
        fetch = make_fetch({CH_A: [video("a1", CH_A)], CH_B: [video("b1", CH_B)]})
        client = DryRunClient()
        run_once(make_config(), ReadOnlyStore(self.store), client, fetch=fetch)

        self.assertEqual(sorted(client.calls), [("a1", PL_A), ("b1", PL_B)])
        self.assertFalse(self.store.is_added("a1", PL_A))

    def test_시험_실행_뒤에도_진짜_첫_회차가_살아_있다(self):
        # 시험 실행이 기록을 남기면 그 채널이 '이미 본 채널' 이 돼서, 진짜로 돌릴 때
        # 첫 회차가 통째로 건너뛰어지고 그 영상들은 한 번도 안 들어간다.
        config = make_config(seed_on_first_run=True)
        fetch = make_fetch({CH_A: [video("a1", CH_A)], CH_B: []})
        run_once(config, ReadOnlyStore(self.store), DryRunClient(), fetch=fetch)

        self.assertFalse(self.store.seen_channel(CH_A))

        client = RecordingClient()
        run_once(config, self.store, client, fetch=fetch)
        self.assertEqual(client.calls, [])
        run_once(config, self.store, client,
                 fetch=make_fetch({CH_A: [video("a1", CH_A), video("a2", CH_A)], CH_B: []}))
        self.assertEqual(client.calls, [("a2", PL_A)])

    def test_요약_문구에_수치가_담긴다(self):
        fetch = make_fetch({CH_A: [video("a1", CH_A)], CH_B: []})
        stats = run_once(make_config(), self.store, RecordingClient(), fetch=fetch)
        self.assertIn("넣음 1개", stats.summary())


class NextDelayTest(unittest.TestCase):
    def test_평소에는_설정한_주기만큼_쉰다(self):
        config = make_config()
        stats = run_once(config, Store(":memory:"), RecordingClient(),
                         fetch=make_fetch({CH_A: [], CH_B: []}))
        self.assertEqual(next_delay(config, stats), 900)

    def test_할당량이_막힌_회차는_더_오래_쉰다(self):
        config = make_config()
        fetch = make_fetch({CH_A: [video("a1", CH_A)], CH_B: []})
        client = RecordingClient({"a1": QuotaExceeded("오늘 치 끝")})
        stats = run_once(config, Store(":memory:"), client, fetch=fetch)
        self.assertEqual(next_delay(config, stats), 3600)


if __name__ == "__main__":
    unittest.main(verbosity=2)

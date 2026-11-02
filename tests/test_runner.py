"""한 회차의 동작. 가장 중요한 것은 어느 채널의 영상이 어느 재생목록으로 가느냐다."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from youtube_auto.config import ChannelRule, Config  # noqa: E402
from youtube_auto.errors import QuotaExceeded, TransientError  # noqa: E402
from youtube_auto.playlist import (  # noqa: E402
    ADDED,
    DUPLICATE,
    DryRunClient,
    PlaylistNotUsable,
    VideoNotAddable,
)
from youtube_auto.runner import next_delay, run_once  # noqa: E402
from youtube_auto.store import ReadOnlyStore, Store  # noqa: E402
from youtube_auto.uploads import (  # noqa: E402
    LookupTransientError,
    UploadsNotAccessible,
    Video,
)

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
    """채널마다 미리 정해 둔 답(영상 목록 또는 예외)을 주고 호출을 받아 적는다."""

    def fetch(channel_id, timeout=20):
        fetch.calls.append(channel_id)
        result = mapping[channel_id]
        if isinstance(result, Exception):
            raise result
        return result

    fetch.calls = []
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


class FlakyConn:
    """특정 문장에서만 터지는 연결. 영상은 기록됐는데 '본 채널' 표시 직전에 끊긴 상황을 만든다."""

    def __init__(self, conn, fail_on):
        self._conn = conn
        self._fail_on = fail_on

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def execute(self, sql, *args, **kwargs):
        if self._fail_on in sql:
            raise RuntimeError("기록 중 끊김")
        return self._conn.execute(sql, *args, **kwargs)


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

    def test_처음_보는_채널은_과거_영상_오십_개가_와도_하나도_안_넣는다(self):
        # 조회 통로를 갈아끼우면 한 번에 오는 목록이 달라질 수 있다. 처음 보는 채널에
        # 과거 영상이 쏟아져도 재생목록에는 한 개도 안 들어가야 한다.
        old = [video(f"old{i}", CH_A) for i in range(50)]
        config = make_config(seed_on_first_run=True)
        client = RecordingClient()
        stats = run_once(config, self.store, client,
                         fetch=make_fetch({CH_A: old, CH_B: []}))

        self.assertEqual(client.calls, [])
        self.assertEqual(stats.seeded, 50)
        self.assertEqual(stats.added, 0)

        # 다음 회차에 같은 목록이 또 와도 마찬가지다 — 전부 기록돼 있다.
        run_once(config, self.store, client, fetch=make_fetch({CH_A: old, CH_B: []}))
        self.assertEqual(client.calls, [])

    def test_첫_회차_기록이_끊기면_본_채널로_남지_않는다(self):
        # 기록만 끊기고 '본 채널' 표시가 남으면, 다음 회차가 그 과거 영상들을 새 영상으로
        # 보고 재생목록에 쏟는다. 둘은 한 트랜잭션이라 같이 되돌아가야 한다.
        config = make_config(seed_on_first_run=True)
        fetch = make_fetch({CH_A: [video("a1", CH_A), video("a2", CH_A)], CH_B: []})

        real_conn = self.store.conn
        self.store.conn = FlakyConn(real_conn, "seen_channels")
        try:
            with self.assertRaises(RuntimeError):
                run_once(config, self.store, RecordingClient(), fetch=fetch)
        finally:
            self.store.conn = real_conn

        self.assertFalse(self.store.seen_channel(CH_A))
        self.assertFalse(self.store.is_added("a1", PL_A))

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

    def test_일시적_조회_오류는_그_채널만_건너뛴다(self):
        fetch = make_fetch({CH_A: LookupTransientError("못 읽음"), CH_B: [video("b1", CH_B)]})
        client = RecordingClient()
        stats = run_once(make_config(), self.store, client, fetch=fetch)

        self.assertEqual(client.calls, [("b1", PL_B)])
        self.assertEqual(stats.lookup_errors, 1)
        self.assertEqual(stats.lookup_ok, 1)
        self.assertEqual(stats.added, 1)

    def test_업로드_목록_접근_오류도_그_채널만_건너뛴다(self):
        fetch = make_fetch({CH_A: UploadsNotAccessible("없는 채널"), CH_B: [video("b1", CH_B)]})
        client = RecordingClient()
        stats = run_once(make_config(), self.store, client, fetch=fetch)

        self.assertEqual(client.calls, [("b1", PL_B)])
        self.assertEqual(stats.lookup_errors, 1)

    def test_조회_실패는_연속_횟수로_쌓인다(self):
        config = make_config()
        fetch = make_fetch({CH_A: LookupTransientError("못 읽음"), CH_B: []})
        run_once(config, self.store, RecordingClient(), fetch=fetch)
        run_once(config, self.store, RecordingClient(), fetch=fetch)

        last_ok, _, _, fails = self.store.health(CH_A)
        self.assertEqual(fails, 2)
        self.assertIsNone(last_ok)

    def test_다시_읽히면_연속_실패가_0으로_돌아간다(self):
        config = make_config()
        run_once(config, self.store, RecordingClient(),
                 fetch=make_fetch({CH_A: LookupTransientError("못 읽음"), CH_B: []}))
        run_once(config, self.store, RecordingClient(),
                 fetch=make_fetch({CH_A: [video("a1", CH_A)], CH_B: []}))

        last_ok, _, _, fails = self.store.health(CH_A)
        self.assertEqual(fails, 0)
        self.assertIsNotNone(last_ok)

    def test_조회가_모두_실패한_회차를_알아본다(self):
        fetch = make_fetch({CH_A: LookupTransientError("못 읽음"), CH_B: LookupTransientError("못 읽음")})
        stats = run_once(make_config(), self.store, RecordingClient(), fetch=fetch)

        self.assertTrue(stats.all_lookups_failed)
        self.assertEqual(stats.lookup_errors, 2)

    def test_조회_할당량이_소진되면_다음_채널은_보지도_않는다(self):
        fetch = make_fetch({CH_A: QuotaExceeded("오늘 치 끝"), CH_B: [video("b1", CH_B)]})
        client = RecordingClient()
        stats = run_once(make_config(), self.store, client, fetch=fetch)

        self.assertEqual(fetch.calls, [CH_A])
        self.assertEqual(client.calls, [])
        self.assertTrue(stats.quota_exceeded)

    def test_조회_함수를_안_넘기면_거절한다(self):
        # 조회에도 로그인한 계정이 필요하다. 기본값으로 떨어뜨리지 않는다.
        with self.assertRaises(TypeError):
            run_once(make_config(), self.store, RecordingClient())

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
        # 재방송·합작처럼 같은 영상 아이디가 두 채널 목록에 나오는 경우다.
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

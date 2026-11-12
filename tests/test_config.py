"""설정 읽기·검증. 핵심은 채널마다 재생목록이 따로 붙는다는 것이다."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from youtube_auto.config import (  # noqa: E402
    ConfigError,
    daily_lookup_units,
    load_config,
    min_interval_for_budget,
)

CHANNEL_A = "UC_x5XG1OV2P6uZZ5FSM9Ttw"
CHANNEL_B = "UCBJycsmduvYEL83R_U4JriQ"
PLAYLIST_A = "PLOU2XLYxmsIKpaV8h0AGE05so0fAwwfTw"
PLAYLIST_B = "PLKcKU7ThUUvX2KBAJ3NsQZSTEI54V9hHk"

GOOD = f"""
channels:
  - name: 가 채널
    channel_id: {CHANNEL_A}
    playlist_id: {PLAYLIST_A}
  - name: 나 채널
    channel_id: {CHANNEL_B}
    playlist_id: {PLAYLIST_B}
check_interval_seconds: 900
"""


def many_channels(count: int, interval: int) -> str:
    lines = ["channels:"]
    for i in range(count):
        lines.append(f"  - name: 채널{i}")
        lines.append(f"    channel_id: UC{str(i).zfill(22)}")
        lines.append(f"    playlist_id: PL{str(i).zfill(20)}")
    lines.append(f"check_interval_seconds: {interval}")
    return "\n".join(lines) + "\n"


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text):
        path = self.root / "config.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def load(self, text, env=None):
        return load_config(self.write(text), env=env or {})

    def test_주기가_너무_짧으면_조회_예산을_넘는다고_거절한다(self):
        # 채널 10개를 1분마다 보면 조회에만 하루 14,400단위다. 하루 치가 10,000이라
        # 그 설정으로는 영상을 한 개도 못 넣는다. 돌기 전에 거절한다.
        with self.assertRaises(ConfigError) as caught:
            self.load(many_channels(10, 60))
        message = str(caught.exception)
        self.assertIn("조회", message)
        self.assertIn("check_interval_seconds", message)

    def test_예산_안에_드는_주기는_통과한다(self):
        config = self.load(many_channels(10, 3600))
        self.assertEqual(len(config.channels), 10)

    def test_하루에_쓰는_조회량을_계산한다(self):
        # 채널 하나에 호출 둘(목록 + 영상 정보). 20개를 한 시간마다 = 24 × 20 × 2 = 960단위.
        self.assertEqual(daily_lookup_units(20, 3600), 960)

    def test_예산에_맞는_가장_짧은_주기를_알려준다(self):
        needed = min_interval_for_budget(20)
        self.assertLessEqual(daily_lookup_units(20, needed), 5000)
        self.assertGreater(daily_lookup_units(20, needed - 1), 5000)

    def test_최소_길이를_분으로_적으면_초로_바꿔_둔다(self):
        text = (
            f"channels:\n  - name: 가\n    channel_id: {CHANNEL_A}\n"
            f"    playlist_id: {PLAYLIST_A}\n    min_duration_minutes: 40\n"
            f"  - name: 나\n    channel_id: {CHANNEL_B}\n"
            f"    playlist_id: {PLAYLIST_B}\ncheck_interval_seconds: 900\n"
        )
        config = self.load(text)
        self.assertEqual(config.channels[0].min_duration_seconds, 2400)
        self.assertEqual(config.channels[1].min_duration_seconds, 0)

    def test_최소_길이와_상관없이_채널마다_두_단위를_쓴다(self):
        # 쇼츠·라이브를 전 채널에서 거르므로 영상 정보 조회가 늘 붙는다.
        text = (
            f"channels:\n  - name: 가\n    channel_id: {CHANNEL_A}\n"
            f"    playlist_id: {PLAYLIST_A}\n    min_duration_minutes: 40\n"
            f"  - name: 나\n    channel_id: {CHANNEL_B}\n"
            f"    playlist_id: {PLAYLIST_B}\ncheck_interval_seconds: 3600\n"
        )
        config = self.load(text)
        # 채널 2개 × 2단위 × 하루 24회차.
        self.assertEqual(config.daily_lookup_units, 96)

    def test_최소_길이가_0이거나_음수면_거절한다(self):
        for bad in ("0", "-5", "사십"):
            text = (
                f"channels:\n  - name: 가\n    channel_id: {CHANNEL_A}\n"
                f"    playlist_id: {PLAYLIST_A}\n    min_duration_minutes: {bad}\n"
                "check_interval_seconds: 900\n"
            )
            with self.assertRaises(ConfigError, msg=f"{bad} 를 받아들였다") as ctx:
                self.load(text)
            self.assertIn("min_duration_minutes", str(ctx.exception))

    def test_쇼츠를_받는_채널은_표시로_남는다(self):
        text = (
            f"channels:\n  - name: 가\n    channel_id: {CHANNEL_A}\n"
            f"    playlist_id: {PLAYLIST_A}\n    include_shorts: true\n"
            f"  - name: 나\n    channel_id: {CHANNEL_B}\n"
            f"    playlist_id: {PLAYLIST_B}\ncheck_interval_seconds: 900\n"
        )
        config = self.load(text)
        self.assertTrue(config.channels[0].include_shorts)
        self.assertFalse(config.channels[1].include_shorts)

    def test_쇼츠_표시가_참거짓이_아니면_거절한다(self):
        text = (
            f"channels:\n  - name: 가\n    channel_id: {CHANNEL_A}\n"
            f"    playlist_id: {PLAYLIST_A}\n    include_shorts: 네\n"
            "check_interval_seconds: 900\n"
        )
        with self.assertRaises(ConfigError) as ctx:
            self.load(text)
        self.assertIn("include_shorts", str(ctx.exception))

    def test_라이브_표시가_참거짓이_아니면_거절한다(self):
        text = (
            f"channels:\n  - name: 가\n    channel_id: {CHANNEL_A}\n"
            f"    playlist_id: {PLAYLIST_A}\n    include_live: 네\n"
            "check_interval_seconds: 900\n"
        )
        with self.assertRaises(ConfigError) as ctx:
            self.load(text)
        self.assertIn("include_live", str(ctx.exception))

    def test_쇼츠_받기와_최소_길이를_같이_쓰면_거절한다(self):
        # 최소 길이가 쇼츠를 전부 걸러서 쇼츠 받기가 아무 일도 안 한다.
        text = (
            f"channels:\n  - name: 가\n    channel_id: {CHANNEL_A}\n"
            f"    playlist_id: {PLAYLIST_A}\n    include_shorts: true\n"
            "    min_duration_minutes: 35\ncheck_interval_seconds: 900\n"
        )
        with self.assertRaises(ConfigError) as ctx:
            self.load(text)
        self.assertIn("include_shorts", str(ctx.exception))

    def test_제목_제외어는_소문자로_모아_둔다(self):
        text = (
            f"channels:\n  - name: 가\n    channel_id: {CHANNEL_A}\n"
            f"    playlist_id: {PLAYLIST_A}\n"
            '    title_exclude: ["PD수첩.ZIP", "  예고  "]\n'
            f"  - name: 나\n    channel_id: {CHANNEL_B}\n"
            f"    playlist_id: {PLAYLIST_B}\ncheck_interval_seconds: 900\n"
        )
        config = self.load(text)
        self.assertEqual(config.channels[0].title_exclude, ("pd수첩.zip", "예고"))
        self.assertEqual(config.channels[1].title_exclude, ())

    def test_제목_제외어를_목록이_아니라_한_줄로_적으면_거절한다(self):
        # 문자열을 그대로 받으면 글자 하나하나가 제외어가 되어 전부 막힌다.
        text = (
            f"channels:\n  - name: 가\n    channel_id: {CHANNEL_A}\n"
            f"    playlist_id: {PLAYLIST_A}\n    title_exclude: PD수첩.zip\n"
            "check_interval_seconds: 900\n"
        )
        with self.assertRaises(ConfigError) as ctx:
            self.load(text)
        self.assertIn("title_exclude", str(ctx.exception))

    def test_빈_제목_제외어는_거절한다(self):
        # 빈 말은 모든 제목에 들어 있다고 판정돼 그 채널을 통째로 막는다.
        for bad in ('[""]', '["   "]', "[35]"):
            text = (
                f"channels:\n  - name: 가\n    channel_id: {CHANNEL_A}\n"
                f"    playlist_id: {PLAYLIST_A}\n    title_exclude: {bad}\n"
                "check_interval_seconds: 900\n"
            )
            with self.assertRaises(ConfigError, msg=f"{bad} 를 받아들였다") as ctx:
                self.load(text)
            self.assertIn("title_exclude", str(ctx.exception))

    def test_채널마다_재생목록이_따로_붙는다(self):
        config = self.load(GOOD)
        self.assertEqual(len(config.channels), 2)
        self.assertEqual(config.playlist_for(CHANNEL_A), PLAYLIST_A)
        self.assertEqual(config.playlist_for(CHANNEL_B), PLAYLIST_B)
        self.assertNotEqual(config.playlist_for(CHANNEL_A), config.playlist_for(CHANNEL_B))

    def test_이름과_순서가_보존된다(self):
        config = self.load(GOOD)
        self.assertEqual([c.name for c in config.channels], ["가 채널", "나 채널"])

    def test_모르는_채널은_재생목록이_없다(self):
        config = self.load(GOOD)
        self.assertIsNone(config.playlist_for("UCzzzzzzzzzzzzzzzzzzzzzz"))

    def test_재생목록을_안_적으면_거절한다(self):
        text = f"channels:\n  - name: 가\n    channel_id: {CHANNEL_A}\n"
        with self.assertRaises(ConfigError) as ctx:
            self.load(text)
        self.assertIn("playlist_id", str(ctx.exception))

    def test_채널_아이디_모양이_틀리면_거절한다(self):
        text = f"channels:\n  - channel_id: '@handle'\n    playlist_id: {PLAYLIST_A}\n"
        with self.assertRaises(ConfigError) as ctx:
            self.load(text)
        self.assertIn("UC", str(ctx.exception))

    def test_나중에_볼_동영상은_거절한다(self):
        text = f"channels:\n  - channel_id: {CHANNEL_A}\n    playlist_id: WL\n"
        with self.assertRaises(ConfigError) as ctx:
            self.load(text)
        self.assertIn("나중에 볼", str(ctx.exception))

    def test_같은_채널을_두_번_적으면_거절한다(self):
        text = (f"channels:\n  - channel_id: {CHANNEL_A}\n    playlist_id: {PLAYLIST_A}\n"
                f"  - channel_id: {CHANNEL_A}\n    playlist_id: {PLAYLIST_B}\n")
        with self.assertRaises(ConfigError) as ctx:
            self.load(text)
        self.assertIn("한 채널은", str(ctx.exception))

    def test_채널이_비면_거절한다(self):
        with self.assertRaises(ConfigError):
            self.load("channels: []\n")

    def test_모르는_항목은_거절한다(self):
        with self.assertRaises(ConfigError) as ctx:
            self.load(GOOD + "chek_interval_seconds: 100\n")
        self.assertIn("chek_interval_seconds", str(ctx.exception))

    def test_주기가_너무_짧으면_거절한다(self):
        with self.assertRaises(ConfigError) as ctx:
            self.load(GOOD.replace("check_interval_seconds: 900", "check_interval_seconds: 5"))
        self.assertIn("60", str(ctx.exception))

    def test_주기가_음수면_거절한다(self):
        with self.assertRaises(ConfigError):
            self.load(GOOD.replace("check_interval_seconds: 900", "check_interval_seconds: -1"))

    def test_설정에_없는_값은_기본값으로_채운다(self):
        config = self.load(GOOD)
        self.assertEqual(config.check_interval_seconds, 900)
        self.assertEqual(config.max_videos_per_channel_per_run, 10)
        self.assertTrue(config.seed_on_first_run)
        self.assertEqual(config.quota_backoff_seconds, 3600)

    def test_첫_회차_기록만_하기를_끌_수_있다(self):
        config = self.load(GOOD + "seed_on_first_run: false\n")
        self.assertFalse(config.seed_on_first_run)

    def test_참거짓이_아닌_값은_거절한다(self):
        with self.assertRaises(ConfigError):
            self.load(GOOD + "seed_on_first_run: 네\n")

    def test_상대경로는_설정파일_옆으로_푼다(self):
        config = self.load(GOOD, env={"YTAP_DB_FILE": "state/db.sqlite3"})
        self.assertEqual(config.db_file, self.root / "state" / "db.sqlite3")

    def test_절대경로는_그대로_쓴다(self):
        config = self.load(GOOD, env={"YTAP_TOKEN_FILE": "/tmp/somewhere/token.json"})
        self.assertEqual(str(config.token_file), "/tmp/somewhere/token.json")

    def test_설정_파일이_없으면_거절한다(self):
        with self.assertRaises(ConfigError) as ctx:
            load_config(self.root / "없는파일.yaml", env={})
        self.assertIn("config.yaml.example", str(ctx.exception))

    def test_빈_설정_파일은_거절한다(self):
        with self.assertRaises(ConfigError):
            self.load("")


if __name__ == "__main__":
    unittest.main(verbosity=2)

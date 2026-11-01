"""설정 읽기·검증. 핵심은 채널마다 재생목록이 따로 붙는다는 것이다."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from youtube_auto.config import ConfigError, load_config  # noqa: E402

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

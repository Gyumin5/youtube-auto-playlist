"""중복 방지 기록. 키가 (영상, 재생목록) 짝이라는 것이 핵심이다."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from youtube_auto.store import ReadOnlyStore, Store  # noqa: E402


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_기록하기_전에는_안_넣은_것이다(self):
        self.assertFalse(self.store.is_added("vid1", "PL_A"))

    def test_기록하면_넣은_것으로_본다(self):
        self.store.mark_added("vid1", "PL_A", "UC_A", title="제목")
        self.assertTrue(self.store.is_added("vid1", "PL_A"))

    def test_같은_영상이라도_재생목록이_다르면_따로_센다(self):
        self.store.mark_added("vid1", "PL_A", "UC_A")
        self.assertTrue(self.store.is_added("vid1", "PL_A"))
        self.assertFalse(self.store.is_added("vid1", "PL_B"))

    def test_두_번_기록해도_처음_시각을_지킨다(self):
        self.store.mark_added("vid1", "PL_A", "UC_A", title="처음")
        first = self.store.recent(1)[0]
        self.store.mark_added("vid1", "PL_A", "UC_A", title="나중")
        again = self.store.recent(1)[0]
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(first, again)

    def test_처음_보는_채널을_가려낸다(self):
        self.assertFalse(self.store.seen_channel("UC_A"))
        self.store.mark_channel_seen("UC_A")
        self.assertTrue(self.store.seen_channel("UC_A"))
        self.assertFalse(self.store.seen_channel("UC_B"))

    def test_출처를_구분해_적는다(self):
        self.store.mark_added("vid1", "PL_A", "UC_A", source="seed")
        self.store.mark_added("vid2", "PL_A", "UC_A", source="api")
        sources = {row[0]: row[5] for row in self.store.recent(10)}
        self.assertEqual(sources["vid1"], "seed")
        self.assertEqual(sources["vid2"], "api")

    def test_읽기전용_겹은_기록을_버린다(self):
        self.store.mark_added("vid1", "PL_A", "UC_A")
        self.store.mark_channel_seen("UC_A")
        readonly = ReadOnlyStore(self.store)

        readonly.mark_added("vid2", "PL_A", "UC_A")
        readonly.mark_channel_seen("UC_B")

        self.assertFalse(readonly.is_added("vid2", "PL_A"))
        self.assertFalse(self.store.is_added("vid2", "PL_A"))
        self.assertFalse(self.store.seen_channel("UC_B"))

    def test_읽기전용_겹도_읽기는_진짜_기록을_본다(self):
        self.store.mark_added("vid1", "PL_A", "UC_A")
        self.store.mark_channel_seen("UC_A")
        readonly = ReadOnlyStore(self.store)

        self.assertTrue(readonly.is_added("vid1", "PL_A"))
        self.assertTrue(readonly.seen_channel("UC_A"))
        self.assertEqual(readonly.count(), 1)

    def test_파일로_만들면_다시_열어도_남아_있다(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state" / "db.sqlite3"
            store = Store(path)
            store.mark_added("vid1", "PL_A", "UC_A")
            store.close()

            self.assertTrue(path.exists())
            again = Store(path)
            self.assertTrue(again.is_added("vid1", "PL_A"))
            again.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

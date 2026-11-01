"""피드 해석. 실제 채널 피드와 같은 모양의 본문을 쓰고 연결은 하지 않는다."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from youtube_auto.feeds import FEED_URL, FeedError, Video, parse_feed  # noqa: E402

CHANNEL = "UC_x5XG1OV2P6uZZ5FSM9Ttw"

FEED = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <id>yt:channel:{CHANNEL}</id>
  <yt:channelId>{CHANNEL}</yt:channelId>
  <title>본보기 채널</title>
  <entry>
    <id>yt:video:newest00000</id>
    <yt:videoId>newest00000</yt:videoId>
    <yt:channelId>{CHANNEL}</yt:channelId>
    <title>가장 새 영상</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=newest00000"/>
    <published>2026-11-01T09:00:00+00:00</published>
  </entry>
  <entry>
    <id>yt:video:middle00000</id>
    <yt:videoId>middle00000</yt:videoId>
    <yt:channelId>{CHANNEL}</yt:channelId>
    <title>가운데 영상</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=middle00000"/>
    <published>2026-10-30T09:00:00+00:00</published>
  </entry>
  <entry>
    <id>yt:video:oldest00000</id>
    <yt:channelId>{CHANNEL}</yt:channelId>
    <title>가장 오래된 영상</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=oldest00000&amp;t=3"/>
    <published>2026-10-28T09:00:00+00:00</published>
  </entry>
</feed>
"""


class FeedTest(unittest.TestCase):
    def test_영상을_모두_읽는다(self):
        videos = parse_feed(FEED.encode("utf-8"), CHANNEL)
        self.assertEqual(len(videos), 3)
        self.assertTrue(all(isinstance(v, Video) for v in videos))

    def test_오래된_것부터_돌려준다(self):
        videos = parse_feed(FEED.encode("utf-8"), CHANNEL)
        self.assertEqual(
            [v.video_id for v in videos], ["oldest00000", "middle00000", "newest00000"]
        )

    def test_제목과_올린_시각을_읽는다(self):
        videos = parse_feed(FEED.encode("utf-8"), CHANNEL)
        self.assertEqual(videos[-1].title, "가장 새 영상")
        self.assertEqual(videos[-1].published, "2026-11-01T09:00:00+00:00")

    def test_영상_아이디가_없으면_주소에서_꺼낸다(self):
        videos = parse_feed(FEED.encode("utf-8"), CHANNEL)
        # 세 번째 항목에는 yt:videoId 가 없고 주소에 다른 인자까지 붙어 있다.
        self.assertEqual(videos[0].video_id, "oldest00000")

    def test_채널_아이디를_항목에서_읽는다(self):
        videos = parse_feed(FEED.encode("utf-8"), CHANNEL)
        self.assertTrue(all(v.channel_id == CHANNEL for v in videos))

    def test_볼_주소를_만든다(self):
        videos = parse_feed(FEED.encode("utf-8"), CHANNEL)
        self.assertEqual(videos[0].url, "https://www.youtube.com/watch?v=oldest00000")

    def test_영상_아이디를_못_구하는_항목은_건너뛴다(self):
        broken = FEED.replace(
            '<link rel="alternate" href="https://www.youtube.com/watch?v=oldest00000&amp;t=3"/>',
            '<link rel="alternate" href="https://example.com/nothing"/>',
        )
        videos = parse_feed(broken.encode("utf-8"), CHANNEL)
        self.assertEqual([v.video_id for v in videos], ["middle00000", "newest00000"])

    def test_항목이_없는_피드는_빈_목록이다(self):
        empty = f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">' \
                f'<id>yt:channel:{CHANNEL}</id></feed>'
        self.assertEqual(parse_feed(empty.encode("utf-8"), CHANNEL), [])

    def test_해석할_수_없는_본문은_오류다(self):
        with self.assertRaises(FeedError):
            parse_feed(b"\x00\x01 not a feed at all <<<", CHANNEL)

    def test_피드_주소에_채널_아이디가_들어간다(self):
        url = FEED_URL.format(channel_id=CHANNEL)
        self.assertEqual(
            url, f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL}"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

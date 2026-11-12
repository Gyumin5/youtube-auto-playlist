# youtube-auto-playlist

구독 중인 채널에 새 영상이 올라오면, 그 채널에 정해 둔 재생목록으로 자동으로 넣는다.
채널마다 재생목록이 따로다 — 가 채널의 영상은 가 재생목록으로, 나 채널의 영상은 나
재생목록으로 간다.

새 영상을 찾는 것도 넣는 것도 공식 API 를 쓴다. 채널마다 "업로드" 라는 재생목록이 하나
자동으로 있고(채널 아이디 `UC...` 의 앞 두 글자를 `UU` 로 바꾼 값), 거기서 최근 열다섯
개를 읽는다. 채널 하나에 조회가 둘(목록 받기 + 그 영상들의 길이·라이브 여부 묻기)이라
채널이 스물이면 하루 960단위다.

쇼츠와 라이브는 기본으로 넣지 않는다. 채널마다 include_shorts·include_live 로 풀 수 있다.

## 지금 상태

사용자 서비스로 이미 상주하고 있다.

```
systemctl --user status youtube-auto-playlist
journalctl --user -u youtube-auto-playlist -f
```

설정이나 로그인이 아직 없으면 죽지 않고 5분마다 다시 본다. 아래 셋을 채우면 그 자리에서
알아서 돌기 시작한다 — 서비스를 다시 켤 필요가 없다.

---

## 1. 구글 클라우드 콘솔에서 자격 받기

영상을 넣으려면 내 유튜브 계정을 대신할 권한이 필요하고, 그 권한을 받으려면 앱 자격이
있어야 한다. 한 번만 하면 된다.

1. [console.cloud.google.com](https://console.cloud.google.com) 에서 프로젝트를 하나 만든다.
2. API 및 서비스 → 라이브러리 → "YouTube Data API v3" 를 찾아 사용 설정한다.
3. API 및 서비스 → OAuth 동의 화면
   - 사용자 유형: 외부
   - 앱 이름·지원 이메일·개발자 이메일만 채우면 된다.
   - 대상(테스트 사용자)에 내 구글 계정을 넣는다.
   - 다 채운 뒤 "앱 게시" 를 눌러 프로덕션으로 올린다. 테스트 모드로 두면 갱신 토큰이
     7일 만에 만료돼서 매주 다시 로그인해야 한다. 심사를 받지 않아도 본인 계정은 쓸 수 있고,
     로그인할 때 "확인되지 않은 앱" 경고가 한 번 뜨는데 고급 → 계속으로 넘어간다.
4. API 및 서비스 → 사용자 인증 정보 → 사용자 인증 정보 만들기 → OAuth 클라이언트 ID
   - 애플리케이션 유형: **데스크톱 앱**
5. 만들어진 자격의 JSON 을 내려받아 이 폴더에 `client_secret.json` 으로 둔다.

## 2. 볼 채널과 넣을 재생목록 적기

```
cp config.yaml.example config.yaml
```

`config.yaml` 에 채널과 재생목록을 짝지어 적는다.

```yaml
channels:
  - name: 보기 좋으라고 붙이는 이름
    channel_id: UC_x5XG1OV2P6uZZ5FSM9Ttw
    playlist_id: PLOU2XLYxmsIKpaV8h0AGE05so0fAwwfTw
```

- **채널 아이디** — 채널 주소가 `youtube.com/channel/UC...` 면 그 `UC` 로 시작하는 값이다.
  주소가 `@핸들` 이면 그 페이지 소스에서 `channelId` 를 찾는다. `@핸들` 자체는 채널
  아이디가 아니라서 그대로 적으면 시작할 때 거절한다.
- **재생목록 아이디** — 재생목록을 열었을 때 주소의 `list=` 뒤에 붙는 값이다.
  '나중에 볼 동영상'(WL)과 '좋아요 표시한 동영상'(LL)에는 공개 API 로 넣을 수 없어서 거절한다.

채널에 `playlist_id` 를 안 적으면 시작하지 않는다. 기본 재생목록으로 떨어뜨리면 엉뚱한
곳에 쌓이고, 그걸 나중에 손으로 걷어내야 한다.

### 넣지 않는 것

라이브는 기본으로 전 채널에서 거르고, `include_live: true` 를 적은 채널만 끝나서 다시보기가 된 뒤에 넣는다(예약·진행 중은 다음 회차로 미룬다). 쇼츠도 기본은 거르고, 채널에
`include_shorts: true` 를 적은 경우에만 그 채널의 쇼츠를 넣는다.

- **쇼츠** — 길이가 3분 이하인 영상만 `youtube.com/shorts/<아이디>` 로 머리를 물어본다.
  그 주소가 그대로 열리면 쇼츠고, 일반 보기로 넘기면 아니다. 할당량을 안 쓴다.
  길이로 가르지 않는 이유는 1~2분짜리 가로 영상이 실제로 있기 때문이다 — 길이만 보면
  그것까지 쇼츠로 몰려 통째로 빠진다.
- **라이브** — `liveStreamingDetails` 칸을 들고 있으면 라이브다. 예약·진행 중·끝난
  다시보기가 다 그 칸을 가지므로 셋이 한 번에 걸린다. 거르는 채널은 기록해 두기 때문에 예약이 끝나
  다시보기가 된 뒤에도 들어가지 않는다. `include_live` 채널은 `actualEndTime` 이 붙은 뒤에 넣는다.

### 쇼츠까지 받는 채널

올리는 것이 거의 다 쇼츠인 채널은 전용 재생목록을 하나 만들어 통째로 받는다.

```yaml
  - name: 1분미만
    channel_id: UC2xkO7XCiStWfR3fKbzkbqw
    playlist_id: PLKbCD3nJQk5Y
    include_shorts: true
```

그 채널은 쇼츠인지 묻지 않는다(묻는 만큼만 느려지고 결과가 안 바뀐다). 라이브는 그래도
거른다. `min_duration_minutes` 와 같이 적으면 시작할 때 거절한다 — 최소 길이가 쇼츠를
전부 걸러서 `include_shorts` 가 아무 일도 안 한다.

### 짧은 영상 거르기

예고·하이라이트가 본편과 같이 올라오는 채널은 `min_duration_minutes` 로 본편만 고른다.

```yaml
  - name: KBS 추적60분
    channel_id: UCy-6oZwPwrR_W-zYnqLD8dg
    playlist_id: PLHPYrlCEsHhA
    min_duration_minutes: 35
```

길이는 쇼츠·라이브를 가릴 때 이미 묻기 때문에 이 칸을 쓴다고 조회가 더 들지는 않는다.
거른 영상은 기록해 두므로 다음 회차에 다시 묻지 않는다. 정보를 못 받은 영상만 아무것도
안 하고 넘겨서 다음 회차에 다시 본다 — 모르는 채로 넣으면 거르는 뜻이 없어진다.

### 제목으로 거르기

같은 채널이 본편과 다른 꼭지를 같이 올려서 길이로는 안 갈릴 때 `title_exclude` 로 가른다.
적은 말이 제목 어디든 들어 있으면 그 영상은 안 넣는다. 대소문자는 안 가린다.

```yaml
  - name: MBC PD Note
    channel_id: UCawCv9kO2UXbTtsK4EammRQ
    playlist_id: PLHPYrlCEsHhA
    min_duration_minutes: 35
    title_exclude: ["PD수첩.zip"]
```

제목은 목록을 받을 때 이미 들고 있어서 조회가 더 들지 않는다. 거른 영상은 기록해 두므로
다음 회차에 다시 보지 않는다. 한 개라도 목록(`[...]`)으로 적고, 빈 문자열은 거절한다 —
빈 문자열은 모든 제목에 들어 있다고 판정돼 그 채널이 통째로 막힌다.

기본 확인 주기는 한 시간이다. `check_interval_seconds` 로 바꾼다(최소 60초). 주기를
짧게 잡으면 조회만으로 하루 치 할당량을 다 쓰게 되므로, 조회가 하루 5,000단위를 넘기는
설정은 시작할 때 거절하면서 쓸 수 있는 가장 짧은 주기를 알려 준다.

## 3. 한 번 로그인

```
.venv/bin/python main.py --auth-only
```

브라우저가 열리고, 허용하면 `token.json` 이 생긴다. 그다음부터는 이 파일을 재사용하고
만료된 접근 토큰은 알아서 갱신한다. 상주로 도는 쪽은 브라우저를 열지 않으므로 이 한 번은
화면이 있는 자리에서 해야 한다.

---

## 손으로 돌려 보기

```
.venv/bin/python main.py --list      # 채널과 재생목록 짝만 확인한다 (연결 없음)
.venv/bin/python main.py --dry-run   # 무엇을 넣을지만 보여준다 (조회에 로그인이 필요하다)
.venv/bin/python main.py --once      # 한 회차만 돌고 끝낸다
```

시험 실행도 조회는 진짜로 하므로 로그인이 있어야 한다. 대신 넣지도 기록하지도 않는다 —
몇 번을 돌려도 그 뒤의 진짜 첫 회차가 그대로 살아 있다.

### 첫 회차는 넣지 않는다

한 번에 받는 목록에는 그 채널의 최근 영상 열다섯 개가 들어 있다. 처음 보는 채널의 그것을
그대로 넣으면 재생목록이 과거 영상으로 덮인다. 그래서 처음 보는 채널의 첫 회차는 기록만
하고 넘기고, 그다음 회차부터 새로 올라오는 영상을 넣는다. 기록과 "본 채널" 표시는 한
트랜잭션이라 도중에 끊겨도 반만 남지 않는다. 과거 영상도 한 번에 넣고 싶으면
`config.yaml` 에서 `seed_on_first_run: false` 로 바꾼다.

새 영상인지는 공개 시각이 아니라 "기록에 있나" 로 가른다. 그래서 한 번에 받는 개수를
열다섯보다 늘리면 과거 영상이 새 영상으로 보여 쏟아진다 — 그 판정을 먼저 고치기 전에는
늘리지 않는다.

## 할당량

| 하는 일 | 드는 할당량 |
|---|---|
| 채널 하나의 새 영상 보기 | 1 |
| 그 영상들의 길이·라이브 여부 묻기 | 1 |
| 쇼츠인지 물어보기 | 0 (공식 API 가 아니다) |
| 영상 하나 넣기 | 50 |

하루 기본 한도가 10,000 이다. 채널 20개를 한 시간마다 보면 조회가 960단위고, 나머지로
하루 180개쯤 넣을 수 있다. 한도를 다 쓰면 조회에서 걸리든 넣기에서 걸리든 그 회차를
그 자리에서 멈추고 `quota_backoff_seconds` 만큼 쉰 뒤 다시 본다. 한도는 태평양 시간
자정에 초기화된다.

## 남는 파일

| 파일 | 하는 일 |
|---|---|
| `token.json` | 로그인 결과. 지우면 다시 로그인해야 한다. |
| `state/added_videos.sqlite3` | 이미 넣은 (영상, 재생목록) 짝. 지우면 중복 방지 기록이 사라진다. |
| `youtube_auto_add.log` | 실행 기록. 5MB 씩 세 벌까지 두고 돌려 쓴다. |

`.env`·`client_secret.json`·`token.json`·`config.yaml`·`state/` 는 `.gitignore` 에 들어 있다.

## 테스트

```
.venv/bin/python -m unittest discover -s tests -p "test_*.py"
```

138개가 돈다. 연결도 로그인도 하지 않는다 — 조회도 넣기도 가짜 객체로 잰다.

## 막히는 자리

| 증상 | 까닭과 할 일 |
|---|---|
| 로그에 "설정을 기다린다" 가 한 줄 있고 조용하다 | 아직 `config.yaml` 이 없다. 2번을 채우면 5분 안에 집어든다. |
| 로그에 "로그인을 기다린다" | 자격 파일이나 `token.json` 이 없다. 1번·3번을 한다. |
| 시작할 때 "채널 아이디 모양이 아니다" | `@핸들` 을 적었다. `UC` 로 시작하는 값을 찾아 넣는다. |
| `playlistNotFound` / `playlistItemsNotAccessible` | 재생목록 아이디가 틀렸거나, 로그인한 계정의 재생목록이 아니다. |
| 매주 다시 로그인하라고 한다 | 동의 화면이 테스트 모드다. 1번의 "앱 게시" 를 한다. |
| 영상이 안 들어오는데 오류도 없다 | 처음 보는 채널의 첫 회차다. 다음 회차부터 들어간다. |
| `quotaExceeded` | 하루 치를 다 썼다. 태평양 시간 자정까지 기다린다. |
| "업로드 목록 UU... 를 읽을 수 없다" | 채널 아이디가 틀렸다. `UC` 로 시작하는 값을 다시 본다. |
| "채널 N개를 하나도 못 읽었다" | 로그인이 끊겼거나 유튜브 쪽이 막힌 것이다. `token.json` 을 먼저 본다. 다시 읽히면 그 사실도 기록에 남는다. |
| 어떤 채널의 영상이 하나도 안 들어온다 | 그 채널이 쇼츠만 올리거나, `min_duration_minutes` 가 너무 크거나, `title_exclude` 가 너무 넓다. 로그의 "N개 중 M개를 넣는다 — 거른 것: …" 줄을 본다. |

## 설치 (다른 자리에 새로 올릴 때)

```
python3 -m venv .venv            # python3-venv 가 없으면: uv venv --python 3.12 .venv
.venv/bin/python -m pip install -r requirements.txt
cp systemd/youtube-auto-playlist.service.example ~/.config/systemd/user/youtube-auto-playlist.service
systemctl --user daemon-reload
systemctl --user enable --now youtube-auto-playlist.service
```

판본까지 똑같이 맞추고 싶으면 `requirements.lock.txt` 를 쓴다.

# youtube-auto-playlist

구독 중인 채널에 새 영상이 올라오면, 그 채널에 정해 둔 재생목록으로 자동으로 넣는다.
채널마다 재생목록이 따로다 — 가 채널의 영상은 가 재생목록으로, 나 채널의 영상은 나
재생목록으로 간다.

새 영상을 찾는 데는 채널 RSS 피드를 쓰고, 넣을 때만 공식 API(playlistItems.insert)를
부른다. 피드는 할당량을 쓰지 않아서, 하루 치 할당량이 오로지 "넣는" 쪽에만 쓰인다.

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

## 2. 설치

```
cd ~/youtube-auto-playlist
python3 -m venv .venv            # python3-venv 가 없으면: uv venv --python 3.12 .venv
.venv/bin/python -m pip install -r requirements.txt
```

판본까지 똑같이 맞추고 싶으면 `requirements.lock.txt` 를 쓴다.

## 3. 설정

```
cp config.yaml.example config.yaml
cp .env.example .env
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

`.env` 에는 파일 경로만 들어간다. 그대로 둬도 된다.

## 4. 로그인

```
.venv/bin/python main.py --auth-only
```

브라우저가 열리고, 허용하면 `token.json` 이 생긴다. 그다음부터는 이 파일을 재사용하고
만료된 접근 토큰은 알아서 갱신한다.

## 5. 실행

```
.venv/bin/python main.py --list      # 채널과 재생목록 짝만 확인한다 (연결 없음)
.venv/bin/python main.py --dry-run   # 피드를 읽고 무엇을 넣을지만 보여준다 (로그인 없이 된다)
.venv/bin/python main.py --once      # 한 회차만 돌고 끝낸다
.venv/bin/python main.py             # 주기마다 계속 돈다
```

시험 실행은 기록을 남기지 않는다. 몇 번을 돌려도 그 뒤의 진짜 첫 회차가 그대로 살아 있다.

상주로 돌리려면 `systemd/youtube-auto-playlist.service.example` 을 보고 사용자 서비스로 올린다.

### 첫 회차는 넣지 않는다

피드에는 그 채널의 최근 영상 열다섯 개가 들어 있다. 처음 보는 채널의 그것을 그대로 넣으면
재생목록이 과거 영상으로 덮인다. 그래서 처음 보는 채널의 첫 회차는 기록만 하고 넘기고,
그다음 회차부터 새로 올라오는 영상을 넣는다. 과거 영상도 한 번에 넣고 싶으면
`config.yaml` 에서 `seed_on_first_run: false` 로 바꾼다.

## 할당량

| 하는 일 | 드는 할당량 |
|---|---|
| 피드로 새 영상 찾기 | 0 (API 를 안 쓴다) |
| 영상 하나 넣기 | 50 |

하루 기본 한도가 10,000 이라 하루에 200개까지 넣을 수 있다. 한도를 다 쓰면 그 회차를
멈추고 `quota_backoff_seconds` 만큼 쉰 뒤 다시 본다. 한도는 태평양 시간 자정에 초기화된다.

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

78개가 돈다. 연결도 로그인도 하지 않는다 — 피드는 고정 본문으로, API 는 가짜 객체로 재는다.

## 막히는 자리

| 증상 | 까닭과 할 일 |
|---|---|
| 시작할 때 "채널 아이디 모양이 아니다" | `@핸들` 을 적었다. `UC` 로 시작하는 값을 찾아 넣는다. |
| `playlistNotFound` / `playlistItemsNotAccessible` | 재생목록 아이디가 틀렸거나, 로그인한 계정의 재생목록이 아니다. |
| 매주 다시 로그인하라고 한다 | 동의 화면이 테스트 모드다. 1번의 "앱 게시" 를 한다. |
| 영상이 안 들어오는데 오류도 없다 | 처음 보는 채널의 첫 회차다. 다음 회차부터 들어간다. |
| `quotaExceeded` | 하루 200개를 넘겼다. 태평양 시간 자정까지 기다린다. |
| 피드 404 | 그런 채널이 없다. 채널 아이디를 다시 본다. |

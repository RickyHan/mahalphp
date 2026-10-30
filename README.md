# mahalphp — 카카오톡 알림 수집기

Android 휴대폰에 오는 **카카오톡 알림**을 읽어 **누가 · 어느 방에서 · 무슨 말을** 했는지, 그리고 가능한 경우 **사진**까지 MySQL에 저장합니다.

```
[Android 앱: 카톡 수집기]                 [수집 서버: Python FastAPI]           [MySQL]
 NotificationListenerService   ── HTTP ──▶  POST /api/messages      ──────▶  kakao_collector
  · com.kakao.talk 알림만 수신   X-Api-Key   · 중복 제거(dedup_key)            ├ kakao_messages
  · 로컬 SQLite 큐(오프라인 보관)             · 사진 → storage/media           └ kakao_media
  · WorkManager 자동 재전송
```

앱이 DB에 직접 접속하지 않고 서버를 거치는 이유는 **DB 계정을 APK 안에 넣지 않기 위해서**입니다. APK는 누구나 풀어 볼 수 있으므로, 앱에는 해지 가능한 API 키만 넣고 DB 계정은 서버에만 둡니다.

| 경로 | 내용 |
|---|---|
| `android/` | Android 앱 (Kotlin, minSdk 26 / targetSdk 34) |
| `server/` | 수집 API (Python 3.10+, FastAPI, PyMySQL) |
| `server/sql/schema.sql` | 스키마·테이블 DDL |
| `.github/workflows/ci.yml` | 서버 통합 테스트 + APK 빌드 (APK는 Actions 아티팩트로 내려받음) |

---

## 1. DB 스키마 생성

```bash
cd server
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # DB_HOST / DB_PORT / DB_USER / DB_PASS / API_KEY 입력
python -m scripts.install_db
# 접속: ...@61.83.189.246:33306 / 스키마: kakao_collector
# 테이블: kakao_media, kakao_messages
```

또는 MySQL 클라이언트로 직접 실행합니다: `mysql -h <host> -P <port> -u <user> -p < server/sql/schema.sql`

### 테이블

**`kakao_messages`**: 메시지 1개당 1행

| 컬럼 | 설명 |
|---|---|
| `room_name` | 채팅방 이름 (1:1 대화는 상대방 이름) |
| `sender_name` | 보낸 사람 (내가 보낸 답장은 `나`) |
| `message_text` | 내용 |
| `message_type` | `text` / `photo` / `video` / `emoticon` / `file` / `other` |
| `is_group_chat` | 단톡방 여부 |
| `has_media` | 사진 저장 여부 |
| `posted_at` | 수신 시각 (KST, ms 단위) |
| `received_at` | 서버 저장 시각 |
| `device_id` | 수집한 휴대폰 (앱 설치 시 생성되는 UUID) |
| `dedup_key` | 중복 방지 키 (UNIQUE) |

**`kakao_media`**: 사진 파일 정보. 파일 본체는 `server/storage/media/YYYY/MM/<sha256>.<ext>`에 저장합니다(`MEDIA_ROOT`로 변경 가능).

```sql
-- 방별 최근 대화
SELECT posted_at, sender_name, message_text FROM kakao_messages
WHERE room_name = '개발팀' ORDER BY posted_at DESC LIMIT 100;
```

## 2. 수집 서버 실행

휴대폰에서 접속할 수 있는 PC나 서버에서 실행합니다.

```bash
cd server && . .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
# 확인: curl http://<서버IP>:8000/api/health  →  {"ok":true,"db":"kakao_collector","messages":0}
```

| API | 설명 |
|---|---|
| `GET /api/health` | DB 연결 확인 (인증 불필요) |
| `POST /api/messages` | 메시지 저장 (multipart, `image` 첨부 가능) |
| `GET /api/messages?room=&sender=&limit=&before_id=` | 조회 |
| `GET /api/media/{id}` | 사진 내려받기 |
| `GET /docs` | Swagger UI |

`/api/health`를 뺀 모든 API는 `X-Api-Key` 헤더가 필요합니다.

상시 운영할 때는 systemd나 Docker로 띄우고, 앞단에 Nginx와 HTTPS를 두는 구성을 권장합니다.

## 3. Android 앱 설치

1. APK 받기: GitHub **Actions → CI → 해당 실행 → Artifacts → `kakao-collector-debug-apk`**. 직접 빌드하려면 `cd android && ./gradlew assembleDebug`를 실행합니다(결과물은 `app/build/outputs/apk/debug/app-debug.apk`).
2. 휴대폰에 설치합니다. "출처를 알 수 없는 앱" 설치를 허용해야 합니다.
3. 앱을 열고 **알림 접근 권한 설정 열기** → **카톡 수집기**를 켭니다.
   - Android 13 이상에서 스위치가 회색이면: 설정 → 애플리케이션 → 카톡 수집기 → 우측 상단 ⋮ → **제한된 설정 허용**을 누른 뒤 다시 켭니다.
4. **배터리 최적화 예외 설정 열기** → 카톡 수집기를 "제한 없음"으로 설정합니다. 이 설정이 없으면 백그라운드에서 전송이 지연됩니다.
5. 서버 주소(`http://<서버IP>:8000`)와 API 키(`.env`의 `API_KEY`)를 입력하고 **저장 → 연결 테스트**를 누릅니다.
6. 카톡이 오면 화면 하단 목록에 `…`(대기) → `✔`(전송 완료)로 표시됩니다. 서버가 꺼져 있어도 휴대폰에 쌓아 두었다가 연결되면 자동으로 다시 보냅니다.

## 동작 원리와 한계

- **알림에 뜬 것만 수집합니다.** 아래 경우에는 알림이 오지 않으므로 수집되지 않습니다.
  - 카톡에서 해당 방이 알림 꺼짐 상태일 때
  - 그 방을 휴대폰에서 열어 보고 있을 때
  - PC 카톡이 활성화되어 휴대폰 알림이 억제될 때
  - 카톡 설정에서 "메시지 미리보기"가 꺼져 있을 때 (이 경우 내용이 "새로운 메시지"로만 옵니다)
- **사진은 알림 미리보기 이미지만 저장할 수 있습니다.** 카카오톡 버전에 따라 미리보기가 첨부되지 않거나(`content://` 접근 거부 포함) "사진을 보냈습니다." 텍스트만 오는 경우가 많습니다. 이때는 `message_type='photo'`인 텍스트 행만 저장됩니다. 원본 해상도 사진은 알림으로는 얻을 수 없습니다.
- 매우 긴 메시지는 알림 단계에서 잘려 있을 수 있습니다.
- 같은 알림이 여러 번 갱신되어도 단말(SQLite UNIQUE)과 서버(`dedup_key` UNIQUE)에서 두 번 걸러, 한 번만 저장됩니다.

## 보안·운영 주의

- **DB 비밀번호가 대화에 평문으로 공유되었으므로 교체를 권장합니다.** `.env`는 `.gitignore`에 포함되어 있어 커밋되지 않습니다.
- 서버 `.env`에는 DBA 계정 대신 `schema.sql` 하단 주석에 있는 **전용 최소 권한 계정**(`SELECT, INSERT, UPDATE`)을 사용하십시오.
- `API_KEY`는 32자 이상의 무작위 문자열로 정합니다: `python -c "import secrets;print(secrets.token_urlsafe(32))"`
- 앱은 IP 기반 `http://` 서버를 위해 평문 통신을 허용합니다(`network_security_config.xml`). 인터넷 구간을 지난다면 HTTPS로 전환하십시오.
- 단톡방 참여자의 대화와 사진은 개인정보입니다. 수집·보관 목적과 기간을 정하고, 필요하면 참여자 동의를 받으십시오.

## 개발·테스트

```bash
# 서버 통합 테스트 (MySQL/MariaDB 필요, 임시 스키마 kakao_collector_test 생성 후 삭제)
cd server && pip install -r requirements-dev.txt
TEST_DB_HOST=127.0.0.1 TEST_DB_USER=root TEST_DB_PASS=... pytest -q

# 앱 단위 테스트(알림 파서) + APK 빌드
cd android && ./gradlew testDebugUnitTest assembleDebug
```

## 다음 단계 (챗봇화)

현재는 수집 기능만 있습니다. 같은 알림의 **답장(RemoteInput) 액션**을 이용하면 특정 키워드나 서버 응답에 따라 해당 방에 자동 답장을 보낼 수 있습니다. 서버에 응답 규칙이나 LLM을 붙이고, 앱이 그 결과를 알림 답장으로 전송하는 구조로 확장할 수 있습니다.

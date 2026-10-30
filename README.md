# mahalphp — 카카오톡 알림 수집기

Android 휴대폰에 오는 **카카오톡 알림**을 읽어 **누가 · 어느 방에서 · 무슨 말을** 했는지, 그리고 가능한 경우 **사진**까지 MySQL에 저장합니다. 수집한 대화와 사진은 **관리자 웹(`/admin`)**에서 볼 수 있습니다.

```
[Android 앱: 카톡 수집기]                 [수집 서버: Python FastAPI]           [MySQL]
 NotificationListenerService   ── HTTP ──▶  POST /api/messages      ──────▶  kakao_collector
  · com.kakao.talk 알림만 수신   X-Api-Key   · 중복 제거(dedup_key)            ├ kakao_messages
  · 로컬 SQLite 큐(오프라인 보관)             · 사진 → storage/media           └ kakao_media
  · WorkManager 자동 재전송                 /admin  관리자 웹 (로그인)
                                            · 대화방 · 대화 · 사진 · 검색
```

앱이 DB에 직접 접속하지 않고 서버를 거치는 이유는 **DB 계정을 APK 안에 넣지 않기 위해서**입니다. APK는 누구나 풀어 볼 수 있으므로, 앱에는 해지 가능한 API 키만 넣고 DB 계정은 서버에만 둡니다.

| 경로 | 내용 |
|---|---|
| `android/` | Android 앱 (Kotlin, minSdk 26 / targetSdk 34) |
| `server/` | 수집 API (Python 3.10+, FastAPI, PyMySQL) |
| `server/app/static/` | 관리자 웹 화면 (HTML/CSS/JS, 빌드 불필요) |
| `server/sql/schema.sql` | 스키마·테이블 DDL (MySQL) |
| `server/sql/schema_sqlite.sql` | 같은 구성의 SQLite DDL (테스트·단일 PC용) |
| `.github/workflows/ci.yml` | 서버 통합 테스트 + APK 빌드 (APK는 Actions 아티팩트로 내려받음) |

---

## 가장 간단한 사용법: 앱만 설치 (PC·서버 불필요)

1. 휴대폰 브라우저로 [APK 다운로드](https://github.com/rickyhan/mahalphp/raw/claude/new-creation-dz2guv/release/kakao-collector.apk) → 설치
2. 앱에서 **알림 접근 권한**을 켬 (필요하면 "수집할 방 선택")
3. 카톡을 받은 뒤 **📖 수집한 대화 보기**: 대화방 목록 → 대화(보낸 사람·시각·사진) → 사진 크게 보기(해상도 표시), 내용·보낸 사람 검색
4. 데이터는 휴대폰 안 SQLite와 사진 파일로 저장됩니다. **CSV 내보내기**로 메일·드라이브 등에 공유할 수 있습니다(엑셀에서 한글 정상 표시).

### 입장·퇴장·대화명 이력

메인 화면 **👥 입장·퇴장·대화명 이력**에서 볼 수 있고, 대화방 화면 오른쪽 위 **참여자 이력** 메뉴로도 열 수 있습니다. 단톡방·오픈채팅에만 적용됩니다.

| 기록 | 방법 | 조건 |
|---|---|---|
| 첫 발언 / 최근 발언 / 발언 수 | 방별로 처음 메시지를 보낸 사람을 기록 | 항상 (실제 입장 시각이 아니라 첫 메시지 시각) |
| 입장·퇴장·내보냄·초대 | "○○님이 들어왔습니다/나갔습니다/…님을 내보냈습니다/…님을 초대했습니다" 안내 메시지 인식 | 카카오톡이 해당 안내를 **알림으로 보낼 때만** |
| 대화명 변경 (이전 → 새 이름) | 알림에 담긴 사람별 고유 키가 같은데 이름이 다르면 기록 | 카카오톡이 알림에 **고유 키를 넣어 줄 때만** (참여자 목록에 "대화명 변경 추적 불가"가 보이면 미제공) |

"알림 구조 진단"의 `person: key=` 줄에서 고유 키 제공 여부를 확인할 수 있습니다.

### 민원 자동 분류 (Google Gemini)

공백을 뺀 글자 수가 5자 이상인 텍스트 메시지를 Gemini API로 보내, **건물 입주민의 민원인지** 판정합니다. 민원으로 분류된 메시지만 **🚨 민원 목록**에 모아 보여 줍니다.

- **설정:** 앱 메인 화면의 "5. 민원 자동 분류"에서 Gemini API 키([Google AI Studio](https://aistudio.google.com/apikey)에서 발급)를 입력하고 켭니다. 키는 휴대폰에만 저장되고 APK에는 포함되지 않습니다.
- **분류:** 소음 / 누수·하자 / 주차 / 청소·쓰레기 / 시설·고장 / 관리비 / 보안·출입 / 생활·이웃 / 기타. 민원마다 한 줄 요약이 붙습니다. 인사, 잡담, 관리사무소 공지, 광고, 개인 거래는 민원에서 제외합니다.
- **문제 단위 묶음:** 같은 방에서 같은 사람이 같은 분류로 보낸 민원이 직전 메시지와 30분 이내로 이어지면 하나의 문제로 묶어 목록에 한 줄만 표시합니다("3건" 등). 누르면 관련 메시지 전체가 보이고, 각 메시지를 누르면 대화 위치로 이동합니다.
- **접수 안내 자동 답장:** 새 문제의 첫 민원이 들어오면 해당 채팅방에 카톡 '답장'으로 `[{분류}] 민원이 접수되었습니다.`를 보냅니다(사용자 카톡 계정으로 전송). 같은 문제의 반복 민원과 30분 지난 메시지에는 보내지 않습니다. 문구는 `{분류} {요약} {보낸사람} {방}`을 넣어 바꿀 수 있고, 끌 수 있습니다. 답장 버튼은 앱이 실행 중일 때 받은 알림에서만 쓸 수 있어서, 앱이 재시작된 직후에는 실패할 수 있습니다(민원 목록에 결과 표시). 내가 보낸 메시지는 민원 분류에서 제외합니다.
- **모델:** 기본값은 `gemini-3.5-flash-lite`이며 앱에서 바꿀 수 있습니다. 새 메시지가 오면 백그라운드에서 20건씩 묶어 자동 분류합니다. 일시 오류(503·429)는 자동으로 다시 시도합니다.
- **주의:** 메시지 내용이 Google로 전송되며, API 사용료가 발생할 수 있습니다.

여러 휴대폰의 데이터를 한곳에 모으거나 PC 관리자 웹으로 보려면 아래 서버를 실행하고 앱에서 **서버 전송 사용**을 켜십시오. 켜는 순간 휴대폰에만 있던 메시지도 함께 전송됩니다.

## 빠른 시작 (서버를 실행할 PC에서, 선택)

DB에 접속할 수 있는 PC에 Python 3.10 이상을 설치하고 아래 파일을 실행하면 끝납니다. Windows에서는 Python 설치 시 "Add python.exe to PATH"를 체크하십시오.

| OS | 실행 |
|---|---|
| Windows | `server\start.bat` 더블클릭 |
| Linux / macOS | `server/start.sh` |

최초 실행 때는 다음 순서로 진행됩니다.

1. 가상환경을 만들고 패키지를 설치합니다.
2. DB 종류를 고릅니다.
   - **1) SQLite (기본, 테스트용):** DB 설치 없이 `server/storage/kakao_collector.db` 파일 하나에 저장합니다.
   - **2) MySQL/MariaDB (운영용):** 호스트·포트·계정·비밀번호를 입력합니다.
3. `server/.env`를 만들고 테이블을 생성합니다.
4. 서버를 시작합니다.

이때 **관리자 웹 주소·계정과 앱에 넣을 서버 주소·API 키가 화면에 표시**됩니다. 두 번째 실행부터는 바로 서버만 시작합니다. 설정을 다시 하려면 `python -m scripts.setup_env`를 실행하십시오. DB 접속에 실패하면 MySQL 오류 코드와 원인(2003 연결 불가 / 1045 비밀번호 오류 / 1044 권한 부족)을 안내합니다.

아래 1~3은 같은 과정을 수동으로 하는 방법입니다.

## 1. DB 스키마 생성

테스트용으로 SQLite를 쓰면 이 단계는 필요 없습니다(`server/.env`에 `DB_ENGINE=sqlite`). 서버를 처음 켤 때 테이블이 자동으로 만들어집니다. SQLite 파일과 사진은 `server/storage/`에 저장되므로, 백업할 때는 이 폴더를 통째로 복사하면 됩니다.

```bash
cd server
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # DB_HOST / DB_PORT / DB_USER / DB_PASS / API_KEY 입력
python -m scripts.install_db
# 접속: ...@<DB호스트>:<포트> / 스키마: kakao_collector
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
| `GET /api/messages?room=&sender=&limit=&before_id=` | 조회 (API 키 또는 관리자 세션) |
| `GET /api/media/{id}` | 사진 내려받기 (API 키 또는 관리자 세션) |
| `GET /docs` | Swagger UI |

메시지 저장(`POST /api/messages`)에는 `X-Api-Key` 헤더가 필요합니다. 조회 API는 API 키나 관리자 로그인 세션 중 하나만 있으면 됩니다.

상시 운영할 때는 systemd나 Docker로 띄우고, 앞단에 Nginx와 HTTPS를 두는 구성을 권장합니다.

## 2-1. 관리자 웹

`server/.env`에 `ADMIN_USER`와 `ADMIN_PASSWORD`를 설정하고 서버를 다시 시작한 뒤, 브라우저에서 **`http://<서버IP>:8000/admin`**에 접속합니다. `ADMIN_PASSWORD`가 비어 있으면 관리자 웹은 비활성 상태가 됩니다.

| 화면 | 기능 |
|---|---|
| 상단 현황 | 전체 메시지 수, 오늘 수신 수, 대화방 수, 사진 수, 마지막 수신 시각 |
| 대화방 목록 | 최근 대화순으로 정렬. 방 이름 검색, 마지막 메시지 미리보기, 메시지 수 표시 |
| 대화 | 카카오톡과 같은 말풍선. 날짜 구분선, 보낸 사람·시각, 사진 썸네일, 링크를 표시하고 이전 메시지를 더 불러올 수 있습니다. 새 메시지는 10초마다 자동으로 반영됩니다 |
| 사진 | 전체 또는 선택한 방의 사진 갤러리. 클릭하면 크게 보이고, ← → 키로 넘기며 Esc로 닫습니다 |
| 검색 | 내용·보낸 사람으로 검색하고 종류·방으로 거를 수 있습니다. 결과를 누르면 해당 대화의 앞뒤 맥락으로 이동합니다 |

보안 설정:
- 로그인 세션은 서명된 HttpOnly·SameSite=Strict 쿠키입니다. 유효 시간은 `ADMIN_SESSION_HOURS`(기본 12시간)이며, HTTPS 접속 시 Secure 속성이 붙습니다.
- 같은 IP에서 로그인을 5회 실패하면 10분간 차단됩니다. Nginx 같은 프록시 뒤에서 운영하면 모든 접속이 같은 IP로 보이므로, `uvicorn --proxy-headers --forwarded-allow-ips=<프록시IP>` 옵션으로 실제 접속 IP를 넘기십시오.
- `API_KEY`나 `ADMIN_PASSWORD`를 바꾸면 기존 로그인이 모두 무효화됩니다.
- 화면에는 CSP(`script-src 'self'`)를 적용했고, 수집된 텍스트는 HTML이 아닌 글자로만 표시됩니다.
- 관리자 웹은 **조회 전용**입니다(수정·삭제 기능 없음).
- 단말용 API 키로는 관리자 API에 접근할 수 없습니다.
- 관리자 웹을 외부에 공개할 때는 반드시 HTTPS를 적용하십시오.

| 관리자 API (로그인 세션 필요) | 설명 |
|---|---|
| `GET /api/admin/summary` | 현황 수치 |
| `GET /api/admin/rooms?q=` | 대화방 목록 |
| `GET /api/admin/messages?room=&q=&sender=&type=&before_id=&after_id=&limit=` | 메시지(오래된 → 최신 순, `has_more` 포함) |
| `GET /api/admin/photos?room=&before_id=&limit=` | 사진 목록 |

## 3. Android 앱 설치

1. APK 받기: GitHub **Actions → CI → 해당 실행 → Artifacts → `kakao-collector-debug-apk`**. 직접 빌드하려면 `cd android && ./gradlew assembleDebug`를 실행합니다(결과물은 `app/build/outputs/apk/debug/app-debug.apk`).
2. 휴대폰에 설치합니다. "출처를 알 수 없는 앱" 설치를 허용해야 합니다.
3. 앱을 열고 **알림 접근 권한 설정 열기** → **카톡 수집기**를 켭니다.
   - Android 13 이상에서 스위치가 회색이면: 설정 → 애플리케이션 → 카톡 수집기 → 우측 상단 ⋮ → **제한된 설정 허용**을 누른 뒤 다시 켭니다.
4. **배터리 최적화 예외 설정 열기** → 카톡 수집기를 "제한 없음"으로 설정합니다. 이 설정이 없으면 백그라운드에서 전송이 지연됩니다.
5. 서버 주소(`http://<서버IP>:8000`)와 API 키(`.env`의 `API_KEY`)를 입력하고 **저장 → 연결 테스트**를 누릅니다.
6. (선택) **수집할 방 선택**: 모든 방 / 선택한 방만 / 선택한 방 제외 중 하나를 고릅니다. 방 목록에는 카톡 알림이 한 번이라도 온 방이 자동으로 쌓이고, 아직 알림이 오지 않은 방은 이름을 직접 추가할 수 있습니다. 제외된 방의 대화 내용은 휴대폰에도 서버에도 저장되지 않습니다(방 이름과 최근 알림 시각만 목록용으로 기록).
7. 카톡이 오면 화면 하단 목록에 `…`(대기) → `✔`(전송 완료)로 표시됩니다. 서버가 꺼져 있어도 휴대폰에 쌓아 두었다가 연결되면 자동으로 다시 보냅니다.

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
# 서버 통합 테스트: SQLite(기본, 설치 불필요)
cd server && pip install -r requirements-dev.txt
pytest -q
# 같은 테스트를 MySQL/MariaDB 로 (임시 스키마 kakao_collector_test 생성 후 삭제)
TEST_DB_ENGINE=mysql TEST_DB_HOST=127.0.0.1 TEST_DB_USER=root TEST_DB_PASS=... pytest -q

# 앱 단위 테스트(알림 파서) + APK 빌드
cd android && ./gradlew testDebugUnitTest assembleDebug
```

## 다음 단계 (챗봇화)

현재는 수집 기능만 있습니다. 같은 알림의 **답장(RemoteInput) 액션**을 이용하면 특정 키워드나 서버 응답에 따라 해당 방에 자동 답장을 보낼 수 있습니다. 서버에 응답 규칙이나 LLM을 붙이고, 앱이 그 결과를 알림 답장으로 전송하는 구조로 확장할 수 있습니다.

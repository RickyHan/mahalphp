# mahalphp — 카카오톡 알림 수집 · 민원 관리

Android 휴대폰에 오는 **카카오톡 알림**을 수집해 서버(MySQL)에 저장하고, **웹 관리 화면**에서 대화·사진·민원·통계·참여자 이력을 봅니다. 민원 분류(Google Gemini)를 비롯한 **모든 처리는 서버가 하고, 앱은 수집만** 합니다.

```
[휴대폰: 카톡 수집기 앱]                    [Ubuntu 서버: kakao-collector 서비스]            [MySQL]
 카톡 알림 수신 ──────── POST /api/messages ──▶  저장 · 참여자 이력 기록                 ─▶ kakao_collector
 (누가·어느 방·내용·사진)                       백그라운드 워커: Gemini 민원 분류·묶음
 '민원 접수 안내' 카톡 답장 ◀─ GET /api/replies ─  새 민원 → 접수 안내 대기열
                                                웹 관리 화면 /admin (대화·사진·검색·민원·통계·이력·설정)
```

| 경로 | 내용 |
|---|---|
| `deploy/install.sh` | Ubuntu 서버 원클릭 설치 (MySQL·서비스 등록 포함) |
| `server/` | 서버 (Python 3.10+, FastAPI, PyMySQL) — API · 웹 · 민원 분류 워커 |
| `server/app/static/` | 웹 관리 화면 (HTML/CSS/JS, 빌드 불필요) |
| `server/sql/` | MySQL / SQLite 스키마 |
| `android/` | 수집 앱 (Kotlin, Android 8.0+) |
| `release/kakao-collector.apk` | 앱 설치 파일 |

---

## 1. 서버 설치 (Ubuntu 22.04 / 24.04)

서버에 SSH 로 접속해 아래 한 줄을 실행합니다. MySQL 계정은 **sudo 를 실행한 OS 계정과 같은 이름**으로 만들고, 비밀번호는 실행 중에 입력합니다(OS 계정과 같게 입력).

```bash
curl -fsSL https://raw.githubusercontent.com/rickyhan/mahalphp/claude/new-creation-dz2guv/deploy/install.sh | sudo bash
```

설치 과정에서 묻는 것: **MySQL 비밀번호**(2번), **Gemini API 키**(민원 분류용, 없으면 Enter 후 나중에 추가).
끝나면 다음이 출력됩니다.

```
 관리자 웹   : http://192.168.0.68:8000/admin
 관리자 계정 : admin / (무작위 생성)
 앱 서버 주소: http://192.168.0.68:8000
 앱 API 키   : (무작위 생성)
```

- 서비스 이름 `kakao-collector` — 재부팅 시 자동 시작. 로그 `journalctl -u kakao-collector -f`, 재시작 `sudo systemctl restart kakao-collector`
- 설정 파일 `/opt/kakao-collector/server/.env` (DB 계정·API 키·관리자 비밀번호·`GEMINI_API_KEY`, 권한 600)
- **업데이트**: 같은 설치 명령을 다시 실행 (설정·데이터 유지)
- 휴대폰이 서버와 **같은 Wi-Fi 가 아니면** 공유기에서 외부 포트 → `서버IP:8000` 포워딩 후 앱에 `http://공인IP:외부포트` 입력

## 2. 앱 설치

1. 휴대폰 브라우저로 [APK 다운로드](https://github.com/rickyhan/mahalphp/raw/claude/new-creation-dz2guv/release/kakao-collector.apk) → 설치
2. **알림 접근 권한** 켜기 (Android 13+ 에서 스위치가 회색이면 앱 정보 → ⋮ → '제한된 설정 허용')
3. **배터리 최적화 예외** 설정
4. **서버 주소·API 키** 입력 → 저장 → 연결 테스트
5. (선택) **수집할 방 선택** — 모든 방 / 선택한 방만 / 선택한 방 제외

앱은 수집·전송만 합니다. 휴대폰에는 전송 대기분만 남고, 서버에 저장된 사진은 휴대폰에서 지웁니다. 맨 위 **🖥 웹 관리 화면 열기**로 웹을 엽니다.

## 3. 웹 관리 화면 (`/admin`)

| 탭 | 내용 |
|---|---|
| 대화 | 대화방 목록(최근순) · 카톡형 말풍선 · 사진 · 링크 · 이전 더 보기 · 10초 자동 갱신 |
| 사진 | 사진 갤러리 · 크게 보기(해상도) |
| 검색 | 내용·보낸 사람 검색 → 해당 대화 위치로 이동 |
| 민원 | Gemini 가 민원으로 분류한 메시지를 **문제 단위로 묶어** 표시(같은 방·사람·분류로 30분 이내 반복 = 1건), 펼치면 관련 메시지 · 접수 안내 결과 · 지금 분류 / 실패 재시도 |
| 통계 | 오늘/7일/30일/전체 — 문제 수, 반복 민원, 안내 발송·실패, 분류·방·제기자 TOP 10, 일별·시간대별 |
| 참여자 이력 | 입장·퇴장·내보냄·초대·대화명 변경·첫 발언 이력, 방별 참여자 |
| 설정 | 민원 자동 분류 사용 · 모델 · 접수 안내 사용 · 안내 문구 · 워커 상태 |

### 민원 자동 분류와 접수 안내
- 대상: 공백 제외 5자 이상 텍스트 메시지(내가 보낸 메시지 제외). 서버 워커가 새 메시지마다 Gemini 로 20건씩 판정
- 분류: 소음 · 누수·하자 · 주차 · 청소·쓰레기 · 시설·고장 · 관리비 · 보안·출입 · 생활·이웃 · 기타 + 한 줄 요약
- 새 문제의 첫 민원(수신 30분 이내)이면 서버가 `[{분류}] 민원이 접수되었습니다.` 안내를 만들고, **휴대폰 앱이 그 방에 카톡 '답장'으로 전송** 후 결과를 보고합니다. (카톡 답장은 휴대폰 알림으로만 보낼 수 있어서 전송만 앱이 담당)
- Gemini 키는 서버 `.env` 의 `GEMINI_API_KEY` — 앱·저장소에는 없습니다. 메시지 내용이 Google 로 전송되며 사용료가 발생할 수 있습니다.

### 참여자 이력의 조건
| 기록 | 조건 |
|---|---|
| 첫 발언·발언 수 | 항상 (실제 입장 시각이 아닌 첫 메시지 시각) |
| 입장·퇴장·내보냄·초대 | 카톡이 해당 안내 메시지를 **알림으로 보낼 때만** |
| 대화명 변경 | 카톡 알림에 사람별 **고유 키**가 있을 때만 (앱의 '알림 구조 진단' `person: key=` 로 확인) |

## 동작 원리와 한계
- **알림에 뜬 것만 수집**: 방 알림 꺼짐, 그 방을 보고 있는 중, PC 카톡 사용 중, '메시지 미리보기' 꺼짐이면 수집되지 않습니다.
- 사진은 알림의 **미리보기 이미지**만 저장됩니다(원본 아님). 미리보기가 없으면 "📷 사진" 기록만 남습니다.
- 단톡·오픈채팅 방 이름은 대화 제목 → 대화 바로가기(Android 12+) → subText → summaryText 순으로 찾습니다. 방 이름이 이상하면 앱의 **알림 구조 진단 → 이 내용 보내기** 로 원인을 확인합니다.
- 접수 안내는 앱이 실행 중일 때 받은 알림으로만 보낼 수 있어, 휴대폰 재시작 직후에는 실패할 수 있습니다(웹 민원 탭에 결과 표시).

## 보안
- 공개 저장소이므로 비밀번호·API 키는 저장소에 없습니다. 설치 시 입력받아 서버 `.env`(권한 600)에만 저장합니다.
- 웹: 서명된 HttpOnly·SameSite=Strict 세션, 5회 실패 시 10분 차단, CSP. 외부 공개 시 HTTPS 를 권장합니다.
- 단톡방 참여자의 대화·사진은 개인정보입니다. 보관 목적과 기간을 정하십시오.

## 개발·테스트

```bash
# 서버: SQLite(기본) / MySQL
cd server && pip install -r requirements-dev.txt && pytest -q
TEST_DB_ENGINE=mysql TEST_DB_HOST=127.0.0.1 TEST_DB_USER=root TEST_DB_PASS=... pytest -q

# 개발용 실행 (SQLite, Gemini 없이)
python -m scripts.setup_env && uvicorn app.main:app --reload

# 앱: 단위 테스트(Robolectric 포함) + APK
cd android && ./gradlew testDebugUnitTest assembleDebug
```

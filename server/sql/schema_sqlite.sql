-- ---------------------------------------------------------------------------
-- 카카오톡 알림 수집 스키마 (SQLite, 테스트·단일 PC용)
-- 운영(MySQL)용 schema.sql 과 같은 테이블·컬럼 구성. 시각은 ISO 문자열(YYYY-MM-DDTHH:MM:SS.fff, KST)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS kakao_messages (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id        TEXT    NOT NULL,
  room_name        TEXT    NOT NULL,
  sender_name      TEXT    NOT NULL,
  message_text     TEXT,
  message_type     TEXT    NOT NULL DEFAULT 'text',
  is_group_chat    INTEGER NOT NULL DEFAULT 0,
  has_media        INTEGER NOT NULL DEFAULT 0,
  posted_at        TEXT    NOT NULL,
  received_at      TEXT    NOT NULL,
  notification_key TEXT,
  dedup_key        TEXT    NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_kakao_messages_room_time ON kakao_messages (room_name, posted_at);
CREATE INDEX IF NOT EXISTS idx_kakao_messages_sender_time ON kakao_messages (sender_name, posted_at);
CREATE INDEX IF NOT EXISTS idx_kakao_messages_posted ON kakao_messages (posted_at);

CREATE TABLE IF NOT EXISTS kakao_media (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id  INTEGER NOT NULL REFERENCES kakao_messages (id) ON DELETE CASCADE,
  file_path   TEXT    NOT NULL,
  mime_type   TEXT    NOT NULL,
  size_bytes  INTEGER NOT NULL,
  width       INTEGER,
  height      INTEGER,
  sha256      TEXT    NOT NULL,
  created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_kakao_media_message ON kakao_media (message_id);
CREATE INDEX IF NOT EXISTS idx_kakao_media_sha256 ON kakao_media (sha256);

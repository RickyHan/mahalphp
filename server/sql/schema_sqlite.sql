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
  sender_key       TEXT,
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

CREATE TABLE IF NOT EXISTS complaint_check (
  message_id   INTEGER PRIMARY KEY REFERENCES kakao_messages (id) ON DELETE CASCADE,
  verdict      INTEGER NOT NULL,
  category     TEXT    NOT NULL DEFAULT '',
  summary      TEXT    NOT NULL DEFAULT '',
  confidence   REAL    NOT NULL DEFAULT 0,
  model        TEXT    NOT NULL DEFAULT '',
  checked_at   TEXT    NOT NULL,
  reply_status INTEGER NOT NULL DEFAULT 0,
  reply_note   TEXT
);
CREATE INDEX IF NOT EXISTS idx_complaint_verdict ON complaint_check (verdict, category);

CREATE TABLE IF NOT EXISTS room_members (
  room_name     TEXT    NOT NULL,
  member_key    TEXT    NOT NULL,
  display_name  TEXT    NOT NULL,
  keyed         INTEGER NOT NULL,
  first_seen    TEXT    NOT NULL,
  last_seen     TEXT    NOT NULL,
  message_count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (room_name, member_key)
);

CREATE TABLE IF NOT EXISTS member_events (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  room_name TEXT NOT NULL,
  type      TEXT NOT NULL,
  member    TEXT NOT NULL,
  old_name  TEXT,
  actor     TEXT,
  at        TEXT NOT NULL,
  text      TEXT
);
CREATE INDEX IF NOT EXISTS idx_member_events_room ON member_events (room_name, at);

CREATE TABLE IF NOT EXISTS reply_queue (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id INTEGER NOT NULL,
  device_id  TEXT    NOT NULL,
  room_name  TEXT    NOT NULL,
  text       TEXT    NOT NULL,
  status     TEXT    NOT NULL DEFAULT 'pending',
  note       TEXT,
  created_at TEXT    NOT NULL,
  done_at    TEXT,
  kind        TEXT NOT NULL DEFAULT 'ack',
  vendor_id   INTEGER,
  vendor_name TEXT
);
CREATE INDEX IF NOT EXISTS idx_reply_queue_status ON reply_queue (status, device_id);

CREATE TABLE IF NOT EXISTS app_settings (
  name  TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vendors (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  name       TEXT NOT NULL,
  biz_no     TEXT NOT NULL DEFAULT '',
  phone      TEXT NOT NULL DEFAULT '',
  address    TEXT NOT NULL DEFAULT '',
  room_name  TEXT,
  category_id INTEGER,
  doc_path   TEXT,
  doc_mime   TEXT,
  doc_name   TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vendors_room ON vendors (room_name);

CREATE TABLE IF NOT EXISTS complaint_categories (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL UNIQUE,
  description TEXT NOT NULL DEFAULT '',
  sort_order  INTEGER NOT NULL DEFAULT 0,
  enabled     INTEGER NOT NULL DEFAULT 1,
  created_at  TEXT NOT NULL
);


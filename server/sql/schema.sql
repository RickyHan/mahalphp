-- ---------------------------------------------------------------------------
-- 카카오톡 알림 수집 스키마
-- 실행: mysql -h <host> -P <port> -u <user> -p < server/sql/schema.sql
-- 또는: php server/bin/install.php  (.env의 DB_NAME으로 스키마명을 바꿀 수 있음)
-- ---------------------------------------------------------------------------

CREATE DATABASE IF NOT EXISTS `kakao_collector`
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE `kakao_collector`;

-- 알림 1건(메시지 1개)당 1행. 누가(sender_name), 어디서(room_name), 무엇을(message_text)
CREATE TABLE IF NOT EXISTS `kakao_messages` (
  `id`               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `device_id`        VARCHAR(64)     NOT NULL COMMENT '수집한 단말 식별자(앱 설치 시 생성되는 UUID)',
  `room_name`        VARCHAR(255)    NOT NULL COMMENT '채팅방 이름(1:1 대화는 상대방 이름)',
  `sender_name`      VARCHAR(255)    NOT NULL COMMENT '보낸 사람',
  `message_text`     TEXT            NULL     COMMENT '메시지 본문',
  `message_type`     VARCHAR(20)     NOT NULL DEFAULT 'text' COMMENT 'text|photo|video|emoticon|file|other',
  `is_group_chat`    TINYINT(1)      NOT NULL DEFAULT 0,
  `has_media`        TINYINT(1)      NOT NULL DEFAULT 0 COMMENT 'kakao_media 행 존재 여부',
  `posted_at`        DATETIME(3)     NOT NULL COMMENT '메시지 수신 시각(단말 기준, KST)',
  `received_at`      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '서버 저장 시각',
  `notification_key` VARCHAR(255)    NULL     COMMENT 'Android StatusBarNotification key',
  `dedup_key`        CHAR(64)        NOT NULL COMMENT '중복 방지 SHA-256(device|room|sender|text|posted_at)',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_kakao_messages_dedup` (`dedup_key`),
  KEY `idx_kakao_messages_room_time` (`room_name`, `posted_at`),
  KEY `idx_kakao_messages_sender_time` (`sender_name`, `posted_at`),
  KEY `idx_kakao_messages_posted` (`posted_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='카카오톡 알림에서 수집한 대화';

-- 사진 메시지에 딸린 이미지. 파일은 서버 디스크(storage/media)에, 경로·메타데이터는 여기에 저장
CREATE TABLE IF NOT EXISTS `kakao_media` (
  `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `message_id`   BIGINT UNSIGNED NOT NULL,
  `file_path`    VARCHAR(512)    NOT NULL COMMENT 'storage/media 기준 상대 경로',
  `mime_type`    VARCHAR(100)    NOT NULL,
  `size_bytes`   INT UNSIGNED    NOT NULL,
  `width`        INT UNSIGNED    NULL,
  `height`       INT UNSIGNED    NULL,
  `sha256`       CHAR(64)        NOT NULL,
  `created_at`   DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`),
  KEY `idx_kakao_media_message` (`message_id`),
  KEY `idx_kakao_media_sha256` (`sha256`),
  CONSTRAINT `fk_kakao_media_message` FOREIGN KEY (`message_id`)
    REFERENCES `kakao_messages` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='카카오톡 알림에서 수집한 사진';

-- 권장: DBA 계정 대신 이 스키마 전용 최소 권한 계정을 만들어 서버 .env에 사용
-- CREATE USER 'kakao_app'@'%' IDENTIFIED BY '<강력한 비밀번호>';
-- GRANT SELECT, INSERT, UPDATE ON `kakao_collector`.* TO 'kakao_app'@'%';

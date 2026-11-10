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
  `sender_key`       VARCHAR(255)    NULL     COMMENT '카톡 알림의 보낸 사람 고유 키(있을 때만, 대화명 변경 추적용)',
  `room_key`         VARCHAR(64)     NULL     COMMENT '카톡 채팅방 고유 번호(방 이름이 바뀌어도 그대로)',
  `room_trusted`     TINYINT(1)      NULL     COMMENT '0 = 방 이름을 못 읽어 대신한 이름(나중에 바로잡음)',
  `dedup_key`        CHAR(64)        NOT NULL COMMENT '중복 방지 SHA-256(device|room|sender|text|posted_at)',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_kakao_messages_dedup` (`dedup_key`),
  KEY `idx_kakao_messages_room_time` (`room_name`, `posted_at`),
  KEY `idx_kakao_messages_sender_time` (`sender_name`, `posted_at`),
  KEY `idx_kakao_messages_posted` (`posted_at`),
  KEY `idx_kakao_messages_sender_key` (`sender_key`),
  KEY `idx_kakao_messages_room_key` (`room_key`)
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

-- Gemini 민원 분류 결과 (메시지 1건당 1행). verdict: 1=민원, 0=아님, -1=분류 실패(재시도 대상)
CREATE TABLE IF NOT EXISTS `complaint_check` (
  `message_id`   BIGINT UNSIGNED NOT NULL,
  `verdict`      TINYINT         NOT NULL,
  `category`     VARCHAR(30)     NOT NULL DEFAULT '',
  `summary`      VARCHAR(200)    NOT NULL DEFAULT '',
  `confidence`   DOUBLE          NOT NULL DEFAULT 0,
  `model`        VARCHAR(80)     NOT NULL DEFAULT '',
  `checked_at`   DATETIME(3)     NOT NULL,
  `reply_status` TINYINT         NOT NULL DEFAULT 0 COMMENT '0=없음 1=보냄 -1=실패 2=같은 문제 3=오래됨 4=발송 대기',
  `reply_note`   VARCHAR(255)    NULL,
  PRIMARY KEY (`message_id`),
  KEY `idx_complaint_verdict` (`verdict`, `category`),
  CONSTRAINT `fk_complaint_message` FOREIGN KEY (`message_id`) REFERENCES `kakao_messages` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='민원 분류';

-- 방별 참여자(발언 기준)
CREATE TABLE IF NOT EXISTS `room_members` (
  `room_name`     VARCHAR(255) NOT NULL,
  `member_key`    VARCHAR(255) NOT NULL,
  `display_name`  VARCHAR(255) NOT NULL,
  `keyed`         TINYINT(1)   NOT NULL,
  `first_seen`    DATETIME(3)  NOT NULL,
  `last_seen`     DATETIME(3)  NOT NULL,
  `message_count` INT UNSIGNED NOT NULL DEFAULT 0,
  PRIMARY KEY (`room_name`(191), `member_key`(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='참여자';

-- 입장·퇴장·내보냄·초대·대화명 변경·첫 발언 이력
CREATE TABLE IF NOT EXISTS `member_events` (
  `id`        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `room_name` VARCHAR(255)    NOT NULL,
  `type`      VARCHAR(20)     NOT NULL,
  `member`    VARCHAR(255)    NOT NULL,
  `old_name`  VARCHAR(255)    NULL,
  `actor`     VARCHAR(255)    NULL,
  `at`        DATETIME(3)     NOT NULL,
  `text`      TEXT            NULL,
  PRIMARY KEY (`id`),
  KEY `idx_member_events_room` (`room_name`(191), `at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='참여자 이력';

-- 휴대폰이 카톡 '답장'으로 보낼 안내 메시지 대기열 (서버가 만들고, 앱이 가져가 전송 후 결과 보고)
CREATE TABLE IF NOT EXISTS `reply_queue` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `message_id` BIGINT UNSIGNED NOT NULL,
  `device_id`  VARCHAR(64)     NOT NULL,
  `room_name`  VARCHAR(255)    NOT NULL,
  `text`       TEXT            NOT NULL,
  `status`     VARCHAR(10)     NOT NULL DEFAULT 'pending' COMMENT 'pending|sent|failed|expired',
  `note`       VARCHAR(255)    NULL,
  `created_at` DATETIME(3)     NOT NULL,
  `done_at`    DATETIME(3)     NULL,
  `kind`        VARCHAR(10)     NOT NULL DEFAULT 'ack' COMMENT 'ack=민원 방 접수 안내, vendor=업체 카톡방 처리 요청, schedule=예약 메시지, faq=FAQ 자동 답변',
  `vendor_id`   BIGINT UNSIGNED NULL,
  `vendor_name` VARCHAR(100)    NULL,
  `schedule_id` BIGINT UNSIGNED NULL COMMENT 'kind=schedule 일 때 예약 메시지 id',
  PRIMARY KEY (`id`),
  KEY `idx_reply_queue_status` (`status`, `device_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='안내 발송 대기열';

-- 웹에서 바꾸는 설정 (민원 분류 사용, 모델, 자동 안내, 안내 문구)
CREATE TABLE IF NOT EXISTS `app_settings` (
  `name`  VARCHAR(64) NOT NULL,
  `value` TEXT        NOT NULL,
  PRIMARY KEY (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='설정';

-- 아파트: 별 하나로 시작하는 카톡 채팅방(원래 이름)과 관리용 아파트 이름(별 제외, 수정 가능)
CREATE TABLE IF NOT EXISTS `apartments` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `name`        VARCHAR(100)    NOT NULL COMMENT '건물 이름',
  `room_name`   VARCHAR(255)    NULL     COMMENT '연결된 카톡 채팅방 이름 (없으면 미연결)',
  `address`     VARCHAR(255)    NULL     COMMENT '주소',
  `listed`      TINYINT(1)      NOT NULL DEFAULT 0 COMMENT '1=건물 목록에서 등록, 0=별(★) 채팅방에서 자동 추가',
  `room_mode`   VARCHAR(10)     NOT NULL DEFAULT 'auto' COMMENT 'auto=자동 매핑 대상, manual=직접 지정(자동 매핑 안 함)',
  `match_score` SMALLINT        NULL     COMMENT '자동 매핑 유사도(%)',
  `created_at`  DATETIME(3)     NOT NULL,
  `updated_at`  DATETIME(3)     NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_apartments_room` (`room_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='건물';

-- 아파트별 민원 분류 담당 업체. 행이 없으면 기본 업체(vendors.category_id), vendor_id 가 NULL 이면 '보내지 않음'
CREATE TABLE IF NOT EXISTS `apartment_vendors` (
  `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `apartment_id` BIGINT UNSIGNED NOT NULL,
  `category_id`  BIGINT UNSIGNED NOT NULL,
  `vendor_id`    BIGINT UNSIGNED NULL,
  `updated_at`   DATETIME(3)     NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_apartment_vendors` (`apartment_id`, `category_id`),
  KEY `idx_apartment_vendors_vendor` (`vendor_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='아파트별 담당 업체';

-- 예약 메시지: 정한 날짜·요일·시각에 채팅방으로 보냄
CREATE TABLE IF NOT EXISTS `scheduled_messages` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `title`       VARCHAR(100)    NOT NULL,
  `room_name`   VARCHAR(255)    NOT NULL,
  `text`        TEXT            NOT NULL,
  `kind`        VARCHAR(10)     NOT NULL COMMENT 'once|daily|weekly|monthly',
  `run_date`    VARCHAR(10)     NULL COMMENT 'once: YYYY-MM-DD',
  `weekdays`    VARCHAR(20)     NULL COMMENT 'weekly: 0=월~6=일, 쉼표 구분',
  `month_day`   INT             NULL COMMENT 'monthly: 1~31 (없는 날은 말일)',
  `run_time`    VARCHAR(5)      NOT NULL COMMENT 'HH:MM',
  `enabled`     TINYINT         NOT NULL DEFAULT 1,
  `next_run_at` DATETIME(3)     NULL,
  `last_run_at` DATETIME(3)     NULL,
  `last_note`   VARCHAR(255)    NULL,
  `created_at`  DATETIME(3)     NOT NULL,
  `updated_at`  DATETIME(3)     NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_scheduled_due` (`enabled`, `next_run_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='예약 메시지';

-- 민원 분류 (웹 '민원 카테고리'에서 관리, Gemini 분류에 사용). '기타'는 삭제 불가
CREATE TABLE IF NOT EXISTS `complaint_categories` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `name`        VARCHAR(30)     NOT NULL,
  `description` VARCHAR(200)    NOT NULL DEFAULT '' COMMENT 'Gemini 판정 기준 설명',
  `sort_order`  INT             NOT NULL DEFAULT 0,
  `enabled`     TINYINT         NOT NULL DEFAULT 1,
  `created_at`  DATETIME(3)     NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_complaint_categories_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='민원 분류';

-- 민원처리업체 (담당 카톡 대화방 1개 지정)
CREATE TABLE IF NOT EXISTS `vendors` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `name`       VARCHAR(100)    NOT NULL COMMENT '업체명',
  `biz_no`     VARCHAR(20)     NOT NULL DEFAULT '' COMMENT '사업자등록번호',
  `phone`      VARCHAR(20)     NOT NULL DEFAULT '',
  `address`    VARCHAR(300)    NOT NULL DEFAULT '',
  `room_name`  VARCHAR(255)    NULL COMMENT '담당 카톡 대화방',
  `category_id` BIGINT UNSIGNED NULL COMMENT '담당 민원 분류 (complaint_categories.id)',
  `doc_path`   VARCHAR(255)    NULL COMMENT '사업자등록증 파일 (storage/vendor-docs 기준)',
  `doc_mime`   VARCHAR(50)     NULL,
  `doc_name`   VARCHAR(200)    NULL,
  `created_at` DATETIME(3)     NOT NULL,
  `updated_at` DATETIME(3)     NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_vendors_room` (`room_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='민원처리업체';

-- 권장: DBA 계정 대신 이 스키마 전용 최소 권한 계정을 만들어 서버 .env에 사용
-- CREATE USER 'kakao_app'@'%' IDENTIFIED BY '<강력한 비밀번호>';
-- GRANT SELECT, INSERT, UPDATE ON `kakao_collector`.* TO 'kakao_app'@'%';

-- FAQ 자동 답변: 건물 채팅방의 질문이 FAQ 질문과 의도가 같으면 답변을 보낸다
CREATE TABLE IF NOT EXISTS `faq_items` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `category`   VARCHAR(50)     NOT NULL DEFAULT '' COMMENT '분야',
  `question`   VARCHAR(500)    NOT NULL COMMENT '질문(의도)',
  `answer`     TEXT            NOT NULL COMMENT '보낼 답변',
  `enabled`    TINYINT(1)      NOT NULL DEFAULT 1 COMMENT '자동 답변 사용',
  `sort_order` INT             NOT NULL DEFAULT 0,
  `created_at` DATETIME(3)     NOT NULL,
  `updated_at` DATETIME(3)     NOT NULL,
  `report`     TINYINT(1)      NOT NULL DEFAULT 1 COMMENT '신고형: 민원도 함께 접수',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='FAQ';

-- FAQ 자동 답변 이력 (어떤 메시지에 어떤 답변을 보냈는지)
CREATE TABLE IF NOT EXISTS `faq_answers` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `message_id` BIGINT UNSIGNED NOT NULL,
  `faq_id`     BIGINT UNSIGNED NULL,
  `question`   VARCHAR(500)    NOT NULL DEFAULT '' COMMENT '보낼 당시 FAQ 질문',
  `room_name`  VARCHAR(255)    NOT NULL,
  `reply_id`   BIGINT UNSIGNED NULL COMMENT 'reply_queue id (보내지 않았으면 NULL)',
  `confidence` DECIMAL(4,3)    NOT NULL DEFAULT 0,
  `note`       VARCHAR(255)    NULL,
  `created_at` DATETIME(3)     NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_faq_answers_room` (`room_name`, `faq_id`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='FAQ 자동 답변 이력';

-- 건물의 추가 채팅방 (예: 세대방 외에 상가방). 기본 채팅방은 apartments.room_name
CREATE TABLE IF NOT EXISTS `apartment_rooms` (
  `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `apartment_id` BIGINT UNSIGNED NOT NULL,
  `room_name`    VARCHAR(255)    NOT NULL,
  `created_at`   DATETIME(3)     NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_apartment_rooms_room` (`room_name`),
  KEY `idx_apartment_rooms_apt` (`apartment_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='건물 추가 채팅방';

-- 업체의 기본 담당 민원 분류 (여러 개). 건물별로 따로 정하지 않은 분류의 처리 요청을 받는다
CREATE TABLE IF NOT EXISTS `vendor_categories` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `vendor_id`   BIGINT UNSIGNED NOT NULL,
  `category_id` BIGINT UNSIGNED NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_vendor_categories` (`vendor_id`, `category_id`),
  KEY `idx_vendor_categories_cat` (`category_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='업체 담당 분류';

-- 카톡 채팅방 고유 번호 → 최근 방 이름 (방 이름 변경 따라가기)
CREATE TABLE IF NOT EXISTS `chat_rooms` (
  `room_key`   VARCHAR(64)  NOT NULL,
  `room_name`  VARCHAR(255) NOT NULL,
  `updated_at` DATETIME(3)  NOT NULL,
  PRIMARY KEY (`room_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='채팅방 번호';

CREATE TABLE IF NOT EXISTS `classification_examples` (
  `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `message_id`   BIGINT UNSIGNED NULL COMMENT '바로잡은 메시지 (직접 넣은 사례면 NULL)',
  `text`         VARCHAR(1000)   NOT NULL,
  `is_complaint` TINYINT(1)      NOT NULL DEFAULT 0,
  `note`         VARCHAR(255)    NOT NULL DEFAULT '' COMMENT '판정 이유',
  `created_at`   DATETIME(3)     NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='관리자가 바로잡은 민원 판정 사례 (Gemini 지시문에 함께 보냄)';

CREATE TABLE IF NOT EXISTS `reply_ready` (
  `device_id`  VARCHAR(64)  NOT NULL,
  `room_name`  VARCHAR(255) NOT NULL COMMENT '공백 정리한 방 이름',
  `updated_at` DATETIME(3)  NOT NULL,
  PRIMARY KEY (`device_id`, `room_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='휴대폰이 지금 카톡 답장을 보낼 수 있는 방 (앱이 30초~5분마다 보고)';

CREATE TABLE IF NOT EXISTS `vendor_contacts` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `vendor_id`  BIGINT UNSIGNED NOT NULL,
  `phone`      VARCHAR(30)     NOT NULL,
  `device`     VARCHAR(20)     NOT NULL DEFAULT '' COMMENT '기종 (안드로이드·아이폰)',
  `region`     VARCHAR(200)    NOT NULL DEFAULT '' COMMENT '담당 지역',
  `sort_order` INT             NOT NULL DEFAULT 0,
  `created_at` DATETIME(3)     NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_vendor_contacts_vendor` (`vendor_id`, `sort_order`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='업체 담당자 연락처 (여러 명)';

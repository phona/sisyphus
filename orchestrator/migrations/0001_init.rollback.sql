-- yoyo 约定：<id>.rollback.sql 文件是对应迁移的回滚（rollback）
-- yoyo apply 时不读这个文件；yoyo rollback 才执行
DROP TABLE IF EXISTS event_seen;
DROP TABLE IF EXISTS req_state;

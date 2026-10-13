-- MariaDB structure preview. Run Python --init-db for idempotent DDL plus transactional data backfill/version 3.

CREATE TABLE IF NOT EXISTS play_sessions (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	name VARCHAR(100) NOT NULL, 
	active INTEGER NOT NULL, 
	settings_json JSON NOT NULL, 
	court_ids_json JSON NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
)

;

CREATE TABLE IF NOT EXISTS queue_groups (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	entry_type VARCHAR(10) NOT NULL, 
	temporary INTEGER NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
)

;

CREATE TABLE IF NOT EXISTS scheduler_lock (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	revision INTEGER NOT NULL, 
	PRIMARY KEY (id)
)

;

CREATE TABLE IF NOT EXISTS winner_holds (
	court_id INTEGER NOT NULL, 
	player_ids_json JSON NOT NULL, 
	streak INTEGER NOT NULL, 
	match_id INTEGER NOT NULL, 
	active INTEGER NOT NULL, 
	PRIMARY KEY (court_id), 
	FOREIGN KEY(court_id) REFERENCES courts (id)
)

;
ALTER TABLE courts ADD COLUMN IF NOT EXISTS enabled INTEGER NOT NULL DEFAULT 1;
ALTER TABLE courts ADD COLUMN IF NOT EXISTS available_since DATETIME NULL;
ALTER TABLE courts ADD COLUMN IF NOT EXISTS level INTEGER NOT NULL DEFAULT 0;
ALTER TABLE player_queue ADD COLUMN IF NOT EXISTS group_id INTEGER NULL;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS matchmaking_mode VARCHAR(30) NOT NULL DEFAULT 'LEGACY_WEIGHTED';
ALTER TABLE matches ADD COLUMN IF NOT EXISTS session_id INTEGER NULL;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS matchmaking_json JSON NULL;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS queue_groups_json JSON NULL;
INSERT IGNORE INTO scheduler_lock(id, revision) VALUES (1,0);
-- Do not insert migration marker here; Python CLI backfills legacy SOLO groups before marking version 3.

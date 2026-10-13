-- Schema 3 initial creation. For existing databases use the Python --init-db migration.
SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS admin_users (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	username VARCHAR(80) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	`role` VARCHAR(16) NOT NULL, 
	active INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (username)
)

;


CREATE TABLE IF NOT EXISTS courts (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	name VARCHAR(100) NOT NULL, 
	number INTEGER NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	active_match_id INTEGER, 
	enabled INTEGER NOT NULL DEFAULT '1', 
	available_since DATETIME, 
	level INTEGER NOT NULL DEFAULT '0', 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (number)
)

;


CREATE TABLE IF NOT EXISTS device_snapshots (
	device_id INTEGER NOT NULL AUTO_INCREMENT, 
	snapshot_json JSON NOT NULL, 
	received_at DATETIME NOT NULL, 
	PRIMARY KEY (device_id)
)

;


CREATE TABLE IF NOT EXISTS operator_audit (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	actor VARCHAR(100) NOT NULL, 
	action VARCHAR(100) NOT NULL, 
	target VARCHAR(150) NOT NULL, 
	details JSON NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
)

;


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


CREATE TABLE IF NOT EXISTS players (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	first_name VARCHAR(80) NOT NULL, 
	last_name VARCHAR(80) NOT NULL, 
	display_name VARCHAR(100) NOT NULL, 
	nickname VARCHAR(80) NOT NULL, 
	gender VARCHAR(30) NOT NULL, 
	skill_rating FLOAT, 
	rating_type VARCHAR(30) NOT NULL, 
	games_played INTEGER NOT NULL, 
	wins INTEGER NOT NULL, 
	losses INTEGER NOT NULL, 
	status VARCHAR(20) NOT NULL, 
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


CREATE TABLE IF NOT EXISTS schema_migrations (
	version INTEGER NOT NULL AUTO_INCREMENT, 
	applied_at DATETIME NOT NULL, 
	PRIMARY KEY (version)
)

;


CREATE TABLE IF NOT EXISTS system_settings (
	`key` VARCHAR(80) NOT NULL, 
	value_json JSON NOT NULL, 
	PRIMARY KEY (`key`)
)

;


CREATE TABLE IF NOT EXISTS matches (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	court_id INTEGER NOT NULL, 
	match_type VARCHAR(20) NOT NULL, 
	scoring_type VARCHAR(20) NOT NULL, 
	points_to_win INTEGER NOT NULL, 
	win_by INTEGER NOT NULL, 
	best_of INTEGER NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	settings_json JSON NOT NULL, 
	state_json JSON, 
	notes TEXT NOT NULL, 
	starting_side VARCHAR(10) NOT NULL, 
	started_at DATETIME, 
	ended_at DATETIME, 
	winner_team VARCHAR(1), 
	matchmaking_mode VARCHAR(30) NOT NULL DEFAULT 'LEGACY_WEIGHTED', 
	session_id INTEGER, 
	matchmaking_json JSON, 
	queue_groups_json JSON, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(court_id) REFERENCES courts (id), 
	FOREIGN KEY(session_id) REFERENCES play_sessions (id)
)

;

CREATE INDEX ix_matches_status ON matches (status);


CREATE TABLE IF NOT EXISTS phone_access (
	id VARCHAR(64) NOT NULL, 
	court_id INTEGER NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	expires_at DATETIME NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(court_id) REFERENCES courts (id)
)

;


CREATE TABLE IF NOT EXISTS player_checkins (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	player_id INTEGER NOT NULL, 
	checkin_time DATETIME NOT NULL, 
	checkout_time DATETIME, 
	status VARCHAR(20) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(player_id) REFERENCES players (id)
)

;

CREATE INDEX ix_player_checkins_player_id ON player_checkins (player_id);


CREATE TABLE IF NOT EXISTS scoreboard_devices (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	device_name VARCHAR(100) NOT NULL, 
	court_id INTEGER NOT NULL, 
	hostname VARCHAR(150) NOT NULL, 
	ip_address VARCHAR(50) NOT NULL, 
	bridge_url VARCHAR(200) NOT NULL, 
	last_seen_at DATETIME, 
	status VARCHAR(20) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (device_name), 
	UNIQUE (court_id), 
	FOREIGN KEY(court_id) REFERENCES courts (id)
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


CREATE TABLE IF NOT EXISTS games (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	match_id INTEGER NOT NULL, 
	game_number INTEGER NOT NULL, 
	team_a_score INTEGER NOT NULL, 
	team_b_score INTEGER NOT NULL, 
	winner_team VARCHAR(1), 
	started_at DATETIME, 
	ended_at DATETIME, 
	PRIMARY KEY (id), 
	UNIQUE (match_id, game_number), 
	FOREIGN KEY(match_id) REFERENCES matches (id)
)

;


CREATE TABLE IF NOT EXISTS match_players (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	match_id INTEGER NOT NULL, 
	player_id INTEGER NOT NULL, 
	team VARCHAR(1) NOT NULL, 
	position INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (match_id, player_id), 
	UNIQUE (match_id, team, position), 
	FOREIGN KEY(match_id) REFERENCES matches (id), 
	FOREIGN KEY(player_id) REFERENCES players (id)
)

;


CREATE TABLE IF NOT EXISTS player_queue (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	player_id INTEGER NOT NULL, 
	checkin_id INTEGER NOT NULL, 
	group_id INTEGER, 
	joined_at DATETIME NOT NULL, 
	position INTEGER NOT NULL, 
	priority INTEGER NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (player_id), 
	FOREIGN KEY(player_id) REFERENCES players (id), 
	FOREIGN KEY(checkin_id) REFERENCES player_checkins (id), 
	FOREIGN KEY(group_id) REFERENCES queue_groups (id)
)

;

CREATE INDEX ix_player_queue_position ON player_queue (position);


CREATE TABLE IF NOT EXISTS teams (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	match_id INTEGER NOT NULL, 
	side VARCHAR(1) NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (match_id, side), 
	FOREIGN KEY(match_id) REFERENCES matches (id)
)

;


CREATE TABLE IF NOT EXISTS match_events (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	event_id VARCHAR(36) NOT NULL, 
	match_id INTEGER NOT NULL, 
	game_id INTEGER, 
	sequence_no INTEGER NOT NULL, 
	event_type VARCHAR(40) NOT NULL, 
	team VARCHAR(1), 
	player_id INTEGER, 
	payload_json JSON NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (event_id), 
	UNIQUE (match_id, sequence_no), 
	FOREIGN KEY(match_id) REFERENCES matches (id), 
	FOREIGN KEY(game_id) REFERENCES games (id), 
	FOREIGN KEY(player_id) REFERENCES players (id)
)

;

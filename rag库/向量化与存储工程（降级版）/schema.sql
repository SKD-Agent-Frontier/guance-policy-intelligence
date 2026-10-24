-- SQLite本地权威来源与发布结构；禁止直接执行服务端PostgreSQL语法。

PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,planid TEXT NOT NULL,source_hash TEXT NOT NULL,data TEXT NOT NULL,data_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS objects(id TEXT PRIMARY KEY,document_id TEXT REFERENCES documents(id),kind TEXT NOT NULL,data TEXT NOT NULL,data_hash TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS object_document_kind ON objects(document_id,kind);
CREATE TABLE IF NOT EXISTS children(id TEXT PRIMARY KEY REFERENCES objects(id),parent_id TEXT NOT NULL REFERENCES objects(id));
CREATE TABLE IF NOT EXISTS links(id TEXT PRIMARY KEY REFERENCES objects(id),source_id TEXT NOT NULL REFERENCES objects(id),target_id TEXT REFERENCES objects(id),status TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS link_source ON links(source_id,status);
CREATE TABLE IF NOT EXISTS releases(id TEXT PRIMARY KEY,status TEXT NOT NULL,manifest TEXT NOT NULL,validation TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS representations(id TEXT PRIMARY KEY,source_id TEXT NOT NULL REFERENCES objects(id),document_id TEXT NOT NULL REFERENCES documents(id),point_id TEXT UNIQUE NOT NULL,input_hash TEXT NOT NULL,profile_id TEXT NOT NULL,record TEXT NOT NULL,text TEXT,description TEXT,data_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS members(generation TEXT REFERENCES releases(id),representation_id TEXT REFERENCES representations(id),payload TEXT NOT NULL,PRIMARY KEY(generation,representation_id));
CREATE TABLE IF NOT EXISTS tasks(generation TEXT REFERENCES releases(id),input_hash TEXT,status TEXT NOT NULL,error TEXT,PRIMARY KEY(generation,input_hash));
CREATE TABLE IF NOT EXISTS runtime(singleton INTEGER PRIMARY KEY CHECK(singleton=1),generation TEXT NOT NULL REFERENCES releases(id),revision INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS activation_history(id INTEGER PRIMARY KEY,generation TEXT NOT NULL,revision INTEGER NOT NULL,at TEXT NOT NULL);

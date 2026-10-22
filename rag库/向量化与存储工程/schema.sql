-- search_path由Python安全设置为每版独立schema，不把用户字符串拼入SQL。
CREATE TABLE IF NOT EXISTS document_versions (
  document_id text PRIMARY KEY, document_version_id text UNIQUE NOT NULL,
  planid text NOT NULL, source_hash text NOT NULL, snapshot_path text NOT NULL,
  data jsonb NOT NULL, data_hash text NOT NULL, UNIQUE(planid,source_hash)
);
CREATE TABLE IF NOT EXISTS source_objects (
  object_id text PRIMARY KEY, document_id text REFERENCES document_versions(document_id),
  kind text NOT NULL, data jsonb NOT NULL, data_hash text NOT NULL
);
CREATE INDEX IF NOT EXISTS source_objects_document ON source_objects(document_id,kind);
CREATE TABLE IF NOT EXISTS parents (
  parent_id text PRIMARY KEY, document_id text NOT NULL REFERENCES document_versions(document_id),
  source_start bigint NOT NULL, source_end bigint NOT NULL, data jsonb NOT NULL, data_hash text NOT NULL,
  UNIQUE(parent_id,document_id), CHECK(source_start>=0 AND source_end>source_start)
);
CREATE TABLE IF NOT EXISTS chunks (
  chunk_id text PRIMARY KEY, parent_id text NOT NULL, document_id text NOT NULL,
  status text NOT NULL, data jsonb NOT NULL, data_hash text NOT NULL,
  FOREIGN KEY(parent_id,document_id) REFERENCES parents(parent_id,document_id)
);
CREATE INDEX IF NOT EXISTS chunks_parent ON chunks(parent_id,document_id);
CREATE TABLE IF NOT EXISTS relations (
  relation_id text PRIMARY KEY, document_id text NOT NULL REFERENCES document_versions(document_id),
  source_id text NOT NULL REFERENCES source_objects(object_id),
  target_id text REFERENCES source_objects(object_id), resolution_status text NOT NULL,
  data jsonb NOT NULL, data_hash text NOT NULL
);
CREATE TABLE IF NOT EXISTS media_assets (
  asset_id text PRIMARY KEY, sha256 text NOT NULL UNIQUE, data jsonb NOT NULL, data_hash text NOT NULL
);
CREATE TABLE IF NOT EXISTS media_occurrences (
  occurrence_id text PRIMARY KEY, document_id text NOT NULL REFERENCES document_versions(document_id),
  asset_id text REFERENCES media_assets(asset_id), data jsonb NOT NULL, data_hash text NOT NULL
);
CREATE TABLE IF NOT EXISTS visual_extractions (
  extraction_id text PRIMARY KEY, asset_id text NOT NULL REFERENCES media_assets(asset_id),
  profile_id text NOT NULL, status text NOT NULL, data jsonb NOT NULL, data_hash text NOT NULL
);
CREATE TABLE IF NOT EXISTS visual_regions (
  region_id text PRIMARY KEY, asset_id text NOT NULL REFERENCES media_assets(asset_id),
  extraction_id text NOT NULL REFERENCES visual_extractions(extraction_id),
  x0 integer NOT NULL, y0 integer NOT NULL, x1 integer NOT NULL, y1 integer NOT NULL,
  width integer NOT NULL, height integer NOT NULL, data jsonb NOT NULL, data_hash text NOT NULL,
  CHECK(0<=x0 AND x0<x1 AND x1<=width AND 0<=y0 AND y0<y1 AND y1<=height)
);
CREATE TABLE IF NOT EXISTS visual_parents (
  parent_id text PRIMARY KEY, occurrence_id text NOT NULL REFERENCES media_occurrences(occurrence_id),
  document_id text NOT NULL REFERENCES document_versions(document_id), data jsonb NOT NULL, data_hash text NOT NULL,
  UNIQUE(parent_id,document_id)
);
CREATE TABLE IF NOT EXISTS visual_chunks (
  chunk_id text PRIMARY KEY, parent_id text NOT NULL, document_id text NOT NULL,
  region_id text NOT NULL REFERENCES visual_regions(region_id),
  data jsonb NOT NULL, data_hash text NOT NULL,
  FOREIGN KEY(parent_id,document_id) REFERENCES visual_parents(parent_id,document_id)
);
CREATE TABLE IF NOT EXISTS embedding_profiles (
  profile_id text PRIMARY KEY, data jsonb NOT NULL, data_hash text NOT NULL
);
CREATE TABLE IF NOT EXISTS embedding_inputs (
  input_hash text PRIMARY KEY, data jsonb NOT NULL, data_hash text NOT NULL
);
CREATE TABLE IF NOT EXISTS embedding_records (
  representation_id text PRIMARY KEY, document_id text NOT NULL REFERENCES document_versions(document_id),
  source_id text NOT NULL REFERENCES source_objects(object_id), source_type text NOT NULL,
  representation_type text NOT NULL CHECK(representation_type IN ('text','image','image_text')),
  profile_id text NOT NULL REFERENCES embedding_profiles(profile_id),
  input_hash text NOT NULL REFERENCES embedding_inputs(input_hash), point_id uuid NOT NULL UNIQUE,
  data jsonb NOT NULL, data_hash text NOT NULL
);
CREATE TABLE IF NOT EXISTS embedding_cache (
  profile_id text NOT NULL REFERENCES embedding_profiles(profile_id), input_hash text NOT NULL REFERENCES embedding_inputs(input_hash),
  checksum text NOT NULL, path text NOT NULL, dimensions integer NOT NULL CHECK(dimensions=4096),
  data jsonb NOT NULL, data_hash text NOT NULL, PRIMARY KEY(profile_id,input_hash)
);
CREATE TABLE IF NOT EXISTS index_releases (
  generation_id text PRIMARY KEY, prepared_id text NOT NULL, collection text NOT NULL UNIQUE,
  profile_id text NOT NULL REFERENCES embedding_profiles(profile_id),
  status text NOT NULL CHECK(status IN ('building','validating','ready','failed')),
  manifest jsonb NOT NULL, counts jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(), validated_at timestamptz
);
CREATE TABLE IF NOT EXISTS release_documents (
  generation_id text NOT NULL REFERENCES index_releases(generation_id),
  document_id text NOT NULL REFERENCES document_versions(document_id),
  metadata_snapshot jsonb NOT NULL, PRIMARY KEY(generation_id,document_id)
);
CREATE TABLE IF NOT EXISTS release_representations (
  generation_id text NOT NULL REFERENCES index_releases(generation_id),
  representation_id text NOT NULL REFERENCES embedding_records(representation_id),
  point_id uuid NOT NULL, payload jsonb NOT NULL, payload_hash text NOT NULL,
  PRIMARY KEY(generation_id,representation_id), UNIQUE(generation_id,point_id)
);
CREATE TABLE IF NOT EXISTS release_exclusions (
  generation_id text NOT NULL REFERENCES index_releases(generation_id),
  source_id text NOT NULL REFERENCES source_objects(object_id), reason text NOT NULL,
  data jsonb NOT NULL, PRIMARY KEY(generation_id,source_id,reason)
);
CREATE TABLE IF NOT EXISTS release_warnings (
  generation_id text NOT NULL REFERENCES index_releases(generation_id),
  source_id text NOT NULL REFERENCES source_objects(object_id), reason text NOT NULL,
  data jsonb NOT NULL, PRIMARY KEY(generation_id,source_id,reason)
);
CREATE TABLE IF NOT EXISTS index_tasks (
  generation_id text NOT NULL REFERENCES index_releases(generation_id), task_key text NOT NULL,
  status text NOT NULL CHECK(status IN ('pending','running','ready','failed')),
  attempts integer NOT NULL DEFAULT 0, error text, updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(generation_id,task_key)
);
CREATE TABLE IF NOT EXISTS dedup_pairs (
  generation_id text NOT NULL REFERENCES index_releases(generation_id), pair_id text NOT NULL,
  data jsonb NOT NULL, PRIMARY KEY(generation_id,pair_id)
);
CREATE TABLE IF NOT EXISTS dedup_groups (
  generation_id text NOT NULL REFERENCES index_releases(generation_id), group_id text NOT NULL,
  data jsonb NOT NULL, PRIMARY KEY(generation_id,group_id)
);
CREATE TABLE IF NOT EXISTS rag_runtime (
  knowledge_base_id text PRIMARY KEY, active_generation_id text REFERENCES index_releases(generation_id),
  revision bigint NOT NULL DEFAULT 0, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS activation_history (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, knowledge_base_id text NOT NULL,
  generation_id text NOT NULL REFERENCES index_releases(generation_id),
  previous_generation_id text, reason text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);

-- stage-cloud-04: D1 initial schema (plan §14-28)
-- 边界校准（docs/CLOUD_BOUNDARY_CALIBRATION.md）：
--   * 密码/Cookie 只入 order_credentials，encryption_version 仅允许 enc-v2
--     （AES-256-GCM）；enc:v1 的解密与重加密发生在本地迁移工具，D1 无明文无 v1。
--   * orders.account 为第三方登录账号（明文，低敏，供中央"按账号查单"语义使用，
--     对应本地 2026-09-22 新增的 /qacc 能力）；密码绝不入 orders。
-- 所有语句 IF NOT EXISTS，保证重放幂等。

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    password_scheme TEXT NOT NULL DEFAULT 'legacy-hmac-v1',
    role TEXT NOT NULL DEFAULT 'user',
    status TEXT NOT NULL DEFAULT 'active',
    registration_source TEXT NOT NULL DEFAULT 'manual',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    revoked_at INTEGER,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    config_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    product_id TEXT,
    product_code TEXT NOT NULL,
    platform TEXT NOT NULL,
    courses TEXT NOT NULL DEFAULT '',
    account TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    note TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'cloud',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS order_credentials (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    credential_type TEXT NOT NULL,
    ciphertext TEXT,
    r2_object_key TEXT,
    encryption_version TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY(order_id) REFERENCES orders(id)
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    execution_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    priority INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL DEFAULT '{}',
    required_capabilities_json TEXT NOT NULL DEFAULT '[]',
    executor_id TEXT,
    lease_id TEXT,
    lease_expires_at INTEGER,
    attempt_no INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 2,
    next_attempt_at INTEGER,
    started_at INTEGER,
    finished_at INTEGER,
    last_heartbeat_at INTEGER,
    result_code INTEGER,
    error_code TEXT,
    error_message TEXT,
    result_json TEXT,
    idempotency_key TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY(order_id) REFERENCES orders(id)
);

CREATE TABLE IF NOT EXISTS task_attempts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    executor_id TEXT,
    lease_id TEXT,
    status TEXT NOT NULL,
    started_at INTEGER,
    finished_at INTEGER,
    last_heartbeat_at INTEGER,
    exit_code INTEGER,
    error_code TEXT,
    error_message TEXT,
    result_json TEXT,
    stdout_object_key TEXT,
    stderr_object_key TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY(task_id) REFERENCES tasks(id),
    UNIQUE(task_id, attempt_no)
);

CREATE TABLE IF NOT EXISTS executor_nodes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    execution_path TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    version TEXT NOT NULL,
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'offline',
    last_seen_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    r2_object_key TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER,
    FOREIGN KEY(task_id) REFERENCES tasks(id),
    FOREIGN KEY(attempt_id) REFERENCES task_attempts(id)
);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    actor_type TEXT NOT NULL,
    actor_id TEXT,
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    scope TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    response_hash TEXT,
    response_json TEXT,
    created_at INTEGER NOT NULL,
    expires_at INTEGER,
    PRIMARY KEY(scope, actor_id, idempotency_key)
);

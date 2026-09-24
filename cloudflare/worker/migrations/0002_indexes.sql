-- stage-cloud-04: core D1 indexes (plan §29)
-- 额外：idx_orders_account 支撑"按账号查单"（本地 /qacc 语义的中央对应物）。
-- 全部 IF NOT EXISTS，重放幂等。

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username
ON users(username);

CREATE INDEX IF NOT EXISTS idx_sessions_user
ON auth_sessions(user_id);

CREATE INDEX IF NOT EXISTS idx_sessions_expire
ON auth_sessions(expires_at);

CREATE INDEX IF NOT EXISTS idx_orders_user_created
ON orders(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_orders_status_created
ON orders(status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_orders_account
ON orders(account);

CREATE INDEX IF NOT EXISTS idx_tasks_queue
ON tasks(execution_path, status, priority DESC, created_at);

CREATE INDEX IF NOT EXISTS idx_tasks_executor_status
ON tasks(executor_id, status);

CREATE INDEX IF NOT EXISTS idx_tasks_lease
ON tasks(status, lease_expires_at);

CREATE INDEX IF NOT EXISTS idx_attempts_task
ON task_attempts(task_id, attempt_no DESC);

CREATE INDEX IF NOT EXISTS idx_executor_path_status
ON executor_nodes(execution_path, status);

CREATE INDEX IF NOT EXISTS idx_audit_entity
ON audit_events(entity_type, entity_id, created_at DESC);

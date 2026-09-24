/**
 * stage-cloud-31 — §71 executors/task-dispatch：调度态 → D1 持久真相的镜像。
 * 自 executor-api.ts 拆出；ack/start/complete 成功后由 API 层调用。
 */

import type { Env } from "../auth/auth-service";

/** DO 是调度态、D1 是持久真相：ack/complete 转发成功后镜像写 D1（stage-cloud-16b）。 */
export async function mirrorTaskState(
  env: Env,
  taskId: string,
  leaseId: string,
  executorId: string,
  status: string,
  errorCode?: string,
  attemptId?: string,
): Promise<void> {
  const now = Date.now();
  const task = await env.DB.prepare("SELECT attempt_no, order_id, task_type FROM tasks WHERE id=?")
    .bind(taskId)
    .first<{ attempt_no: number; order_id: string; task_type: string }>();
  if (!task) return; // 非 D1 登记的任务（纯 DO 测试）无需镜像
  const attemptId2 = attemptId || `${taskId}#${task.attempt_no}`;
  if (status === "running") {
    await env.DB.batch([
      env.DB.prepare(
        "UPDATE tasks SET status='running', executor_id=?, lease_id=?, started_at=COALESCE(started_at,?), last_heartbeat_at=?, updated_at=? WHERE id=?",
      ).bind(executorId, leaseId, now, now, now, taskId),
      env.DB.prepare(
        `INSERT INTO task_attempts(id,task_id,attempt_no,executor_id,lease_id,status,started_at,last_heartbeat_at,created_at,updated_at)
         VALUES(?,?,?,?,?,'running',?,?,?,?)
         ON CONFLICT(id) DO UPDATE SET status='running', last_heartbeat_at=excluded.last_heartbeat_at, updated_at=excluded.updated_at`,
      ).bind(attemptId2, taskId, task.attempt_no, executorId, leaseId, now, now, now, now),
    ]);
  } else if (["succeeded", "failed", "retry_wait"].includes(status)) {
    const finished = ["succeeded", "failed"].includes(status);
    // stage-cloud-28：辅助任务（如 chaoxing.courses 查课、demo.echo）完成不终结订单 ——
    // 订单终态只由主任务（*.run）驱动，否则查完课订单就变 succeeded 无法再入队。
    // stage-cloud-34 修复：白名单驱动（*.run），非 .courses 的辅助任务（demo 等）
    // 不再误置订单终态 —— 三路 demo E2E 实测发现的回归。
    const auxiliary = !task.task_type.endsWith(".run");
    await env.DB.batch([
      env.DB.prepare(
        "UPDATE tasks SET status=?, error_code=COALESCE(?,error_code), finished_at=COALESCE(finished_at,?), updated_at=? WHERE id=?",
      ).bind(status, errorCode ?? null, finished ? now : null, now, taskId),
      env.DB.prepare(
        `UPDATE task_attempts SET status=?, finished_at=COALESCE(finished_at,?), error_code=COALESCE(?,error_code), updated_at=?
         WHERE task_id=? AND attempt_no=(SELECT attempt_no FROM tasks WHERE id=?) AND status IN ('running','leased')`,
      ).bind(status === "retry_wait" ? "failed" : status, finished ? now : null, errorCode ?? null, now, taskId, taskId),
      ...(finished && !auxiliary
        ? [
            env.DB.prepare("UPDATE orders SET status=?, control='', updated_at=? WHERE id=?").bind(
              status,
              now,
              task.order_id,
            ),
          ]
        : []),
    ]);
  }
}


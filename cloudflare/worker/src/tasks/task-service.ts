/**
 * stage-cloud-31 — §71 tasks/task-service：D1 task/attempt 链维护。
 *
 * 自 storage/artifacts.ts 拆出（首次工件上传时把 DO 调度态落库为持久真相）。
 */

import type { Env } from "../auth/auth-service";

export async function upsertTaskChain(
  env: Env,
  args: {
    taskId: string;
    orderId: string;
    attemptNo: number;
    executorId: string;
    leaseId: string;
    executionPath: string;
  },
): Promise<void> {
  const now = Date.now();
  await env.DB.prepare(
    `INSERT INTO tasks(id,order_id,task_type,execution_path,status,executor_id,lease_id,lease_expires_at,attempt_no,created_at,updated_at,last_heartbeat_at)
     VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
     ON CONFLICT(id) DO UPDATE SET last_heartbeat_at=excluded.last_heartbeat_at, updated_at=excluded.updated_at`,
  )
    .bind(
      args.taskId,
      args.orderId,
      "external.task", // DO 队列回写首次落库时的占位类型
      args.executionPath,
      "running",
      args.executorId,
      args.leaseId,
      null,
      args.attemptNo,
      now,
      now,
      now,
    )
    .run();
  const attemptId = `${args.taskId}#${args.attemptNo}`;
  await env.DB.prepare(
    `INSERT INTO task_attempts(id,task_id,attempt_no,executor_id,lease_id,status,started_at,last_heartbeat_at,created_at,updated_at)
     VALUES(?,?,?,?,?,?,?,?,?,?)
     ON CONFLICT(id) DO UPDATE SET last_heartbeat_at=excluded.last_heartbeat_at, updated_at=excluded.updated_at`,
  )
    .bind(attemptId, args.taskId, args.attemptNo, args.executorId, args.leaseId, "running", now, now, now, now)
    .run();
}

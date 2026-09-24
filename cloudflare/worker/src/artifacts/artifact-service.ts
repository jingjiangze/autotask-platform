/**
 * stage-cloud-14 — 工件上传/下载（计划 §48/§90）
 *
 * 上传：POST /api/executor/v1/artifacts（raw body 即内容）
 *   query: executor_id, execution_path, task_id, lease_id, artifact_type, content_type
 *   流程：三元认证 → DO verify-lease → upsert D1 tasks/task_attempts
 *         （D1 是持久真相，DO 调度态在此回写）→ R2 put（sha256 键）→ artifacts 记录
 *
 * 下载：GET /api/v1/orders/:orderId/artifacts/:artifactId
 *   仅订单所有者或 admin；R2 流式返回。
 *
 * 大小红线：Free R2 单对象上限远大于任务产物；这里限定 ≤ 8MB 防滥用。
 */

import type { Env } from "../auth/auth-service";
import { authenticateExecutor } from "../executors/executor-auth";
import { errorResponse } from "../errors";

const MAX_ARTIFACT_BYTES = 8 * 1024 * 1024;

interface VerifyVerdict {
  valid: boolean;
  reason?: string;
  order_id?: string;
  attempt_no?: number;
}

async function verifyLease(
  env: Env,
  executionPath: string,
  taskId: string,
  leaseId: string,
): Promise<VerifyVerdict> {
  const ns = env.COORDINATOR;
  const stub = ns.get(ns.idFromName(executionPath));
  const res = await stub.fetch("https://do/message", {
    method: "POST",
    body: JSON.stringify({ type: "verify-lease", task_id: taskId, lease_id: leaseId }),
  });
  return (await res.json()) as VerifyVerdict;
}

async function upsertTaskChain(
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

export async function uploadArtifact(env: Env, request: Request): Promise<Response> {
  const url = new URL(request.url);
  const q = url.searchParams;
  const executorId = q.get("executor_id") ?? "";
  const executionPath = q.get("execution_path") ?? "";
  const taskId = q.get("task_id") ?? "";
  const leaseId = q.get("lease_id") ?? "";
  const artifactType = q.get("artifact_type") ?? "log";
  const contentType = q.get("content_type") ?? "application/octet-stream";
  if (!executorId || !executionPath || !taskId || !leaseId) {
    return errorResponse(400, "VALIDATION_FAILED");
  }
  const auth = await authenticateExecutor(env, request, {
    executor_id: executorId,
    execution_path: executionPath,
  });
  if ("code" in auth) return errorResponse(auth.status, auth.code);

  const verdict = await verifyLease(env, executionPath, taskId, leaseId);
  if (!verdict.valid || !verdict.order_id || typeof verdict.attempt_no !== "number") {
    return errorResponse(403, "LEASE_INVALID");
  }

  const body = await request.arrayBuffer();
  if (body.byteLength === 0) return errorResponse(400, "VALIDATION_FAILED");
  if (body.byteLength > MAX_ARTIFACT_BYTES) return errorResponse(413, "VALIDATION_FAILED");

  const digest = await crypto.subtle.digest("SHA-256", body);
  const sha256 = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
  const objectKey = `artifacts/${verdict.order_id}/${taskId}/${sha256}`;
  await env.ARTIFACTS.put(objectKey, body, {
    httpMetadata: { contentType },
  });

  await upsertTaskChain(env, {
    taskId,
    orderId: verdict.order_id,
    attemptNo: verdict.attempt_no,
    executorId,
    leaseId,
    executionPath,
  });

  const attemptId = `${taskId}#${verdict.attempt_no}`;
  const artifactId = crypto.randomUUID();
  await env.DB.prepare(
    "INSERT INTO artifacts(id,task_id,attempt_id,artifact_type,r2_object_key,content_type,size_bytes,sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
  )
    .bind(artifactId, taskId, attemptId, artifactType, objectKey, contentType, body.byteLength, sha256, Date.now())
    .run();
  await env.DB.prepare(
    "INSERT INTO audit_events(id,actor_type,actor_id,event_type,entity_type,entity_id,created_at) VALUES(?,?,?,?,?,?,?)",
  )
    .bind(crypto.randomUUID(), "executor", executorId, "ARTIFACT_UPLOADED", "task", taskId, Date.now())
    .run();
  return Response.json(
    { ok: true, artifact_id: artifactId, size_bytes: body.byteLength, sha256 },
    { status: 201 },
  );
}

export async function downloadArtifact(
  env: Env,
  request: Request,
  orderId: string,
  artifactId: string,
): Promise<Response> {
  const { getSessionUser } = await import("../auth/session-service");
  const session = await getSessionUser(env.DB, request);
  if (!session) return errorResponse(401, "AUTH_REQUIRED");
  const order = await env.DB.prepare("SELECT user_id FROM orders WHERE id = ?")
    .bind(orderId)
    .first<{ user_id: string }>();
  if (!order) return errorResponse(404, "NOT_FOUND");
  if (order.user_id !== session.id && session.role !== "admin") {
    return errorResponse(404, "NOT_FOUND");
  }
  const row = await env.DB.prepare(
    "SELECT a.r2_object_key, a.content_type FROM artifacts a JOIN tasks t ON a.task_id = t.id WHERE a.id = ? AND t.order_id = ?",
  )
    .bind(artifactId, orderId)
    .first<{ r2_object_key: string; content_type: string }>();
  if (!row) return errorResponse(404, "NOT_FOUND");
  const obj = await env.ARTIFACTS.get(row.r2_object_key);
  if (!obj) return errorResponse(404, "NOT_FOUND");
  return new Response(obj.body, {
    headers: { "Content-Type": row.content_type },
  });
}

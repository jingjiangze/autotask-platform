/**
 * stage-cloud-05 — 中央平台口令哈希（计划 §17 / 边界校准 §1.3）
 *
 * 方案：pbkdf2-sha256-v1（WebCrypto 原生 PBKDF2，纯 native 实现）。
 * ⚠ 计划 §17 红线：新 KDF 上真实 Workers 前必须单独做 CPU benchmark
 *   （Free 层单次 invocation 10ms），iterations 由部署侧 env 校准。
 * 本地 legacy-hmac-v1（HMAC(SECRET,"wk"+pw)）依赖本地 SECRET 文件，
 * 云端无法验证 —— 该 scheme 的迁移验证收敛在本地迁移工具（stage-cloud-25）。
 */

export const SCHEME = "pbkdf2-sha256-v1";
export const DEFAULT_ITERATIONS = 50_000;

function b64(buf: ArrayBuffer | Uint8Array): string {
  const bytes = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s);
}

function unb64(s: string): Uint8Array {
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function deriveBits(
  password: string,
  salt: Uint8Array,
  iterations: number,
): Promise<Uint8Array> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(password),
    "PBKDF2",
    false,
    ["deriveBits"],
  );
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", salt: salt as BufferSource, iterations, hash: "SHA-256" },
    key,
    256,
  );
  return new Uint8Array(bits);
}

export function isSupportedScheme(stored: string): boolean {
  return stored.startsWith(`${SCHEME}$`);
}

export async function hashPassword(password: string): Promise<string> {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const bits = await deriveBits(password, salt, DEFAULT_ITERATIONS);
  return `${SCHEME}$${DEFAULT_ITERATIONS}$${b64(salt)}$${b64(bits)}`;
}

export async function verifyPassword(
  password: string,
  stored: string,
): Promise<boolean> {
  const parts = stored.split("$");
  if (parts.length !== 4 || parts[0] !== SCHEME) return false;
  const iterations = Number.parseInt(parts[1], 10);
  if (!Number.isFinite(iterations) || iterations <= 0 || iterations > 10_000_000) {
    return false;
  }
  let salt: Uint8Array;
  let expected: Uint8Array;
  try {
    salt = unb64(parts[2]);
    expected = unb64(parts[3]);
  } catch {
    return false;
  }
  const bits = await deriveBits(password, salt, iterations);
  if (bits.length !== expected.length) return false;
  let diff = 0;
  for (let i = 0; i < bits.length; i++) diff |= bits[i]! ^ expected[i]!;
  return diff === 0;
}

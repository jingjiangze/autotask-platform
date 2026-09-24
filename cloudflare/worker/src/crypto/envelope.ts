/**
 * stage-cloud-31 — §71 crypto/envelope：enc-v2 信封加解密（AES-256-GCM）。
 *
 * 自 storage/credentials.ts 拆出（plan §21）：凭据密文格式与密钥装载
 * 只在此处定义；storage/credentials.ts 负责业务流（store/release）。
 *
 * 格式：enc-v2$<b64(12B nonce)>$<b64(ciphertext+GCM tag)>
 *   - key：env.CREDENTIAL_KEY（base64 32B）→ AES-256-GCM；AAD 绑定上下文标签
 *   - GCM tag 提供篡改检测；解密失败一律 CREDENTIAL_DECRYPT_FAILED，不返回部分明文
 */

import type { Env } from "../auth/auth-service";

export const ENC_VERSION = "enc-v2";
const AAD = new TextEncoder().encode("autotask.order_credentials.v2");

function b64encode(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s);
}

function b64decode(s: string): Uint8Array {
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function importKey(env: Env): Promise<CryptoKey> {
  const raw = env.CREDENTIAL_KEY;
  if (!raw) throw new Error("CREDENTIAL_KEY not configured");
  const bytes = b64decode(raw);
  if (bytes.length !== 32) throw new Error("CREDENTIAL_KEY must decode to 32 bytes");
  return crypto.subtle.importKey("raw", bytes, "AES-GCM", false, ["encrypt", "decrypt"]);
}

export async function encryptCredential(
  env: Env,
  plaintext: string,
): Promise<{ ciphertext: string; encryption_version: string }> {
  const key = await importKey(env);
  const nonce = crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(
    await crypto.subtle.encrypt(
      { name: "AES-GCM", iv: nonce, additionalData: AAD, tagLength: 128 },
      key,
      new TextEncoder().encode(plaintext),
    ),
  );
  return {
    ciphertext: `${ENC_VERSION}$${b64encode(nonce)}$${b64encode(ct)}`,
    encryption_version: ENC_VERSION,
  };
}

export async function decryptCredential(
  env: Env,
  stored: string,
): Promise<string> {
  const parts = stored.split("$");
  if (parts.length !== 3 || parts[0] !== ENC_VERSION) {
    throw new Error("BAD_FORMAT");
  }
  const key = await importKey(env);
  try {
    const pt = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: b64decode(parts[1]!), additionalData: AAD, tagLength: 128 },
      key,
      b64decode(parts[2]!),
    );
    return new TextDecoder().decode(pt);
  } catch {
    // GCM 校验失败（篡改/密钥错/损坏）——不区分原因，不给部分明文
    throw new Error("CREDENTIAL_DECRYPT_FAILED");
  }
}

/**
 * stage-cloud-31 — §71 crypto/hashing：共享哈希工具。
 *
 * executor token（§39/§40）与 session token（§15）都只存 SHA-256 hash；
 * 统一在此实现，禁止各处手写。
 */

export async function sha256Hex(input: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(input));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

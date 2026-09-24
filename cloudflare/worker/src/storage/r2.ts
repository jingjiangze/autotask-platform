/**
 * stage-cloud-31 — §71 storage/r2：R2 对象访问层（plan §30/§31/§67）。
 *
 * 统一对象键布局与读写；D1 只存 metadata（artifacts 表），实际字节在此层。
 * 所有 Bucket 私有；上传走短时授权（presign）或租约门控直传。
 */

export const MAX_ARTIFACT_BYTES = 8 * 1024 * 1024;

/** §30：artifacts/{order_id}/{task_id}/{sha256} 私有对象键。 */
export function artifactObjectKey(orderId: string, taskId: string, sha256: string): string {
  return `artifacts/${orderId}/${taskId}/${sha256}`;
}

export async function putArtifact(
  bucket: R2Bucket,
  key: string,
  body: ArrayBuffer,
  contentType: string,
): Promise<void> {
  await bucket.put(key, body, { httpMetadata: { contentType } });
}

export async function getArtifact(bucket: R2Bucket, key: string): Promise<R2ObjectBody | null> {
  return bucket.get(key);
}

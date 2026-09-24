// stage-cloud-31：contracts/ 目录位于 worker 包之外（§73 双端共享契约），
// tsconfig include 无法覆盖跨包 JSON，这里做环境模块声明。
declare module "*contracts/executor-protocol.json" {
  const value: Record<string, unknown>;
  export default value;
}
declare module "*contracts/task-schema.json" {
  const value: Record<string, unknown>;
  export default value;
}
declare module "*contracts/result-schema.json" {
  const value: Record<string, unknown>;
  export default value;
}
declare module "*contracts/error-codes.json" {
  const value: Record<string, unknown>;
  export default value;
}

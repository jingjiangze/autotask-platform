# BASELINE_REPORT — stage-cloud-02 本地功能基线冻结

- **日期**：2026-09-22
- **被测对象**：`order_platform.py` @ stage-cloud 分支 `743452a`（字节级复制到隔离 APP_DIR，SHA-256 校验一致后由子进程启动真实 waitress 服务）
- **命令**：`python -m pytest tests/baseline -v`
- **结果**：**12 passed**（5.8s，复跑两次均 PASS）
- **回归**：`pytest tests/cloud_test` → **254 passed**（既有契约套件不受影响）

## 隔离方案（为何可信）

真实平台 `import` 即执行 init_db / 迁移 / recover_stale_orders 并启动 4 个守护线程，
直接 import 会触碰生产库。基线将 `order_platform.py` **字节级复制**到 pytest 临时目录，
以其为 APP_DIR 启动真实代码（waitress 子进程）：DB / secrets / 订单目录全部落在临时目录，
对 `D:\web` 生产环境零接触。隔离手段全部使用平台真实机制（`paused` 设置、`last_backup`），
**无 Mock、无 sleep 桩替代真实逻辑**。

## 覆盖矩阵（禁止假 PASS：未真实执行的明确标注）

| 计划验收项 | 用例 | 状态 |
|---|---|---|
| AUTH 注册 | `test_register_login_logout_wk_token`（成功）、`test_register_wrong_reg_code`、`test_register_short_password` | **PASS** |
| AUTH 登录/登出 | 同上（错误口令 200 错误页不发 token；logout 后保护路由 302） | **PASS** |
| AUTH 会话形态 | wk_token=uid:SHA256hex，uid 与 users 表一致（无过期/无吊销的 Local Ground Truth 冻结） | **PASS** |
| ORDER 下单 | `test_buy_creates_order_with_encrypted_password`（密码 enc:v1 密文入库、详情页无明文/密文） | **PASS** |
| ORDER 归属 | `test_my_orders_scoped_to_owner` | **PASS** |
| OWNERSHIP 越权 | `test_ownership_denied_for_other_user`（详情页+set_courses 双路径） | **PASS** |
| QUERY 访客查单 | `test_guest_query_limited_fields`（全号/前 8 位/<6 位/不存在；无账号无密码回显） | **PASS** |
| EXECUTION | `test_worker_claim_execute_classify`：真实 worker 认领 → 真实子进程 spawn → RollingLog 落盘 → rc 分类 → failed 收敛、attempt=1 不重试 | **PASS**（编排+真实进程；引擎文件缺失 = 真实引擎级失败路径） |
| EXECUTION 真实第三方 | 学习通/知到真实登录与执行 | **NOT AVAILABLE**（本基线不触碰第三方，遵守禁 Mock 规则；留待三路 E2E） |
| QR 状态机 | `test_restart_recovery_paths` 中 waiting_qr → canceled/expired（真实重启路径） | **PASS**（恢复语义） |
| QR 真实扫码 | 生成二维码/用户扫码/确认/cookies 回传 | **NOT AVAILABLE**（需真人扫码+第三方在线；留待 QR E2E，状态记 WAITING_MANUAL_VERIFICATION） |
| RECOVERY | `test_restart_recovery_paths`：waiting_qr→canceled；attempt<MAX_RETRY→pending 重新排队；attempt==MAX_RETRY→failed（真实 re-import 触发 recover_stale_orders） | **PASS** |
| ADMIN | `test_admin_gate`（非管理员拒绝页/403/匿名 302）、`test_admin_bootstrap_password_and_tune`（引导口令轮换 admin123 失效、设置持久化） | **PASS** |
| LIVE（长期行为） | order_watchdog 30s 周期、order_timeout_min 超时树杀、备份链 | **SKIP**（周期>基线时间预算；已有 `tests/test_spawn_timeout.py` 等人工演练脚本登记，不入 CI） |

## 已知限制

1. **注册限流预算**：注册接口 5 次/分钟/IP（内存态），全基线恰好消耗 5 次（3 次在 AUTH，
   2 次建共享用户）。新增用例若需注册必须复用 `conftest.users` 夹具。
2. **账号明文入库**是现状事实（baseline 断言留证），云化迁移时按
   `docs/CLOUD_BOUNDARY_CALIBRATION.md` §3.1 处理。
3. 隔离环境中引擎脚本故意缺失：EXECUTION 用例冻结的是"认领→spawn→分类→收敛"编排
   真实路径 + 真实引擎级失败路径，**不**证明真实刷课成功——那属于三路真实 E2E。

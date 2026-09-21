# CACHE_CLASSIFICATION — Cache 逐项判断（真实审计）

> 逐项判断"可再生 vs 恢复必需 vs 隐私"，不按目录名一刀切。

| 路径模式 | 类别 | 判断依据 | 处理 |
|---|---|---|---|
| orders\<id>\log.txt | 可再生（恢复非必需） | 引擎重跑可重建；但含诊断价值 | 明文不必要同步；如需保留历史可随目录 bundle（可选项） |
| orders\<id>\cookies.json | **E 恢复必需 + B 隐私** | 登录态，删除=重新登录 | 加密同步（文件 .enc） |
| orders\<id>\tmp\ temp | D 可再生 | 临时 | 不同步 |
| orders\<id>\work\ 工作产出 | 视内容：引擎中间产物一般可再生；如无业务依赖→D | - | 不同步 |
| orders\<id>\home\ | **B 隐私/E**（可能含浏览器/会话） | 用户 HOME 隔离环境 | 目录 bundle 加密 |
| orders\<id>\logs\（引擎 LOG_DIR） | D 可再生 | 日志 | 不同步 |
| cf\browser_profile\ | **B 隐私/E（登录态）** | 浏览器全部状态含 Cookies/Trust Tokens | **目录 bundle 加密（首期）** |
| _hist\_hikejs\_onlineweb_js | D 历史诊断（已过期） | 非业务依赖 | 不同步（或归档） |
| _archive_dev | B（含口令材料） | 隐私存档 | 加密归档 bundle（或明确不迁移） |
| __pycache__ | D | 可再生 | 排除 |
| static/tabler_pkg | A | 前端资源 | 明文同步 |
| orders\_query | C | 1h 临时 | 排除 |
| .workbuddy\memory | 工程记忆（非业务恢复必需） | - | 可不同步 |

## 结论

- 需要加密同步的 cache/状态类 = orders 的 cookies.json/home、cf\browser_profile、引擎 cookies/.zhs_cred。
- 其余可重生 cache 全部排除；不存在"cache 目录整体排除"的误伤。
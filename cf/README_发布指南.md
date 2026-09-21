# Cloudflare 发布（本地平台 → 你的域名）

当前状态：cloudflared 2026.9.1 已就位，一键部署脚本已就绪。
**只差你的 API Token 和域名。**

---

## 第 1 步：创建 API Token（约 1 分钟）

打开（需登录 Cloudflare）：<https://dash.cloudflare.com/profile/api-tokens>

1. 点 **Create Token**
2. 最下方选 **Create Custom Token**（不要用模板）
3. **Token name** 填：`wk-platform-tunnel`
4. **Permissions** 添加以下 4 条（左侧下拉 → 中间下拉 → 右侧选权限）：

| # | 类别 (1st) | 资源 (2nd) | 权限 (3rd) |
|---|---|---|---|
| 1 | Account | Cloudflare Tunnel | **Edit** |
| 2 | Account | Account Settings | **Read** |
| 3 | Zone | DNS | **Edit** |
| 4 | Zone | Zone | **Read** |

5. **Zone Resources**：`Include` → `Specific zone` → 选你的域名
6. **Account Resources**：`Include` → 你的账号
7. TTL 留空（永久有效），点 **Continue to summary** → **Create Token**
8. 复制那串 Token（只显示一次）

> 为什么是这 4 条：Tunnel Edit 用于创建/管理隧道；Account Settings Read 用于定位账号 ID；
> DNS Edit 用于自动添加域名解析；Zone Read 用于定位域名 ID。没有多余的权限。

---

## 第 2 步：填配置

把 `cf_config.example.json` 复制为 `cf_config.json`，填入：

```json
{
  "api_token": "刚才复制的 Token",
  "domain": "order.你的域名.com",
  "port": 8766
}
```

- `domain` 填你想要的访问地址（子域名字随意，例如 `order`、`wk`、`pt`）
- 前提：该域名已托管在 Cloudflare（NS 已指向 CF）
- 平台（order_platform.py，8766 端口）需保持运行

---

## 第 3 步：一键发布

```powershell
cd C:\Users\Administrator\WorkBuddy\2026-09-11-10-49-22\cf
C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe deploy_cf.py
```

脚本会自动：定位账号/域名 → 创建隧道 → 配置路由 → 添加 DNS → 后台静默启动 cloudflared。
成功后终端会打印 `🎉 发布成功: https://你的域名`。

其他命令：

```powershell
python deploy_cf.py status   # 查看本地隧道进程与配置
python deploy_cf.py stop     # 停止隧道（域名即不可访问）
```

---

## 注意事项

- 发布后任何人访问域名都能看到平台首页（有登录/注册），**建议先只自己注册、改掉 admin 默认密码**，再决定是否开放注册。
- 平台与隧道都在本机跑；电脑关机/睡眠则域名不可访问。
- 不需要在路由器上开放任何端口，也不用公网 IP。
- 隧道的连接信息保存在 `cf_state.json`，日志在 `cloudflared.log`。

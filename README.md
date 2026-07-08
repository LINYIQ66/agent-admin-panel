# Agent Admin Panel 🤖

单服务器 Kopi Agent (Hermes) 管理后台。在单台服务器上部署和管理多个客户端实例。

## 功能

- **📊 仪表盘** — 实时监控服务器资源和所有 Gateway 状态
- **🧙 创建向导** — 3 步创建新 Client Profile（自动配置 API Key + Telegram Bot）
- **🔄 管理操作** — 启动/停止/重启任意 Gateway
- **🗑️ 删除 Profile** — 完整清理（停止服务 + 删除目录）
- **🔑 3 层 API Key 降级策略** — Auto-provision → Boss Key → .env
- **⚡ SSE 实时日志流** — 创建进度实时展示

## 架构

```
┌─────────────────┐     HTTP/8011      ┌──────────────────┐
│   Browser        │ ◄──────────────► │  Flask Backend    │
│   (admin.html)   │                   │  (admin_app.py)   │
└─────────────────┘                   └────────┬─────────┘
                                               │
                    ┌──────────────────────────┼──────────────────────────┐
                    │                          │                          │
            ┌───────▼──────┐          ┌────────▼────────┐         ┌──────▼──────┐
            │  systemd      │          │  hermes CLI     │         │  Kopi Proxy │
            │  (gateway     │          │  (profile       │         │  (API Key   │
            │   services)   │          │   management)   │         │   provision)│
            └───────────────┘          └─────────────────┘         └─────────────┘
```

### 设计原则

- **Profile 隔离** — 每个 Client 独立的 `~/.hermes/profiles/<name>/` 目录
- **Systemd 托管** — 每个 Profile 独立的 `hermes-<name>-gateway.service`
- **实时日志** — Server-Sent Events (SSE) 流式传输创建进度
- **白底主题** — 简洁清晰的白色背景设计

## 文件结构

```
agent-admin-panel/
├── admin_app.py           # Flask 后端主程序
├── templates/
│   ├── login.html         # 密码登录页面
│   └── admin.html         # 主管理界面
├── requirements.txt       # Python 依赖
└── README.md              # 本文件
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
export ADMIN_PASSWORD="your_password"    # 管理后台密码
export HERMES_HOME="$HOME/.hermes"       # Hermes 主目录
export BOSS_API_KEY="kp-agent-..."       # Boss API Key（回退用）
export PORT=8011                         # 端口（可选，默认 8011）
```

### 3. 启动

```bash
python admin_app.py
```

### 4. Systemd 自启动（推荐）

```ini
# /etc/systemd/system/agent-admin.service
[Unit]
Description=Kopi Agent Admin Panel
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/agent-admin
ExecStart=/usr/bin/python3 /root/agent-admin/admin_app.py
Restart=on-failure
RestartSec=5
Environment="PYTHONUNBUFFERED=1"

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now agent-admin
```

## API 接口

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/login` | POST | 登录 |
| `/api/logout` | POST | 登出 |
| `/api/stats` | GET | 服务器资源统计 |
| `/api/profiles` | GET | 所有 Profile 列表 |
| `/api/profile/create` | POST | 创建新 Profile |
| `/api/profile/create/stream/<task_id>` | GET | SSE 实时日志流 |
| `/api/profile/<name>/start` | POST | 启动 Gateway |
| `/api/profile/<name>/stop` | POST | 停止 Gateway |
| `/api/profile/<name>/restart` | POST | 重启 Gateway |
| `/api/profile/<name>/status` | GET | 单个 Profile 状态 |
| `/api/profile/<name>` | DELETE | 删除 Profile |

## API Key 3 层降级策略

1. **Layer 1: Auto-provision** — POST `/v1/auto-provision` → `/v1/provision`（2 次重试）
2. **Layer 2: Boss Key 回退** — 使用 `BOSS_API_KEY` 验证并使用
3. **Layer 3: Default .env 复制** — 从 `~/.hermes/.env` 读取 `KOPI_API_KEY`

## 容量规划

- 每个 Profile 约 **300MB RAM** + **300MB Disk**
- 7GB 服务器可稳定运行 **8-10 个 Profile**
- 容量公式：`(ram_available - 1500) // 500`

## 技术栈

- **后端**: Python 3 + Flask
- **前端**: 原生 HTML/CSS/JS（无框架依赖）
- **实时通信**: Server-Sent Events (SSE)
- **进程管理**: Systemd
- **认证**: Session Cookie

---

*KOPI AI AGENT ☕ by Kopi Ai Agent Pte Ltd（新加坡）*

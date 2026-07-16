# Agent Admin Panel 🤖

Multi-client management dashboard for **KOPI AI AGENT** instances on a single server.
Deploy, monitor, and manage Hermes agent gateways through a clean web UI.

## Features

- **📊 Dashboard** — Real-time server resource monitoring & all gateway statuses
- **🧙 3-Step Creation Wizard** — Create new client profiles with automated API key provisioning + Telegram bot setup (SSE live logs)
- **🔄 Lifecycle Management** — Start / Stop / Restart any gateway with one click
- **🗑️ Clean Deletion** — Full cleanup: stop service + remove directory + systemd unit
- **🔑 API Key Column** — Copy masked keys with one click; instant balance bar per key
- **📦 3-Layer Key Degradation** — Auto-provision → Boss Key → `.env` fallback
- **⚡ Real-time SSE Logs** — Live creation progress streamed to the browser

## Architecture

```
┌──────────────┐    HTTP/8011     ┌────────────────┐
│   Browser     │ ◄─────────────► │  Flask Backend  │
│ (admin.html)  │                 │ (admin_app.py)  │
└──────────────┘                 └────────┬───────┘
                                          │
         ┌────────────────────────────────┼─────────────────────────────┐
         │                                │                             │
  ┌──────▼──────┐                  ┌──────▼───────┐            ┌───────▼────────┐
  │   systemd    │                  │  hermes CLI   │            │  KOPI Proxy    │
  │  (gateway    │                  │  (profile     │            │  (auto-key     │
  │   services)  │                  │   mgmt)       │            │   provision)   │
  └─────────────┘                  └──────────────┘            └────────────────┘
```

### Design Principles

- **Profile Isolation** — Each client lives in `~/.hermes/profiles/<name>/` with its own `.env` and `config.yaml`
- **Systemd Managed** — Each profile gets a dedicated `hermes-<name>-gateway.service`
- **Real-time Logs** — SSE stream for profile creation progress
- **Clean White Theme** — Minimal, distraction-free interface

## File Structure

```
agent-admin-panel/
├── admin_app.py           # Flask backend (all API + SSE + auth)
├── templates/
│   ├── login.html         # Password login page
│   └── admin.html         # Main admin dashboard
├── requirements.txt       # flask + pytz
└── README.md              # This file
```

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
export ADMIN_PASSWORD="your_password"    # Admin panel login password
export HERMES_HOME="$HOME/.hermes"       # Hermes home directory
export BOSS_API_KEY="kopi-..."           # Boss API Key (fallback, optional)
export PORT=8011                         # Server port (default: 8011)
```

### 3. Run

```bash
python admin_app.py
```

Open `http://your-server:8011` in your browser.

### 4. Systemd Auto-Start (Recommended)

```ini
# /etc/systemd/system/agent-admin.service
[Unit]
Description=KOPI Agent Admin Panel
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/agent-admin
ExecStart=/usr/bin/python3 /root/agent-admin/admin_app.py
Restart=on-failure
RestartSec=5
Environment="PYTHONUNBUFFERED=1"
Environment="ADMIN_PASSWORD=your_password"
Environment="HERMES_HOME=/root/.hermes"

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now agent-admin
```

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/login` | POST | Authenticate with password |
| `/api/logout` | POST | End session |
| `/api/stats` | GET | Server resource stats |
| `/api/profiles` | GET | List all profiles |
| `/api/profile/create` | POST | Create new profile (returns task_id) |
| `/api/profile/create/stream/<task_id>` | GET | SSE live creation log |
| `/api/profile/<name>/start` | POST | Start gateway |
| `/api/profile/<name>/stop` | POST | Stop gateway |
| `/api/profile/<name>/restart` | POST | Restart gateway |
| `/api/profile/<name>/status` | GET | Single profile details + recent logs |
| `/api/profile/<name>` | DELETE | Delete profile |

## API Key 3-Layer Degradation Strategy

1. **Layer 1: Auto-provision** — `POST /v1/auto-provision/ready` on the KOPI Proxy (2 retries). Returns a fresh key with 5M quota.
2. **Layer 2: Boss Key Fallback** — Uses `BOSS_API_KEY` env var if auto-provision fails.
3. **Layer 3: Default `.env`** — Reads `KOPI_API_KEY` from `~/.hermes/.env` as last resort.

## Capacity Planning

- ~**300MB RAM** + ~**300MB Disk** per profile
- 7GB server: stable for **8–10 profiles**
- Formula: `(ram_available - 1500) // 500`

## Tech Stack

- **Backend**: Python 3 + Flask
- **Frontend**: Vanilla HTML/CSS/JS (zero framework dependencies)
- **Real-time**: Server-Sent Events (SSE)
- **Process Management**: Systemd
- **Auth**: Session cookie

---

*KOPI AI AGENT ☕ by Kopi Ai Agent Pte Ltd (Singapore)*

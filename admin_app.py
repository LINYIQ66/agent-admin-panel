#!/usr/bin/env python3
"""
Agent Admin Panel — Flask 管理后台
用于在单台服务器上部署和管理多个 Kopi Agent (Hermes) 客户端实例。

GitHub: https://github.com/kopiagent/agent-admin-panel

功能：
  - 📊 仪表盘：实时监控服务器资源和所有 Gateway 状态
  - 🧙 创建向导：3 步创建新 Client Profile
  - 🔄 管理操作：启动/停止/重启任意 Gateway
  - 🗑️ 删除 Profile：完整清理（停止服务 + 删除目录）
  - 🔑 3 层 AI 降级策略：Auto-provision → Boss Key → .env
  - ⚡ SSE 实时日志流

依赖：pip install flask pytz
"""

import atexit
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from functools import wraps
from pathlib import Path

import pytz
from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

# ── Configuration ──────────────────────────────────────────────────────────────

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "030380")
HERMES_HOME = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
HERMES_CLI = os.environ.get(
    "HERMES_CLI",
    "/usr/local/lib/hermes-agent/venv/bin/hermes",
)
BOSS_API_KEY = os.environ.get("BOSS_API_KEY", "")
KOPI_PROXY_URL = os.environ.get(
    "KOPI_PROXY_URL", "https://kopiaiagent.com"
)
SERVER_PORT = int(os.environ.get("PORT", "8011"))
SECRET_KEY = os.environ.get("SECRET_KEY", secrets.token_hex(32))

# ── App Setup ──────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = SECRET_KEY

# SSE log streams: {task_id: [log_entries]}
LOG_STREAMS: dict[str, list[dict]] = {}


# ── Helpers ────────────────────────────────────────────────────────────────────


def run_cmd(cmd, timeout=30):
    """Run a shell command and return (stdout, stderr, exit_code)."""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except subprocess.TimeoutExpired:
        return "", f"Command timed out after {timeout}s", -1
    except Exception as e:
        return "", str(e), -1


def sgt_now():
    """Return current time string in SGT (Asia/Singapore)."""
    tz = pytz.timezone("Asia/Singapore")
    now = datetime.now(tz)
    return now.strftime("%Y-%m-%d %H:%M:%S") + " SGT"


def get_profile_status(name):
    """Get status info for a single profile."""
    profile_dir = Path(HERMES_HOME) / "profiles" / name
    service_name = f"hermes-{name}-gateway"
    config_path = profile_dir / "config.yaml"
    env_path = profile_dir / ".env"

    # Service status
    _, stderr, rc = run_cmd(f"systemctl is-active {service_name}")
    gateway_status = "running" if rc == 0 else "stopped"

    # PID
    pid = "—"
    if gateway_status == "running":
        pid_out, _, _ = run_cmd(
            f"systemctl show {service_name} -p MainPID --value"
        )
        pid = pid_out if pid_out and pid_out != "0" else "—"

    # Model
    model = "kopi-o"
    if config_path.exists():
        for line in config_path.read_text().splitlines():
            if "model.default" in line or "model:" in line and ":" in line:
                parts = line.split(":", 1)
                if len(parts) == 2:
                    val = parts[1].strip().strip('"').strip("'")
                    if val and val != "default":
                        model = val
                        break

    # Disk
    disk = "—"
    if profile_dir.exists():
        du_out, _, _ = run_cmd(f"du -sh {profile_dir}")
        if du_out:
            disk = du_out.split()[0]

    # Token usage
    token_usage = "—"
    api_key = ""
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("KOPI_API_KEY="):
                api_key = line.split("=", 1)[1].strip()
                break
    if api_key:
        # Try to get token usage from proxy
        t_out, _, t_rc = run_cmd(
            f'curl -s -o /dev/null -w "%{{http_code}}" '
            f'-H "Authorization: Bearer {api_key}" '
            f"{KOPI_PROXY_URL}/v1/token-usage"
        )
        if t_rc == 0 and t_out and t_out != "500":
            usage_resp, _, _ = run_cmd(
                f'curl -s -H "Authorization: Bearer {api_key}" '
                f"{KOPI_PROXY_URL}/v1/token-usage"
            )
            if usage_resp:
                try:
                    data = json.loads(usage_resp)
                    used = data.get("total_tokens", data.get("usage", 0))
                    token_usage = f"{int(used) // 1000}K" if used else "—"
                except (json.JSONDecodeError, ValueError):
                    pass

    # Created time
    created = "—"
    if profile_dir.exists():
        try:
            ctime = os.stat(str(profile_dir)).st_ctime
            tz = pytz.timezone("Asia/Singapore")
            created_dt = (
                datetime.fromtimestamp(ctime, tz=pytz.UTC)
                .astimezone(tz)
                .strftime("%Y-%m-%d %H:%M:%S SGT")
            )
            created = created_dt
        except Exception:
            pass

    # TG token
    has_tg_token = False
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("TELEGRAM_BOT_TOKEN=") and len(line) > 20:
                has_tg_token = True
                break

    # Default check
    is_default = name == "default"

    return {
        "name": name,
        "model": model,
        "gateway": gateway_status,
        "is_default": is_default,
        "disk": disk,
        "pid": pid,
        "token_usage": token_usage,
        "created_sgt": created,
        "tg_username": name,
        "has_tg_token": has_tg_token,
    }


def get_all_profiles():
    """List all profiles."""
    profiles_dir = Path(HERMES_HOME) / "profiles"
    if not profiles_dir.exists():
        return []

    names = sorted(
        d.name
        for d in profiles_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )
    profiles = [get_profile_status(n) for n in names]

    # Default first, then alphabetical
    default_idx = next(
        (i for i, p in enumerate(profiles) if p["is_default"]), None
    )
    if default_idx is not None and default_idx > 0:
        profiles.insert(0, profiles.pop(default_idx))

    return profiles


def get_server_stats():
    """Get server resource stats."""
    # CPU cores
    cpu_out, _, _ = run_cmd("nproc")
    cpu_cores = int(cpu_out) if cpu_out.isdigit() else 4

    # RAM
    ram_out, _, _ = run_cmd(
        "free -m | awk '/^Mem:/ {print $2, $3, $4, $7}'"
    )
    ram_parts = ram_out.split()
    ram_total = int(ram_parts[0]) if len(ram_parts) > 0 else 0
    ram_used = int(ram_parts[1]) if len(ram_parts) > 1 else 0
    ram_avail = int(ram_parts[2]) if len(ram_parts) > 2 else 0
    ram_free = int(ram_parts[3]) if len(ram_parts) > 3 else 0

    # Disk
    disk_out, _, _ = run_cmd(
        "df -m / | awk 'NR==2 {print $2, $3, $4}'"
    )
    disk_parts = disk_out.split()
    disk_total = int(disk_parts[0]) if len(disk_parts) > 0 else 0
    disk_used = int(disk_parts[1]) if len(disk_parts) > 1 else 0
    disk_avail = int(disk_parts[2]) if len(disk_parts) > 2 else 0

    # Load
    load_out, _, _ = run_cmd("cat /proc/loadavg | awk '{print $1}'")
    load_val = float(load_out) if load_out else 0.0

    # Profile count
    profiles = get_all_profiles()

    # Max profiles capacity (per profile ~500MB RAM overhead)
    max_profiles = max(0, (ram_avail - 1500) // 500) if ram_avail > 1500 else 0

    return {
        "cpu_cores": cpu_cores,
        "ram_total": ram_total,
        "ram_used": ram_used,
        "ram_available": ram_avail,
        "ram_free": ram_free,
        "disk_total": disk_total,
        "disk_used": disk_used,
        "disk_available": disk_avail,
        "load": load_val,
        "profile_count": len(profiles),
        "max_profiles": int(max_profiles),
    }


# ── Authentication ────────────────────────────────────────────────────────────


def login_required(f):
    """Decorator to require login session."""

    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required"}), 401
            return render_template("login.html")
        return f(*args, **kwargs)

    return decorated


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json() or {}
    password = data.get("password", "")
    if password == ADMIN_PASSWORD:
        session["logged_in"] = True
        session.permanent = True
        return jsonify({"success": True})
    return jsonify({"error": "Invalid password"}), 401


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.pop("logged_in", None)
    return jsonify({"success": True})


# ── Pages ─────────────────────────────────────────────────────────────────────


@app.route("/")
@login_required
def index():
    return render_template("admin.html")


@app.route("/login")
def login_page():
    if session.get("logged_in"):
        return redirect(url_for("index"))
    return render_template("login.html")


# ── API Routes ────────────────────────────────────────────────────────────────


@app.route("/api/stats")
@login_required
def api_stats():
    return jsonify(get_server_stats())


@app.route("/api/profiles")
@login_required
def api_profiles():
    return jsonify(get_all_profiles())


@app.route("/api/profile/<name>/status")
@login_required
def api_profile_status(name):
    profile_dir = Path(HERMES_HOME) / "profiles" / name
    if not profile_dir.exists():
        return jsonify({"error": f"Profile '{name}' not found"}), 404

    status = get_profile_status(name)

    # Recent logs
    service_name = f"hermes-{name}-gateway"
    logs, _, _ = run_cmd(
        f"journalctl -u {service_name} --no-pager -n 20 --no-hostname 2>&1"
    )
    status["recent_logs"] = logs

    return jsonify(status)


@app.route("/api/profile/<name>/start", methods=["POST"])
@login_required
def api_profile_start(name):
    profile_dir = Path(HERMES_HOME) / "profiles" / name
    if not profile_dir.exists():
        return jsonify({"error": f"Profile '{name}' not found"}), 404

    service_name = f"hermes-{name}-gateway"
    _, stderr, rc = run_cmd(f"systemctl start {service_name}")
    if rc == 0:
        return jsonify({"success": True, "message": f"✅ {name} started"})
    return jsonify({"error": f"Failed to start: {stderr}"}), 500


@app.route("/api/profile/<name>/stop", methods=["POST"])
@login_required
def api_profile_stop(name):
    profile_dir = Path(HERMES_HOME) / "profiles" / name
    if not profile_dir.exists():
        return jsonify({"error": f"Profile '{name}' not found"}), 404

    service_name = f"hermes-{name}-gateway"
    _, stderr, rc = run_cmd(f"systemctl stop {service_name}")
    if rc == 0:
        return jsonify({"success": True, "message": f"⏹ {name} stopped"})
    return jsonify({"error": f"Failed to stop: {stderr}"}), 500


@app.route("/api/profile/<name>/restart", methods=["POST"])
@login_required
def api_profile_restart(name):
    """
    Restart a gateway service.
    Uses double-fork to fully detach from any gateway context.
    """
    profile_dir = Path(HERMES_HOME) / "profiles" / name
    if not profile_dir.exists():
        return jsonify({"error": f"Profile '{name}' not found"}), 404

    service_name = f"hermes-{name}-gateway"

    # Double-fork to fully detach from parent process
    pid = os.fork()
    if pid > 0:
        # Parent: wait for first child
        os.waitpid(pid, 0)
    else:
        # First child
        os.setsid()
        pid2 = os.fork()
        if pid2 > 0:
            # First child exits immediately
            os._exit(0)
        else:
            # Second child (fully detached)
            time.sleep(2)
            subprocess.run(
                ["systemctl", "restart", service_name],
                capture_output=True,
                timeout=30,
            )
            os._exit(0)

    return jsonify(
        {"success": True, "message": f"🔄 Restarting {name}..."}
    )


@app.route("/api/profile/<name>", methods=["DELETE"])
@login_required
def api_profile_delete(name):
    """
    Delete a profile: stop service, disable, remove files.
    """
    profile_dir = Path(HERMES_HOME) / "profiles" / name
    if not profile_dir.exists():
        return jsonify({"error": f"Profile '{name}' not found"}), 404

    if name == "default":
        return jsonify({"error": "Cannot delete default profile"}), 400

    service_name = f"hermes-{name}-gateway"
    service_path = Path(f"/etc/systemd/system/{service_name}.service")

    # Stop and disable service
    run_cmd(f"systemctl stop {service_name}")
    run_cmd(f"systemctl disable {service_name}")

    if service_path.exists():
        service_path.unlink()
    run_cmd("systemctl daemon-reload")

    # Remove profile directory
    if profile_dir.exists():
        shutil.rmtree(str(profile_dir))

    return jsonify(
        {"success": True, "message": f"🗑️ Profile '{name}' deleted"}
    )


# ── Profile Creation (Async + SSE) ────────────────────────────────────────────


def _provision_api_key(log):
    """
    3-layer AI key provisioning strategy:
      Layer 1: Auto-provision via Kopi Proxy (2 retries)
      Layer 2: Boss Key fallback
      Layer 3: Default .env copy
    """
    kopi_api_key = None

    # Layer 1: Auto-provision
    log("🔑 Attempting auto-provision...", "step")
    for attempt in range(1, 3):
        log(f"  Auto-provision attempt {attempt}/2...", "info")
        auto_out, auto_err, auto_rc = run_cmd(
            f'curl -s -X POST {KOPI_PROXY_URL}/v1/auto-provision '
            f'-H "Content-Type: application/json" '
            f'-d \'{{}}\'',
            timeout=15,
        )
        if auto_rc == 0 and auto_out:
            try:
                prov_data = json.loads(auto_out)
                provision_token = prov_data.get("provision_token") or prov_data.get(
                    "token"
                )
                if provision_token and len(str(provision_token)) > 16:
                    # Exchange token for key
                    key_out, key_err, key_rc = run_cmd(
                        f'curl -s -X POST {KOPI_PROXY_URL}/v1/provision '
                        f'-H "Content-Type: application/json" '
                        f'-d \'{{"token": "{provision_token}"}}\'',
                        timeout=15,
                    )
                    if key_rc == 0 and key_out:
                        try:
                            key_data = json.loads(key_out)
                            kopi_api_key = (
                                key_data.get("api_key")
                                or key_data.get("key")
                                or key_data.get("apiKey")
                            )
                            if kopi_api_key and len(str(kopi_api_key)) >= 40:
                                # Verify key
                                verify_out, _, verify_rc = run_cmd(
                                    f'curl -s -o /dev/null -w "%{{http_code}}" '
                                    f'-H "Authorization: Bearer {kopi_api_key}" '
                                    f"{KOPI_PROXY_URL}/v1/models",
                                    timeout=10,
                                )
                                if verify_rc == 0 and verify_out == "200":
                                    log(
                                        f"✅ API key provisioned & verified: "
                                        f"{kopi_api_key[:20]}... (len={len(kopi_api_key)})",
                                        "success",
                                    )
                                    return kopi_api_key
                                log(
                                    f"  ❌ Key verification failed (HTTP {verify_out})",
                                    "warning",
                                )
                            else:
                                log(
                                    f"  ❌ Key too short ({len(kopi_api_key or '')} chars)",
                                    "warning",
                                )
                        except (json.JSONDecodeError, TypeError):
                            log(f"  ❌ Failed to parse key response", "warning")
            except (json.JSONDecodeError, TypeError):
                log(f"  ❌ Failed to parse provision response", "warning")

        if attempt < 2:
            time.sleep(2)

    # Layer 2: Boss Key fallback
    if BOSS_API_KEY and len(BOSS_API_KEY) >= 40:
        log("🔑 Falling back to Boss API Key...", "step")
        verify_out, _, verify_rc = run_cmd(
            f'curl -s -o /dev/null -w "%{{http_code}}" '
            f'-H "Authorization: Bearer {BOSS_API_KEY}" '
            f"{KOPI_PROXY_URL}/v1/models",
            timeout=10,
        )
        if verify_rc == 0 and verify_out == "200":
            kopi_api_key = BOSS_API_KEY
            log(f"✅ Boss key verified: {kopi_api_key[:20]}...", "success")
            return kopi_api_key
        log("  ❌ Boss key verification failed", "warning")

    # Layer 3: Default .env
    log("🔑 Falling back to default .env...", "step")
    default_env = Path(HERMES_HOME) / ".env"
    if default_env.exists():
        for line in default_env.read_text().splitlines():
            if line.startswith("KOPI_API_KEY="):
                kopi_api_key = line.split("=", 1)[1].strip()
                if kopi_api_key and len(kopi_api_key) >= 40:
                    log(f"✅ Using default API key: {kopi_api_key[:20]}...", "success")
                    return kopi_api_key

    log("❌ All key provisioning layers failed!", "error")
    return kopi_api_key


def _create_profile_worker(task_id, name, tg_token, description):
    """Background worker that creates a profile step by step."""
    logs = LOG_STREAMS.setdefault(task_id, [])
    profile_dir = Path(HERMES_HOME) / "profiles" / name
    service_name = f"hermes-{name}-gateway"
    service_path = Path(f"/etc/systemd/system/{service_name}.service")
    env_path = profile_dir / ".env"
    config_path = profile_dir / "config.yaml"
    source_skills = Path(HERMES_HOME) / "skills"

    def log(msg, status="info"):
        entry = {"msg": msg, "status": status, "time": time.time()}
        logs.append(entry)

    try:
        # Step 1: Create profile directory
        log("📁 Creating profile directory...", "step")
        out, err, rc = run_cmd(
            f"{HERMES_CLI} profile create {name} --no-alias 2>&1"
        )
        if rc != 0:
            log(f"❌ Failed to create profile: {err or out}", "error")
            logs.append({"done": True, "success": False, "profile": name})
            return
        log("✅ Profile directory created", "success")

        # Step 2: Provision API key
        kopi_api_key = _provision_api_key(log)
        if not kopi_api_key or len(kopi_api_key) < 40:
            log("❌ Could not obtain valid API key. Aborting.", "error")
            logs.append({"done": True, "success": False, "profile": name})
            return

        # Step 3: Write .env
        log("🔐 Writing .env...", "step")
        env_content = (
            f"TZ=Asia/Hong_Kong\n"
            f"TELEGRAM_BOT_TOKEN={tg_token}\n"
            f"KOPI_API_KEY={kopi_api_key}\n"
        )
        env_path.write_text(env_content)
        log("✅ .env written", "success")

        # Step 4: Write config.yaml
        log("⚙️ Writing config.yaml...", "step")
        config_content = f"""# Hermes Agent Configuration - Profile: {name}
model:
  default: kopi-o
providers:
  custom:
    api_key: {kopi_api_key[:20]}...
    base_url: {KOPI_PROXY_URL}
agent:
  name: {name}
  description: {description or f"Kopi Agent - {name}"}
  system_prompt: ""
telegram:
  bot_token: ${{TELEGRAM_BOT_TOKEN}}
  enabled: {"true" if tg_token else "false"}
"""
        config_path.write_text(config_content)
        log("✅ config.yaml written", "success")

        # Step 5: Copy skills
        log("📚 Copying skills...", "step")
        target_skills = profile_dir / "skills"
        if target_skills.exists():
            shutil.rmtree(str(target_skills))

        if source_skills.exists():
            shutil.copytree(str(source_skills), str(target_skills))
            log(f"✅ Skills copied ({len(list(target_skills.iterdir()))} files)", "success")
        else:
            log("  ⚠️ No source skills to copy", "warning")
            target_skills.mkdir(parents=True, exist_ok=True)

        # Step 6: Install systemd service
        log("🚀 Installing gateway service...", "step")
        venv_path = "/usr/local/lib/hermes-agent/venv"
        service_content = f"""[Unit]
Description=Hermes Agent Gateway - Profile: {name}
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
EnvironmentFile={profile_dir}/.env
Type=simple
User=root
Group=root
ExecStart={venv_path}/bin/python -m hermes_cli.main --profile {name} gateway run
WorkingDirectory={profile_dir}
Environment="HOME=/root"
Environment="USER=root"
Environment="HERMES_HOME={profile_dir}"
Environment="PATH={venv_path}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="VIRTUAL_ENV={venv_path}"
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
"""
        service_path.write_text(service_content)
        run_cmd("systemctl daemon-reload")
        log("✅ Service file installed", "success")

        # Step 7: Start gateway
        log("🔌 Starting gateway...", "step")
        run_cmd(f"systemctl enable {service_name}")
        out, err, rc = run_cmd(f"systemctl start {service_name}")
        time.sleep(3)

        # Verify
        check_out, _, check_rc = run_cmd(f"systemctl is-active {service_name}")
        if check_rc == 0:
            log(f"✅ Gateway is running ({check_out})", "success")
            log(f"🎉 Profile \"{name}\" is ready!", "done")
            logs.append({"done": True, "success": True, "profile": name})
        else:
            log(f"❌ Gateway failed to start: {check_out}", "error")
            logs.append({"done": True, "success": False, "profile": name})

    except Exception as e:
        log(f"❌ Unexpected error: {str(e)}", "error")
        logs.append({"done": True, "success": False, "profile": name})


@app.route("/api/profile/create", methods=["POST"])
@login_required
def api_profile_create():
    data = request.get_json() or {}
    name = data.get("name", "").strip().lower()
    tg_token = data.get("tg_token", "").strip()
    description = data.get("description", "").strip()

    # Validation
    if not name or not name[0].isalpha():
        return jsonify({"error": "Profile name must start with a letter"}), 400
    if not all(c.isalnum() or c in ("-", "_") for c in name):
        return jsonify({"error": "Profile name: letters, numbers, hyphens only"}), 400
    if len(name) < 2 or len(name) > 31:
        return jsonify({"error": "Profile name: 2-31 characters"}), 400

    profile_dir = Path(HERMES_HOME) / "profiles" / name
    if profile_dir.exists():
        return jsonify({"error": f"Profile '{name}' already exists"}), 409

    # Start background task
    task_id = str(uuid.uuid4())
    thread = threading.Thread(
        target=_create_profile_worker,
        args=(task_id, name, tg_token, description),
        daemon=True,
    )
    thread.start()

    return jsonify({"task_id": task_id})


@app.route("/api/profile/create/stream/<task_id>")
@login_required
def stream_create(task_id):
    """SSE endpoint for real-time profile creation logs."""

    def generate():
        last_idx = 0
        max_wait = 120  # 2-minute timeout
        waited = 0
        while waited < max_wait:
            logs = LOG_STREAMS.get(task_id, [])
            while last_idx < len(logs):
                entry = logs[last_idx]
                yield f"data: {json.dumps(entry)}\n\n"
                last_idx += 1
                if entry.get("done"):
                    return
            time.sleep(0.3)
            waited += 0.3
        # Timeout
        yield f"data: {json.dumps({'msg': '⏱️ Operation timed out', 'status': 'error'})}\n\n"
        yield f"data: {json.dumps({'done': True, 'success': False, 'timeout': True})}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── Main ──────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    from datetime import datetime

    print(f"🤖 Agent Admin Panel")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"Port:     {SERVER_PORT}")
    print(f"Hermes:   {HERMES_HOME}")
    print(f"Started:  {sgt_now()}")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━")

    app.run(
        host="0.0.0.0",
        port=SERVER_PORT,
        debug=False,
        threaded=True,
    )

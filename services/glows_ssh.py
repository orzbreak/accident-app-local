"""Bootstrap the YOLO worker over an SSH tunnel to an SDK-created GPU."""
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

PORT = 18001
WORKER = Path(__file__).resolve().parents[1] / "glows_worker"


def launch(instance, token, folder, log):
    access = next((a for a in instance.get("accesses") or [] if a.get("protocol") == "ssh"), {})
    address = urlsplit(access.get("url") or "")
    host = address.hostname
    credentials = instance.get("sshCred") or {}
    password = credentials.get("password")
    if address.scheme != "ssh" or not host or not host.endswith(".glows.ai") or not address.port or not password:
        raise ValueError("Glows.ai 尚未提供可用的 SSH 連線。")
    windows = os.name == "nt"
    ssh = shutil.which("ssh.exe" if windows else "ssh")
    scp = shutil.which("scp.exe" if windows else "scp")
    if not ssh or not scp:
        raise ValueError("找不到 OpenSSH。")
    ask = folder / ("ask.cmd" if windows else "ask")
    if windows:
        ask.write_text('@powershell.exe -NoProfile -NonInteractive -Command "[Console]::WriteLine($env:GLOWS_SSH_PASSWORD)"\n', encoding="ascii")
    else:
        ask.write_text('#!/bin/sh\nprintf "%s\\n" "$GLOWS_SSH_PASSWORD"\n', encoding="ascii")
        ask.chmod(0o700)
    env = {
        **os.environ, "SSH_ASKPASS": str(ask), "SSH_ASKPASS_REQUIRE": "force",
        "GLOWS_SSH_PASSWORD": password,
    }
    # Do not forward application secrets to the remote process.
    worker_token = folder / "yolo-worker-token"
    worker_token.write_text(token, encoding="ascii")
    options = [
        "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={folder / 'known_hosts'}",
        "-o", "ConnectTimeout=12", "-o", "NumberOfPasswordPrompts=1",
    ]
    host = "root@" + host
    try:
        subprocess.run([
            scp, *options, "-P", str(address.port), str(WORKER / "app.py"),
            str(WORKER / "requirements.txt"), str(worker_token), host + ":/root/",
        ], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=True, timeout=35)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"Glows.ai SSH 上傳 YOLO worker 失敗（{type(exc).__name__}）。") from exc
    command = (
        'cd /root; PY=/root/miniconda3/envs/workenv/bin/python; '
        'test -x "$PY" && "$PY" -m pip install -q -r requirements.txt && '
        'export GLOWS_YOLO_TOKEN=$(cat yolo-worker-token) && '
        'exec "$PY" -u -m uvicorn app:app --host 127.0.0.1 --port 8001 --no-access-log'
    )
    return subprocess.Popen([
        ssh, *options, "-T", "-p", str(address.port), "-L", f"127.0.0.1:{PORT}:127.0.0.1:8001",
        "-o", "ExitOnForwardFailure=yes", host, command,
    ], env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)


def infer(instance, source, worker_token, deadline, wait, request):
    try:
        log_path = Path(source).with_name("glows-yolo.log")
        with TemporaryDirectory(prefix="glows-worker-") as directory, log_path.open("wb", buffering=0) as log:
            started = datetime.now(timezone.utc).isoformat(timespec="seconds")
            log.write(f"Glows.ai instance: {instance['instanceID']}\nSSH started (UTC): {started}\n\n".encode("utf-8"))
            tunnel = launch(instance, worker_token, Path(directory), log)
            try:
                url = f"http://127.0.0.1:{PORT}/infer"
                try:
                    wait(url, worker_token, deadline, tunnel)
                except ValueError as exc:
                    raise ValueError(f"Glows.ai worker 啟動階段失敗：{exc}") from exc
                try:
                    result = request(source, url, worker_token, deadline=deadline)
                except ValueError as exc:
                    raise ValueError(f"Glows.ai worker 推論或下載階段失敗：{exc}") from exc
                log.write(b"Annotated MP4 received by the local App.\n")
                return result
            finally:
                if tunnel.poll() is None:
                    tunnel.terminate()
                try:
                    tunnel.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    tunnel.kill()
                    tunnel.wait(timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("Glows.ai GPU 的 SSH 推論服務未能啟動。") from exc

"""Lifecycle of one on-demand GPU for a local video analysis."""
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path
from threading import Lock, Timer
from urllib.parse import urlsplit, urlunsplit

import requests

from services import glows_ssh

SDK_URL = "https://a.glows.ai/sdk/v1/instance"
PORT = 8001
MAX_SECONDS = 600
GPU = "NVIDIA GeForce RTX 4090"
TERMINAL = {'Failed', 'Finished', 'Terminated', 'Released', 'Canceled', 'Cancelled'}
logger = logging.getLogger(__name__)
slot = Lock()


def enabled():
    return os.getenv("GLOWS_YOLO_AUTO", "").strip() == "1"


def configured():
    return all(os.getenv(name, "").strip() for name in ("GLOWS_SDK_TOKEN", "GLOWS_YOLO_IMAGE_ID", "GLOWS_YOLO_REGION"))


def sdk(method, token, **kwargs):
    with requests.Session() as session:
        if os.getenv("GLOWS_SDK_DIRECT") == "1":
            session.trust_env = False
        response = session.request(method, SDK_URL, headers={"Authorization": "Bearer " + token},
                                   timeout=(5, 10), allow_redirects=False, **kwargs)
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict) or data.get("code") != 0:
        raise ValueError("Glows.ai SDK 拒絕操作。")
    return data


def release(token, meta_key):
    for attempt in range(3):
        try:
            try:
                instance = sdk("GET", token, params={"meta_key": meta_key}).get("instance") or {}
                if instance.get("status") in TERMINAL:
                    return True
            except (ValueError, requests.RequestException):
                pass
            sdk("DELETE", token, params={"meta_key": meta_key})
            for _ in range(6):
                instance = sdk("GET", token, params={"meta_key": meta_key}).get("instance") or {}
                if instance.get("status") in TERMINAL:
                    return True
                time.sleep(2)
        except (ValueError, requests.RequestException):
            if attempt < 2:
                time.sleep(2)
    logger.error("Could not confirm Glows GPU release for %s", meta_key)
    return False


def recover(data_dir):
    marker = Path(data_dir) / "glows-pending-release"
    if marker.is_file():
        token = os.getenv("GLOWS_SDK_TOKEN", "").strip()
        key = marker.read_text(encoding="ascii").strip()
        if not token or not re.fullmatch(r"accident-yolo-[a-f0-9]{32}", key) or not release(token, key):
            raise ValueError("上一台 Glows.ai GPU 尚未確認釋放，請到 My Instances 查核。")
        marker.unlink()


def recover_on_startup(data_dir):
    with slot:
        try:
            recover(data_dir)
        except (ValueError, OSError):
            logger.error("An earlier Glows GPU may still be active; check My Instances.")


def endpoint(instance):
    for access in instance.get("accesses") or []:
        if access.get("listenPort") != PORT:
            continue
        url = urlsplit(access.get("url") or "")
        if (url.scheme == "https" and url.hostname and url.hostname.endswith(".glows.ai")
                and not url.username and not url.password):
            return urlunsplit(url._replace(path=url.path.rstrip("/") + "/infer", fragment=""))
    return None


def worker_ready(url, worker_token):
    try:
        response = requests.post(url, headers={"Authorization": "Bearer " + worker_token},
                                 timeout=(3, 5), allow_redirects=False)
        return response.status_code == 400 and response.json().get("detail") == "Missing video"
    except (ValueError, requests.RequestException):
        return False


def wait_for_ssh_worker(url, worker_token, deadline, tunnel):
    while tunnel.poll() is None and time.monotonic() < deadline:
        if worker_ready(url, worker_token):
            return
        time.sleep(2)
    raise ValueError("Glows.ai SSH 推論服務未在時限內啟動。")


def wait_for_worker(token, key, worker_token, deadline, ssh_bootstrap=False):
    while time.monotonic() < deadline:
        instance = sdk("GET", token, params={"meta_key": key}).get("instance") or {}
        if instance.get("status") in TERMINAL:
            break
        if instance.get("status") == "Running":
            if ssh_bootstrap:
                if any(a.get("protocol") == "ssh" and a.get("url") for a in instance.get("accesses") or []):
                    return instance
            else:
                url = endpoint(instance)
                if url and worker_ready(url, worker_token):
                    return url
        time.sleep(3)
    raise ValueError("Glows.ai GPU 未在時限內完成啟動。")


def detect(source, data_dir, infer):
    if not configured():
        raise ValueError("Glows.ai 自動啟動尚未設定 SDK Token、YOLO 映像或機房。")
    if not slot.acquire(blocking=False):
        raise ValueError("另一支影片正在使用 Glows.ai GPU，請稍後重試。")
    try:
        recover(data_dir)
        return run(source, data_dir, infer)
    except OSError as exc:
        raise ValueError("Glows.ai 無法安全記錄本次 GPU 狀態。") from exc
    finally:
        slot.release()


def run(source, data_dir, infer):
    token = os.environ["GLOWS_SDK_TOKEN"].strip()
    worker_token = secrets.token_hex(32)
    key = "accident-yolo-" + secrets.token_hex(16)
    marker = Path(data_dir) / "glows-pending-release"
    with marker.open("x", encoding="ascii") as pending:
        pending.write(key)
        pending.flush()
        os.fsync(pending.fileno())
    deadline = time.monotonic() + MAX_SECONDS
    watchdog = Timer(MAX_SECONDS, release, args=(token, key))
    watchdog.daemon = True
    watchdog.start()
    try:
        ssh_bootstrap = os.getenv("GLOWS_YOLO_SSH_BOOTSTRAP") == "1"
        body = {
            "from_image_id": os.environ["GLOWS_YOLO_IMAGE_ID"].strip(),
            "gpu_name": GPU, "unit_qty": 1, "instance_category": "container",
            "region_name": os.environ["GLOWS_YOLO_REGION"].strip(), "custom_meta_key": key,
            "remark": "accident-yolo",
        }
        if not ssh_bootstrap:
            body.update(
                cmd="python -m uvicorn app:app --app-dir /opt/glows-worker --host 0.0.0.0 --port 8001",
                envs="GLOWS_YOLO_TOKEN=" + worker_token,
                ports=json.dumps([{"port": PORT, "protocol": 2}]),
            )
        sdk("POST", token, json=body)
        startup_deadline = min(deadline, time.monotonic() + 180)
        worker = wait_for_worker(token, key, worker_token, startup_deadline, ssh_bootstrap)
        if ssh_bootstrap:
            result = glows_ssh.infer(worker, source, worker_token, deadline, wait_for_ssh_worker, infer)
        else:
            result = infer(source, worker, worker_token, deadline=deadline)
    except requests.RequestException as exc:
        raise ValueError("Glows.ai SDK 暫時無法連線。") from exc
    finally:
        watchdog.cancel()
        if release(token, key):
            marker.unlink(missing_ok=True)
    return result

"""DottedSign OAuth client for the agreement signing workflow."""
import base64
import io
import os
import re
import zipfile
from pathlib import Path

import requests

SCOPES = "sign_tasks.general.write sign_tasks.general.read sign_tasks.download.read"
EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
HOSTS = {"sandbox": "https://api.sandbox.dottedsign.com", "production": "https://api.dottedsign.com"}


class ServiceError(Exception):
    pass


class RejectedRequest(ServiceError):
    pass


def environment():
    return os.getenv("DOTTEDSIGN_ENV", "sandbox").strip().lower()


def configured():
    return bool(os.getenv("DOTTEDSIGN_CLIENT_ID", "").strip()
                and os.getenv("DOTTEDSIGN_CLIENT_SECRET", "").strip())


class Client:
    def __init__(self):
        self.host = HOSTS.get(environment())
        if not self.host:
            raise ServiceError("DOTTEDSIGN_ENV must be sandbox or production")
        token = self.request("POST", "/v1/oauth/token", json={
            "grant_type": "client_credentials", "scope": SCOPES,
            "client_id": os.environ["DOTTEDSIGN_CLIENT_ID"].strip(),
            "client_secret": os.environ["DOTTEDSIGN_CLIENT_SECRET"].strip(),
        }).json().get("access_token")
        if not isinstance(token, str) or not token:
            raise ServiceError("DottedSign did not return an access token")
        self.headers = {"Authorization": f"Bearer {token}"}

    def request(self, method, path, *, headers=None, **kwargs):
        try:
            response = requests.request(method, self.host + path,
                                        headers={**getattr(self, "headers", {}), **(headers or {})},
                                        timeout=(5, 35), allow_redirects=False, **kwargs)
            if response.status_code in (400, 401, 403, 404, 422):
                raise RejectedRequest(f"DottedSign {path}: HTTP {response.status_code}")
            if not 200 <= response.status_code < 300:
                raise ServiceError(f"DottedSign {path}: HTTP {response.status_code}")
            return response
        except requests.RequestException as exc:
            raise ServiceError(f"DottedSign {path}: {type(exc).__name__}") from exc

    def create_task(self, pdf, title, signers, boxes, *, signature_page=0):
        stages = []
        for (name, email), (x, y, width, height) in zip(signers, boxes, strict=True):
            stages.append({"name": name, "email": email, "field_settings": [{
                "field_type": "signature", "page": signature_page, "coord": [x, y, x + width, y + height],
                "options": {"force": True},
            }]})
        result = self.request("POST", "/v1/sign_tasks", json={
            "file_name": title, "base64_file": base64.b64encode(Path(pdf).read_bytes()).decode("ascii"),
            "has_order": False, "start": False, "stages": stages,
        }).json()
        data = result.get("data", result)
        task_id = data.get("task_id")
        if type(task_id) is not int or task_id <= 0:
            raise ServiceError("DottedSign did not return a task ID")
        return task_id

    def start_task(self, task_id):
        self.request("POST", "/v1/sign_tasks/start", json={"task_id": task_id})

    def get_task(self, task_id):
        data = self.request("GET", "/v1/sign_tasks", params={"task_id": task_id}).json()["data"]
        if data.get("task_id") != task_id or data.get("status") not in (
                "draft", "waiting", "completed", "declined", "expired", "voided", "deleted"):
            raise ServiceError("DottedSign returned an unexpected task")
        return data

    def signed_pdf(self, task_id):
        response = self.request("GET", "/v1/sign_tasks/download", headers={"Accept": "application/zip"},
                                params={"task_id": task_id, "file_types[]": "task"})
        try:
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                pdfs = [info for info in archive.infolist() if info.filename.lower().endswith(".pdf")]
                if len(pdfs) == 1 and pdfs[0].file_size <= 100 * 1024 * 1024:
                    pdf = archive.read(pdfs[0])
                    if pdf.startswith(b"%PDF-"):
                        return pdf
        except zipfile.BadZipFile as exc:
            raise ServiceError("DottedSign returned an invalid archive") from exc
        raise ServiceError("DottedSign did not return a single signed PDF")

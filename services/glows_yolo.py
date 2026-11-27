"""Send the case video to an existing Glows.ai YOLO worker."""
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests


def configured():
    return bool(os.getenv("GLOWS_YOLO_URL", "").strip() and os.getenv("GLOWS_YOLO_TOKEN", "").strip())


def detect(source):
    return infer(source, os.environ["GLOWS_YOLO_URL"].strip(), os.environ["GLOWS_YOLO_TOKEN"].strip())


def infer(source, url, token, *, deadline=None):
    def remaining():
        if deadline is None:
            return 240
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise ValueError("Glows.ai 已達單次推論時限。")
        return min(seconds, 240)

    parts = urlsplit(url)
    loopback = parts.scheme == "http" and parts.hostname in {"127.0.0.1", "::1"}
    if not (parts.scheme == "https" or loopback) or not parts.hostname or parts.username or parts.password:
        raise ValueError("Glows.ai 推論網址須使用 HTTPS 或本機 SSH 通道。")
    output = Path(source).with_suffix(".yolo.mp4")
    temporary = output.with_suffix(".tmp")
    try:
        with open(source, "rb") as video, requests.post(
            url, data=video, headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/octet-stream",
                "X-Video-Extension": Path(source).suffix.lower(),
            }, stream=True, allow_redirects=False, timeout=(min(10, remaining()), remaining()),
        ) as result:
            if result.status_code != 200 or result.headers.get("Content-Type", "").split(";")[0] != "video/mp4":
                raise ValueError("Glows.ai 未回傳有效的辨識影片。")
            chunks = result.iter_content(1024 * 1024)
            first = next(chunks, b"")
            if first[4:8] != b"ftyp":
                raise ValueError("Glows.ai 回傳的影片格式不正確。")
            size = len(first)
            with temporary.open("wb") as target:
                target.write(first)
                for chunk in chunks:
                    remaining()
                    size += len(chunk)
                    if size > 512 * 1024 * 1024:
                        raise ValueError("Glows.ai 辨識影片超過下載上限。")
                    target.write(chunk)
        remaining()
        temporary.replace(output)
    except requests.ConnectionError as exc:
        message = (
            "Glows.ai GPU 尚未連線。請確認推論服務和本機 SSH 通道已啟動。"
            if loopback else "無法連線至 Glows.ai 推論服務。"
        )
        raise ValueError(message) from exc
    except (requests.RequestException, OSError) as exc:
        raise ValueError("Glows.ai 推論暫時無法完成。") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return str(output)

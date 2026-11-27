"""Private, single-GPU video inference endpoint for Glows.ai."""
import os
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock

import imageio_ffmpeg
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

TOKEN = os.environ["GLOWS_YOLO_TOKEN"].strip()
if not TOKEN:
    raise RuntimeError("Set GLOWS_YOLO_TOKEN before starting the worker.")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
slot = Lock()
MAX_VIDEO = 200 * 1024 * 1024


def detect(folder, source, output):
    model = os.getenv("GLOWS_YOLO_MODEL", "yolo11n.pt")
    runner = (
        "import torch; from ultralytics.cfg import entrypoint; "
        "print('YOLO device: CUDA:0 (' + torch.cuda.get_device_name(0) + ')', flush=True); entrypoint()"
    )
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"YOLO inference started (UTC): {started}", flush=True)
    subprocess.run([
        sys.executable, "-u", "-c", runner,
        "predict", f"model={model}", f"source={source}", "device=0",
        "classes=0,1,2,3,5,7", "save=True", f"project={folder}",
        "name=prediction", "exist_ok=True", "verbose=False",
    ], check=True, timeout=165)
    finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"YOLO inference completed (UTC): {finished}", flush=True)
    videos = [p for p in (folder / "prediction").iterdir() if p.suffix.lower() in {".avi", ".mp4"}]
    if len(videos) != 1:
        raise ValueError("Expected one annotated video")
    subprocess.run([
        imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
        "-i", str(videos[0]), "-map", "0:v:0", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
    ], check=True, timeout=45)


@app.post("/infer")
async def infer(request: Request):
    header = request.headers.get("authorization", "")
    if not secrets.compare_digest(header.encode(), ("Bearer " + TOKEN).encode()):
        raise HTTPException(401, "Unauthorized")
    extension = request.headers.get("x-video-extension", ".mp4")
    if extension not in {".mp4", ".mov", ".m4v", ".avi", ".webm", ".mkv"}:
        raise HTTPException(400, "Unsupported video format")
    if not slot.acquire(blocking=False):
        raise HTTPException(503, "GPU busy")

    temporary = None
    try:
        temporary = TemporaryDirectory()
        folder = Path(temporary.name)
        source, output = folder / ("input" + extension), folder / "output.mp4"
        size = 0
        with source.open("wb") as video:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_VIDEO:
                    raise HTTPException(413, "Video exceeds 200 MB")
                video.write(chunk)
        if not size:
            raise HTTPException(400, "Missing video")
        await run_in_threadpool(detect, folder, source, output)
        response = FileResponse(output, media_type="video/mp4", background=BackgroundTask(temporary.cleanup))
        temporary = None
        return response
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise HTTPException(502, "YOLO inference failed") from exc
    finally:
        if temporary is not None:
            temporary.cleanup()
        slot.release()

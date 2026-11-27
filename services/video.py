"""事故影片 → 關鍵畫面(供 AI 看、也附在和解書附件)。"""
import base64, math, os
import cv2


MAX_ANALYSIS_FRAMES = 96


def extract_keyframes(video_path: str, out_dir: str):
    os.makedirs(out_dir, exist_ok=True)
    cap = cv2.VideoCapture(video_path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = cap.get(cv2.CAP_PROP_FPS)
    out = []
    try:
        if total <= 0 or not math.isfinite(fps) or fps <= 0:
            raise ValueError("無法讀取影片時間軸，請改用 MP4 / MOV 格式")
        duration = total / fps
        count = min(total, MAX_ANALYSIS_FRAMES, max(2, math.ceil(duration * 4) + 1))
        next_frame = 0
        for i in range(count):
            idx = round(i * (total - 1) / max(1, count - 1))
            if not 0 <= idx - next_frame <= 24:
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            else:
                for _ in range(next_frame, idx):
                    if not cap.grab():
                        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                        break
            ok, fr = cap.read()
            next_frame = idx + 1 if ok else -25
            if not ok:
                continue
            h, w = fr.shape[:2]
            scale = min(1, 1280 / max(h, w))
            if scale < 1:
                fr = cv2.resize(fr, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
            path = os.path.join(out_dir, f"frame_{i}.jpg")
            if cv2.imwrite(path, fr, [cv2.IMWRITE_JPEG_QUALITY, 88]):
                out.append({"path": path, "t": round(idx / fps, 3),
                            "decoded_time": cap.get(cv2.CAP_PROP_POS_MSEC) / 1000})
    finally:
        cap.release()
    if not out:
        raise ValueError("影片中沒有可用的畫面")
    decoded_times = [round(frame.pop("decoded_time"), 3) for frame in out]
    if (all(math.isfinite(t) and t >= 0 for t in decoded_times)
            and all(a < b for a, b in zip(decoded_times, decoded_times[1:]))):
        for frame, timestamp in zip(out, decoded_times, strict=True):
            frame["t"] = timestamp
        duration = max(duration, decoded_times[-1])
    return out, round(duration, 3), count


def to_data_uris(frames):
    uris = []
    for f in frames:
        with open(f["path"], "rb") as image:
            uris.append("data:image/jpeg;base64," + base64.b64encode(image.read()).decode())
    return uris

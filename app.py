"""AI 車禍和解書:事故描述/影片 → AI 追問 → 人工確認 → PDF → 簽署。
執行: python -m uvicorn app:app --reload   開 http://localhost:8000
"""
import json
import logging
import os
import secrets
import smtplib
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from functools import wraps
from pathlib import Path
from threading import Lock, Thread
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from services.api_log import record as record_api_response
from fastapi.staticfiles import StaticFiles
from fastapi.exception_handlers import request_validation_exception_handler
from pydantic import BaseModel, ConfigDict, Field
try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass
from services import compdf, dottedsign, gemini_video, glows_lifecycle, glows_yolo, mixroute, signing

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.abspath(os.getenv("ACCIDENT_DATA_DIR") or os.path.join(BASE, "data"))
os.makedirs(DATA, exist_ok=True)
FREE_PREVIEW = os.getenv("ACCIDENT_FREE_PREVIEW") == "1"
DOTTEDSIGN_ONLY = FREE_PREVIEW and dottedsign.configured() and dottedsign.environment() == "sandbox"
MAX_VIDEO = 200 * 1024 * 1024
MAX_VIDEOS = 1
@asynccontextmanager
async def lifespan(_app):
    if (Path(DATA) / "glows-pending-release").is_file():
        Thread(target=glows_lifecycle.recover_on_startup, args=(DATA,), daemon=True).start()
    yield


app = FastAPI(title="AI 車禍和解書", lifespan=lifespan)
CASES: dict[str, dict] = {}          # demo 用記憶體儲存;正式版請換資料庫
logger = logging.getLogger(__name__)
ANALYSIS_JOBS = {}
ANALYSIS_JOB_TOKENS = {}
SIGNING = signing.Store(Path(DATA) / "signing.sqlite3")
PRIVATE_PDF = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/api/signing/"):
        return JSONResponse({"detail": "簽署或驗證資料格式有誤"}, status_code=422,
                            headers={"Cache-Control": "no-store"})
    return await request_validation_exception_handler(request, exc)


class Answers(BaseModel):
    answers: dict[str, str]


class PdfReq(BaseModel):
    revision: str
    confirmed: bool = False           # 使用者確認內容正確
    injured_ack: bool = False         # 有人受傷時,另需確認已報警/就醫
    email_a: str = ""
    email_b: str = ""
    use_dottedsign: bool = False


class Signature(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image: str = Field(max_length=750_000)
    confirmed: bool


class Verification(BaseModel):
    code: str = Field(pattern=r"^[0-9]{8}$")


def require_owner(case, request):
    value = signing.bearer(request.headers.get("authorization"))
    if not secrets.compare_digest(signing.digest(value), signing.digest(case["owner_token"])):
        raise HTTPException(403, "只有發起人可以管理這個案件")


def credential(request):
    return signing.bearer(request.headers.get("authorization"))


def get_case(cid):
    if cid not in CASES:
        raise HTTPException(404, "找不到案件")
    return CASES[cid]


def set_media_cookie(response, request, case):
    response.headers["Cache-Control"] = "no-store"
    secure = request.url.scheme == "https" or os.getenv("RENDER_EXTERNAL_URL", "").startswith("https://")
    response.set_cookie("case_media", case["media_token"], path=f"/api/cases/{case['id']}",
                        httponly=True, samesite="strict", secure=secure)


def require_media_access(case, request):
    provided = request.cookies.get("case_media", "")
    if not secrets.compare_digest(signing.digest(provided), signing.digest(case["media_token"])):
        raise HTTPException(404, "找不到案件影像")


@app.get("/healthz", include_in_schema=False)
def health():
    return {"status": "ok"}


def public(c, *, created=False):
    draft_url = None
    if c["agent_state"] in ("awaiting_review", "approved"):
        draft_url = f"/api/cases/{c['id']}/draft/{c['revision']}"
    return {"id": c["id"], "data": c["data"], "missing": c["missing"], "missing_total": c["missing_total"],
            "agent_state": c["agent_state"], "revision": c["revision"], "draft_url": draft_url,
            "video_url": f"/api/cases/{c['id']}/video" if c.get("videos") else None,
            "videos": [{"name": item["name"], "url": f"/api/cases/{c['id']}/video/{i}",
                        "analyzed": item["analyzed"]} for i, item in enumerate(c.get("videos", []))],
            "yolo_url": f"/api/cases/{c['id']}/yolo-video" if c.get("yolo_path") else None,
            "yolo_error": c.get("yolo_error"),
            "video_errors": c.get("video_errors", []),
            "summary_warning": c.get("summary_warning"),
            "frames": [{"url": f"/api/cases/{c['id']}/frame/{i}", "t": f["t"]} for i, f in enumerate(c["frames"])],
            "status": c["status"], "signing_enabled": not FREE_PREVIEW or DOTTEDSIGN_ONLY,
            "dottedsign_only": DOTTEDSIGN_ONLY,
            **({"owner_token": c["owner_token"]} if created else {})}


def signing_replay(case):
    data = case["data"]
    scenario = mixroute.normalize_scenario(data.get("scenario"))
    example_rollover = scenario.get("trajectory") == "oncoming_rollover"
    return {
        "collision_type": data.get("collision_type"),
        "maneuver_party": data.get("maneuver_party"),
        "relative_crossing": None if example_rollover else data.get("relative_crossing"),
        "party_a": {"vehicle": "汽車" if example_rollover else data["party_a"]["vehicle"]},
        "party_b": {"vehicle": "汽車" if example_rollover else data["party_b"]["vehicle"]},
        "fault_hint": {key: data["fault_hint"][key] for key in ("a_percent", "b_percent")},
        "scenario": scenario,
        "reconstruction": None if example_rollover or case.get("videos") else data.get("reconstruction"),
    }


def signing_record_path(case):
    return Path(DATA) / f"{case['id']}_{case['revision']}_dottedsign.json"


def signing_record(case):
    path = signing_record_path(case)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def case_locked(handler):
    @wraps(handler)
    def wrapped(cid, *args, **kwargs):
        with get_case(cid)["lock"]:
            return handler(cid, *args, **kwargs)
    return wrapped


def draft_path(case):
    return os.path.join(DATA, f"{case['id']}_{case['revision']}_draft.pdf")


def advance_case(case):
    if mixroute.compute_missing(case["data"], case["answers"], required_only=True)[1]:
        case["agent_state"] = "needs_answers"
        return
    if case["agent_state"] in ("awaiting_review", "approved"):
        return

    path = draft_path(case)
    temporary = path + ".tmp"
    for attempt in range(2):
        try:
            case["signature_page"] = compdf.build_pdf(case["data"], temporary, frames=case["frames"])
            os.replace(temporary, path)
        except Exception:
            logger.exception("Draft generation failed for case %s (attempt %s)", case["id"], attempt + 1)
            try:
                os.remove(temporary)
            except FileNotFoundError:
                pass
            except OSError:
                logger.exception("Could not remove partial draft for case %s", case["id"])
        else:
            case["agent_state"] = "awaiting_review"
            return
    case["agent_state"] = "draft_failed"


def start_case(case):
    case["lock"] = Lock()
    case["owner_token"] = signing.token()
    case["media_token"] = signing.token()
    invalidate_pdf(case)
    mixroute.reanalyze(case)
    advance_case(case)
    CASES[case["id"]] = case
    return public(case, created=True)


@app.get("/api/mode")
def mode():
    return {"mixroute": "real" if mixroute.model_configured() else "mock", "compdf": "real" if compdf.ENGINE == "compdf" else "mock",
            "compdf_configured": bool(os.getenv("COMPDF_API_KEY", "").strip()),
            "glows_yolo_configured": (
                glows_lifecycle.configured() if glows_lifecycle.enabled() else glows_yolo.configured()
            ),
            "glows_yolo_auto": glows_lifecycle.enabled(),
            "dottedsign": dottedsign.environment() if dottedsign.configured() else "mock"}


@app.post("/api/demo")
def demo(request: Request, response: Response):
    cid = uuid.uuid4().hex
    narrative = (
        "11月20日下午3點10分在中山路與民權路口，"
        "甲方駕駛汽車 DEM-1001 停等紅燈，被乙方駕駛汽車 DEM-1002 從後方追撞。"
        "車尾保險桿凹陷，無人受傷。雙方商議維修費為 8000 元。"
    )
    case = {
        "id": cid, "narrative": narrative, "answers": {}, "qtext": {},
        "frames": [], "sigs": {},
        "status": "draft", "demo": True,
    }
    result = start_case(case)
    set_media_cookie(response, request, case)
    return result


def stage_analysis(text, files, location=""):
    files = [file for file in files if file.filename]
    if len(files) > MAX_VIDEOS:
        raise HTTPException(400, "每個案件最多上傳 1 支影片。")
    location = location.strip()
    if len(location) > 300:
        raise HTTPException(400, "事故地址最多 300 個字。")
    if len(text.strip()) < 10 and not files and not location:
        raise HTTPException(400, "請至少描述時間、地點與經過,或上傳事故影片")
    cid = uuid.uuid4().hex
    folder = os.path.join(DATA, cid)
    os.makedirs(folder, exist_ok=True)
    videos = []
    for i, file in enumerate(files):
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in (".mp4", ".mov", ".m4v", ".avi", ".webm", ".mkv"):
            raise HTTPException(400, "影片格式請用 mp4 / mov")
        vp, size = os.path.join(folder, f"video_{i}" + ext), 0
        with open(vp, "wb") as out:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_VIDEO:
                    raise HTTPException(400, "影片超過 200MB,請剪短後再上傳")
                out.write(chunk)
        videos.append({"path": vp, "name": file.filename, "analyzed": False})
    return cid, text.strip(), videos, location


def complete_analysis(cid, text, videos, progress=None, location=""):
    yolo_path = yolo_error = None
    with ThreadPoolExecutor(max_workers=1) as worker:
        detection = None
        auto_gpu = glows_lifecycle.enabled() and glows_lifecycle.configured()
        if videos and auto_gpu:
            detection = worker.submit(glows_lifecycle.detect, videos[0]["path"], DATA, glows_yolo.infer)
        elif videos and glows_yolo.configured() and not glows_lifecycle.enabled():
            detection = worker.submit(glows_yolo.detect, videos[0]["path"])
        descriptions, errors = describe_videos(cid, videos, progress)
        if detection is not None:
            if progress and not detection.done():
                progress("detecting_video")
            try:
                yolo_path = detection.result()
            except ValueError as exc:
                yolo_error = str(exc)
                logger.warning("Glows.ai inference failed for case %s: %s", cid, yolo_error)
    if videos and not descriptions and not (yolo_path and (text.strip() or location)):
        raise ValueError("影片事故經過未能完成分析。 " + " ".join(errors))
    if progress:
        progress("summarizing")
    return start_case({
        "id": cid, "narrative": text.strip(), "selected_location": location, "answers": {}, "qtext": {},
        "frames": [], "videos": videos, "video_description": "\n\n".join(descriptions), "video_errors": errors,
        "yolo_path": yolo_path, "yolo_error": yolo_error, "sigs": {}, "status": "draft",
    })


def describe_videos(cid, videos, progress):
    descriptions, errors = [], []
    for index, item in enumerate(videos, 1):
        def report(stage):
            if progress:
                progress(stage, current=index, total=len(videos))

        try:
            caption = gemini_video.describe(item["path"], report)
        except ValueError as error:
            errors.append(f"影片 {index}：{error}")
            record_api_response("gemini_video", case_id=cid, video=index, filename=item["name"], error=str(error))
        else:
            descriptions.append(f"影片 {index}（{item['name']}）\n{caption}")
            record_api_response("gemini_video", case_id=cid, video=index, filename=item["name"], response=caption)
            item["analyzed"] = True
    return descriptions, errors


@app.post("/api/analyze")
def analyze(request: Request, response: Response, text: str = Form(""), file: list[UploadFile] = File(default=[]), location: str = Form("")):
    cid, text, videos, location = stage_analysis(text, file, location)
    try:
        result = complete_analysis(cid, text, videos, location=location)
        set_media_cookie(response, request, get_case(cid))
        return result
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


def run_analysis_job(cid, text, videos, location):
    def progress(stage, **details):
        ANALYSIS_JOBS[cid] = {"stage": stage, **details}

    try:
        result = complete_analysis(cid, text, videos, progress, location)
    except ValueError as error:
        progress("failed", message=str(error))
    except Exception:
        logger.exception("Analysis failed for case %s", cid)
        progress("failed", message="分析失敗，請重新上傳影片或查看服務紀錄。")
    else:
        progress("done", result=result)


@app.post("/api/analysis-jobs", status_code=202)
def create_analysis_job(background: BackgroundTasks, text: str = Form(""), file: list[UploadFile] = File(...),
                        location: str = Form("")):
    cid, text, videos, location = stage_analysis(text, file, location)
    ANALYSIS_JOBS[cid] = {"stage": "sending_video"}
    job_token = signing.token()
    ANALYSIS_JOB_TOKENS[cid] = signing.digest(job_token)
    background.add_task(run_analysis_job, cid, text, videos, location)
    return {"job_id": cid, "job_token": job_token}


@app.get("/api/analysis-jobs/{cid}")
async def get_analysis_job(cid: str, request: Request, response: Response):
    stored = ANALYSIS_JOB_TOKENS.get(cid, "")
    if not secrets.compare_digest(stored, signing.digest(credential(request))):
        raise HTTPException(404, "找不到分析工作，服務可能已重啟。")
    job = ANALYSIS_JOBS[cid]
    if job["stage"] == "done":
        set_media_cookie(response, request, get_case(cid))
    return job


@app.post("/api/cases/{cid}/answers")
@case_locked
def answer(cid: str, a: Answers, request: Request):
    c = get_case(cid)
    require_owner(c, request)
    if signing_record(c):
        raise HTTPException(409, "此版本已有 DottedSign 送簽紀錄，請保留原文件或另開新案件")
    before = c["answers"].copy()
    for key, value in a.answers.items():
        if value.strip():
            c["answers"][key] = value
        else:
            c["answers"].pop(key, None)
    if c["answers"] != before:
        try:
            mixroute.reanalyze(c)
        except ValueError as error:
            c["answers"] = before
            raise HTTPException(502, str(error)) from error
        invalidate_pdf(c)
    advance_case(c)
    return public(c)


def invalidate_pdf(case):
    if case.get("revision"):
        SIGNING.revoke(case["id"], case["revision"])
    case["status"] = "draft"
    case["revision"] = uuid.uuid4().hex
    case.pop("previewed_revision", None)
    case["agent_state"] = "needs_answers"
    case["sigs"].clear()


@app.put("/api/cases/{cid}")
@case_locked
def update(cid: str, data: dict, request: Request):
    c = get_case(cid)
    require_owner(c, request)
    normalized = mixroute.normalize(data)
    if normalized != c["data"]:
        if signing_record(c):
            raise HTTPException(409, "此版本已有 DottedSign 送簽紀錄，請保留原文件或另開新案件")
        invalidate_pdf(c)
        c["data"] = normalized
        c["missing"], c["missing_total"] = mixroute.compute_missing(normalized, c["answers"])
    advance_case(c)
    return public(c)


@app.post("/api/cases/{cid}/revoke")
@case_locked
def revoke_invitations(cid: str, request: Request):
    case = get_case(cid)
    require_owner(case, request)
    invalidate_pdf(case)
    return {"revoked": True}


@app.get("/api/cases/{cid}/draft/{revision}")
@case_locked
def preview_draft(cid: str, revision: str, request: Request):
    c = get_case(cid)
    require_owner(c, request)
    path = draft_path(c)
    if revision != c["revision"] or c["agent_state"] not in ("awaiting_review", "approved") or not os.path.isfile(path):
        raise HTTPException(404, "目前沒有可預覽的草稿")
    c["previewed_revision"] = revision
    return FileResponse(path, media_type="application/pdf", headers=PRIVATE_PDF)


@app.get("/api/cases/{cid}/frame/{i}")
def frame(cid: str, i: int, request: Request):
    c = get_case(cid)
    require_media_access(c, request)
    if not 0 <= i < len(c["frames"]):
        raise HTTPException(404, "沒有這張畫面")
    return FileResponse(c["frames"][i]["path"], media_type="image/jpeg", headers=PRIVATE_PDF)


@app.get("/api/cases/{cid}/video")
@app.get("/api/cases/{cid}/video/{index}")
def original_video(cid: str, request: Request, index: int = 0):
    case = get_case(cid)
    require_media_access(case, request)
    videos = case.get("videos", [])
    if not 0 <= index < len(videos) or not os.path.isfile(videos[index]["path"]):
        raise HTTPException(404, "找不到原始影片")
    return FileResponse(videos[index]["path"], headers={"Cache-Control": "no-store"})


@app.get("/api/cases/{cid}/yolo-video")
def yolo_video(cid: str, request: Request):
    case = get_case(cid)
    require_media_access(case, request)
    path = case.get("yolo_path")
    if not path or not os.path.isfile(path):
        raise HTTPException(404, "找不到 YOLO 辨識影片")
    return FileResponse(path, media_type="video/mp4", headers=PRIVATE_PDF)


@app.post("/api/cases/{cid}/pdf")
@case_locked
def make_pdf(cid: str, r: PdfReq, request: Request):
    if FREE_PREVIEW and not (DOTTEDSIGN_ONLY and r.use_dottedsign):
        raise HTTPException(403, "公開展示版僅支援 DottedSign Sandbox 送簽")
    c = get_case(cid)
    require_owner(c, request)
    if not r.confirmed:
        raise HTTPException(400, "請先確認內容正確")
    if r.revision != c["revision"]:
        raise HTTPException(409, "案件內容已更新，請重新核對草稿")
    if c["agent_state"] not in ("awaiting_review", "approved"):
        raise HTTPException(409, "請先補齊資料並確認草稿已產生")
    if c.get("previewed_revision") != r.revision:
        raise HTTPException(409, "請先開啟這一版送簽 PDF 核對")
    if mixroute.compute_missing(c["data"], c["answers"], required_only=True)[1]:
        raise HTTPException(409, "請先補齊和解資料並重新核對草稿")
    if c["data"]["injured"] == "yes" and not r.injured_ack:
        raise HTTPException(400, "有人受傷時,請先確認已報警/就醫")
    emails = tuple(email.strip() for email in (r.email_a, r.email_b))
    if any(len(email) > 254 or not dottedsign.EMAIL.fullmatch(email) for email in emails):
        raise HTTPException(422, "請填寫甲乙雙方有效的電子郵件")
    if emails[0].casefold() == emails[1].casefold():
        raise HTTPException(422, "甲乙雙方請使用不同的電子郵件")
    record = signing_record(c)
    if not r.use_dottedsign:
        if record:
            raise HTTPException(409, "此版本已有 DottedSign 任務，請維持原簽署方式")
        mailer = signing.Mailer()
        SIGNING.create(cid, c["revision"], signing.digest(c["owner_token"]),
                       Path(draft_path(c)).read_bytes(), c["signature_page"], emails,
                       replay=json.dumps(signing_replay(c), ensure_ascii=False))
        c["agent_state"] = "approved"
        return {"provider": "local", "delivery_failed": deliver_invitations(cid, c["revision"], mailer)}
    if SIGNING.has_document(cid, c["revision"]):
        raise HTTPException(409, "此版本已有自建簽署任務，請維持原簽署方式")
    if not dottedsign.configured():
        raise HTTPException(503, "請在本機 .env 設定 DOTTEDSIGN_CLIENT_ID 與 DOTTEDSIGN_CLIENT_SECRET")
    if dottedsign.environment() != "sandbox":
        raise HTTPException(503, "本機送簽目前使用 DottedSign Sandbox，請設定 DOTTEDSIGN_ENV=sandbox")
    email_digest = signing.digest("\n".join(email.casefold() for email in emails))
    if record and (record["environment"] != "sandbox" or record.get("email_digest") != email_digest):
        raise HTTPException(409, "此版本已有送簽紀錄，請使用原信箱或另開案件")
    try:
        client = dottedsign.Client()
        if record is None:
            boxes = compdf.signature_boxes(Path(draft_path(c)).read_bytes())
            task_id = client.create_task(
                draft_path(c), "交通事故和解書.pdf", zip(("甲方", "乙方"), emails),
                (boxes["A"], boxes["B"]), signature_page=c["signature_page"],
            )
            record = {"task_id": task_id, "environment": "sandbox", "email_digest": email_digest}
            path = signing_record_path(c)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(record), encoding="utf-8")
            temporary.replace(path)
            status = "draft"
        else:
            status = client.get_task(record["task_id"])["status"]
        if status == "draft":
            client.start_task(record["task_id"])
        elif status not in ("waiting", "completed"):
            raise HTTPException(409, "此 DottedSign 任務已結束，請另開案件送簽")
    except (dottedsign.ServiceError, ValueError, KeyError) as exc:
        logger.warning("DottedSign dispatch failed for case %s (%s)", cid,
                       str(exc) if isinstance(exc, dottedsign.ServiceError) else type(exc).__name__)
        raise HTTPException(502, "DottedSign 暫時無法送簽，請檢查 Sandbox 設定後再試") from exc
    c["agent_state"] = "approved"
    return {"provider": "dottedsign", "task_id": record["task_id"]}


def deliver_invitations(cid, revision, mailer):
    failed = []
    for role in ("A", "B"):
        try:
            SIGNING.deliver(cid, revision, role, mailer)
        except (OSError, smtplib.SMTPException, ValueError) as exc:
            logger.warning("Signing invite delivery failed for %s role %s (%s)", cid, role, type(exc).__name__)
            failed.append(role)
    return failed


@app.get("/api/signing/owner/{revision}")
def owner_status(revision: str, request: Request):
    return SIGNING.status(credential(request), revision)


@app.get("/api/signing/owner/{revision}/replay")
def owner_replay(revision: str, request: Request):
    with SIGNING.transaction() as db:
        doc = SIGNING.owner_document(db, credential(request), revision)
        if doc["superseded"]:
            raise HTTPException(409, "此版本已撤銷")
        cid = doc["case_id"]
        row = db.execute(
            "SELECT payload FROM document_replays WHERE case_id = ? AND revision = ?", (cid, revision)
        ).fetchone()
    if row:
        return Response(row["payload"], media_type="application/json", headers=PRIVATE_PDF)

    case = CASES.get(cid)
    if case:
        with case["lock"]:
            if case["revision"] == revision:
                return JSONResponse(signing_replay(case), headers=PRIVATE_PDF)
    raise HTTPException(404, "此送簽版本沒有可下載的重播資料。")


@app.post("/api/signing/owner/{revision}/retry")
def retry_delivery(revision: str, request: Request):
    with SIGNING.transaction() as db:
        doc = SIGNING.owner_document(db, credential(request), revision)
        if doc["superseded"]:
            raise HTTPException(409, "此版本已撤銷")
        cid = doc["case_id"]
    return {"delivery_failed": deliver_invitations(cid, revision, signing.Mailer())}


@app.get("/api/signing/owner/{revision}/pdf")
def owner_pdf(revision: str, request: Request):
    with SIGNING.transaction() as db:
        doc = SIGNING.owner_document(db, credential(request), revision)
        if not doc["final_pdf"]:
            raise HTTPException(409, "雙方尚未完成簽署")
        return Response(doc["final_pdf"], media_type="application/pdf", headers={
            **PRIVATE_PDF, "Content-Disposition": 'attachment; filename="settlement-signed.pdf"'})


@app.post("/api/signing/code")
def request_code(request: Request):
    try:
        SIGNING.send_code(credential(request), signing.Mailer())
    except (OSError, smtplib.SMTPException, ValueError) as exc:
        raise HTTPException(502, "驗證碼寄送失敗，請稍後再試") from exc
    return {"sent": True}


@app.post("/api/signing/verify")
def verify_signer(request: Request, body: Verification):
    session = SIGNING.verify(credential(request), body.code)
    return Response(json.dumps({"session": session}), media_type="application/json",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/signing/session")
def signer_session(request: Request):
    with SIGNING.transaction() as db:
        row = SIGNING.invite(db, credential(request), verified=True)
        return Response(json.dumps({"role": row["role"], "revision": row["revision"], "sha256": row["sha256"]}),
                        media_type="application/json", headers={"Cache-Control": "no-store"})


@app.get("/api/signing/pdf")
def signer_pdf(request: Request):
    with SIGNING.transaction() as db:
        row = SIGNING.invite(db, credential(request), verified=True)
        doc = SIGNING.document(db, row)
        if signing.digest(doc["pdf"]) != doc["sha256"]:
            raise HTTPException(409, "此文件無法驗證，請聯絡發起人")
        return Response(doc["pdf"], media_type="application/pdf", headers=PRIVATE_PDF)


@app.post("/api/signing/submit")
def submit_signature(request: Request, body: Signature):
    value = credential(request)
    if not body.confirmed:
        raise HTTPException(422, "請先確認已閱讀並同意這一版和解書")
    return SIGNING.submit(value, body.image)


@app.get("/api/cases/{cid}/signing-status")
@case_locked
def signing_status(cid: str, revision: str, request: Request):
    c = get_case(cid)
    require_owner(c, request)
    record = signing_record(c)
    if revision != c["revision"] or not record or not record["task_id"]:
        raise HTTPException(404, "找不到此版本的 DottedSign 任務")
    if record["environment"] != dottedsign.environment() or not dottedsign.configured():
        raise HTTPException(503, "請恢復送簽時使用的 DottedSign 環境設定")
    try:
        client = dottedsign.Client()
        status = client.get_task(record["task_id"])["status"]
        if status == "completed" and c["status"] != "signed":
            path = Path(DATA) / f"{cid}_signed.pdf"
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(client.signed_pdf(record["task_id"]))
            temporary.replace(path)
            c["status"] = "signed"
    except (dottedsign.ServiceError, ValueError, KeyError) as exc:
        raise HTTPException(502, "暫時無法取得 DottedSign 簽署結果，請再試一次") from exc
    return {"status": status, "task_id": record["task_id"], "signed_pdf_url": f"/api/cases/{cid}/file" if c["status"] == "signed" else None}


@app.get("/api/cases/{cid}/signing-replay")
@case_locked
def dottedsign_replay(cid: str, revision: str, request: Request):
    c = get_case(cid)
    require_owner(c, request)
    if revision != c["revision"] or not signing_record(c):
        raise HTTPException(404, "找不到此送簽版本的重播資料")
    return JSONResponse(signing_replay(c), headers=PRIVATE_PDF)


@app.get("/api/cases/{cid}/file")
@case_locked
def file(cid: str, request: Request):
    c = get_case(cid)
    require_owner(c, request)
    p = os.path.join(DATA, f"{cid}{'_signed' if c['status'] == 'signed' else ''}.pdf")
    if c["status"] == "draft" or not os.path.exists(p):
        raise HTTPException(404, "尚未產生")
    return FileResponse(p, media_type="application/pdf", headers=PRIVATE_PDF)


app.mount("/", StaticFiles(directory=os.path.join(BASE, "static"), html=True))

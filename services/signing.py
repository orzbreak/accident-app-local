"""Persistent signing records and mail delivery for the first-party signing flow."""
import base64
import binascii
import hashlib
import hmac
import io
import os
import secrets
import smtplib
import sqlite3
import ssl
import time
from contextlib import contextmanager
from email.message import EmailMessage
from threading import Lock
from urllib.parse import urlsplit

from fastapi import HTTPException
from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from services.compdf import SIG_BOX

INVITE_TTL = 7 * 86400
CODE_TTL = 600
SESSION_TTL = 1800

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    case_id TEXT NOT NULL, revision TEXT NOT NULL, owner TEXT NOT NULL,
    pdf BLOB NOT NULL, sha256 TEXT NOT NULL, signature_page INTEGER NOT NULL,
    created INTEGER NOT NULL, superseded INTEGER, final_pdf BLOB,
    PRIMARY KEY (case_id, revision)
);
CREATE INDEX IF NOT EXISTS documents_owner ON documents(owner);
CREATE TABLE IF NOT EXISTS document_replays (
    case_id TEXT NOT NULL, revision TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY (case_id, revision),
    FOREIGN KEY (case_id, revision) REFERENCES documents(case_id, revision)
);
CREATE TABLE IF NOT EXISTS invitations (
    case_id TEXT NOT NULL, revision TEXT NOT NULL, role TEXT NOT NULL,
    email TEXT NOT NULL, token TEXT UNIQUE, expires INTEGER, sent INTEGER,
    code_hash TEXT, code_expires INTEGER, code_sent INTEGER, attempts INTEGER DEFAULT 0,
    session TEXT UNIQUE, session_expires INTEGER, verified INTEGER,
    signed INTEGER, image BLOB,
    PRIMARY KEY (case_id, revision, role),
    FOREIGN KEY (case_id, revision) REFERENCES documents(case_id, revision)
);
"""


def token():
    return secrets.token_urlsafe(32)


def digest(value):
    if isinstance(value, str):
        value = value.encode()
    return hashlib.sha256(value).hexdigest()


def bearer(header):
    scheme, _, value = (header or "").partition(" ")
    if scheme.lower() != "bearer" or not value or len(value) > 128:
        raise HTTPException(401, "請使用原本的專屬連結")
    return value


class Store:
    def __init__(self, path):
        self.path = path
        self.initialized = False
        self.init_lock = Lock()

    def initialize(self, db):
        if self.initialized:
            return
        with self.init_lock:
            if not self.initialized:
                db.execute("PRAGMA journal_mode = WAL")
                db.executescript(SCHEMA)
                self.initialized = True

    @contextmanager
    def transaction(self, write=False):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys = ON")
            self.initialize(db)
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def revoke(self, cid, revision):
        with self.transaction(write=True) as db:
            db.execute("""
                UPDATE documents SET superseded = ?
                WHERE case_id = ? AND revision = ? AND superseded IS NULL
            """, (int(time.time()), cid, revision))

    def create(self, cid, revision, owner, pdf, page, emails, replay=None):
        with self.transaction(write=True) as db:
            current = db.execute("""
                SELECT email FROM invitations WHERE case_id = ? AND revision = ? ORDER BY role
            """, (cid, revision)).fetchall()
            if current:
                if tuple(row["email"] for row in current) != emails:
                    raise HTTPException(409, "此版本已指定收件人，請先更新文件版本")
            else:
                db.execute("""
                    INSERT INTO documents (case_id, revision, owner, pdf, sha256, signature_page, created)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (cid, revision, owner, pdf, digest(pdf), page, int(time.time())))
                db.executemany("""
                    INSERT INTO invitations (case_id, revision, role, email) VALUES (?, ?, ?, ?)
                """, ((cid, revision, role, email) for role, email in zip(("A", "B"), emails)))
            if replay is not None:
                db.execute("""
                    INSERT INTO document_replays (case_id, revision, payload) VALUES (?, ?, ?)
                    ON CONFLICT (case_id, revision) DO NOTHING
                """, (cid, revision, replay))

    def deliver(self, cid, revision, role, mailer):
        with self.transaction(write=True) as db:
            row = db.execute("""
                SELECT i.*, d.superseded FROM invitations i JOIN documents d USING (case_id, revision)
                WHERE i.case_id = ? AND i.revision = ? AND i.role = ?
            """, (cid, revision, role)).fetchone()
            now = int(time.time())
            if row["superseded"] or row["signed"] or (row["sent"] and row["expires"] > now):
                return
            invitation = token()
            mailer.send(row["email"], "交通事故和解書簽署邀請",
                        f"請開啟以下專屬網頁並驗證受邀信箱。連結 7 天內有效。\n\n"
                        f"{mailer.base}/sign.html#{invitation}")
            db.execute("""
                UPDATE invitations SET token = ?, expires = ?, sent = ?, code_hash = NULL,
                    code_expires = NULL, code_sent = NULL, attempts = 0, session = NULL,
                    session_expires = NULL, verified = NULL
                WHERE case_id = ? AND revision = ? AND role = ?
            """, (digest(invitation), now + INVITE_TTL, now, cid, revision, role))

    @staticmethod
    def invite(db, credential, verified=False):
        column = "session" if verified else "token"
        row = db.execute(f"""
            SELECT i.*, d.superseded, d.sha256 FROM invitations i JOIN documents d USING (case_id, revision)
            WHERE i.{column} = ?
        """, (digest(credential),)).fetchone()
        now = int(time.time())
        if (not row or row["superseded"] or row["signed"] or row["expires"] <= now
                or (verified and row["session_expires"] <= now)):
            raise HTTPException(401, "簽署連結或驗證已失效")
        return row

    def send_code(self, credential, mailer):
        with self.transaction(write=True) as db:
            row = self.invite(db, credential)
            now = int(time.time())
            if row["code_sent"] and now - row["code_sent"] < 60:
                raise HTTPException(429, "請等 60 秒再索取驗證碼")
            code = f"{secrets.randbelow(100_000_000):08d}"
            mailer.send(row["email"], "和解書信箱驗證碼", f"驗證碼為 {code}，10 分鐘內有效。")
            db.execute("""
                UPDATE invitations SET code_hash = ?, code_expires = ?, code_sent = ?, attempts = 0
                WHERE token = ?
            """, (code_digest(credential, code), now + CODE_TTL, now, digest(credential)))

    def verify(self, credential, code):
        session = token()
        with self.transaction(write=True) as db:
            row = self.invite(db, credential)
            now = int(time.time())
            allowed = row["code_hash"] and row["code_expires"] > now and row["attempts"] < 5
            valid = allowed and hmac.compare_digest(row["code_hash"], code_digest(credential, code))
            if valid:
                db.execute("""
                    UPDATE invitations SET session = ?, session_expires = ?, verified = ?, code_hash = NULL
                    WHERE token = ?
                """, (digest(session), now + SESSION_TTL, now, digest(credential)))
            elif allowed:
                db.execute("UPDATE invitations SET attempts = attempts + 1 WHERE token = ?", (digest(credential),))
        if not valid:
            raise HTTPException(400, "驗證碼錯誤或已失效，請重新索取")
        return session

    @staticmethod
    def document(db, row):
        return db.execute("SELECT * FROM documents WHERE case_id = ? AND revision = ?",
                          (row["case_id"], row["revision"])).fetchone()

    def submit(self, credential, image):
        with self.transaction(write=True) as db:
            row = self.invite(db, credential, verified=True)
            signature = decode_signature(image)
            document = self.document(db, row)
            if digest(document["pdf"]) != document["sha256"]:
                raise HTTPException(409, "此文件無法驗證，請聯絡發起人")
            now = int(time.time())
            db.execute("""
                UPDATE invitations SET signed = ?, image = ?, token = NULL, session = NULL, code_hash = NULL
                WHERE session = ?
            """, (now, signature, digest(credential)))
            signatures = db.execute("""
                SELECT role, image FROM invitations WHERE case_id = ? AND revision = ? AND signed IS NOT NULL
            """, (row["case_id"], row["revision"])).fetchall()
            if len(signatures) == 2:
                pdf = signed_pdf(document, signatures)
                db.execute("UPDATE documents SET final_pdf = ? WHERE case_id = ? AND revision = ?",
                           (pdf, row["case_id"], row["revision"]))
            return {"completed": len(signatures) == 2}

    def owner_document(self, db, credential, revision):
        doc = db.execute("SELECT * FROM documents WHERE owner = ? AND revision = ?",
                         (digest(credential), revision)).fetchone()
        if not doc:
            raise HTTPException(404, "找不到此版本的送簽紀錄")
        return doc

    def status(self, credential, revision):
        with self.transaction() as db:
            doc = self.owner_document(db, credential, revision)
            rows = db.execute("""
                SELECT role, sent, signed, expires FROM invitations WHERE case_id = ? AND revision = ? ORDER BY role
            """, (doc["case_id"], revision)).fetchall()
            now = int(time.time())
            return {
                "revision": revision, "sha256": doc["sha256"], "completed": bool(doc["final_pdf"]),
                "superseded": bool(doc["superseded"]),
                "signers": [
                    {"role": row["role"], "confirmed_at": row["signed"], "status":
                     "signed" if row["signed"] else "revoked" if doc["superseded"] else
                     "unsent" if not row["sent"] else "expired" if row["expires"] <= now else "waiting"}
                    for row in rows
                ],
            }


def code_digest(credential, code):
    return hmac.new(credential.encode(), code.encode(), hashlib.sha256).hexdigest()


def decode_signature(value):
    prefix = "data:image/png;base64,"
    try:
        if not value.startswith(prefix):
            raise ValueError
        raw = base64.b64decode(value[len(prefix):], validate=True)
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != "PNG" or not (1 <= image.width <= 4096 and 1 <= image.height <= 1024):
                raise ValueError
            rgba = image.convert("RGBA")
            # Flatten on white to distinguish actual visible strokes from transparent pixels.
            white = Image.new("RGBA", rgba.size, "white")
            visible = Image.alpha_composite(white, rgba).convert("L")
            if visible.getextrema()[0] > 240:
                raise ValueError
            output = io.BytesIO()
            rgba.save(output, format="PNG")
            return output.getvalue()
    except (binascii.Error, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, "請提供有效且有筆跡的手寫簽名") from exc


def signed_pdf(document, signatures):
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(document["pdf"])))
    page = writer.pages[document["signature_page"]]
    buffer = io.BytesIO()
    overlay = Canvas(buffer, pagesize=(float(page.mediabox.width), float(page.mediabox.height)))
    for row in signatures:
        x, y, w, h = SIG_BOX[row["role"]]
        overlay.drawImage(ImageReader(io.BytesIO(row["image"])), x + 4, y + 4, w - 8, h - 8,
                          mask="auto", preserveAspectRatio=True)
    overlay.save()
    page.merge_page(PdfReader(io.BytesIO(buffer.getvalue())).pages[0])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


class Mailer:
    def __init__(self):
        self.base = (os.getenv("SIGNING_PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL", "")).rstrip("/")
        self.host = os.getenv("SIGNING_SMTP_HOST")
        self.sender = os.getenv("SIGNING_SMTP_FROM")
        self.user = os.getenv("SIGNING_SMTP_USER")
        self.password = os.getenv("SIGNING_SMTP_PASSWORD")
        self.security = os.getenv("SIGNING_SMTP_SECURITY", "starttls")
        url = urlsplit(self.base)
        if (not self.host or not self.sender or not url.hostname or url.path or url.query or url.fragment
                or url.username or url.password or self.security not in ("starttls", "ssl")
                or (url.scheme != "https" and not (url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1")))):
            raise HTTPException(503, "請先設定 App 網址及簽署郵件服務")
        try:
            self.port = int(os.getenv("SIGNING_SMTP_PORT", "465" if self.security == "ssl" else "587"))
        except ValueError as exc:
            raise HTTPException(503, "簽署郵件服務連接埠設定有誤") from exc

    def send(self, to, subject, body):
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
        msg.set_content(body)
        tls = ssl.create_default_context()
        if self.security == "ssl":
            client = smtplib.SMTP_SSL(self.host, self.port, timeout=8, context=tls)
        else:
            client = smtplib.SMTP(self.host, self.port, timeout=8)
        with client:
            if self.security == "starttls":
                client.starttls(context=tls)
            if self.user:
                client.login(self.user, self.password or "")
            client.send_message(msg)


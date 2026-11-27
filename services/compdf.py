"""Generate the agreement with ComPDF or the local PDF renderer."""
import logging
import base64, io
from datetime import date
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

import glob, os
# 優先嵌入 fonts/ 內的 TTF(例如 NotoSansTC-Regular.ttf),確保任何閱讀器都不缺字;
# 找不到時退回內建 CID 字型(需要閱讀器有中文字型)。
_ttf = os.getenv("PDF_FONT") or next(iter(glob.glob(os.path.join(os.path.dirname(__file__), "..", "fonts", "*.ttf"))), None)
if not _ttf and os.name == "nt":
    windows_font = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "msjh.ttc")
    if os.path.isfile(windows_font):
        _ttf = windows_font
if _ttf:
    pdfmetrics.registerFont(TTFont("CJK", _ttf)); FONT = "CJK"
else:
    pdfmetrics.registerFont(UnicodeCIDFont("MSung-Light")); FONT = "MSung-Light"
SIGNATURE_MARKER = "https://example.invalid/settlement-signature-slot"
SIGNATURE_Y_KEY = "/SettlementSignatureY"
SIG_BOX = {"A": (60, 150, 220, 70), "B": (315, 150, 220, 70)}   # x, y, w, h


def party_details(party):
    details = [party["name"], party["vehicle"]]
    if party["vehicle"] != "行人" and party["plate"]:
        details.append(f"車牌 {party['plate']}")
    return "  ".join(item for item in details if item)


def agreement_rows(case):
    terms = case.get("settlement") or {}
    names = {"A": "甲方", "B": "乙方"}
    amount = case.get("amount")
    payer = terms.get("payer")
    if amount == 0:
        payment = "本和解所列項目，雙方互不請求金錢給付。"
    elif amount is None:
        payment = "和解金額待雙方確認。"
    else:
        sender = names.get(payer, "付款方待確認")
        receiver = names.get({"A": "B", "B": "A"}.get(payer), "收款方待確認")
        payment = f"{sender}給付{receiver}新臺幣 {amount:,} 元整。"
        if terms.get("payment_details"):
            payment += "\n" + terms["payment_details"]
    rows = [
        ("事故時間", case["time"]), ("事故地點", case["location"]),
        ("甲方", "\n".join(filter(None, (party_details(case["party_a"]), terms.get("contact_a"))))),
        ("乙方", "\n".join(filter(None, (party_details(case["party_b"]), terms.get("contact_b"))))),
        ("事故經過", case["description"]), ("車輛損害", case["damage"]),
        ("人員受傷", {"yes": "有人受傷", "no": "無人受傷"}.get(case.get("injured"), "待確認")),
        ("和解範圍", terms.get("scope") or "雙方就本書明列的損害及給付項目達成協議。"),
        ("金額與給付", payment),
        ("權利保留", ("給付完成後，" if amount and amount > 0 else "") +
         "雙方就本書已和解的項目不再重複請求。未列入的項目及依法得主張的權利，依各該情形處理。"),
        ("保險約定", terms.get("insurance") or "雙方未另約定保險給付如何計入和解金額。保險請求依相關法令及保險契約處理。"),
        ("文件留存", "甲乙雙方各留存一份相同內容的和解書。"),
    ]
    if amount and amount > 0:
        rows.insert(9, ("履行與收據", "付款方保留付款憑證。收款方收到款項後，依雙方約定確認收訖。"))
    for key, label in (("case_reference", "事故案號"), ("additional", "其他約定"), ("signing_place", "立書地點")):
        if terms.get(key):
            rows.append((label, terms[key]))
    return rows


def agreement_sections(case):
    rows = agreement_rows(case)
    return [
        ("一、當事人與事故資料", rows[:7]),
        ("二、和解金額與給付", [row for row in rows[7:] if row[0] in ("金額與給付", "履行與收據")]),
        ("三、和解範圍與保險", [row for row in rows[7:] if row[0] in ("和解範圍", "權利保留", "保險約定")]),
        ("四、其他約定與文件留存", [row for row in rows[7:] if row[0] not in (
            "金額與給付", "履行與收據", "和解範圍", "權利保留", "保險約定")]),
    ]


def _wrap(c, text, x, y, width, size=11, lead=17):
    line = ""
    for ch in text:
        if c.stringWidth(line + ch, FONT, size) > width or ch == "\n":
            c.drawString(x, y, line); y -= lead; line = "" if ch == "\n" else ch
        else:
            line += ch
    if line:
        c.drawString(x, y, line); y -= lead
    return y


def signature_boxes(pdf: bytes):
    metadata = PdfReader(io.BytesIO(pdf)).metadata or {}
    base_y = float(metadata.get(SIGNATURE_Y_KEY, SIG_BOX["A"][1]))
    return {role: (x, base_y, width, height) for role, (x, _, width, height) in SIG_BOX.items()}


def _build_local(case: dict, path: str, sigs: dict | None = None, frames: list | None = None, *, draft=False):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    W, H = A4
    title = "道路交通事故和解書（待確認草稿）" if draft else "道路交通事故和解書"
    c.setFillColorRGB(.10, .16, .24)
    c.setFont(FONT, 22); c.drawCentredString(W / 2, H - 61, title)
    c.setStrokeColorRGB(.66, .72, .78)
    c.line(60, H - 75, W - 60, H - 75)
    y = H - 101
    page = 0
    c.setFont(FONT, 11)
    def continuation():
        nonlocal y, page
        c.showPage()
        page += 1
        y = H - 79
        c.setFont(FONT, 11)
        c.setFillColorRGB(.10, .16, .24)
        c.drawString(60, H - 48, title + "（續）")

    def section_heading(heading):
        nonlocal y
        c.setFillColorRGB(.91, .94, .97)
        c.rect(60, y - 5, W - 120, 19, fill=1, stroke=0)
        c.setFillColorRGB(.10, .16, .24)
        c.setFont(FONT, 11)
        c.drawString(68, y, heading)
        y -= 24

    for heading, rows in agreement_sections(case):
        if y < 105:
            continuation()
        section_heading(heading)
        for label, value in rows:
            if y < 60:
                continuation()
                section_heading(heading + "（續）")
            c.setFillColorRGB(.26, .33, .42)
            c.drawString(64, y, label)
            c.setFillColorRGB(.10, .16, .24)
            line = ""
            font_size = 12 if label == "金額與給付" else 11
            c.setFont(FONT, font_size)
            for ch in (value or "（待補）") + "\n":
                if ch == "\n" or (ch not in "，。；：！？、）」』" and
                                   c.stringWidth(line + ch, FONT, font_size) > W - 222):
                    if y < 60:
                        continuation()
                        section_heading(heading + "（續）")
                        c.setFont(FONT, font_size)
                    c.drawString(145, y, line)
                    y -= 15
                    line = "" if ch == "\n" else ch
                else:
                    line += ch
            c.setFont(FONT, 11)
            y -= 2
        y -= 4
    if y < 170:
        continuation()
    signature_y = y - 110
    _signature_page(c, case, sigs, signature_y)
    _attachment_page(c, case, frames or [])
    c.save()
    writer = PdfWriter(clone_from=PdfReader(buffer))
    writer.add_metadata({SIGNATURE_Y_KEY: str(signature_y)})
    with open(path, "wb") as output:
        writer.write(output)
    return page


def _signature_page(c, case, sigs, base_y):
    W, _ = A4
    c.setFont(FONT, 11)
    c.setStrokeColorRGB(.66, .72, .78)
    c.line(60, base_y + 105, W - 60, base_y + 105)
    c.setFillColorRGB(.10, .16, .24)
    c.drawString(60, base_y + 91, "五、立書與簽署")
    for k, (x, _, w, h) in SIG_BOX.items():
        by = base_y
        c.rect(x, by, w, h)
        party = case["party_a" if k == "A" else "party_b"]
        caption = f"{'甲' if k == 'A' else '乙'}方簽名　{party['name']}"
        while c.stringWidth(caption, FONT, 11) > w:
            caption = caption[:-2].rstrip("…") + "…"
        c.drawString(x, by + h + 7, caption)
        if sigs and k in sigs:
            img = ImageReader(io.BytesIO(base64.b64decode(sigs[k].split(",")[-1])))
            c.drawImage(img, x + 4, by + 4, w - 8, h - 8, mask="auto", preserveAspectRatio=True)
    c.setFont(FONT, 9)
    c.drawString(60, base_y - 25, f"文件產出日期　{date.today():%Y年%m月%d日}　　雙方簽署日期以簽署紀錄為準。")
    c.showPage()


def _attachment_page(c, case, frames):
    W, H = A4
    c.setFont(FONT, 16); c.drawString(60, H - 60, "附件:事故資料與 AI 初判摘要")
    y = H - 95
    c.setFont(FONT, 11)
    inj = {"yes": "有人受傷", "no": "無人受傷", "unknown": "未確認"}[case.get("injured", "unknown")]
    c.drawString(60, y, f"人員受傷情形:{inj}"); y -= 22
    f = case["fault_hint"]
    a, b = f.get("a_percent"), f.get("b_percent")
    if all(type(value) in (int, float) and 0 <= value <= 100 for value in (a, b)):
        estimate = f"甲 {a}% / 乙 {b}%"
        if f.get("confidence"):
            estimate += f"  信心：{f['confidence']}"
    else:
        estimate = "尚無法判定"
    c.drawString(60, y, f"AI 責任初判（僅供參考）：{estimate}")
    y -= 20
    for r in f["reasons"]:
        y = _wrap(c, "- " + r, 70, y, 460, 10, 15)
    for e in case.get("evidence", []):
        y = _wrap(c, "- 畫面觀察:" + e, 70, y, 460, 10, 15)
    y -= 10
    if frames:
        c.setFont(FONT, 11); c.drawString(60, y, "影片關鍵畫面"); y -= 12
        cw, ch = 235, 132
        for i, fr in enumerate(frames[:6]):
            col, row = i % 2, i // 2
            x, yy = 60 + col * (cw + 15), y - (row + 1) * (ch + 22)
            if yy < 60:
                break
            c.drawImage(ImageReader(fr["path"]), x, yy + 14, cw, ch, preserveAspectRatio=True, anchor="n")
            c.setFont(FONT, 8); c.drawString(x, yy + 2, f"第 {fr['t']} 秒")
    c.setFont(FONT, 8)
    c.drawString(60, 40, "AI 初判不具責任認定效力,雙方以協議內容為準。影片畫面僅供雙方核對。")
    c.showPage()


# ====================== ComPDF 雲端 API:HTML → PDF ======================
import html as _html
import requests

ENGINE = "local"
logger = logging.getLogger(__name__)


def _esc(s):
    return _html.escape(str(s if s is not None else ""))


def _html_doc(case):
    sections = "".join(f"<section><h2>{_esc(title)}</h2><table>" + "".join(
        f'<tr class="{"payment" if label == "金額與給付" else ""}">'
        f"<th>{_esc(label)}</th><td>{_esc(value) or '（待補）'}</td></tr>" for label, value in rows
    ) + "</table></section>" for title, rows in agreement_sections(case))
    return f"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8"><style>
body{{font-family:"Noto Sans CJK TC","Noto Sans TC","Microsoft JhengHei","PingFang TC",sans-serif;font-size:12px;color:#111;margin:0;padding:40px 46px}}
h1{{text-align:center;font-size:22px;margin:0 0 20px;padding-bottom:12px;border-bottom:1px solid #a8b8c7}}
h2{{font-size:13px;margin:0 0 5px;padding:4px 7px;background:#e8eff6;color:#19293d;break-after:avoid}}
section{{margin-bottom:9px}}
table{{width:100%;border-collapse:collapse}} th{{width:96px;text-align:left;vertical-align:top;padding:3px 0;color:#42546b}} td{{padding:3px 0}}
.payment td{{font-size:14px;font-weight:700}}
td{{white-space:pre-wrap;overflow-wrap:anywhere}}
a.signature-slot{{display:block;height:184px;break-inside:avoid;page-break-inside:avoid;color:white;text-decoration:none}}
@page{{size:A4}}
</style></head><body>
<h1>道路交通事故和解書</h1>
{sections}
<a class="signature-slot" href="{SIGNATURE_MARKER}">&nbsp;</a>
</body></html>"""


def _compdf_generate(html_str):
    key = os.environ["COMPDF_API_KEY"].strip()
    base = os.getenv("COMPDF_API_BASE_URL", "https://api-server.compdf.com/server").rstrip("/")
    response = requests.post(
        base + "/v2/process/html/pdf",
        headers={"x-api-key": key},
        files={"file": ("settlement.html", html_str.encode("utf-8"), "text/html")},
        data={"language": "2"}, timeout=(10, 60),
    )
    response.raise_for_status()
    result = response.json()
    data = result.get("data") or {}
    url = data.get("downloadUrl")
    if str(result.get("code")) != "200" or not url or not url.startswith("https://"):
        raise ValueError("ComPDF 沒有回傳可下載的 PDF")
    pdf = requests.get(url, timeout=(10, 45))
    pdf.raise_for_status()
    if not pdf.content.startswith(b"%PDF"):
        raise ValueError("ComPDF 回傳的檔案不是 PDF")
    return pdf.content


def build_pdf(case: dict, path: str, sigs: dict | None = None, frames: list | None = None, *, draft=False, fixed_layout=False):
    """Build the preview and return the zero-based page used for signing."""
    global ENGINE
    if not draft and not fixed_layout and os.getenv("COMPDF_API_KEY", "").strip():
        try:
            writer = PdfWriter()
            writer.append(PdfReader(io.BytesIO(_compdf_generate(_html_doc(case)))), import_outline=False)
            signature_page = len(writer.pages) - 1
            page = writer.pages[signature_page]
            annotations = page.get("/Annots")
            for ref in annotations or ():
                annotation = ref.get_object()
                if annotation.get("/A", {}).get("/URI") == SIGNATURE_MARKER:
                    _, bottom, _, top = map(float, annotation["/Rect"])
                    if top - bottom < 130 or top < 151:
                        raise ValueError("ComPDF signature slot is too small")
                    signature_y = top - 105
                    annotations.remove(ref)
                    break
            else:
                raise ValueError("ComPDF signature slot missing")
            pages = io.BytesIO()
            local = canvas.Canvas(pages, pagesize=A4)
            _signature_page(local, case, sigs, signature_y)
            _attachment_page(local, case, frames or [])
            local.save()
            overlays = PdfReader(pages)
            page.merge_page(overlays.pages[0])
            writer.append(overlays, pages=(1, len(overlays.pages)), import_outline=False)
            writer.add_metadata({SIGNATURE_Y_KEY: str(signature_y)})
            with open(path, "wb") as out:
                writer.write(out)
            ENGINE = "compdf"
            logger.info("ComPDF generated the settlement agreement")
            return signature_page
        except Exception as exc:
            logger.warning("ComPDF generation failed, using local PDF (%s)", type(exc).__name__)
    ENGINE = "local"
    return _build_local(case, path, sigs, frames, draft=draft)

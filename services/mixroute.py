"""事故描述(+影片關鍵畫面)→ 結構化 JSON、追問清單、責任初判理由。
有 MixRoute 金鑰走 LLM(OpenAI 相容端點,支援圖片);否則用規則式模擬。"""
import json, logging, math, os, re
import requests
from services.api_log import record as record_api_response

logger = logging.getLogger(__name__)
# (key, 問題, 類型, 選項)── 依重要性排序;injured 放最前面
QUESTIONS = [
    ("injured", "有人受傷嗎?", "choice", ["沒有", "有", "不確定"]),
    ("plate_b", "對方的車牌號碼?", "text", None),
    ("plate_a", "你的車牌號碼?", "text", None),
    ("time", "事故是什麼時候發生的?(例:今天下午3點10分)", "text", None),
    ("location", "事故地點在哪裡?(路名、路口或里程)", "text", None),
    ("damage", "車輛損壞的部位與狀況?", "text", None),
    ("amount", "雙方協議的賠償金額(元)?沒有請填 0", "text", None),
    ("name_a", "甲方(你)的姓名?", "text", None),
    ("name_b", "乙方(對方)的姓名?", "text", None),
]
INJ = {"沒有": "no", "有": "yes", "不確定": "unknown"}

SCHEMA = """只輸出 JSON,欄位:
time, location, party_a{name,plate,vehicle}, party_b{name,plate,vehicle},
vehicle 使用「行人、機車、自行車、公車、貨車、汽車」之一，資訊不足留空。
description(客觀摘要), damage, amount(整數新台幣;未知填 null;明確不賠填 0),
injured(yes|no|unknown), collision_type(rear_end|lane_change|intersection|other),
evidence[](從影片畫面看到的客觀事實,每項一句,沒有影片則空陣列),
fault_hint{a_percent,b_percent,reasons[](逐點列出判斷理由),confidence(低|中|高)} ← 僅供參考,不是責任認定,
extra_questions[{key(以 x_ 開頭),question}] ← 還需向使用者確認的關鍵問題(最多 3 題)。
甲方=使用者本人。看不出來的資料填空字串或 unknown,不要猜測車牌與人名。"""


def model_configured():
    return all(os.getenv(key, "").strip() for key in ("MIXROUTE_BASE_URL", "MIXROUTE_API_KEY", "MIXROUTE_MODEL"))


def model_json(prompt, content, *, read_timeout=80):
    response = requests.post(
        os.environ["MIXROUTE_BASE_URL"].rstrip("/") + "/chat/completions",
        headers={"Authorization": "Bearer " + os.environ["MIXROUTE_API_KEY"]},
        json={"model": os.environ["MIXROUTE_MODEL"], "temperature": 0,
              "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": content}]},
        timeout=(10, read_timeout),
    )
    response.raise_for_status()
    raw = response.json()["choices"][0]["message"]["content"]
    record_api_response("case_summary", response=raw)
    raw = raw.strip()
    result = json.loads(re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw))
    if not isinstance(result, dict):
        raise ValueError("Expected a JSON object")
    return result


def aftermath_only(video_description):
    first_line = video_description.lstrip().split("\n", 1)[0].strip()
    if first_line.startswith("影片 ") and "）" in first_line:
        first_line = video_description.lstrip().partition("\n")[2].split("\n", 1)[0].strip()
    return first_line == "影片涵蓋：僅事後"


class SummaryUnavailable(ValueError):
    pass


def analyze(text, video_description=""):
    if model_configured():
        try:
            content = json.dumps({"使用者事故經過": text, "Gemini 影片事發經過": video_description}, ensure_ascii=False)
            prompt = "你是台灣交通事故文件助理。" + SCHEMA
            if video_description:
                prompt += """
整合兩份事故經過，將時間順序、各方行向、號誌及接觸前後寫入 description。
description 必須逐一交代直接相關物件在接觸前怎麼移動。用可辨認的起點、終點或固定地標寫出「由哪裡往哪裡」。有橫越時明寫以誰的行進方向為前方，另一物件由哪一側往哪一側；單寫「行駛」或「穿越」不算方向。只有影片畫面方向可確認時，明寫「影片畫面由左往右／由右往左」並將 relative_crossing 留空。方向完全未知時，在 description 指明哪個物件的方向無法確認。
區分當事人陳述、事發過程的直接證據及事後現場觀察。僅事後或涵蓋範圍不明的影片中，依當事人陳述整理其明確提供的車種、行向與是否接觸，並標明來源。將事後可確認的內容另列在 evidence，必要時在 description 說明影片只能確認事後狀況。
事後到場的警用機車、警車、救護車與其他人車，用現場角色或中性代號描述；禁止只因它們在翻覆車輛附近停放或倒地，就將它們配對成甲乙方、填入 thirdVehicle／thirdPedestrian、推定接觸，或將車種與接觸列為兩份資料的矛盾。只有能確認影片中是同一事故參與者，且該畫面可直接支持相反結論時，才把兩份來源列為矛盾。僅事後畫面不可用來生成事發時的 relative_crossing、impact 或 reconstruction。
若接觸或身分無法從影片確認，明寫無法確認，不替當事人猜測身分、速度、車損或號誌。
只有影片時，以影片敘述整理。畫面缺少證據時不要補造事故。
若甲乙間有可確認的橫向移動，另填 relative_crossing {reference_party, direction}。reference_party 為提供前進方向參照的一方（A 或 B），另一方是橫向移動對象，兩方皆不限類型。以參照對象事發前的行進方向為前方，另一方從左往右填 left_to_right，從右往左填 right_to_left。依兩個以上有時間順序的位置與固定地標判定，或採用當事人以明確參照對象提供的方向更正。保留原始參照與時間在 description 或 evidence。單憑畫面左右、甲乙角色無法對應、只知道起點、或來源矛盾且未獲更正時填 null。勿依甲乙順序、物件類型、責任比例或接觸後的移動方向推定。
另填 scenario {weather,light,signal,speedA,speedB,speedVehicle,speedPedestrian,thirdVehicle,thirdPedestrian}。
速度以 km/h 表示，無可確認數值填 null。signal 只用 紅燈、黃燈、綠燈、無號誌、未設定。
weather 用 晴朗、陰天、下雨、起霧。light 用 白天、黃昏、夜間。
thirdVehicle 用 無、汽車、機車、自行車、公車、貨車。thirdPedestrian 用 無、有。
另填 maneuver_party 為 A 或 B，追撞代表後車，變道碰撞代表切入的一方。看不清則填 null。
collision_type 為 rear_end 或 lane_change 且無法辨明甲乙角色情形時，仍保留類型並將 maneuver_party 留空。
將不同影片按共同事件與地標對齊，保留各來源的時間偏移及矛盾。缺乏對齊依據的鏡頭不可強行拼接。
另輸出 reconstruction，只有能提出共同時間與空間依據時才輸出，否則為 null。
格式：{basis,origin,scale,impact,objects:[{id,kind,evidence,poses:[{t,p,r}]}],
signals:[{id,evidence,p,yaw,states:[{start,end,color}]}]}。
impact 為 {t,p,evidence} 或 null。僅在能確認接觸時間時填 t，無法定位接觸點就令 p 為 null，evidence 引用影片編號及時間戳。
basis 填對齊證據，origin 填可辨認固定地標，scale 填公尺尺度與來源。
t、start、end 為共同時間軸相對於重播開始的秒數；每個物件至少兩個可追溯的姿態點。
p 為 [x,y,z] 公尺，y 向上，物件原點以模型底部中央表示；r 為連續的 XYZ 歐拉角 [rx,ry,rz] 弧度，
模型未旋轉時朝 +x，翻覆保留途中姿態及連續角度，不把 360 度折回零度。
重要動作與翻覆期間補足可確認的中間姿態，間距最好不超過 2 秒；遮擋時不可補點。
kind 採前述參與者類型；evidence 引用影片編號、時間戳以及可見依據。
signals 只收錄位置與朝向有依據的已見號誌；p 是燈頭中心，yaw 是其面向角（弧度，0 面向 +x）。
states 只收錄畫面可確認的時間區間與 color（紅燈、黃燈、綠燈），未知時段不要延長或猜測燈色。
不要為了填欄位編造三維座標；缺尺度、深度、姿態或時間證據就省略該點或物件。
"""
            data = model_json(prompt, content)
            if aftermath_only(video_description):
                data["reconstruction"] = None
                data["relative_crossing"] = None
                if isinstance(data.get("scenario"), dict):
                    data["scenario"].update(thirdVehicle="無", thirdPedestrian="無")
            return normalize(data)
        except Exception as exc:
            if isinstance(exc, requests.HTTPError) and exc.response is not None:
                logger.warning("Case summary failed (HTTP %s)", exc.response.status_code)
            else:
                logger.warning("Case summary failed (%s)", type(exc).__name__)
            if video_description:
                raise SummaryUnavailable("已取得影片事發經過，但整合事故經過時失敗，請稍後重試。") from exc
    return mock_analyze(text)


SCENARIO_OPTIONS = {
    "weather": ("晴朗", "陰天", "下雨", "起霧"),
    "light": ("白天", "黃昏", "夜間"),
    "signal": ("未設定", "紅燈", "黃燈", "綠燈", "無號誌"),
    "thirdVehicle": ("無", "汽車", "機車", "自行車", "公車", "貨車"),
    "thirdPedestrian": ("無", "有"),
}


def normalize_scenario(value):
    source = value if isinstance(value, dict) else {}
    scenario = {key: source[key] if source.get(key) in options else options[0]
                for key, options in SCENARIO_OPTIONS.items()}
    defaults = {"speedA": None, "speedB": None, "speedVehicle": 25, "speedPedestrian": 4}
    for key, default in defaults.items():
        speed = source.get(key, default)
        limit = 20 if key == "speedPedestrian" else 130
        scenario[key] = speed if type(speed) in (int, float) and 0 <= speed <= limit else default
    if source.get("trajectory") == "oncoming_rollover":
        scenario["trajectory"] = source["trajectory"]
    return scenario


def normalize_reconstruction(value):
    if not isinstance(value, dict):
        return None

    def label(raw):
        return raw.strip()[:500] if isinstance(raw, str) else ""

    def number(raw, low, high):
        return type(raw) in (int, float) and low <= raw <= high and math.isfinite(raw)

    def vector(raw, limit):
        return isinstance(raw, list) and len(raw) == 3 and all(number(n, -limit, limit) for n in raw)

    def records(key, limit):
        raw = value.get(key)
        return [item for item in raw[:limit] if isinstance(item, dict)] if isinstance(raw, list) else []

    scene = {key: label(value.get(key)) for key in ("basis", "origin", "scale")}
    if not all(scene.values()):
        return None
    objects = []
    for item in records("objects", 40):
        name, kind, evidence = (label(item.get(key)) for key in ("id", "kind", "evidence"))
        poses = item.get("poses")
        if not name or kind not in PARTICIPANT_TYPES or not evidence or not isinstance(poses, list):
            continue
        points = {point["t"]: {"t": point["t"], "p": point["p"], "r": point["r"]}
                  for point in poses[:240] if isinstance(point, dict)
                  and number(point.get("t"), 0, 3600) and vector(point.get("p"), 500)
                  and vector(point.get("r"), 64)}
        if len(points) >= 2:
            objects.append({"id": name, "kind": kind, "evidence": evidence,
                            "poses": sorted(points.values(), key=lambda point: point["t"])})
    if not objects:
        return None
    signals = []
    for item in records("signals", 20):
        name, evidence = label(item.get("id")), label(item.get("evidence"))
        position, yaw, states = (item.get(key) for key in ("p", "yaw", "states"))
        if not name or not evidence or not vector(position, 500) or not number(yaw, -64, 64):
            continue
        phases = [{"start": phase["start"], "end": phase["end"], "color": phase["color"]}
                  for phase in states[:240] if isinstance(phase, dict)
                  and phase.get("color") in ("紅燈", "黃燈", "綠燈")
                  and number(phase.get("start"), 0, 3600) and number(phase.get("end"), phase["start"], 3600)
                  ] if isinstance(states, list) else []
        signals.append({"id": name, "evidence": evidence, "p": position, "yaw": yaw, "states": phases})
    impact = value.get("impact")
    if (isinstance(impact, dict) and number(impact.get("t"), 0, 3600)
            and min(item["poses"][0]["t"] for item in objects) <= impact["t"] <= max(item["poses"][-1]["t"] for item in objects)
            and label(impact.get("evidence"))):
        scene["impact"] = {"t": impact["t"], "evidence": label(impact["evidence"]),
                           "p": impact["p"] if vector(impact.get("p"), 500) else None}
    return {**scene, "objects": objects, "signals": signals}


def crossing_video_illustration(description, direction):
    if not isinstance(description, str) or direction not in ("left_to_right", "right_to_left"):
        return None
    if not (re.search(r"機車|摩托車", description) and "行人" in description
            and re.search(r"斑馬線|行人穿越道|穿越馬路", description)
            and re.search(r"發生碰撞|撞到|撞上|擦撞", description)):
        return None
    if re.search(r"(?:未見|沒有|無法|不能|未能|不確定|難以)[^。；\n]{0,18}(?:碰撞|接觸)", description):
        return None
    stamp = re.search(r"(\d{1,2})[:：]([0-5]\d)\s*[^。；\n]{0,24}(?:碰撞|撞到|撞上|擦撞)", description)
    hit = max(2, int(stamp[1]) * 60 + int(stamp[2])) if stamp else 3
    start = hit - 2
    stopped = hit + 2

    def actor(name, rows):
        return {"id": name, "kind": name, "evidence": description[:500], "poses": [
            {"t": time, "p": position, "r": rotation} for time, position, rotation in rows
        ]}

    fallen = "雙方倒地" in description or bool(re.search(r"機車[^。；\n]{0,15}(?:倒地|側倒)", description))
    slid = "滑行" in description
    motor = [(start, [-10, 0, 0], [0, 0, 0]), (hit, [-1.3, 0, 0], [0, 0, 0])]
    side = -1 if direction == "left_to_right" else 1
    walker = [(start, [0, 0, 3 * side], [0, side * math.pi / 2, 0]),
              (hit, [0, 0, 0], [0, side * math.pi / 2, 0])]
    events = [{"t": hit, "title": "機車與行人接觸", "detail": "依整合後的事故敘述，接觸位置為示意。"}]
    if fallen:
        motor += [(hit+.5, [-.4, .25, .6], [math.pi/2, 0, 0]),
                  (stopped, [2.5 if slid else -.4, .25, .6], [math.pi/2, 0, 0])]
        if "雙方倒地" in description:
            walker += [(hit+.5, [0, .2, .45 * side], [0, 0, math.pi/2]),
                       (stopped, [0, .2, .45 * side], [0, 0, math.pi/2])]
        events.append({"t": hit+.5, "title": "倒地示意", "detail": "倒地時間先後、姿態與滑行距離均為示意。"})
    return {"illustrative": True, "origin": "斑馬線區域示意原點", "scale": "示意比例，無實測尺度",
            "basis": "依整合後敘述呈現機車直行、行人穿越與接觸。號誌位置、速度、距離與倒地側未經校準。",
            "objects": [actor("機車", motor), actor("行人", walker)], "signals": [], "events": events}


def normalize(d):
    if "settlement" in d:
        terms = d["settlement"] if isinstance(d["settlement"], dict) else {}
        d["settlement"] = {
            key: terms[key].strip()[:limit]
            for key, limit in (("payer", 1), ("scope", 1000), ("payment_details", 1000),
                               ("insurance", 1000), ("case_reference", 120),
                               ("additional", 1000), ("signing_place", 120),
                               ("contact_a", 240), ("contact_b", 240))
            if isinstance(terms.get(key), str) and terms[key].strip()
        }
        if d["settlement"].get("payer") not in ("A", "B") or d.get("amount") in (0, "0"):
            d["settlement"].pop("payer", None)
    if "relative_crossing" in d:
        crossing = d["relative_crossing"]
        if (not isinstance(crossing, dict) or crossing.get("reference_party") not in ("A", "B")
                or crossing.get("direction") not in ("left_to_right", "right_to_left")):
            d["relative_crossing"] = None
    if "reconstruction" in d:
        d["reconstruction"] = normalize_reconstruction(d["reconstruction"])
    if "scenario" in d:
        d["scenario"] = normalize_scenario(d["scenario"])
    for p in ("party_a", "party_b"):
        d.setdefault(p, {})
        for k in ("name", "plate", "vehicle"):
            d[p].setdefault(k, "")
        d[p]["vehicle"] = participant_type(d[p]["vehicle"] or "")
        if d[p]["vehicle"] == "行人":
            d[p]["plate"] = ""
    for k in ("time", "location", "description", "damage"):
        d[k] = d.get(k) or ""
    a = d.get("amount")
    try:
        d["amount"] = None if a in (None, "") else int(str(a).replace(",", ""))
    except ValueError:
        d["amount"] = None
    if d.get("injured") not in ("yes", "no", "unknown"):
        d["injured"] = "unknown"
    d.setdefault("collision_type", "other")
    d["evidence"] = list(d.get("evidence") or [])
    d["extra_questions"] = list(d.get("extra_questions") or [])
    f = d.setdefault("fault_hint", {})
    f.setdefault("a_percent", 50); f.setdefault("b_percent", 50)
    f["reasons"] = list(f.get("reasons") or ([f["reason"]] if f.get("reason") else []))
    f.setdefault("confidence", "低")
    return d


def compute_missing(d, answers=None, limit=6, *, required_only=False):
    ans = answers or {}
    have = {"time": d["time"], "location": d["location"], "plate_a": d["party_a"]["plate"],
            "plate_b": d["party_b"]["plate"], "name_a": d["party_a"]["name"], "name_b": d["party_b"]["name"],
            "damage": d["damage"], "injured": d["injured"] if d["injured"] != "unknown" else "",
            "amount": "" if d["amount"] is None else "x"}
    for suffix in ("a", "b"):
        if d[f"party_{suffix}"]["vehicle"] == "行人":
            have[f"plate_{suffix}"] = True
    if d["amount"] is not None and d["amount"] < 0:
        have["amount"] = ""
    out = [dict(key=k, question=q, type=t, options=o) for k, q, t, o in QUESTIONS
           if not have[k] or (isinstance(have[k], str) and not have[k].strip())]
    if not d["description"].strip() or (
            not required_only and d["collision_type"] == "other" and not ans.get("description", "").strip()):
        out.append(dict(key="description", question="請再描述碰撞經過(誰在前/後、行進方向、有無號誌)", type="text", options=None))
    if required_only:
        if (d.get("amount") or 0) > 0 and (d.get("settlement") or {}).get("payer") not in ("A", "B"):
            out.append(dict(key="settlement_payer", question="請確認和解金額由哪一方支付", type="choice", options=["甲方", "乙方"]))
        if (d.get("amount") or 0) > 0 and not (d.get("settlement") or {}).get("payment_details"):
            out.append(dict(key="settlement_payment", question="請確認付款方式與期限", type="text", options=None))
        return out, len(out)
    for x in d["extra_questions"]:
        if x.get("key") and x["key"] not in ans:
            out.append(dict(key=x["key"], question=x.get("question", ""), type="text", options=None))
    return out[:limit], len(out)


def apply_fields(d, ans):
    g = lambda k: (ans.get(k) or "").strip()
    if g("time"): d["time"] = g("time")
    if g("location"): d["location"] = g("location")
    if g("damage"): d["damage"] = g("damage")
    if g("plate_a"): d["party_a"]["plate"] = g("plate_a").upper()
    if g("plate_b"): d["party_b"]["plate"] = g("plate_b").upper()
    if g("name_a"): d["party_a"]["name"] = g("name_a")
    if g("name_b"): d["party_b"]["name"] = g("name_b")
    if g("injured"): d["injured"] = INJ.get(g("injured"), "unknown")
    if g("amount"):
        m = re.fullmatch(r"([0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)\s*元?", g("amount"))
        d["amount"] = int(m.group(1).replace(",", "")) if m else None
    return d


def reanalyze(case):
    """原始描述 + 使用者補充 → 重新分析,再以使用者的明確答案覆蓋欄位。"""
    parts = [case["narrative"]]
    selected_location = case.get("selected_location", "")
    if selected_location:
        parts.append(f"當事人指定事故地點：{selected_location}")
    for k, v in case["answers"].items():
        if k == "description" or k.startswith("x_"):
            parts.append(f"補充({case['qtext'].get(k, k)}):{v}")
    text = "\n".join(p for p in parts if p).strip()
    previous_summary_warning = case.pop("summary_warning", None)
    if case.get("demo"):
        d = mock_analyze(text)
    else:
        video_description = case.get("video_description", "")
        try:
            d = analyze(text, video_description)
        except SummaryUnavailable:
            case["summary_warning"] = "事故摘要服務暫時無法整合。已保留原始陳述與影片觀察，請核對並補充。"
            if case.get("data") and not previous_summary_warning:
                d = case["data"].copy()
            else:
                d = normalize({
                    "description": text or video_description,
                    "evidence": [video_description],
                    "fault_hint": {"a_percent": None, "b_percent": None, "reasons": [], "confidence": ""},
                })

    if selected_location:
        d["location"] = selected_location
    if (not case.get("summary_warning") and case.get("video_description") and not aftermath_only(case["video_description"])
            and not d.get("reconstruction") and not d.get("video_template")):
        crossing = d.get("relative_crossing")
        direction = None
        if crossing:
            reference = crossing["reference_party"].lower()
            other = "b" if reference == "a" else "a"
            if d[f"party_{reference}"]["vehicle"] == "機車" and d[f"party_{other}"]["vehicle"] == "行人":
                direction = crossing["direction"]
        d["reconstruction"] = crossing_video_illustration(
            d.get("description", ""), direction)
    d = apply_fields(d, case["answers"])
    if selected_location and d["location"] not in d["description"]:
        d["description"] = f"事故地點：{d['location']}。\n{d['description']}".strip()
    if not d["description"].strip() and case["answers"].get("description"):
        d["description"] = case["answers"]["description"].strip()
    if case.get("data", {}).get("settlement"):
        d["settlement"] = case["data"]["settlement"]
    case["data"] = d
    case["missing"], case["missing_total"] = compute_missing(d, case["answers"])
    for q in case["missing"]:
        case["qtext"][q["key"]] = q["question"]
    return case


PARTICIPANT_TYPES = ("行人", "機車", "自行車", "公車", "貨車", "汽車")
VEHICLE_ALIASES = {
    "行人": ("行人", "步行"), "機車": ("機車", "摩托車", "速克達"),
    "自行車": ("自行車", "腳踏車", "單車"), "公車": ("公車", "巴士", "客運"),
    "貨車": ("貨車", "卡車"), "汽車": ("汽車", "轎車", "小客車"),
}


def participant_type(text):
    for kind in PARTICIPANT_TYPES:
        if any(alias in text for alias in VEHICLE_ALIASES[kind]):
            return kind
    return ""


def described_participant(text, party):
    # Match only the participant's label and adjacent description.
    prefix = "甲方|我(?:騎乘|騎|開|是)" if party == "甲" else "乙方|對方(?:騎乘|騎|開|是)?"
    aliases = "|".join(alias for group in VEHICLE_ALIASES.values() for alias in group)
    match = re.search(rf"(?:{prefix})\s*(?:為|是|駕駛|騎乘|騎著|騎|開著|開|的)?\s*({aliases})", text)
    return participant_type(match.group(1)) if match else ""


def mock_analyze(text, n_frames=0):
    plates = re.findall(r"[A-Z]{2,3}-?\d{3,4}|\d{3,4}-?[A-Z]{2,3}", text.upper())
    m = re.search(r"(\d[\d,]{2,})\s*(?:元|塊)", text)
    amount = int(m.group(1).replace(",", "")) if m else (0 if re.search(r"互不請求|不用賠|不賠|不求償", text) else None)
    t = re.search(r"((?:今天|昨天|\d{1,2}[/月]\d{1,2}日?)?\s*(?:上午|下午|晚上|凌晨)?\s*\d{1,2}[:點時]\d{0,2}分?)", text)
    loc = re.search(r"在([^，。,.]{2,20}?(?:路|街|道|橋|口|段|交流道|停車場)[^，。,.]{0,8})", text)
    if re.search(r"我.{0,6}(?:追撞|撞到前|撞上前)", text):
        ctype, a, b = "rear_end", 100, 0
        why = ["你的車為後車(追撞方),後車通常需保持可隨時煞停的安全距離"]
    elif re.search(r"追撞|撞到我後|後方來車|從後面", text):
        ctype, a, b = "rear_end", 0, 100
        why = ["你的車為前車,對方為後車(追撞方)", "後車通常需保持可隨時煞停的安全距離",
               "前車若有不當急煞或違規停車,責任會改變"]
    elif re.search(r"變換車道|切入|變道|並線", text):
        ctype, a, b = "lane_change", 30, 70
        why = ["變換車道時應讓直行車先行,未注意來車者通常負較高責任", "是否打方向燈、與來車的距離會影響判斷"]
    elif re.search(r"路口|紅綠燈|左轉|右轉|直行", text):
        ctype, a, b = "intersection", 50, 50
        why = ["路口事故需依號誌、行向與有無減速禮讓判斷", "目前資訊不足,無法區分雙方責任"]
    else:
        ctype, a, b, why = "other", 50, 50, ["資訊不足,無法初判"]
    why.append("以上為一般原則;實際責任仍依現場跡證、行車記錄器與警方/鑑定單位認定")
    if re.search(r"沒人受傷|無人受傷|沒有受傷|沒受傷|均無受傷", text):
        inj = "no"
    elif re.search(r"受傷|流血|送醫|很痛|骨折|救護車", text):
        inj = "yes"
    else:
        inj = "unknown"
    dm = re.search(r"((?:保險桿|車門|車尾|車頭|後視鏡|烤漆|車燈)[^，。,.]{0,12})", text)
    ev = [f"已擷取 {n_frames} 張影片關鍵畫面並附於文件(模擬模式不做影像理解,接上 MixRoute 後由 AI 解讀畫面)"] if n_frames else []
    return normalize({
        "time": t.group(1).strip() if t else "", "location": loc.group(1) if loc else "",
        "party_a": {"name": "", "plate": plates[0] if plates else "", "vehicle": described_participant(text, "甲")},
        "party_b": {"name": "", "plate": plates[1] if len(plates) > 1 else "", "vehicle": described_participant(text, "乙")},
        "description": text.strip(), "damage": dm.group(1) if dm else "", "amount": amount, "injured": inj,
        "collision_type": ctype, "evidence": ev,
        "fault_hint": {"a_percent": a, "b_percent": b, "reasons": why, "confidence": "低"}})

"""Send the original video to Gemini and retrieve its account of the event."""
import logging
import mimetypes
import os
import time
from urllib.parse import urlsplit

import requests

ROOT = "https://generativelanguage.googleapis.com"
PROMPT = """請分析整段事故影片，使用繁體中文詳細說明事發經過。你的敘述將與當事人填寫的事故經過整合，並用於後續模擬。

先判斷是否真的拍到事發過程。第一行使用「影片涵蓋：事發過程／僅事後／無法判定」三者之一。
若畫面只拍到翻覆後、停在現場或事後到場的人車，標為「僅事後」。警用機車、警車、救護車、救援人員和旁觀者屬事後現場觀察，除非連續畫面直接證明他們參與事發過程，否則不要列為直接參與者，也不要拿停放或倒地位置推斷車輛曾碰撞、行駛方向或事故當事人身分。只看到翻覆後的最終姿態，不代表拍到翻覆過程。

先找出可能與事故有關的時間區間。依影片播放時間（從 00:00 起算）描述事故前、疑似接觸或異常事件當下，以及事故後。一般行駛或等待的畫面只交代有關背景。若無法定位事故，請說明可確認的畫面、可疑時間區間及無法確認的原因。

若有橫向移動，記錄參照對象和移動對象的穩定代號，兩者不限類型。用至少兩個依時間排序的畫面追蹤同一移動對象，列出時間戳、相對位置及可辨認的固定地標（例如分隔島、路緣或停止線）。以參照對象在事件前可確認的行進方向為前方，記下移動對象相對它由左往右、由右往左或方向未知，並交代鏡頭朝向與這個前方的關係。將接觸前的移動與接觸後的反彈、偏移分開。跨鏡頭、鏡像、轉向或移動視角不可直接沿用畫面左右；只有一幀或無法確認參照方向時，保留未知。

按以下順序輸出：
1. 事故區間與依據：列出疑似區間、最可能的事件時間，以及支持判斷的可見或可聽線索。時間不精確時使用約略時間或區間。
2. 相關人車：以「車輛 A」「行人 1」等穩定代號識別。說明可辨認的類型、顏色、初始位置及行進方向。區分直接參與者、影響事件的其他人車和一般背景。未能確認身分時保留代號。
3. 詳細時間線：每一段以「[mm:ss–mm:ss]」開頭。逐步描述相關人車的相對位置、車道、行進方向、轉向、變道、煞停、閃避、疑似接觸部位及事後動作。方向以畫面或道路上可辨認的參照物說明。若接觸被遮擋或在畫外，說明是哪些線索讓你懷疑發生接觸。
4. 號誌、速度與車損：標出有關號誌的時間、燈色及可確認的管制方向。速度有可讀數值或可解釋的測量依據時才列 km/h，並說明來源；其餘只描述可觀察到的快慢變化。逐車列出可見的受損部位與外觀，並說明何時可見、是否能確認與本次事件有關。
5. 無法確認事項：列出因角度、遮擋、影像品質、鏡頭移動或畫外範圍而無法判斷的關鍵資訊。
6. 空間與翻覆證據：指出可跨鏡頭對齊的共同事件及固定地標、可用的比例尺和相機視角。對相關人車記錄各時間點可重建的位置、朝向和姿態。若車輛翻覆，說明開始時間、翻轉方向、旋轉軸、輪胎離地、車身哪一側接觸地面與最後姿態，並說明可見依據。在有固定原點與尺度時，用右手座標 [x,y,z]（公尺，y 向上）與連續的 [rx,ry,rz]（弧度，XYZ 歐拉角）描述；角度要保留整圈變化，不能只寫起點與終點。無法重建深度、尺度或姿態時，保留未知並提供畫面或地標的相對關係。號誌需交代燈頭相對固定地標的位置、面向及各時間點可辨認的實際燈色；看不到的位置或燈色保留未知。
7. 接觸前方向：逐一列出直接相關物件的代號、起點、移動終點、前後兩個時間戳及參照物。若有橫越，明寫「以〔參照對象〕的行進方向為前方，〔移動對象〕由〔左側／右側〕向〔右側／左側〕橫越」。即使只確認畫面方向，也要明寫畫面中的起點與終點，並標明「影片畫面」的參照。方向無法確認時，逐一指出是哪個物件及缺少什麼證據。

清楚區分「畫面可見」「音訊可聽」「依線索推測」。每項推測都要附上依據，資訊不足就寫「無法確認」。以影片內容為準，避免編造車速、傷勢、身分或肇事責任。"""
logger = logging.getLogger(__name__)


def describe(path, progress=None):
    key = os.getenv("MIXROUTE_API_KEY", "").strip()
    model = os.getenv("MIXROUTE_MODEL", "").strip().removeprefix("models/")
    host = urlsplit(os.getenv("MIXROUTE_BASE_URL", "").strip()).hostname
    if host != "generativelanguage.googleapis.com" or not key or not model.startswith("gemini-"):
        raise ValueError("請先設定 Gemini API 網址、金鑰與支援影片的 Gemini 模型。")

    base = ROOT + "/v1beta/"
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    size = str(os.path.getsize(path))
    name = None
    with requests.Session() as session:
        session.headers["x-goog-api-key"] = key

        def request(method, url, **kwargs):
            kwargs.setdefault("timeout", (10, 45))
            response = session.request(method, url, **kwargs)
            response.raise_for_status()
            return response

        try:
            if progress:
                progress("sending_video")
            start = request("POST", ROOT + "/upload/v1beta/files", headers={
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": size,
                "X-Goog-Upload-Header-Content-Type": mime,
            }, json={"file": {"display_name": "accident-video"}})
            upload = start.headers["X-Goog-Upload-URL"]
            if urlsplit(upload).hostname != "generativelanguage.googleapis.com" or urlsplit(upload).scheme != "https":
                raise ValueError("Gemini 回傳的上傳網址無效。")
            with open(path, "rb") as source:
                file = request("POST", upload, headers={
                    "Content-Length": size, "X-Goog-Upload-Offset": "0",
                    "X-Goog-Upload-Command": "upload, finalize",
                }, data=source, timeout=(10, 120)).json()["file"]
            name = file["name"]
            until = time.monotonic() + 150
            while file["state"] == "PROCESSING" and time.monotonic() < until:
                if progress:
                    progress("processing_video")
                time.sleep(3)
                file = request("GET", base + name, timeout=(5, 10)).json()
            if file["state"] != "ACTIVE":
                raise ValueError("Gemini 未能完成影片處理，請確認影片格式或稍後重試。")
            if progress:
                progress("describing_video")
            result = request("POST", base + f"models/{model}:generateContent", json={
                "contents": [{"parts": [{"file_data": {"mime_type": mime, "file_uri": file["uri"]}}, {"text": PROMPT}]}],
            }, timeout=(10, 150)).json()
            text = "\n".join(part["text"] for candidate in result.get("candidates", [])[:1]
                             for part in candidate.get("content", {}).get("parts", [])
                             if part.get("text") and not part.get("thought")).strip()
            if not text:
                raise ValueError("Gemini 沒有回傳影片事發經過。")
            return text
        except requests.Timeout as exc:
            raise ValueError("等待 Gemini 處理影片逾時，請稍後重試。") from exc
        except requests.HTTPError as exc:
            status = exc.response.status_code
            raise ValueError(f"Gemini 影片請求失敗（HTTP {status}）。") from exc
        except requests.RequestException as exc:
            raise ValueError("無法連線至 Gemini 影片 API。") from exc
        finally:
            if name and name.startswith("files/"):
                try:
                    request("DELETE", base + name, timeout=(3, 5))
                except requests.RequestException:
                    logger.warning("Could not remove temporary Gemini video")

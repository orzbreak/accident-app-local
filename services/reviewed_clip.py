from hashlib import file_digest
from pathlib import Path


# Footage checked against the uploaded frames and YouTube R-DDymkqVTI.
CLIP_SIZE = 34_859_220
CLIP_SHA256 = "8d1615d43a2c0f801584c91e8ca38e9bbdf29e1b26da871e1841da29ba7b47fe"
DESCRIPTION = (
    "約 00:39–00:41，公車在道路右側，數輛機車出現在公車左後方。"
    "約 00:42–00:44，公車左後方附近先後可見兩輛機車倒地。"
    "誰先接觸誰、是否碰到公車及傷勢皆無法確認。"
)
BASIS = "依人工核對的 00:39–00:48 畫面，示意公車靠右、左後方兩輛機車倒地。距離、速度與姿態使用示意值，接觸不明。"


def matches(path):
    path = Path(path)
    if path.stat().st_size != CLIP_SIZE:
        return False
    with path.open("rb") as source:
        return file_digest(source, "sha256").hexdigest() == CLIP_SHA256


def replay():
    def actor(name, kind, points):
        return {
            "id": name, "kind": kind, "evidence": DESCRIPTION,
            "poses": [
                {"t": seconds, "p": [x, y, z], "r": [roll, 0, 0]}
                for seconds, x, y, z, roll in points
            ],
        }

    return {
        "illustrative": True, "origin": "道路示意原點", "scale": "無實測尺度", "basis": BASIS,
        "objects": [
            actor("公車", "公車", [
                (39, 0, 0, 1.9, 0), (41, 7, 0, 1.9, 0), (43, 12, 0, 1.9, 0),
                (45, 16, 0, 1.9, 0), (48, 19, 0, 1.9, 0),
            ]),
            actor("機車 1", "機車", [
                (39, -8, 0, -1, 0), (41, 1.5, 0, -1, 0), (42, 5, .08, -1, .38),
                (43, 7, .22, -1, 1.45), (44, 7.5, .22, -1, 1.45), (48, 7.5, .22, -1, 1.45),
            ]),
            actor("機車 2", "機車", [
                (39, -13, 0, -3, 0), (41, -5, 0, -3, 0), (42, -1, 0, -3, 0),
                (43, 1.5, .10, -3, .45), (44, 2.5, .22, -3, 1.45), (48, 2.5, .22, -3, 1.45),
            ]),
        ],
        "signals": [],
    }

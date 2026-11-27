# AI 車禍和解書 MVP

事故描述或影片 → AI 追問 → 預覽 PDF → 發起人送簽 → 雙方各自驗證信箱並簽名。文字示範可在無金鑰時執行。

## 執行
    pip install -r requirements.txt
    cp .env.example .env          # 有金鑰再填
    # 下載 NotoSansTC-Regular.ttf 放進 fonts/(PDF 中文才不會缺字)
    uvicorn app:app --reload      # 開 http://localhost:8000(手機同網段用電腦 IP)

## 影片分析與重播

在 `.env` 設定 Gemini 的 `MIXROUTE_BASE_URL`、`MIXROUTE_API_KEY` 與支援影片的 `MIXROUTE_MODEL`。重啟服務後重新上傳影片。

每案可上傳一支影片，上限 200 MB。App 將原片送給 Gemini，並整合影片觀察及使用者敘述。輸出包含可確認的共同座標系、物件 XYZ、姿態弧度與號誌位置和實際燈色。重播預設循環，可展開查看 3D 座標與來源依據。無法確定三維位置時保留文字敘述與原片供核對。

若已取得影片觀察但事故摘要 API 暫時失敗，仍可到核對頁查看原始影片與觀察結果。YOLO 失敗時會顯示出錯階段。

## 站內送簽

在伺服器的 .env 設定以下項目，再重啟 App。

| 變數 | 用途 |
|---|---|
| SIGNING_PUBLIC_URL | 雙方裝置皆可連線的 App HTTPS 網址，例如 https://accident.example.com |
| SIGNING_SMTP_HOST | 郵件服務主機 |
| SIGNING_SMTP_PORT | SMTP 連接埠，預設 STARTTLS 587 或 SSL 465 |
| SIGNING_SMTP_SECURITY | starttls（預設）或 ssl |
| SIGNING_SMTP_FROM | 郵件服務允許使用的寄件地址 |
| SIGNING_SMTP_USER、SIGNING_SMTP_PASSWORD | SMTP 帳號與密碼，使用憑證驗證的郵件服務才需填寫 |

本機單機測試可將 SIGNING_PUBLIC_URL 設為 http://localhost:8000。讓手機等其他裝置開啟邀請時，請使用可連線的 HTTPS 網址。

1. 發起人在核對頁輸入兩個不同的 Email，開啟送簽版 PDF，確認後寄出邀請。後端保存這一版的 PDF 和 SHA-256。
2. 甲乙各自開啟郵件中的連結，索取並輸入寄到原信箱的驗證碼，再查看完整文件並手寫簽名。邀請有效期為 7 天，驗證碼為 10 分鐘，已驗證的簽署操作有效期為 30 分鐘。Email 驗證證明收件人可存取該信箱。
3. 發起人可從進度頁補寄未送達或已過期的邀請，查看各方的確認簽署時間。雙方完成後，可下載已簽 PDF。請私下保存發起人連結，以便重啟服務後繼續查詢。
4. 儲存文件修改會撤銷舊版尚未完成的邀請。新版需再次預覽與送簽。已完成的舊版仍可從原發起人連結下載。

送簽文件、邀請及簽署紀錄儲存在 data/signing.sqlite3。部署時請使用持久磁碟與 SQLite 相容的備份方式，並限制資料庫檔案的存取。事故分析及可編輯草稿仍隨單一 App 程序重啟清除，已送簽版本仍可完成簽署或由發起人查詢。

### 選用 DottedSign Sandbox

預設使用上面的站內簽署。發起人預覽 PDF 後，可勾選「使用 DottedSign Sandbox 送簽」，由 DottedSign 建立並寄出測試簽署邀請。App 顯示整份文件的進度，個別簽署狀態可在 DottedSign 查看，雙方完成後可在 App 下載已簽 PDF。

在本機 `.env` 設定 `DOTTEDSIGN_ENV=sandbox`、`DOTTEDSIGN_CLIENT_ID` 和 `DOTTEDSIGN_CLIENT_SECRET`。應用程式權限需勾選 `sign_tasks.general.write`、`sign_tasks.general.read`、`sign_tasks.download.read`。請使用虛構案件與測試信箱，憑證留在本機設定。

## Glows.ai YOLO 影片偵測

在已啟動、有 CUDA 和 PyTorch 的 Glows.ai GPU 上傳 `glows_worker`，在該資料夾內使用 GPU 映像的 Python 環境執行：

```bash
python -m pip install -r requirements.txt
read -rs GLOWS_YOLO_TOKEN
export GLOWS_YOLO_TOKEN
python -m uvicorn app:app --host 0.0.0.0 --port 8001 --workers 1
```

用 Glows.ai 的 HTTPS Port Forwarding 對應到 8001。在事故 App 的服務環境設定 `GLOWS_YOLO_URL` 為該 HTTPS 端點加上 `/infer`，`GLOWS_YOLO_TOKEN` 填入 worker 使用的相同隨機密鑰，再重啟事故 App。未設定這兩項時只執行原有流程。

上傳影片後按「分析事故」，App 會平行送出 Gemini 和 YOLO 的工作。核對頁的「和解資料」可預覽及下載加上辨識框的 MP4。YOLO 使用現成的 YOLO11n 模型，偵測畫面中的人車。GPU 建立及釋放由 Glows.ai 管理，App 只使用你設定的端點。

### 分析時自動啟動 GPU

本機 App 使用單一 worker。可用 `glows_worker/Dockerfile` 建置容器，或先在 Glows.ai 準備已安裝 `glows_worker/requirements.txt` 的 CUDA 容器映像，將 `glows_worker/app.py` 放在映像內的 `/opt/glows-worker/app.py`，並將 YOLO11n 權重預先下載到映像的工作目錄。從 Glows.ai 的「Platform Setting → SDK Token」取得 SDK Token。

Windows 本機也可用 `GLOWS_YOLO_SSH_BOOTSTRAP=1`，並選擇 `img-nrpy8bl6` 官方映像。此方式使用 Windows OpenSSH 安裝推論服務並建立本機 SSH 通道，無須自訂容器或開放額外的 HTTP 埠。若本機代理伺服器無法連往 Glows.ai SDK，可設 `GLOWS_SDK_DIRECT=1` 讓 SDK 請求直連。

在本機 `.env` 設定 `GLOWS_YOLO_AUTO=1`、`GLOWS_SDK_TOKEN`、`GLOWS_YOLO_IMAGE_ID`、`GLOWS_YOLO_REGION`，再重啟 8000。機房必須提供 RTX 4090 單卡和可用的 HTTPS 存取網址。按「分析事故」時，自動模式會取代固定推論網址。公開展示模式不會自動開機。

同時只會開一台 GPU，下載完成或失敗後即要求取消，最晚在啟動請求後 10 分鐘要求取消。以每小時 0.490 Credit 估算，10 分鐘約 0.082 Credit。Glows.ai SDK 沒有提供此流程的逐次硬性扣點上限，斷線或取消失敗時應立即到 My Instances 查核。若前一次未確認釋放，程式會先重試取消，再決定是否允許開新機。

## ComPDF 雲端產製

在服務環境設定 `COMPDF_API_KEY` 並重啟後，新產生的和解書會優先透過 ComPDF 將正文轉成 PDF。簽名欄接在正文後方，空間不足時換頁，事故摘要另列附件。未設定金鑰、轉檔失敗或無法定位簽名欄時，整份和解書會由本機產生。`/api/mode` 的 `compdf_configured` 表示金鑰是否已設定，`compdf` 表示最近一次 PDF 產製使用的引擎。

## 事故地點

在案件表單直接輸入事故地址。


## 公開部署

使用能長時間執行 Docker、提供公開 HTTPS 網址、持久磁碟和對外 SMTP 的主機。站點入口可公開，原始影片與影格需要建立案件時發給該瀏覽器的案件 Cookie，送簽文件和下載仍各自使用專屬憑證。

- 建置映像：docker build -t accident-app .
- 掛載持久磁碟到 /var/lib/accident-app，讓容器的 UID 10001 可寫入。這個目錄存放 SQLite、PDF 及上傳資料。
- 對外使用主機提供的 HTTPS 入口，把流量轉發到容器的 8000 埠，健康檢查使用 /healthz。
- 保持單一實例及單一 worker，因為可編輯草稿及影片分析工作目前在程序記憶體。在主機後台存入 SMTP 和模型憑證。Render 會自動提供 RENDER_EXTERNAL_URL，App 可直接用這個 HTTPS 網址產生邀請。日後使用自己的網域，可用 SIGNING_PUBLIC_URL 指定。
- 公開流量前，在入口設定請求大小和速率限制，涵蓋分析、影片上傳與送簽。驗證代管主機的請求逾時及上傳容量是否符合預定的影片大小，並設定定期備份與事故資料保存期限。
- 以兩個測試信箱從外部網路驗證完整流程，再把網址提供給使用者。映像預設關閉模型回應內容記錄。

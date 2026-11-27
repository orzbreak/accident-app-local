const byId = id => document.getElementById(id);
const roleName = role => role === "A" ? "甲方" : "乙方";
const secret = decodeURIComponent(location.hash.slice(1));
if (location.hash) history.replaceState(null, "", location.pathname + location.search);

function message(text, error = false) {
  const status = byId("status");
  status.textContent = text;
  status.classList.toggle("error", error);
}

async function request(path, credential, {method = "GET", data, pdf = false} = {}) {
  const headers = {Authorization: "Bearer " + credential};
  if (data) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {method, headers, body: data && JSON.stringify(data), cache: "no-store"});
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : "操作失敗，請稍後再試。");
  }
  return pdf ? response.blob() : response.json();
}

function handle(control, action) {
  return async event => {
    event?.preventDefault();
    if (control.disabled) return;
    control.disabled = true;
    message("處理中…");
    try {
      await action();
    } catch (error) {
      message(error.message, true);
    } finally {
      control.disabled = false;
    }
  };
}

function signaturePad(pad, clear) {
  const ctx = pad.getContext("2d");
  let pointer = null;
  let ink = false;
  pad.width = 1200;
  pad.height = 280;
  ctx.lineWidth = 4.8;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.strokeStyle = "#111";
  const position = event => {
    const bounds = pad.getBoundingClientRect();
    return [
      (event.clientX - bounds.left) * pad.width / bounds.width,
      (event.clientY - bounds.top) * pad.height / bounds.height,
    ];
  };
  pad.onpointerdown = event => {
    if (pointer !== null || event.button !== 0) return;
    pointer = event.pointerId;
    ctx.beginPath();
    ctx.moveTo(...position(event));
    pad.setPointerCapture(pointer);
  };
  pad.onpointermove = event => {
    if (event.pointerId !== pointer) return;
    ctx.lineTo(...position(event));
    ctx.stroke();
    ink = true;
  };
  pad.onlostpointercapture = () => { pointer = null; };
  clear.onclick = () => {
    ctx.clearRect(0, 0, pad.width, pad.height);
    ink = false;
  };
  return () => {
    if (!ink) throw new Error("請先在簽名區手寫簽名。");
    return pad.toDataURL("image/png");
  };
}

function initSigner() {
  const storageKey = "signing.invitation";
  if (secret) sessionStorage.setItem(storageKey, secret);
  const invitation = sessionStorage.getItem(storageKey);
  if (!invitation) {
    message("請從邀請信開啟你的專屬簽署網頁。", true);
    return;
  }
  const verify = byId("verify");
  verify.hidden = false;
  byId("send").onclick = handle(byId("send"), async () => {
    await request("/api/signing/code", invitation, {method: "POST"});
    message("驗證碼已寄到受邀信箱。");
    byId("code").focus();
  });
  const codeForm = byId("codeForm");
  const signatureForm = byId("signatureForm");
  const readInk = signaturePad(byId("pad"), byId("clear"));
  codeForm.onsubmit = handle(codeForm.querySelector("button"), async () => {
    const {session} = await request("/api/signing/verify", invitation, {
      method: "POST", data: {code: byId("code").value},
    });
    const record = await request("/api/signing/session", session);
    const pdf = await request("/api/signing/pdf", session, {pdf: true});
    const url = URL.createObjectURL(pdf);
    byId("openPdf").href = url;
    byId("pdfFrame").src = url;
    byId("title").textContent = roleName(record.role) + "簽署｜交通事故和解書";
    byId("fingerprint").textContent = "版本 " + record.revision + " · SHA-256 " + record.sha256;
    byId("code").value = "";
    verify.hidden = true;
    byId("document").hidden = false;
    message("信箱已驗證。請核對完整和解書。");
    signatureForm.onsubmit = handle(signatureForm.querySelector('button[type="submit"]'), async () => {
      const result = await request("/api/signing/submit", session, {
        method: "POST", data: {confirmed: byId("confirmed").checked, image: readInk()},
      });
      if (sessionStorage.getItem(storageKey) === invitation) sessionStorage.removeItem(storageKey);
      byId("document").hidden = true;
      byId("pdfFrame").removeAttribute("src");
      URL.revokeObjectURL(url);
      message(result.completed ? "你的簽名已儲存，雙方已完成簽署。" : "你的簽名已儲存。");
      byId("status").focus();
    });
  });
}

function initOwner() {
  const revision = new URLSearchParams(location.search).get("revision");
  const key = "signing.owner." + revision;
  if (secret && revision) sessionStorage.setItem(key, secret);
  const owner = sessionStorage.getItem(key);
  if (!owner || !/^[a-f0-9]{32}$/.test(revision || "")) {
    byId("refresh").hidden = true;
    message("請從事故 App 開啟發起人進度頁。", true);
    return;
  }
  const endpoint = "/api/signing/owner/" + revision;
  const refresh = byId("refresh");
  const retry = byId("retry");
  const download = byId("download");
  const replay = byId("downloadReplay");
  const labels = {signed: "已簽署", waiting: "已寄出，待簽署", revoked: "已撤銷", expired: "邀請已到期", unsent: "尚未寄出"};
  const date = new Intl.DateTimeFormat("zh-TW", {
    dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Taipei",
  });
  const update = async () => {
    const record = await request(endpoint, owner);
    byId("signers").replaceChildren(...record.signers.map(signer => {
      const item = document.createElement("li");
      item.textContent = roleName(signer.role) + " " + labels[signer.status] +
        (signer.confirmed_at ? " · " + date.format(new Date(signer.confirmed_at * 1000)) : "");
      return item;
    }));
    byId("fingerprint").textContent = "版本 " + record.revision + " · SHA-256 " + record.sha256;
    retry.hidden = record.superseded || !record.signers.some(signer => ["unsent", "expired"].includes(signer.status));
    download.hidden = !record.completed;
    replay.hidden = record.superseded;
    message(record.completed ? "雙方已完成簽署。" : record.superseded ? "此版本已撤銷。" : "已更新簽署進度。");
  };
  refresh.onclick = handle(refresh, update);
  retry.onclick = handle(retry, async () => {
    const result = await request(endpoint + "/retry", owner, {method: "POST"});
    await update();
    if (result.delivery_failed.length) message(result.delivery_failed.map(roleName).join("、") + "邀請未能寄出。", true);
  });
  replay.onclick = handle(replay, async () => {
    const data = await request(endpoint + "/replay", owner);
    message("正在打包可離線開啟的 HTML…");
    await downloadReplayHtml(revision, data);
    message("已產生 HTML，可離線開啟。");
  });
  download.onclick = handle(download, async () => {
    const blob = await request(endpoint + "/pdf", owner, {pdf: true});
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "settlement-signed.pdf";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    message("已取得雙方簽署的和解書。");
  });
  refresh.onclick();
}

if (document.querySelector("main").dataset.page === "signer") initSigner();
else initOwner();


function replayHtml(viewer, data, dependencies, analysisStyles, modules) {
  const script=value=>"<script>"+value.replace(/<\/script/gi,"<\\/script")+"</"+"script>";
  const payload=JSON.stringify(data).replace(/</g,"\\u003c");
  const embedded=JSON.stringify(modules).replace(/</g,"\\u003c");
  const boot=`
  (async () => {
    const data=JSON.parse(document.getElementById("replayData").textContent);
    const summary=document.getElementById("exportSummary");
    if (data.reconstruction) {
      const {origin,scale,basis}=data.reconstruction;
      summary.textContent="依影片估算 · 原點："+origin+" · 尺度："+scale+" · "+basis;
      document.getElementById("exportDetails").hidden=false;
      document.getElementById("exportCoordinates").textContent=JSON.stringify(data.reconstruction,null,2);
    } else {
      summary.textContent="依事故類型產生的 3D 示意。";
    }
    const moduleUrls=[];
    let animated=false;
    try {
      const modules=JSON.parse(document.getElementById("replayModules").textContent);
      for (const parts of modules) {
        const source=parts.map(part=>typeof part==="number" ? moduleUrls[part] : part).join("");
        moduleUrls.push(URL.createObjectURL(new Blob([source],{type:"text/javascript"})));
      }
      await import(moduleUrls.at(-1));
      animated=window.Replay.load(data);
    } catch (error) {
      console.warn("進階光影重播初始化失敗",error);
    } finally {
      moduleUrls.forEach(url=>URL.revokeObjectURL(url));
    }
    if (!animated) {
      document.getElementById("r3d").replaceChildren();
      animated=window.ReplayFallback?.load(data)===true;
      if (animated) {
        window.Replay=window.ReplayFallback;
        summary.textContent+=" 此瀏覽器使用基本 3D 模式。";
      }
    }
    if (!animated) {
      summary.textContent+=" 此瀏覽器無法啟用 WebGL。";
      document.getElementById("viewer").hidden=true;
    }
    document.getElementById("rv").hidden=!!data.reconstruction;
    document.getElementById("replayMode").textContent=animated ? "3D 場景" : "參數重播";
    window.ReplayAnalysis.load(data,window.Replay,animated);
  })();
  `;
  return `<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>事故 3D 重播</title>
<style>
body{font:15px/1.55 "Segoe UI","Noto Sans TC",system-ui,sans-serif;margin:0;background:#09111e;color:#f1f6fc}
main{max-width:1390px;margin:auto;padding:clamp(14px,3vw,36px)}
h1{font-size:1.5rem}
#viewer{background:#101d2e;color:#f0f6fd;border-radius:14px;overflow:hidden}
#r3d{height:min(58vh,560px);min-height:280px}
.viewer-controls{padding:12px 16px}
.legend,.row,.seg{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}
button{font:inherit;color:inherit;background:#20354c;border:1px solid #4b6076;border-radius:8px;padding:6px 10px;cursor:pointer}
button.on{background:#34639a}#rt{width:100%;margin:8px 0}
.sub{margin:.5em 0}pre{overflow:auto;max-height:25rem}details{margin-top:14px}
[hidden]{display:none!important}
${analysisStyles}
</style></head><body><main class="replay-shell"><header class="stage-heading"><div><h2>事故重播與軌跡分析</h2><p class="sub" id="exportSummary"></p></div></header>
<div class="replay-workspace">${viewer}</div>
<details id="exportDetails" hidden><summary>查看 3D 座標與影片依據</summary><pre id="exportCoordinates"></pre></details>
</main><script id="replayData" type="application/json">${payload}</${"script"}>
<script id="replayModules" type="application/json">${embedded}</${"script"}>
${dependencies.map(script).join("\n")}
${script(boot)}
</body></html>`;
}

// Keep modules in dependency order so the saved file can create local URLs offline.
async function packReplayModules(entry, imports, load, baseUrl) {
  const modules=[];
  const pending=new Map();
  const resolve=(specifier,parent)=>{
    const prefix=Object.keys(imports)
      .filter(key=>key.endsWith("/") && specifier.startsWith(key))
      .sort((a,b)=>b.length-a.length)[0];
    const mapped=imports[specifier] ?? (prefix && imports[prefix]+specifier.slice(prefix.length));
    return new URL(mapped ?? specifier,mapped ? baseUrl : parent).href;
  };
  const visit=url=>{
    if (!pending.has(url)) pending.set(url,(async()=>{
      const source=await load(url);
      const matches=[...source.matchAll(/^[\t ]*(?:import\s+(?:[^"';]+?\s+from\s+)?|export\s+(?:\*[^;]*?|\{[^}]*\})\s+from\s+)(["'])([^"'\r\n]+)\1/gm)];
      const childIds=await Promise.all(matches.map(match=>visit(resolve(match[2],url))));
      const parts=[];
      let cursor=0;
      matches.forEach((match,index)=>{
        const start=match.index+match[0].length-match[2].length-1;
        parts.push(source.slice(cursor,start),childIds[index]);
        cursor=start+match[2].length;
      });
      parts.push(source.slice(cursor));
      return modules.push(parts)-1;
    })());
    return pending.get(url);
  };
  await visit(new URL(entry,baseUrl).href);
  return modules;
}

async function downloadReplayHtml(id, data, source) {
  const replayData = {
    collision_type: data.collision_type, maneuver_party: data.maneuver_party,
    relative_crossing: data.relative_crossing,
    party_a: {vehicle: data.party_a.vehicle}, party_b: {vehicle: data.party_b.vehicle},
    fault_hint: {a_percent: data.fault_hint.a_percent, b_percent: data.fault_hint.b_percent},
    scenario: data.scenario, reconstruction: data.reconstruction,
  };
  const signal = AbortSignal.timeout(15000);
  const load = async url => {
    const response = await fetch(url, {signal});
    if (!response.ok) throw new Error("Replay resource unavailable");
    return response.text();
  };
  let dependencies, modules;
  try {
    source ??= new DOMParser().parseFromString(await load("/index.html"),"text/html");
    const importMap=source.querySelector("script[type=importmap]");
    [dependencies, modules]=await Promise.all([
      Promise.all(["/scenario.js?v=8","https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js",
        "/replay.js?v=5","/replay-analysis.js?v=3"].map(load)),
      packReplayModules(importMap.dataset.replayModule,JSON.parse(importMap.textContent).imports,load,location.href),
    ]);
    dependencies.push("window.ReplayFallback=window.Replay;");
  } catch {
    throw new Error("無法載入重播元件，請確認網路連線後重新下載。");
  }
  const viewer = source.getElementById("viewer").cloneNode(true);
  viewer.hidden = false;
  viewer.querySelector("#r3d").replaceChildren();
  viewer.querySelector("#diagram").replaceChildren();
  for (const node of viewer.querySelectorAll("[hidden]")) node.hidden = false;
  const analysis = source.getElementById("replayAnalysis").cloneNode(true);
  const styles = source.getElementById("replayLayoutStyles").textContent;
  const html = replayHtml(viewer.outerHTML + analysis.outerHTML, replayData, dependencies, styles, modules);
  const url = URL.createObjectURL(new Blob([html], {type: "text/html;charset=utf-8"}));
  const link = document.createElement("a");
  link.href = url;
  link.download = "accident-replay-" + id + ".html";
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

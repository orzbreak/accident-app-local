(function () {
  const find = id => document.getElementById(id);
  const colors = ["#65c9ff", "#ffb476", "#81ebcc", "#d5b3ff"];
  const box = { left: 44, right: 564, top: 12, bottom: 146 };
  const degrees = 180 / Math.PI;
  let session, metric = "speed", cursor, values, lastTime;

  function measured(reconstruction) {
    const start = Math.min(...reconstruction.objects.map(item => item.poses[0].t));
    const end = Math.max(...reconstruction.objects.map(item => item.poses.at(-1).t));
    const series = reconstruction.objects.map(item => ({
      name: item.id,
      points: item.poses.map((point, index, poses) => {
        const prior = poses[index - 1];
        const seconds = prior ? point.t - prior.t : 0;
        return { t: point.t - start, roll: point.r[0] * degrees,
          speed: seconds > 0 && seconds <= 2 ? 3.6 * Math.hypot(point.p[0] - prior.p[0], point.p[2] - prior.p[2]) / seconds : null };
      }),
    }));
    const events = [{ t: 0, title: "重播區間開始", detail: "從最早有參與者座標的畫面開始。" }];
    if (reconstruction.impact) {
      const { t, evidence } = reconstruction.impact;
      events.push({ t: t - start, title: "首次接觸", detail: evidence });
    }
    for (const line of series) {
      const tilt = line.points.find(point => Math.abs(point.roll) >= 45);
      if (tilt) events.push({ t: tilt.t, title: line.name + "側傾達 45°", detail: "依重播姿態，角度取絕對值。" });
    }
    for (const signal of reconstruction.signals) {
      for (const phase of signal.states) {
        if (phase.start >= start && phase.start <= end) {
          events.push({ t: phase.start - start, title: signal.id + " · " + phase.color, detail: signal.evidence });
        }
      }
    }
    return { duration: end - start, series, events, measured: true };
  }

  function template(data, replay) {
    if (!replay?._plan) return null;
    const plan = replay._plan(data);
    const {motions,masses,impulse,hit}=collisionMotion(plan,data,replay._sample);
    const series = motions.map((motion,index) => {
      return { name: index===0 ? "甲方" : "乙方", points: Array.from({ length: 121 }, (_, i) => {
        const t = i * plan.T / 120;
        const end = Math.min(t + .02, plan.T), start = Math.max(0, t - .02);
        const [x,z] = motion(end), [px,pz] = motion(start);
        const roll = motion(t)[3];
        return { t, speed: 3.6 * Math.hypot(x - px, z - pz) / (end - start), roll: roll == null ? null : roll * 180 / Math.PI };
      }) };
    });
    const events = [
      { t: 0, title: "事故前段", detail: "查看接觸前雙方的行進或停等位置。" },
    ];
    if (hit!==null) {
      events.push({ t: hit, title: "示意首次接觸", detail:
        "依車身外緣判定。示意質量：甲方 "+(masses[0] ?? 1500).toLocaleString()+" kg、乙方 "+(masses[1] ?? 1500).toLocaleString()+" kg。"+
        (impulse>0 ? "依質量分配速度變化。" : "接觸時沒有可用的接近速度。") });
    } else {
      events[0].detail="依目前路徑與速度，車身外緣未接觸。";
    }
    if (plan.rolloverAt != null) {
      events.push(
        { t: plan.evasiveAt, title: "甲方開始閃避", detail: "乙方向雙黃線偏移，甲方開始向外側閃避。" },
        { t: plan.rolloverAt, title: "甲方開始翻覆", detail: "依範例呈現車身側傾與翻覆。" },
      );
    }
    const afterContact = hit!==null && series[0].points.find((point,index)=>
      point.t > hit + .35 && series.some(line=>line.points[index].speed >= 2));
    if (afterContact) {
      events.push({ t: afterContact.t, title: "接觸後仍在移動", detail: "依示意軌跡，至少一方在接觸後仍有位移。" });
    }
    events.push({ t: plan.T, title: "重播結束", detail: "查看示意軌跡的最後位置。" });
    return { duration: plan.T, series, events };
  }

  function svg(tag, attributes) {
    const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
    find("replayChart").append(element);
    return element;
  }
  const x = time => box.left + (box.right - box.left) * time / session.duration;

  function renderChart() {
    lastTime = null;
    const chart = find("replayChart");
    chart.replaceChildren();
    const all = session.series.flatMap(line => line.points.map(point => point[metric])).filter(Number.isFinite);
    const step = metric === "speed" ? 10 : 30;
    const max = Math.max(step, Math.ceil(Math.max(0,...all) / step) * step);
    const min = metric === "speed" ? 0 : Math.floor(Math.min(0,...all) / step) * step;
    const y = value => box.bottom - (box.bottom - box.top) * (value - min) / (max - min);
    for (let i = 0; i <= 4; i++) {
      const level = min + (max - min) * i / 4;
      svg("line",{x1:box.left,x2:box.right,y1:y(level),y2:y(level),stroke:"#354a61"});
      svg("text",{x:box.left-6,y:y(level)+4,fill:"#b4c5d6","text-anchor":"end","font-size":11}).textContent=Math.round(level);
      const time = session.duration * i / 4;
      svg("text",{x:x(time),y:166,fill:"#b4c5d6","text-anchor":"middle","font-size":11}).textContent=time.toFixed(1)+" s";
    }
    for (const event of session.events.filter(event => event.title.includes("接觸"))) {
      svg("line",{x1:x(event.t),x2:x(event.t),y1:box.top,y2:box.bottom,stroke:"#cbd4df","stroke-dasharray":"3 4"});
    }
    session.series.forEach((line,index)=>{
      let path="", previous=null;
      for (const point of line.points) {
        const value=point[metric];
        if (!Number.isFinite(value)) { previous=null; continue; }
        path+=(previous && point.t-previous.t<=2 ? "L" : "M")+x(point.t)+","+y(value)+" ";
        previous=point;
      }
      if (path) svg("path",{d:path,fill:"none",stroke:colors[index%colors.length],"stroke-width":2});
    });
    cursor=svg("line",{x1:box.left,x2:box.left,y1:box.top,y2:box.bottom,stroke:"#eaf4ff","stroke-width":1.25});
    const source=session.measured ? "依影片重播座標估算，缺資料時留空。" : "依示意軌跡計算。";
    find("replayChartNote").textContent=(metric==="speed" ? "平面速度（km/h）。" : "X 軸側傾角（°）。")+source;
    find("plotSpeed").setAttribute("aria-pressed",String(metric==="speed"));
    find("plotRoll").setAttribute("aria-pressed",String(metric==="roll"));
    update();
  }

  function seek(time) {
    if (!session) return;
    const slider=find("rt");
    const target=Math.max(0,Math.min(session.duration,time));
    session.time=target;
    if (session.animated) {
      slider.value=Math.round(target*100);
      slider.dispatchEvent(new Event("input",{bubbles:true}));
    }
    update();
  }

  function update() {
    if (!session || find("replayAnalysis").hidden) return;
    const time=session.animated ? Math.min(session.duration, Number(find("rt").value)/100) : session.time;
    if (time===lastTime) return;
    lastTime=time;
    const position=x(time);
    cursor.setAttribute("x1",position);
    cursor.setAttribute("x2",position);
    session.series.forEach((line,index)=>{
      const next=line.points.findIndex(point=>point.t>=time);
      const after=line.points[next], before=line.points[next-1];
      let value=after?.[metric];
      if (after && Math.abs(after.t-time)>.011) {
        value=before && after.t-before.t<=2 && Number.isFinite(before[metric]) && Number.isFinite(value)
          ? before[metric]+(value-before[metric])*(time-before.t)/(after.t-before.t) : null;
      }
      values[index].textContent=line.name+" "+(Number.isFinite(value) ? value.toFixed(1)+(metric==="speed"?" km/h":"°") : "—");
    });
    const current=session.events.findLastIndex(event=>event.t<=time+.005);
    [...find("replayEvents").querySelectorAll("button")].forEach((button,index)=>button.setAttribute("aria-current",String(index===current)));
  }

  function load(data,replay,animated) {
    session=data.reconstruction ? measured(data.reconstruction) : template(data,replay);
    find("replayAnalysis").hidden=!session?.duration;
    if (!session?.duration) return;
    session.animated=animated;
    session.time=0;
    session.events.sort((a,b)=>a.t-b.t);
    const list=find("replayEvents");
    list.replaceChildren();
    for (const event of session.events) {
      const row=document.createElement("li"), button=document.createElement("button");
      button.type="button";
      const timestamp=document.createElement("span"), title=document.createElement("strong");
      timestamp.className="replay-event-time";
      timestamp.textContent=event.t.toFixed(2)+" s";
      title.textContent=event.title;
      button.append(timestamp,title);
      if (event.detail) {
        const detail=document.createElement("small");
        detail.textContent=event.detail;
        button.append(detail);
      }
      button.onclick=()=>seek(event.t);
      row.append(button);
      list.append(row);
    }
    if (!list.children.length) {
      const row=document.createElement("li");
      row.textContent="現有資料未能確認關鍵事件時間。";
      list.append(row);
    }
    const labels=find("replayValues");
    labels.replaceChildren();
    values=session.series.map((_,index)=>{
      const label=document.createElement("span");
      label.style.color=colors[index%colors.length];
      labels.append(label);
      return label;
    });
    renderChart();
  }

  find("plotSpeed").onclick=()=>{ metric="speed"; renderChart(); };
  find("plotRoll").onclick=()=>{ metric="roll"; renderChart(); };
  find("replayChart").addEventListener("click",event=>{
    const transform=find("replayChart").getScreenCTM();
    if (!session || !transform) return;
    const point=new DOMPoint(event.clientX,event.clientY).matrixTransform(transform.inverse());
    seek(session.duration*(point.x-box.left)/(box.right-box.left));
  });
  function tick() { update(); requestAnimationFrame(tick); }
  requestAnimationFrame(tick);
  window.ReplayAnalysis={load};
})();

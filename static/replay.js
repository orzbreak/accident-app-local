/* 事故 3D 重播(示意):依事故類型與責任方向,腳本式生成動畫。非實景還原,不具證據效力。
   座標:道路沿 +x,+z 為駕駛右側;車輛前方為本地 +x。+x 向車道 z=2(內)與 z=5(外)。 */
(function () {
  // ---------- 腳本(純資料,不依賴 three.js)----------
  // 每個關鍵影格 [t, x, z, yaw];yaw>0 逆時針(俯視),車頭 = (cos yaw, -sin yaw)
  const SCEN = {
    rear_end: {
      T: 5, hit: 2.2,
      R: [[0, -26, 2, 0], [2.2, -4.35, 2, 0], [2.8, -3.3, 2, 0], [3.4, -2.1, 2, 0.02], [5, -1.9, 2, 0]],
      F: [[0, 0, 2, 0], [2.2, 0, 2, 0], [2.8, 1.3, 2, 0.02], [3.4, 2.6, 2, 0.03], [5, 2.8, 2, 0]]
    },
    lane_change: {
      T: 5, hit: 1.6,
      S: [[0, -20, 5, 0], [1.6, -4, 5, 0], [2.1, -1.9, 5.4, -0.08], [4.5, 3.2, 5.9, -0.05], [5, 3.4, 5.9, 0]],
      C: [[0, -26, 2, 0], [0.8, -17, 2, 0], [1.6, -8.2, 3.4, -0.18], [2.1, -4.8, 3.5, 0], [4.5, -0.5, 3.2, 0.1], [5, -0.3, 3.2, 0.1]]
    },
    intersection: {
      T: 5.5, hit: 2.7,
      A: [[0, -28, 2, 0], [2.7, -1.1, 2, 0], [3.2, 0.0, 2.3, -0.1], [5.5, 1.2, 2.8, -0.2]],
      B: [[0, 2, 28, Math.PI / 2], [2.7, 2, 4.7, Math.PI / 2], [3.2, 3.7, 3.0, Math.PI / 2 - 0.25], [4.2, 5.0, 0.5, Math.PI / 2 - 0.5], [5.5, 6.2, -2.0, Math.PI / 2 - 0.7]]
    }
  };

  function plan(d) {
    const rollover = oncomingRolloverPlan(d, sample);
    if (rollover) return rollover;
    const f = d.fault_hint, type = d.collision_type;
    const aWorse = ["A","B"].includes(d.maneuver_party) ? d.maneuver_party==="A" : f.a_percent > f.b_percent;
    const length = type => ({"汽車":4.4,"公車":9.4,"貨車":7,"機車":2,"自行車":2,"行人":.6})[type] || 1;
    let s, A, B, cap;
    const crossing = relativeCrossingTracks(d.relative_crossing, SCEN.intersection);
    if (crossing) {
      s = SCEN.intersection; ({A,B} = crossing);
      const direction = d.relative_crossing.direction === "left_to_right" ? "左往右" : "右往左";
      const [reference, moving] = d.relative_crossing.reference_party === "A" ? ["甲方","乙方"] : ["乙方","甲方"];
      cap = `以${reference}行進方向為前方，${moving}由${direction}橫越。`;
    } else if (type === "rear_end") {
      s = SCEN.rear_end;
      const spacing = (length(d.party_a?.vehicle) + length(d.party_b?.vehicle)) / 2 - 4.35;
      const rear = s.R.map(([t,x,z,yaw]) => [t,x-spacing,z,yaw]);
      A = aWorse ? rear : s.F; B = aWorse ? s.F : rear;
      cap = `追撞示意:${aWorse ? "甲方" : "乙方"}為後車。`;
    } else if (type === "lane_change") {
      s = SCEN.lane_change;
      const aChanges = aWorse; // 責任較高者視為變換車道方
      A = aChanges ? s.C : s.S; B = aChanges ? s.S : s.C;
      cap = `變換車道示意:${aChanges ? "甲方" : "乙方"}為切入方。`;
    } else {
      s = SCEN.intersection; A = s.A; B = s.B;
      cap = type === "intersection" ? "路口碰撞示意:雙方行向垂直交會。" : "資訊不足,以路口碰撞作一般性示意。";
    }
    return { A, B, T: s.T, hit: s.hit, cap: cap + "依事故類型腳本生成,不是實景還原,不具證據效力。" };
  }

  function sample(kf, t) {
    const n = kf.length;
    if (t <= kf[0][0]) return kf[0].slice(1);
    if (t >= kf[n - 1][0]) return kf[n - 1].slice(1);
    let i = 0; while (t > kf[i + 1][0]) i++;
    const p0 = kf[Math.max(0, i - 1)], p1 = kf[i], p2 = kf[i + 1], p3 = kf[Math.min(n - 1, i + 2)];
    const h = p2[0] - p1[0], u = (t - p1[0]) / h, u2 = u * u, u3 = u2 * u;
    return p1.slice(1).map((_, index) => {
      const k = index + 1;
      const m1 = (p2[k] - p0[k]) / (p2[0] - p0[0]), m2 = (p3[k] - p1[k]) / (p3[0] - p1[0]);
      return (2 * u3 - 3 * u2 + 1) * p1[k] + (u3 - 2 * u2 + u) * h * m1 + (-2 * u3 + 3 * u2) * p2[k] + (u3 - u2) * h * m2;
    });
  }

  const api = { _plan: plan, _sample: sample };
  window.Replay = api;
  if (typeof THREE === "undefined") { api.load = () => false; return; }

  // ---------- three.js 場景 ----------
  const $ = id => document.getElementById(id);
  let ren, scene, cam, world, cars = {}, flash, P = null, t = 0, playing = true, speed = 1, view = "bird", last = 0, ready = false;
  let sun, ambient, rain, asphalt, measuredLayer;
  const orbit = { th: -1.0, ph: 1.0, r: 30 };
  const el = () => $("r3d");

  function label(text, color) {
    const c = document.createElement("canvas"); c.width = c.height = 128;
    const g = c.getContext("2d"); g.fillStyle = color; g.beginPath(); g.arc(64, 64, 58, 0, 7); g.fill();
    g.fillStyle = "#fff"; g.font = "bold 76px sans-serif"; g.textAlign = "center"; g.textBaseline = "middle"; g.fillText(text, 64, 70);
    const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c) })); s.scale.set(1.7, 1.7, 1); s.position.y = 2.7; return s;
  }
  function bevel(w, h, d, radius, material) {
    const x = w / 2 - radius, y = h / 2 - radius;
    const outline = new THREE.Shape();
    outline.moveTo(-x, -y); outline.lineTo(x, -y); outline.lineTo(x, y);
    outline.lineTo(-x, y); outline.closePath();
    const geometry = new THREE.ExtrudeGeometry(outline, {
      depth: d - 2 * radius, bevelEnabled: true, bevelSize: radius,
      bevelThickness: radius, bevelSegments: 3, steps: 1
    });
    geometry.center();
    return new THREE.Mesh(geometry, material);
  }
  function makeCar(color, text) {
    const car = new THREE.Group();
    const metal = (color, roughness = .48) => new THREE.MeshStandardMaterial({ color, roughness, metalness: .22 });
    const paint = metal(color, .32), rubber = metal(0x17202a, .94), trim = metal(0x283340);
    const glass = metal(0x243e54, .24), alloy = metal(0xb9c9d6), plate = metal(0xf3ead7);
    const lamp = color => new THREE.MeshBasicMaterial({ color });
    const head = lamp(0xfff1cf), tail = lamp(0xff5146);
    const add = (mesh, x, y, z) => {
      mesh.position.set(x,y,z); mesh.castShadow = true; car.add(mesh); return mesh;
    };
    const body = (w,h,d,r,m,x,y,z) => add(bevel(w,h,d,r,m),x,y,z);
    body(4.45,.31,1.86,.11,trim,0,.48,0);
    body(4.4,.53,1.89,.19,paint,0,.74,0);
    body(1.88,.27,1.52,.12,paint,-.32,1.48,0);
    const cabin = new THREE.Shape();
    cabin.moveTo(-1.47,.92); cabin.lineTo(-1.08,1.52); cabin.lineTo(.49,1.52);
    cabin.lineTo(1.19,.92); cabin.closePath();
    const windows = new THREE.ExtrudeGeometry(cabin,{depth:1.47,bevelEnabled:false});
    windows.translate(0,0,-.735);
    add(new THREE.Mesh(windows,glass),0,0,0);
    for (const side of [-1,1]) {
      body(.08,.46,.045,.014,trim,-.30,1.23,side*.749);
      body(.28,.10,.18,.041,paint,.73,1.10,side*.91);
      body(.16,.052,.48,.023,head,2.07,.83,side*.64);
      body(.12,.084,.49,.026,tail,-2.09,.81,side*.62);
      for (const x of [-1.39,1.39]) {
        const wheel = add(new THREE.Mesh(new THREE.CylinderGeometry(.37,.37,.28,32),rubber),x,.37,side*.92);
        wheel.rotation.x = Math.PI / 2;
        const hub = add(new THREE.Mesh(new THREE.CylinderGeometry(.244,.244,.035,24),alloy),x,.37,side*1.071);
        hub.rotation.x = Math.PI / 2;
        for (let i=0;i<5;i++) {
          const angle=i*Math.PI*2/5;
          const spoke=body(.06,.14,.018,.007,trim,x+Math.sin(angle)*.13,.37+Math.cos(angle)*.13,side*1.093);
          spoke.rotation.z=-angle;
        }
      }
    }
    body(.09,.20,.70,.035,trim,2.18,.65,0);
    body(.045,.115,.32,.011,plate,-2.21,.66,0);
    car.add(label(text,color === 0x3185db ? "#2566a5" : "#a8491a"));
    return car;
  }
  function makeParticipant(type, color, text) {
    if (type === "汽車") return makeCar(color,text);
    const group = new THREE.Group();
    const paint = new THREE.MeshStandardMaterial({color});
    const dark = new THREE.MeshStandardMaterial({color:0x222b35});
    const glass = new THREE.MeshStandardMaterial({color:0x486983,roughness:.22});
    const skin = new THREE.MeshStandardMaterial({color:0xe4b590});
    const add = (geometry,material,x,y,z) => {
      const mesh = new THREE.Mesh(geometry,material);
      mesh.position.set(x,y,z); mesh.castShadow = true; group.add(mesh); return mesh;
    };
    const box = (w,h,d,m,x,y,z) => add(new THREE.BoxGeometry(w,h,d),m,x,y,z);
    const ball = (r,m,x,y,z) => add(new THREE.SphereGeometry(r,16,12),m,x,y,z);
    const wheel = (x,y,z,r,w) => {
      const tire = add(new THREE.CylinderGeometry(r,r,w,24),dark,x,y,z);
      tire.rotation.x = Math.PI/2;
    };
    const badge = label(text,color === 0x3185db ? "#2566a5" : "#a8491a");
    if (type === "行人") {
      ball(.16,skin,.01,1.59,0);
      box(.23,.48,.34,paint,0,1.22,0);
      for (const side of [-1,1]) {
        box(.14,.62,.13,dark,side*.09,.56,side*.09).rotation.z=side*.24;
        box(.11,.39,.10,paint,-side*.07,1.18,side*.22).rotation.z=-side*.29;
        box(.25,.10,.14,dark,.06+side*.16,.14,side*.09);
      }
    } else if (type === "機車" || type === "自行車") {
      const bicycle = type === "自行車";
      const r = bicycle ? .35 : .28;
      for (const x of [-.65,.65]) {
        const rim = add(new THREE.TorusGeometry(r,bicycle?.031:.066,10,28),dark,x,r,0);
        rim.rotation.y = 0;
        for (let i=0;i<6;i++) {
          const a=i*Math.PI/3;
          box(.022,r,.025,glass,x+Math.sin(a)*r*.5,r+Math.cos(a)*r*.5,0).rotation.z=-a;
        }
      }
      box(1.12,bicycle?.07:.27,bicycle?.08:.34,paint,-.12,.55,0);
      box(.09,.72,.09,paint,.51,.68,0).rotation.z=.25;
      box(.09,.08,.47,dark,.45,1.02,0);
      box(.38,.09,.24,dark,-.29,.88,0);
      box(.24,.49,.34,paint,-.22,1.21,0).rotation.z=-.24;
      box(.44,.12,.25,dark,-.09,.88,0);
      ball(.16,bicycle?skin:paint,-.10,1.53,0);
    } else if (type === "公車" || type === "貨車") {
      const bus = type === "公車";
      const length = bus ? 9.4 : 7;
      box(length, .53,2.33, dark,0,.7,0);
      box(bus?length:5,2.2,2.36,paint,bus?0:-.94,1.91,0);
      if (!bus) box(1.73,1.82,2.32,paint,2.5,1.59,0);
      for (const side of [-1,1]) {
        for (const x of (bus?[-3,2.9]:[-2.25,2.28])) wheel(x,.52,side*1.1,.51,.30);
        if (bus) {
          for (const x of [-3.72,-2.49,-1.26,-.03,1.2,2.43,3.66]) box(1.07,.83,.035,glass,x,2.42,side*1.194);
          box(length,.15,.04,dark,0,1.44,side*1.195);
        } else {
          box(1.18,.58,.045,glass,2.54,2.05,side*1.17);
          box(4.65,.12,.045,dark,-.94,1.23,side*1.19);
        }
      }
      box(.055,.83,1.94,glass,bus?4.71:3.38,bus?2.39:2.03,0);
      badge.position.y = 3.85;
    } else {
      const marker = add(new THREE.CylinderGeometry(.44,.54,.20,24),paint,0,.1,0);
      marker.receiveShadow = true;
    }
    group.add(badge);
    return group;
  }
  function replaceParticipants(d) {
    const s=d.scenario;
    const nearby=[["V",s.thirdVehicle==="無"?null:s.thirdVehicle,0x358a68,"車"],
                  ["P",s.thirdPedestrian==="有"?"行人":null,0x825ab5,"人"]];
    for (const [key,party,color,text] of [["A",d.party_a,0x3185db,"甲"],["B",d.party_b,0xe97535,"乙"],...nearby.map(([key,vehicle,color,text])=>[key,{vehicle},color,text])]) {
      const type = party.vehicle;
      if (cars[key]?.userData.type === type || (!cars[key] && type === null)) continue;
      if (cars[key]) {
        const materials = new Set();
        cars[key].traverse(object => {
          if (object.isMesh) object.geometry.dispose();
          if (object.material) materials.add(object.material);
        });
        materials.forEach(material => { material.map?.dispose(); material.dispose(); });
        scene.remove(cars[key]);
        delete cars[key];
      }
      if (type === null) continue;
      cars[key] = makeParticipant(type,color,text);
      cars[key].userData.type = type;
      scene.add(cars[key]);
    }
  }

  function rnd(i) { const x = Math.sin(i * 12.9898) * 43758.5453; return x - Math.floor(x); }

  function buildWorld(type) {
    if (world) {
      scene.remove(world);
      const materials = new Set();
      world.traverse(mesh => {
        if (!mesh.isMesh) return;
        mesh.geometry.dispose();
        materials.add(mesh.material);
      });
      materials.forEach(material => material.dispose());
    }
    world = new THREE.Group(); scene.add(world);
    const palette = new Map();
    function mesh(geometry, color, x,y,z, shadow) {
      if (!palette.has(color)) palette.set(color,new THREE.MeshLambertMaterial({color}));
      const item = new THREE.Mesh(geometry,palette.get(color));
      item.position.set(x,y,z); item.receiveShadow = true; item.castShadow = shadow;
      world.add(item); return item;
    }
    const box = (w,h,d,c,x,y,z,shadow=false) => mesh(new THREE.BoxGeometry(w,h,d),c,x,y,z,shadow);
    const straightZ = type === "straight_z";
    const cross = !straightZ && type !== "rear_end" && type !== "lane_change";
    if (straightZ) world.rotation.y = Math.PI / 2;
    const road = 0x3d4957, curb = 0xa6afac, walk = 0xd0c4ad;
    const white = 0xe8ded0, gold = 0xe9af54;
    box(250,.1,250,0x657d6a,0,-.085,0);
    const laneRoadWidth = straightZ ? 9 : 14;
    box(180,.10,laneRoadWidth,road,0,-.052,0);
    if (straightZ) {
      for (const side of [-1,1]) box(180,.10,2.5,0x56616d,0,-.052,side*5.75);
    }
    if (cross) box(14,.10,180,road,0,-.05,0);
    asphalt=palette.get(road);
    const chunks = cross ? [[-49,82],[49,82]] : [[0,180]];
    for (const [mid,length] of chunks) {
      for (const side of [-1,1]) {
        box(length,.18,2.9,walk,mid,.04,side*8.55);
        box(length,.22,.20,curb,mid,.055,side*7.08);
        box(length,.013,.12,white,mid,.007,side*(straightZ ? 4.44 : 6.72));
        box(length,.014,.09,gold,mid,.008,side*.14);
      }
    }
    for (let x=-85;!straightZ && x<86;x+=6) {
      if (cross && Math.abs(x)<11) continue;
      for (const side of [-1,1]) box(2.8,.015,.11,white,x,.008,side*3.5);
    }
    if (cross) {
      for (const side of [-1,1]) {
        // Centerlines on the Z road stop before each approach's stop line.
        for (const offset of [-.14,.14]) box(.09,.014,79.8,gold,offset,.008,side*50);
        for (let n=-5;n<=5;n++) {
          box(1.6,.019,.37,white,side*8.4,.012,n*.99);
          box(.37,.019,1.6,white,n*.99,.012,side*8.4);
        }
        box(.16,.016,6.5,white,side*10,.01,3.36);
        box(6.5,.016,.16,white,-3.36,.01,side*10);
      }
    }
    const walls = [0xa5b3bb,0xb59d89,0xc6b7a3,0x8598a7];
    for (let i=0;i<22;i++) {
      const x=-65+i*6.3, side=i%2 ? 1 : -1, z=side*(16+rnd(i)*3);
      if (cross && Math.abs(x)<12) continue;
      const height=3+Math.floor(rnd(i+70)*4)*1.8;
      box(5,height,5,walls[i%4],x,height/2,z,true);
      box(5.15,.19,5.15,0x6e7e88,x,height,z);
      box(4.8,.18,.15,0xe5dac7,x,2.6,z-side*2.56);
      for (let y=1.25;y<height-.45;y+=1.55) {
        for (const dx of [-1.46,0,1.46]) {
          box(.87,.88,.045,0x526b7b,x+dx,y,z-side*2.515);
          box(.93,.06,.15,0xc7c7bc,x+dx,y-.47,z-side*2.55);
        }
      }
    }
    for (let x=-60;x<=60;x+=10) {
      if (cross && Math.abs(x)<12) continue;
      for (const side of [-1,1]) {
        const z=side*10.5;
        box(1.65,.22,1.35,0xa5aca0,x,.1,z);
        mesh(new THREE.CylinderGeometry(.11,.16,2.3,7),0x756655,x,1.2,z,true);
        mesh(new THREE.IcosahedronGeometry(1.27,1),0x487564,x,2.72,z,true);
        mesh(new THREE.IcosahedronGeometry(.87,1),0x689278,x+.43,3.11,z+.26,true);
      }
    }
    for (const x of [-24,-12,10,22,34]) {
      box(.12,4.8,.12,0x586571,x,2.4,-8.05,true);
      box(.13,.12,1.3,0x586571,x,4.73,-7.45);
      box(.35,.10,.64,0xffe5b0,x,4.67,-6.92);
    }
  }

  function applyEnvironment(s) {
    const palettes = {
      "白天":[0xc5d8e8,0xffe0b6,.9,1.55],
      "黃昏":[0xd99b83,0xff9a4d,.64,1.17],
      "夜間":[0x151f35,0x91a9ef,.48,.60],
    };
    const [sky,sunColor,amb,key]=palettes[s.light];
    scene.background.setHex(sky);
    if (s.weather !== "晴朗") scene.background.lerp(new THREE.Color(s.light==="夜間"?0x253348:0xa5acb1),.56);
    scene.fog.color.copy(scene.background);
    scene.fog.near=s.weather==="起霧"?7:s.weather==="下雨"?19:38;
    scene.fog.far=s.weather==="起霧"?35:s.weather==="下雨"?69:120;
    sun.color.setHex(sunColor);
    sun.intensity=key*(s.weather==="晴朗"?1:.63);
    ambient.intensity=amb;
    asphalt.color.setHex(s.weather==="下雨"?0x293c50:0x3d4957);
    rain.visible=s.weather==="下雨";
  }

  function updateRain(now) {
    if (!rain.visible) return;
    const positions=rain.geometry.attributes.position;
    for(let i=0;i<positions.count/2;i++){
      const x=(rnd(i*3+1)-.5)*58;
      const y=(rnd(i*3+2)*17-now*.011%17+17)%17;
      const z=(rnd(i*3+3)-.5)*43;
      positions.setXYZ(i*2,x,y,z);
      positions.setXYZ(i*2+1,x+.15,y+.72,z+.06);
    }
    positions.needsUpdate=true;
  }

  function init() {
    if (ready) return true;
    try { ren = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" }); } catch (e) { return false; }
    ren.setPixelRatio(Math.min(devicePixelRatio || 1, 1.75));
    ren.outputEncoding = THREE.sRGBEncoding;
    ren.shadowMap.enabled = true;
    ren.shadowMap.type = THREE.PCFSoftShadowMap;
    el().appendChild(ren.domElement);
    scene = new THREE.Scene(); scene.background = new THREE.Color(0xc5d8e8); scene.fog = new THREE.Fog(0xc5d8e8, 38, 120);
    ambient = new THREE.HemisphereLight(0xffffff, 0x778866, .9); scene.add(ambient);
        sun = new THREE.DirectionalLight(0xffe0b6, 1.55);
    sun.position.set(-25,35,-19);
    sun.castShadow = true;
    Object.assign(sun.shadow.camera,{left:-48,right:48,top:45,bottom:-45,far:125});
    sun.shadow.mapSize.set(2048,2048);
    sun.shadow.normalBias = .035;
    sun.shadow.camera.updateProjectionMatrix();
    scene.add(sun);
    const drops=new THREE.BufferGeometry();
    drops.setAttribute("position",new THREE.BufferAttribute(new Float32Array(450*6),3));
    rain=new THREE.LineSegments(drops,new THREE.LineBasicMaterial({color:0xd0e4f4,transparent:true,opacity:.42,depthWrite:false}));
    rain.frustumCulled=false;
    scene.add(rain);
    cam = new THREE.PerspectiveCamera(55, 1, .1, 400);
    flash = new THREE.Mesh(new THREE.RingGeometry(.85, 1, 42), new THREE.MeshBasicMaterial({ color: 0xffba62, transparent: true, opacity: .8, side: THREE.DoubleSide, depthWrite: false }));
    flash.rotation.x = -Math.PI / 2; flash.visible = false; scene.add(flash);
    const dom = ren.domElement;
    dom.style.touchAction = "none"; dom.style.display = "block";
    function togglePlayback() {
      if (!P) return;
      if (t >= P.T) t=0;
      playing=!playing;
      $("rplay").textContent=playing ? "暫停" : "播放";
    }
    bindReplayPlayback(dom,togglePlayback,(dx,dy)=>{
      setView("free");
      orbit.th-=dx*.008;
      orbit.ph=Math.min(1.5,Math.max(.2,orbit.ph-dy*.008));
    });
    dom.addEventListener("wheel", event => {
      event.preventDefault();
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? dom.clientHeight : 1;
      cam.zoom = Math.min(3, Math.max(.55, cam.zoom * Math.exp(-event.deltaY * unit * .0014)));
      cam.updateProjectionMatrix();
    }, { passive: false });
    $("rv").onclick = e => { const v = e.target.dataset && e.target.dataset.view; if (v) setView(v); };
    $("rplay").onclick = togglePlayback;
    $("rspeed").onclick = () => { speed = speed === 1 ? .5 : 1; $("rspeed").textContent = speed === 1 ? "1×" : "0.5×"; };
    $("rt").oninput = e => { playing = false; $("rplay").textContent = "播放"; t = +e.target.value / 100; };
    addEventListener("resize", resize); ready = true; requestAnimationFrame(loop); return true;
  }
  function setView(v) { view = v; document.querySelectorAll("#rv button").forEach(b => b.classList.toggle("on", b.dataset.view === v)); }
  function resize() { const w = el().clientWidth, h = el().clientHeight; if (!w) return; ren.setSize(w, h); cam.aspect = w / h; cam.updateProjectionMatrix(); }

  function poses() { return { A: P.motionA(t), B: P.motionB(t) }; }
  function place() {
    const p = poses();
    for (const k of ["A", "B"]) {
      const [x,z,yaw,roll=0] = p[k];
      const y = 1.11*Math.abs(Math.sin(roll)) + .65*(Math.abs(Math.cos(roll))-Math.cos(roll));
      cars[k].position.set(x,y,z);
      cars[k].rotation.set(roll,yaw,0,"YXZ");
    }
    if (cars.V) cars.V.position.set(12-t*(P.scenario.speedVehicle??25)/3.6,0,-4.9);
    if (cars.V) cars.V.rotation.y=Math.PI;
    if (cars.P) cars.P.position.set(7.8,0,8.5-t*(P.scenario.speedPedestrian??4)/3.6);
    if (cars.P) cars.P.rotation.y=Math.PI/2;
    const dt=t-P.hit;
    if (P.contact && dt>=0 && dt<.5) {
      flash.visible=true;
      flash.position.set(P.contact[0],.035,P.contact[1]);
      flash.scale.setScalar(.6+dt*5);
      flash.material.opacity=.8*(1-dt/.5);
    } else flash.visible=false;
    return p;
  }
  function camera(p) {
    const mx = (p.A[0] + p.B[0]) / 2, mz = (p.A[1] + p.B[1]) / 2, V = THREE.Vector3;
    const fwd = q => new V(Math.cos(q[2]), 0, -Math.sin(q[2]));
    let fov = 55;
    if (view === "bird") { cam.position.set(mx - 8, 15, mz + 15); cam.lookAt(mx, 0, mz); }
    else if (view === "chase") { const f = fwd(p.A); cam.position.set(p.A[0] - f.x * 9, 3.6, p.A[1] - f.z * 9); cam.lookAt(p.A[0] + f.x * 6, .8, p.A[1] + f.z * 6); }
    else if (view === "side") { cam.position.set(mx + 2, 4.5, mz - 17); cam.lookAt(mx, 1, mz); }
    else if (view === "inA" || view === "inB") { const q = view === "inA" ? p.A : p.B, f = fwd(q); fov = 72;
            const type = cars[view === "inA" ? "A" : "B"].userData.type;
      const offset = type === "公車" ? 4.8 : type === "貨車" ? 3.5 : .8;
      const eye = type === "公車" || type === "貨車" ? 2.35 : 1.55;
      cam.position.set(q[0] + f.x * offset, eye, q[1] + f.z * offset); cam.lookAt(q[0] + f.x * 12, 1.1, q[1] + f.z * 12); }
    else { cam.position.set(mx + orbit.r * Math.sin(orbit.ph) * Math.cos(orbit.th), orbit.r * Math.cos(orbit.ph), mz + orbit.r * Math.sin(orbit.ph) * Math.sin(orbit.th)); cam.lookAt(mx, .8, mz); }
    if (cam.fov !== fov) { cam.fov = fov; cam.updateProjectionMatrix(); }
  }
  function loop(now) {
    requestAnimationFrame(loop);
    const dt = Math.min(.05, (now - last) / 1000); last = now;
    if (!P || !el().offsetParent) return;
    if (el().clientWidth !== ren.domElement.clientWidth || el().clientHeight !== ren.domElement.clientHeight) resize();
    if (playing) { t = (t + dt * speed) % P.T; $("rt").value = Math.round(t * 100); }
    const stamp = t.toFixed(1) + " / " + P.T.toFixed(1) + " 秒"; if ($("replayTime").textContent !== stamp) $("replayTime").textContent = stamp; if (P.measured) placeMeasured(); else { updateRain(now); camera(place()); } ren.render(scene, cam);
  }

  function measuredPose(points, time) {
    const index=points.findIndex(point=>point.t>=time);
    if (index<0) return null;
    const next=points[index];
    if (next.t===time) return next;
    const prior=points[index-1];
    if (!prior || next.t-prior.t>2) return null;
    const ratio=(time-prior.t)/(next.t-prior.t);
    const blend=(a,b)=>a.map((value,axis)=>value+(b[axis]-value)*ratio);
    return {p:blend(prior.p,next.p),r:blend(prior.r,next.r)};
  }
  api._measuredPose=measuredPose;

  function loadMeasured(reconstruction) {
    if (measuredLayer) {
      measuredLayer.traverse(node=>{
        node.geometry?.dispose();
        node.material?.map?.dispose();
        node.material?.dispose();
      });
      scene.remove(measuredLayer);
    }
    measuredLayer=new THREE.Group();
    scene.add(measuredLayer);
    if (world) world.visible=false;
    for (const car of Object.values(cars)) car.visible=false;
    rain.visible=flash.visible=false;
    const bounds=new THREE.Box3();
    const objects=reconstruction.objects.map((record,index)=>{
      const node=makeParticipant(record.kind,[0x3185db,0xe97535,0x358a68,0x825ab5][index%4],record.id);
      for (const pose of record.poses) bounds.expandByPoint(new THREE.Vector3(...pose.p));
      measuredLayer.add(node);
      return {node,poses:record.poses};
    });
    const signals=reconstruction.signals.map(record=>{
      const head=new THREE.Group();
      head.position.set(...record.p);
      head.rotation.y=record.yaw;
      const housing=new THREE.Mesh(new THREE.BoxGeometry(.28,.95,.38),new THREE.MeshStandardMaterial({color:0x202a33}));
      head.add(housing);
      const bulbs=[.29,0,-.29].map(height=>{
        const bulb=new THREE.Mesh(new THREE.SphereGeometry(.105,12,8),new THREE.MeshBasicMaterial({color:0x30363a}));
        bulb.scale.x=.35;
        bulb.position.set(.15,height,0);
        head.add(bulb);
        return bulb;
      });
      measuredLayer.add(head);
      bounds.expandByPoint(head.position);
      return {bulbs,states:record.states};
    });
    const center=bounds.getCenter(new THREE.Vector3());
    const extent=bounds.getSize(new THREE.Vector3()).length();
    const distance=Math.max(12,extent*1.15);
    const grid=new THREE.GridHelper(Math.max(20,Math.ceil(extent/10)*10),20,0x587187,0xa0afbb);
    grid.position.set(center.x,-.015,center.z);
    measuredLayer.add(grid);
    scene.background.setHex(0xc5d8e8);
    scene.fog.near=distance*3;
    scene.fog.far=distance*6;
    cam.far=Math.max(400,distance*6);
    cam.fov=55;
    cam.updateProjectionMatrix();
    const start=Math.min(...objects.map(({poses})=>poses[0].t));
    const end=Math.max(...objects.map(({poses})=>poses.at(-1).t));
    P={measured:true,objects,signals,center,distance,start,T:end-start};
    setView("bird");
    cam.position.copy(center).add(new THREE.Vector3(-distance*.65,distance*.8,distance*.75));
    cam.lookAt(center);
    t=0; playing=true; speed=1;
    $("rt").max=Math.max(1,Math.round(P.T*100)); $("rt").value=0;
    $("rplay").textContent="暫停"; $("rspeed").textContent="1×";
    return true;
  }

  function placeMeasured() {
    const time=P.start+t;
    for (const {node,poses} of P.objects) {
      const pose=measuredPose(poses,time);
      node.visible=!!pose;
      if (pose) {
        node.position.set(...pose.p);
        node.rotation.set(...pose.r,"XYZ");
      }
    }
    const colors={"紅燈":0xff4d49,"黃燈":0xffcf54,"綠燈":0x52ff8a};
    for (const {bulbs,states} of P.signals) {
      const observed=new Set(states.filter(phase=>phase.start<=time && time<=phase.end).map(phase=>phase.color));
      const state=observed.size===1 ? observed.values().next().value : null;
      Object.keys(colors).forEach((color,index)=>bulbs[index].material.color.setHex(state===color ? colors[color] : 0x30363a));
    }
    if (view==="free") {
      const radius=P.distance*orbit.r/30, {th,ph}=orbit;
      cam.position.copy(P.center).add(new THREE.Vector3(radius*Math.sin(ph)*Math.cos(th),radius*Math.cos(ph),radius*Math.sin(ph)*Math.sin(th)));
      cam.lookAt(P.center);
    }
  }

  api.load = function (d) {
    if (!init()) return false;
    if (d.reconstruction) return loadMeasured(d.reconstruction);
    cam.far=400;
    cam.updateProjectionMatrix();
    if (measuredLayer) measuredLayer.visible=false;
    for (const car of Object.values(cars)) car.visible=true;
    if (world) world.visible=true;
    replaceParticipants(d);
    P = plan(d);
    const worldType = P.worldType ?? d.collision_type;
    if (world?.userData.type !== worldType) {
      buildWorld(worldType);
      world.userData.type = worldType;
    }
    P.scenario=d.scenario;
    const response=collisionMotion(P,d,sample);
    [P.motionA,P.motionB]=response.motions;
    P.hit=response.hit;
    P.contact=response.point;
    for (const button of $("rv").querySelectorAll("button")) button.hidden=false;
    applyEnvironment(P.scenario);
    t = 0; playing = true; speed = 1;
    $("rt").max = Math.round(P.T * 100); $("rt").value = 0; $("rcap").textContent = P.cap;
    $("rplay").textContent = "暫停"; $("rspeed").textContent = "1×"; setView(view === "free" ? "free" : "bird"); resize(); return true;
  };
})();


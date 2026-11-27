"use strict";

const scenarioFields = {
  weather: ["晴朗","陰天","下雨","起霧"],
  light: ["白天","黃昏","夜間"],
  signal: ["未設定","紅燈","黃燈","綠燈","無號誌"],
  thirdVehicle: ["無","汽車","機車","自行車","公車","貨車"],
  thirdPedestrian: ["無","有"],
};

const scenarioDefaults = {
  weather:"晴朗", light:"白天", signal:"未設定",
  thirdVehicle:"無", thirdPedestrian:"無",
  speedA:null, speedB:null, speedVehicle:25, speedPedestrian:4,
};

// Additional scenes specify the parties, approach conditions, and simulated speeds.
const extraAccidents = [
  ["雨天公車站追撞","公車","機車","停靠站位遭機車從後方追撞","下雨","白天","無號誌",0,24,"汽車"],
  ["夜間路口行人穿越","行人","汽車","步行穿越路口，乙方右轉碰撞","晴朗","夜間","黃燈",4,24,"無","有"],
  ["黃昏自行車遭追撞","自行車","汽車","沿道路右側前進，遭乙方從後方追撞","陰天","黃昏","無號誌",13,36],
  ["起霧貨車排隊追撞","汽車","貨車","於路口停等時遭後方貨車追撞","起霧","白天","紅燈",0,24,"公車"],
  ["雨天兩機車併線","機車","機車","沿外側直行，乙方變換車道擦撞","下雨","白天","無號誌",28,30],
  ["夜間公車左轉遇自行車","自行車","公車","於路口直行時與左轉公車碰撞","晴朗","夜間","綠燈",15,20,"機車"],
  ["黃昏貨車切入公車道","公車","貨車","沿外側車道直行時，乙方變換車道擦撞","晴朗","黃昏","無號誌",32,36],
  ["雨天右轉遇行人","行人","機車","穿越斑馬線時與右轉機車碰撞","下雨","白天","綠燈",4,18,"汽車","有"],
  ["夜間載貨路段追撞","貨車","汽車","減速通過路口時，遭乙方從後方追撞","陰天","夜間","黃燈",14,45],
  ["起霧自行車與機車擦撞","自行車","機車","沿路肩直行，乙方變換車道擦撞","起霧","白天","無號誌",12,33],
  ["無號誌路口公車與貨車","公車","貨車","通過無號誌路口時與側向貨車碰撞","晴朗","白天","無號誌",24,29],
  ["雨天兩輛公車擦撞","公車","公車","離站後直行，乙方變換車道擦撞","下雨","白天","無號誌",21,26,"機車"],
  ["夜間兩輛貨車追撞","貨車","貨車","前車停等，後方貨車追撞","晴朗","夜間","紅燈",0,37],
  ["黃燈路口兩輛自行車","自行車","自行車","在路口直行時與轉向自行車碰撞","晴朗","黃昏","黃燈",14,12,"無","有"],
  ["公車出站遇行人","行人","公車","行人從站牌旁穿越路口，與右轉公車碰撞","陰天","白天","無號誌",5,12,"機車","有"],
  ["雨天貨車轉彎遇行人","行人","貨車","沿斑馬線通過路口，與右轉貨車碰撞","下雨","黃昏","綠燈",3,16,"自行車","有"],
  ["霧中汽車變換車道","汽車","汽車","沿外側直行，乙方變換車道時擦撞","起霧","白天","無號誌",33,40],
  ["夜間公車後方機車追撞","公車","機車","停等紅燈時遭乙方從後方追撞","晴朗","夜間","紅燈",0,26,"貨車"],
  ["雨天自行車道口衝突","行人","自行車","在路口穿越自行車道，與乙方碰撞","下雨","白天","無號誌",4,11,"無","有"],
  ["黃昏公車變換車道","汽車","公車","甲方直行，乙方變換車道準備進站時擦撞","晴朗","黃昏","無號誌",30,22,"自行車"],
  ["霧中機車與汽車追撞","機車","汽車","減速時遭乙方從後方追撞","起霧","白天","黃燈",10,32],
  ["夜間貨車左轉與機車碰撞","機車","貨車","路口綠燈直行，與左轉貨車碰撞","晴朗","夜間","綠燈",32,18,"公車"],
  ["雨天汽車與公車右轉衝突","汽車","公車","通過路口時與右轉公車碰撞","下雨","白天","綠燈",28,19],
  ["黃昏路口機車與行人碰撞","行人","機車","穿越路口時與側向直行機車碰撞","陰天","黃昏","紅燈",6,27,"無","有"],
  ["陰天自行車遭汽車擦撞","自行車","汽車","沿路旁直行，乙方變換車道發生擦撞","陰天","白天","無號誌",14,29,"機車"],
  ["雨夜汽車追撞停等貨車","貨車","汽車","於路口停等紅燈，遭後方汽車追撞","下雨","夜間","紅燈",0,34,"無","有"],
  ["無號誌路口機車與公車","機車","公車","直行通過路口時，與側向公車碰撞","晴朗","白天","無號誌",23,26],
  ["起霧清晨公車與汽車擦撞","公車","汽車","公車直行，乙方變換車道擦撞","起霧","白天","無號誌",24,33,"貨車"],
  ["黃燈路口汽車與自行車","自行車","汽車","路口通過停止線後與轉彎汽車碰撞","陰天","白天","黃燈",16,26,"無","有"],
  ["夜間行人與右轉汽車","行人","汽車","穿越路口時與右轉汽車碰撞","晴朗","夜間","無號誌",3,16,"公車","有"],
  ["雨天貨車與自行車併線","自行車","貨車","沿外側直行，貨車變換車道擦撞","下雨","白天","無號誌",11,28],
  ["黃昏機車與自行車追撞","自行車","機車","沿同一車道前進時，遭機車從後方追撞","晴朗","黃昏","無號誌",10,29,"汽車"],
  ["夜間汽車與公車路口碰撞","汽車","公車","夜間直行通過路口，與側向公車碰撞","陰天","夜間","綠燈",28,26,"機車"],
  ["起霧貨車與公車追撞","貨車","公車","減速進入路口時，遭乙方從後方追撞","起霧","白天","紅燈",8,30],
  ["雨天兩輛汽車低速追撞","汽車","汽車","車流停等期間，遭乙方從後方追撞","下雨","白天","紅燈",0,12,"公車"],
  ["黃昏自行車與公車擦撞","自行車","公車","沿外側直行，乙方變換車道擦撞","晴朗","黃昏","無號誌",14,22,"無","有"],
  ["雨夜路口機車與貨車","機車","貨車","通過路口時與側向貨車碰撞","下雨","夜間","黃燈",28,25,"汽車","有"],
];

function scenarioForExample(index, row) {
  const [,a,b,,weather,light,signal,speedA,speedB,thirdVehicle,thirdPedestrian,trajectory] = row;
  const initialSignals = ["紅燈","無號誌","綠燈","綠燈","紅燈","綠燈","無號誌","無號誌","綠燈","無號誌","無號誌","紅燈","綠燈"];
  return {
    ...scenarioDefaults,
    weather:weather ?? "晴朗", light:light ?? "白天", signal:signal ?? initialSignals[index],
    speedA:speedA ?? (a === "行人" ? 4 : a === "自行車" ? 14 : [0,4,7,11].includes(index) ? 0 : 26),
    speedB:speedB ?? (b === "自行車" ? 14 : 30),
    thirdVehicle:thirdVehicle ?? "無", thirdPedestrian:thirdPedestrian ?? "無",
    ...(trajectory ? {trajectory} : {}),
  };
}

function oncomingRolloverPlan(data, sample) {
  if (data.scenario?.trajectory !== "oncoming_rollover") return null;
  const T = 5.5, evasiveAt = 2.5, rolloverAt = 3.1;
  const speedA = (data.scenario.speedA ?? 26) / 3.6;
  const speedB = (data.scenario.speedB ?? 30) / 3.6;
  const forward = -Math.PI / 2;
  // Both cars use the Z road, on opposite sides of its centerline.
  const slide = [
    [evasiveAt, -2, -4, forward, 0],
    [2.8, -3.1, -2.2, forward - .25, 0],
    [rolloverAt, -3.85, -.4, forward - .38, 0],
    [3.55, -4.25, 2.1, forward - .35, Math.PI / 2],
    [4.05, -5.15, 4.15, forward - .25, Math.PI],
    [4.75, -5.25, 5.3, forward - .2, Math.PI],
  ];
  const A = time => {
    if (time < evasiveAt) return [-2, -4 + (time - evasiveAt) * speedA, forward, 0];
    const [x,z,yaw,roll] = sample(slide, time);
    return [x,z,yaw,Math.max(0,Math.min(roll,Math.PI))];
  };
  const laneChange = [
    [0, 2], [2.05, 2], [2.5, .95], [2.8, .7], [3.35, -.65], [3.75, -.65], [4.6, 2], [T, 2],
  ];
  const B = time => {
    const [x] = sample(laneChange, time);
    const dx = sample(laneChange, time + .01)[0] - sample(laneChange, time - .01)[0];
    const yaw = dx === 0 ? Math.PI / 2 : Math.atan2(speedB * .02, dx);
    return [x, 3 + (evasiveAt - time) * speedB, yaw, 0];
  };
  return {
    T, evasiveAt, rolloverAt, motions: [A, B], worldType: "straight_z",
    cap: "翻覆示意：乙方跨越雙黃線，甲方閃避後失控翻覆，兩車未接觸。",
  };
}

function relativeCrossingTracks(crossing, intersection) {
  const {reference_party:reference, direction} = crossing ?? {};
  if (reference !== "A" && reference !== "B") return null;
  if (direction !== "left_to_right" && direction !== "right_to_left") return null;
  const straight = intersection.A;
  // Facing +X, -Z is left. The original transverse track starts at +Z.
  const laneZ = straight[0][2];
  const transverse = direction === "left_to_right"
    ? intersection.B.map(([time, x, z, yaw]) => [time, x, 2*laneZ-z, -yaw])
    : intersection.B;
  return reference === "A" ? { A:straight, B:transverse } : { A:transverse, B:straight };
}

// Map elapsed time to distance along the original approach, keeping the contact pose fixed.
function approachMotion(track, hit, kmh, sample) {
  if (kmh === null || !Number.isFinite(kmh)) return time => sample(track,time);
  const points = Array.from({length:81},(_,i)=>({pose:sample(track,hit*i/80),distance:0}));
  for (let i=1;i<points.length;i++) {
    const a=points[i-1], b=points[i];
    b.distance=a.distance+Math.hypot(b.pose[0]-a.pose[0],b.pose[1]-a.pose[1]);
  }
  const first=points[0], end=points.at(-1), speed=kmh/3.6;
  return time => {
    if (time >= hit) return sample(track,time);
    const target=end.distance-(hit-time)*speed;
    if (target<=0) {
      const next=points.find(point=>point.distance>.001);
      const dx=next ? next.pose[0]-first.pose[0] : Math.cos(first.pose[2]);
      const dz=next ? next.pose[1]-first.pose[1] : -Math.sin(first.pose[2]);
      const scale=target/Math.hypot(dx,dz);
      return [first.pose[0]+dx*scale,first.pose[1]+dz*scale,first.pose[2]];
    }
    const i=points.findIndex(point=>point.distance>=target);
    const a=points[i-1], b=points[i];
    const ratio=(target-a.distance)/(b.distance-a.distance);
    return a.pose.map((value,k)=>value+(b.pose[k]-value)*ratio);
  };
}

const replayMassKg = {"行人":75,"自行車":90,"機車":200,"汽車":1500,"貨車":6500,"公車":11500};

// Ground-plane bounding boxes follow the rendered models, including bumpers and tires.
const replayHalfExtents={"行人":[.31,.32],"自行車":[1.04,.24],"機車":[1,.24],
  "汽車":[2.24,1.11],"貨車":[3.5,1.26],"公車":[4.74,1.26]};
const replayDot=(a,b)=>a[0]*b[0]+a[1]*b[1];

function replayContact(poses, sizes) {
  const heading=poses.map(pose=>[Math.cos(pose[2]),-Math.sin(pose[2])]);
  const side=heading.map(([x,z])=>[-z,x]);
  const midpoint=poses.map(pose=>pose.slice(0,2));
  const delta=midpoint[1].map((value,i)=>value-midpoint[0][i]);
  const radius=(i,axis)=>sizes[i][0]*Math.abs(replayDot(heading[i],axis))+sizes[i][1]*Math.abs(replayDot(side[i],axis));
  let depth=Infinity, normal;
  for (const axis of [...heading,...side]) {
    const offset=replayDot(delta,axis);
    const overlap=radius(0,axis)+radius(1,axis)-Math.abs(offset);
    if (overlap<0) return null;
    if (overlap<depth) {
      depth=overlap;
      normal=offset<0 ? axis.map(value=>-value) : axis;
    }
  }
  const tangent=[-normal[1],normal[0]];
  const offsets=midpoint.map(point=>replayDot(point,tangent));
  const edge=(Math.max(offsets[0]-radius(0,tangent),offsets[1]-radius(1,tangent))+
    Math.min(offsets[0]+radius(0,tangent),offsets[1]+radius(1,tangent)))/2;
  const plane=(replayDot(midpoint[0],normal)+radius(0,normal)+replayDot(midpoint[1],normal)-radius(1,normal))/2;
  return {normal,depth,point:normal.map((value,i)=>value*plane+tangent[i]*edge)};
}

function collisionMotion(plan,data,sample) {
  const types=[data.party_a?.vehicle,data.party_b?.vehicle];
  const masses=types.map(type=>replayMassKg[type] || null);
  if (plan.motions) return {motions:plan.motions,masses,impulse:0,hit:null};
  const sizes=types.map(type=>replayHalfExtents[type] || [.56,.56]);
  const keys=["A","B"];
  const step=1/240;
  const approach=keys.map(key=>{
    const track=plan[key].filter(point=>point[0]<=plan.hit);
    const motion=approachMotion(track,plan.hit,data.scenario["speed"+key],sample);
    const pose=motion(plan.hit), prior=motion(Math.max(0,plan.hit-.05));
    const velocity=pose.slice(0,2).map((value,i)=>(value-prior[i])/.05);
    return time=>time<=plan.hit ? motion(time) :
      [pose[0]+velocity[0]*(time-plan.hit),pose[1]+velocity[1]*(time-plan.hit),pose[2]];
  });
  let hit=null, contact=null;
  for (let time=0;time<=plan.T;time+=step) {
    if (!replayContact(approach.map(motion=>motion(time)),sizes)) continue;
    let left=Math.max(0,time-step),right=time;
    for (let i=0;i<12;i++) {
      const middle=(left+right)/2;
      if (replayContact(approach.map(motion=>motion(middle)),sizes)) right=middle;
      else left=middle;
    }
    hit=right;
    contact=replayContact(approach.map(motion=>motion(hit)),sizes);
    break;
  }
  if (hit===null) return {motions:approach,masses,impulse:0,hit:null};
  const bodies=approach.map(motion=>{
    const pose=motion(hit), next=motion(hit+.0125), prior=motion(Math.max(0,hit-.0125));
    const elapsed=hit+.0125-Math.max(0,hit-.0125);
    return {pose,velocity:next.slice(0,2).map((value,i)=>(value-prior[i])/elapsed)};
  });
  const inverse=masses.map(mass=>1/(mass || 1500));
  const weight=inverse[0]+inverse[1];
  function resolve(collision,restitution) {
    const normal=collision.normal;
    const closing=replayDot(bodies[0].velocity,normal)-replayDot(bodies[1].velocity,normal);
    const impulse=Math.max(0,closing)*(1+restitution)/weight;
    bodies.forEach((body,index)=>{
      const sign=index===0?-1:1;
      for (let axis=0;axis<2;axis++) {
        body.velocity[axis]+=sign*normal[axis]*impulse*inverse[index];
        body.pose[axis]+=sign*normal[axis]*(collision.depth+.0002)*inverse[index]/weight;
      }
    });
    return impulse;
  }
  const impulse=resolve(contact,.10);
  const history=[bodies.map(body=>body.pose.slice())];
  const wet=data.scenario.weather==="下雨";
  const braking=wet?3.3:5.2, grip=wet?3:5;
  const reduce=(speed,acceleration)=>Math.sign(speed)*Math.max(0,Math.abs(speed)-acceleration*step);
  for (let frame=1;frame<=Math.ceil((plan.T-hit)/step);frame++) {
    for (const body of bodies) {
      const forward=[Math.cos(body.pose[2]),-Math.sin(body.pose[2])];
      const side=[-forward[1],forward[0]];
      const along=reduce(replayDot(body.velocity,forward),braking);
      const across=reduce(replayDot(body.velocity,side),grip);
      body.velocity=forward.map((value,i)=>value*along+side[i]*across);
      for (let i=0;i<2;i++) body.pose[i]+=body.velocity[i]*step;
    }
    const collision=replayContact(bodies.map(body=>body.pose),sizes);
    if (collision) resolve(collision,0);
    history.push(bodies.map(body=>body.pose.slice()));
  }
  const motions=approach.map((motion,index)=>time=>{
    if (time<hit) return motion(time);
    const frame=Math.min(history.length-1,(time-hit)/step);
    const before=history[Math.floor(frame)][index], after=history[Math.ceil(frame)][index];
    return before.map((value,i)=>value+(after[i]-value)*(frame-Math.floor(frame)));
  });
  return {motions,masses,impulse,hit,point:contact.point};
}

function bindReplayPlayback(canvas, toggle, rotate) {
  let drag;
  canvas.onpointerdown=event=>{
    if (!event.isPrimary || event.button!==0) return;
    drag={id:event.pointerId,start:[event.clientX,event.clientY],last:[event.clientX,event.clientY],moved:false};
    canvas.setPointerCapture(event.pointerId);
  };
  canvas.onpointermove=event=>{
    if (!drag || event.pointerId!==drag.id) return;
    if (!drag.moved && Math.hypot(event.clientX-drag.start[0],event.clientY-drag.start[1])<5) return;
    drag.moved=true;
    rotate(event.clientX-drag.last[0],event.clientY-drag.last[1]);
    drag.last=[event.clientX,event.clientY];
  };
  canvas.onpointerup=event=>{
    if (!drag || event.pointerId!==drag.id) return;
    const clicked=!drag.moved && Math.hypot(event.clientX-drag.start[0],event.clientY-drag.start[1])<5;
    drag=undefined;
    if (clicked) toggle();
  };
  canvas.onpointercancel=canvas.onlostpointercapture=()=>{drag=undefined;};
  canvas.tabIndex=0;
  canvas.setAttribute("aria-label","事故重播畫面，點擊或按空白鍵播放與暫停");
  canvas.onkeydown=event=>{
    if (event.key!==" " && event.key!=="Enter") return;
    event.preventDefault();
    if (!event.repeat) toggle();
  };
}

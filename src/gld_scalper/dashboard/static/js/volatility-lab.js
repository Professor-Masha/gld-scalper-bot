import * as THREE from "/static/vendor/three.module.min.js";

const REGIMES=[
  {name:"LOW",color:0x42d9ff,factor:1.15},
  {name:"NORMAL",color:0x39f49a,factor:1},
  {name:"HIGH",color:0xffc857,factor:.58},
  {name:"EXTREME",color:0xff5267,factor:.24},
];

export class VolatilityLab {
  constructor(root){
    this.root=root;this.canvas=root.querySelector("#volatilityNetwork");this.active=false;this.frame=0;this.nodes=[];this.edges=[];
    this.scene=new THREE.Scene();this.camera=new THREE.PerspectiveCamera(46,1,.1,100);this.camera.position.set(0,0,9);
    this.renderer=new THREE.WebGLRenderer({canvas:this.canvas,alpha:true,antialias:true,powerPreference:"low-power"});
    this.renderer.setPixelRatio(Math.min(devicePixelRatio||1,1.5));this.group=new THREE.Group();this.scene.add(this.group);
    this.scene.add(new THREE.AmbientLight(0x9cf5d0,1.5));const light=new THREE.PointLight(0x42d9ff,20,25);light.position.set(-4,5,7);this.scene.add(light);
    this.resizeObserver=new ResizeObserver(()=>this.resize());this.resizeObserver.observe(this.canvas.parentElement);this.resize();this.animate();
  }
  setActive(active){this.active=active;}
  run(rows,options){
    const closes=rows.map(row=>Number(row.close)).filter(Number.isFinite),returns=[];
    for(let i=1;i<closes.length;i++)if(closes[i-1]>0&&closes[i]>0)returns.push(Math.log(closes[i]/closes[i-1]));
    const window=Math.max(5,Number(options.window)||22),volatility=rollingDeviation(returns,window);
    if(volatility.length<20){this.renderEmpty("At least 25 local GLD bars are required.");return null;}
    const sorted=[...volatility].sort((a,b)=>a-b),thresholds=[quantile(sorted,.25),quantile(sorted,.60),quantile(sorted,.85)];
    const regimes=volatility.map(value=>value<=thresholds[0]?0:value<=thresholds[1]?1:value<=thresholds[2]?2:3),current=regimes.at(-1),percentile=sorted.filter(value=>value<=volatility.at(-1)).length/sorted.length;
    const transitions=transitionMatrix(regimes),persistence=transitions[current][current]/Math.max(1,transitions[current].reduce((a,b)=>a+b,0)),runLength=currentRun(regimes),confidence=Math.min(.999,Math.max(.9,Number(options.confidence)/100||.99));
    const losses=returns.map(value=>-value).sort((a,b)=>a-b),varLoss=Math.max(0,quantile(losses,confidence)),tail=losses.filter(value=>value>=varLoss),cvar=Math.max(varLoss,tail.reduce((a,b)=>a+b,0)/Math.max(1,tail.length));
    const riskFraction=Math.max(.0001,(Number(options.risk)||.25)/100),horizon=Math.max(1,Number(options.horizon)||5),forecast=volatility.at(-1)*Math.sqrt(horizon),rawMultiplier=riskFraction/Math.max(cvar,1e-6),multiplier=clamp(rawMultiplier*REGIMES[current].factor,.1,1.5);
    const result={returns,volatility,regimes,current,percentile,persistence,runLength,varLoss,cvar,forecast,multiplier,transitions,thresholds,window,horizon,confidence};
    this.buildNetwork(result);this.render(result);return result;
  }
  renderEmpty(message){this.root.querySelector("#volatilityVerdict").textContent="WAITING";this.root.querySelector("#volatilityNarrative").textContent=message;}
  buildNetwork(result){
    this.clear();const count=Math.min(44,result.regimes.length),offset=result.regimes.length-count;
    for(let i=0;i<count;i++){
      const regime=result.regimes[offset+i],angle=i*.73,radius=1.2+(i%9)*.18,z=(i-count/2)*.075;
      const position=new THREE.Vector3(Math.cos(angle)*radius,Math.sin(angle)*radius*.72,z),color=REGIMES[regime].color;
      const material=new THREE.MeshStandardMaterial({color,emissive:color,emissiveIntensity:.44,roughness:.3,metalness:.2,transparent:true,opacity:.9});
      const mesh=new THREE.Mesh(new THREE.IcosahedronGeometry(.08+regime*.035,1),material);mesh.position.copy(position);mesh.userData={regime,phase:i*.5};this.group.add(mesh);this.nodes.push(mesh);
      if(i){this.addEdge(this.nodes[i-1].position,position,regime===result.regimes[offset+i-1] ? color : 0x355e52);}
      for(let j=Math.max(0,i-8);j<i-2;j++)if(this.nodes[j].userData.regime===regime&&(i+j)%4===0)this.addEdge(this.nodes[j].position,position,color,.16);
    }
  }
  addEdge(from,to,color,opacity=.28){const geometry=new THREE.BufferGeometry().setFromPoints([from,to]),line=new THREE.Line(geometry,new THREE.LineBasicMaterial({color,transparent:true,opacity}));this.group.add(line);this.edges.push(line);}
  clear(){for(const object of [...this.group.children]){object.geometry?.dispose();object.material?.dispose();this.group.remove(object);}this.nodes=[];this.edges=[];}
  render(result){
    const regime=REGIMES[result.current];setText(this.root,"volatilityVerdict",regime.name);setClass(this.root,"volatilityVerdict",`vol-verdict regime-${regime.name.toLowerCase()}`);
    setText(this.root,"volatilityNarrative",`${regime.name.toLowerCase()} volatility at the ${(result.percentile*100).toFixed(1)}th percentile; ${result.runLength} consecutive observations in this cluster.`);
    setText(this.root,"volCurrent",percent(result.volatility.at(-1)));setText(this.root,"volForecast",percent(result.forecast));setText(this.root,"volPersistence",percent(result.persistence));setText(this.root,"volPercentile",percent(result.percentile));
    setText(this.root,"volVar",percent(result.varLoss));setText(this.root,"volCvar",percent(result.cvar));setText(this.root,"volMultiplier",`${result.multiplier.toFixed(2)}x`);setText(this.root,"volTailRisk",`$${(100*result.multiplier*result.cvar).toFixed(2)} per $100 base exposure`);
    drawLine(this.root.querySelector("#volatilityTimeline"),result.volatility,result.regimes);drawHistogram(this.root.querySelector("#returnDistribution"),result.returns,result.varLoss);
    this.renderTransitions(result.transitions);
  }
  renderTransitions(matrix){const table=this.root.querySelector("#volTransitionMatrix");table.innerHTML=matrix.map((row,i)=>{const total=Math.max(1,row.reduce((a,b)=>a+b,0));return `<tr><th>${REGIMES[i].name}</th>${row.map((value,j)=>`<td class="regime-cell-${j}">${(value/total*100).toFixed(0)}%</td>`).join("")}</tr>`;}).join("");}
  resize(){const box=this.canvas.parentElement.getBoundingClientRect(),width=Math.max(220,Math.floor(box.width)),height=Math.max(240,Math.floor(box.height));this.camera.aspect=width/height;this.camera.updateProjectionMatrix();this.renderer.setSize(width,height,false);}
  animate(){if(this.active){this.group.rotation.y+=.0028;this.group.rotation.x=Math.sin(this.frame*.006)*.13;this.nodes.forEach(node=>{const pulse=1+Math.sin(this.frame*.035+node.userData.phase)*.18;node.scale.setScalar(pulse);});this.renderer.render(this.scene,this.camera);this.frame++;this.canvas.dataset.frame=String(this.frame);}requestAnimationFrame(()=>this.animate());}
}

function rollingDeviation(values,window){const output=[];for(let i=window-1;i<values.length;i++){const sample=values.slice(i-window+1,i+1),mean=sample.reduce((a,b)=>a+b,0)/sample.length;output.push(Math.sqrt(sample.reduce((sum,value)=>sum+(value-mean)**2,0)/Math.max(1,sample.length-1)));}return output;}
function quantile(sorted,p){if(!sorted.length)return 0;const index=(sorted.length-1)*p,low=Math.floor(index),high=Math.ceil(index);return sorted[low]+(sorted[high]-sorted[low])*(index-low);}
function transitionMatrix(regimes){const matrix=Array.from({length:4},()=>Array(4).fill(0));for(let i=1;i<regimes.length;i++)matrix[regimes[i-1]][regimes[i]]++;return matrix;}
function currentRun(regimes){let count=1;for(let i=regimes.length-2;i>=0&&regimes[i]===regimes.at(-1);i--)count++;return count;}
function drawLine(canvas,values,regimes){const{ctx,width,height}=frame(canvas),pad=22,min=Math.min(...values),max=Math.max(...values),range=max-min||1;ctx.strokeStyle="#173728";for(let i=0;i<4;i++){const y=pad+(height-pad*2)*i/3;ctx.beginPath();ctx.moveTo(pad,y);ctx.lineTo(width-pad,y);ctx.stroke();}for(let i=1;i<values.length;i++){ctx.strokeStyle=`#${REGIMES[regimes[i]].color.toString(16).padStart(6,"0")}`;ctx.beginPath();ctx.moveTo(pad+(i-1)/(values.length-1)*(width-pad*2),height-pad-(values[i-1]-min)/range*(height-pad*2));ctx.lineTo(pad+i/(values.length-1)*(width-pad*2),height-pad-(values[i]-min)/range*(height-pad*2));ctx.stroke();}}
function drawHistogram(canvas,values,varLoss){const{ctx,width,height}=frame(canvas),bins=32,min=Math.min(...values),max=Math.max(...values),range=max-min||1,counts=Array(bins).fill(0);values.forEach(value=>counts[Math.min(bins-1,Math.floor((value-min)/range*bins))]++);const peak=Math.max(...counts,1),slot=(width-44)/bins;counts.forEach((count,i)=>{const center=min+(i+.5)/bins*range,bar=count/peak*(height-42);ctx.fillStyle=center<=-varLoss?"#ff5267":"#39f49a";ctx.globalAlpha=center<=-varLoss?.9:.5;ctx.fillRect(22+i*slot,height-20-bar,Math.max(1,slot-1),bar);});ctx.globalAlpha=1;ctx.fillStyle="#789485";ctx.font="9px Consolas";ctx.fillText("LOSS TAIL",22,12);}
function frame(canvas){const box=canvas.parentElement.getBoundingClientRect(),width=Math.max(180,Math.floor(box.width)),height=Math.max(120,Math.floor(box.height)),ratio=devicePixelRatio||1;canvas.width=width*ratio;canvas.height=height*ratio;canvas.style.width=`${width}px`;canvas.style.height=`${height}px`;const ctx=canvas.getContext("2d");ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,width,height);return{ctx,width,height};}
function setText(root,id,value){const element=root.querySelector(`#${id}`);if(element)element.textContent=value;}
function setClass(root,id,value){const element=root.querySelector(`#${id}`);if(element)element.className=value;}
function percent(value){return`${(Number(value)*100).toFixed(3)}%`;}
function clamp(value,min,max){return Math.max(min,Math.min(max,value));}

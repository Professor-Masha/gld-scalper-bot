import * as THREE from "/static/vendor/three.module.min.js";

const COLORS={data:0x42d9ff,agent:0x39f49a,model:0xb788ff,risk:0xffc857,execution:0xff6b7a,memory:0x72f2c2,muted:0x355e52};
const NODE_DEFINITIONS=[
  node("stream","Market Stream","data",[-4.8,1.9,.2],"Live GLD quotes, trades and bars from Alpaca IEX."),
  node("features","Feature Engine","data",[-3.15,1.1,-.3],"Indicators, microstructure, order blocks, patterns and freshness."),
  node("indicator","Indicator Agent","agent",[-1.8,2.4,.2],"RSI, EMA, SMA, VWAP, MACD, ATR and volume evidence."),
  node("pattern","Pattern Agent","agent",[-1.35,1.05,.7],"Buildup, break, false break, pullback and compression evidence."),
  node("trend","Trend Agent","agent",[-1.7,-.35,-.2],"Multi-timeframe direction and gold-proxy alignment."),
  node("classical","Classical ML","model",[-.1,2.35,-.5],"Calibrated supervised model with abstention authority."),
  node("transformer","Transformer","model",[1.35,2.35,.35],"Asynchronous sequence model operating in candidate shadow mode."),
  node("council","Decision Council","agent",[1.15,.75,0],"Combines deterministic agents, playbook quality and model evidence."),
  node("risk","Risk Engine","risk",[.15,-1.05,.55],"Freshness, spread, liquidity, exposure and circuit-breaker controls."),
  node("decision","Trade Intent","agent",[3.05,.65,-.25],"Final LONG, SHORT or NO_TRADE intent after all mandatory gates."),
  node("orders","Order Coordinator","execution",[4.25,-.65,.35],"Idempotent order queue, bracket lifecycle and reconciliation."),
  node("alpaca","Alpaca Paper","execution",[5.25,-1.8,-.3],"Paper broker orders, fills, positions and streaming updates."),
  node("llm","LLM Research","model",[-3.65,-1.8,.3],"Ollama or Kimi advisory research with no broker authority."),
  node("sqlite","SQLite Memory","memory",[-1.75,-2.75,-.35],"Paper decisions, market data, fills, outcomes and model lineage."),
  node("journal","Trading Journal","memory",[.35,-3.05,.4],"Decision rationale, execution economics and episode review."),
  node("outcomes","Outcome Labels","memory",[2.55,-2.7,-.25],"Cost-aware 1, 3, 5 and 15-minute learning targets."),
];
const EDGES=[
  ["stream","features"],["features","indicator"],["features","pattern"],["features","trend"],["features","classical"],["features","transformer"],
  ["indicator","council"],["pattern","council"],["trend","council"],["classical","council"],["transformer","council"],["council","risk"],
  ["risk","decision"],["decision","orders"],["orders","alpaca"],["alpaca","journal"],["journal","outcomes"],["outcomes","sqlite"],
  ["stream","sqlite"],["features","sqlite"],["sqlite","classical"],["sqlite","transformer"],["sqlite","llm"],["llm","council"],
];

export class HudScene {
  constructor(){
    this.canvas=document.createElement("canvas");this.canvas.className="hud-3d";
    this.canvas.setAttribute("aria-label","Interactive Obsidian-style graph of GLD bot services and live decision flow");document.body.prepend(this.canvas);
    this.scene=new THREE.Scene();this.camera=new THREE.PerspectiveCamera(43,1,.1,100);this.camera.position.set(0,.1,12.8);
    this.renderer=new THREE.WebGLRenderer({canvas:this.canvas,alpha:true,antialias:true,powerPreference:"low-power"});
    this.renderer.setPixelRatio(Math.min(devicePixelRatio||1,1.5));this.renderer.outputColorSpace=THREE.SRGBColorSpace;
    this.graph=new THREE.Group();this.scene.add(this.graph);this.nodes=new Map();this.edges=[];this.flow=[];
    this.pointer=new THREE.Vector2(9,9);this.raycaster=new THREE.Raycaster();this.focused=false;this.frame=0;this.zoom=12.8;
    this.rotationTarget={x:-.08,y:0};this.drag=null;this.reduced=matchMedia("(prefers-reduced-motion: reduce)").matches;
    this.build();this.bind();this.resize();this.animate();
  }
  build(){
    this.scene.add(new THREE.AmbientLight(0x5dcfa4,1.25));const key=new THREE.PointLight(0x42d9ff,18,30);key.position.set(-4,5,8);this.scene.add(key);
    const fill=new THREE.PointLight(0xb788ff,12,26);fill.position.set(5,-3,6);this.scene.add(fill);
    NODE_DEFINITIONS.forEach(definition=>this.addNode(definition));EDGES.forEach(([from,to],index)=>this.addEdge(from,to,index));
    const starGeometry=new THREE.BufferGeometry(),positions=[];for(let i=0;i<180;i++)positions.push((Math.random()-.5)*24,(Math.random()-.5)*15,(Math.random()-.5)*10-4);
    starGeometry.setAttribute("position",new THREE.Float32BufferAttribute(positions,3));
    this.stars=new THREE.Points(starGeometry,new THREE.PointsMaterial({color:0x3a8f78,size:.025,transparent:true,opacity:.34}));this.scene.add(this.stars);
    this.selectNode("council");
  }
  addNode(definition){
    const color=COLORS[definition.category],group=new THREE.Group();group.position.fromArray(definition.position);group.userData={...definition,status:"idle",metric:"Awaiting telemetry"};
    const material=new THREE.MeshStandardMaterial({color,emissive:color,emissiveIntensity:.35,roughness:.35,metalness:.2,transparent:true,opacity:.92});
    const mesh=new THREE.Mesh(new THREE.IcosahedronGeometry(definition.id==="council"?.31:.22,2),material);mesh.userData.nodeId=definition.id;group.add(mesh);
    const haloMaterial=new THREE.MeshBasicMaterial({color,transparent:true,opacity:.24,side:THREE.DoubleSide});const halo=new THREE.Mesh(new THREE.RingGeometry(.34,.36,48),haloMaterial);halo.rotation.x=Math.PI/2;group.add(halo);
    const label=this.makeLabel(definition.label,color);label.position.set(0,-.52,0);group.add(label);this.graph.add(group);this.nodes.set(definition.id,{definition,group,mesh,halo,label,baseColor:color,activity:0});
  }
  addEdge(fromId,toId,index){
    const from=this.nodes.get(fromId).group.position,to=this.nodes.get(toId).group.position,geometry=new THREE.BufferGeometry().setFromPoints([from,to]);
    const material=new THREE.LineBasicMaterial({color:index%3===0?COLORS.data:COLORS.memory,transparent:true,opacity:.16}),line=new THREE.Line(geometry,material);this.graph.add(line);this.edges.push({fromId,toId,line});
    if(index%2===0){const particle=new THREE.Mesh(new THREE.SphereGeometry(.035,8,8),new THREE.MeshBasicMaterial({color:COLORS.data,transparent:true,opacity:.68}));this.graph.add(particle);this.flow.push({particle,from:from.clone(),to:to.clone(),offset:index/EDGES.length});}
  }
  makeLabel(text,color){
    const canvas=document.createElement("canvas");canvas.width=320;canvas.height=72;const context=canvas.getContext("2d");context.clearRect(0,0,320,72);context.font="600 24px Consolas";context.textAlign="center";context.fillStyle="#d9fff0";context.fillText(text.toUpperCase(),160,38);
    const texture=new THREE.CanvasTexture(canvas);texture.colorSpace=THREE.SRGBColorSpace;const sprite=new THREE.Sprite(new THREE.SpriteMaterial({map:texture,color,transparent:true,opacity:.78,depthWrite:false}));sprite.scale.set(1.75,.39,1);return sprite;
  }
  bind(){
    addEventListener("resize",()=>this.resize());
    addEventListener("pointermove",event=>{this.pointer.x=event.clientX/innerWidth*2-1;this.pointer.y=-(event.clientY/innerHeight)*2+1;if(this.drag){this.rotationTarget.y+=(event.clientX-this.drag.x)*.004;this.rotationTarget.x+=(event.clientY-this.drag.y)*.003;this.drag={x:event.clientX,y:event.clientY,moved:true};}},{passive:true});
    addEventListener("pointerdown",event=>{if(this.focused&&event.target.closest?.(".scene-stage"))this.drag={x:event.clientX,y:event.clientY,moved:false};});
    addEventListener("pointerup",()=>{if(!this.focused)return;const moved=this.drag?.moved;this.drag=null;if(!moved)this.pickNode();});
    addEventListener("wheel",event=>{if(this.focused&&event.target.closest?.(".scene-stage"))this.zoom=Math.max(8.5,Math.min(17,this.zoom+Math.sign(event.deltaY)*.7));},{passive:true});
  }
  update(data={}){
    const signal=data.signal||{},transformer=data.transformer||{},quote=data.quote||{},safety=data.safety||{},processes=data.processes||[],episodes=data.activeEpisodes||[],counts=data.counts||{};
    const paper=processes.some(item=>item.name==="paper"&&["running","stopping"].includes(item.state)),llm=processes.find(item=>String(item.name).startsWith("llm_")&&item.state==="running");
    this.setNode("stream",quote.timestamp?"active":"idle",quote.timestamp?`GLD quote ${ageLabel(quote.timestamp)}`:"No current quote");this.setNode("features",signal.timestamp?"active":"idle",signal.regime||"Waiting for a decision frame");
    this.setNode("indicator",signal.timestamp?"active":"idle",`Bull ${number(signal.bullish_score)} · Bear ${number(signal.bearish_score)}`);this.setNode("pattern",signal.timestamp?"active":"idle",signal.regime||"No pattern classified");this.setNode("trend",signal.timestamp?"active":"idle",Number(signal.bullish_score)>=Number(signal.bearish_score)?"Bull evidence leads":"Bear evidence leads");
    this.setNode("classical",signal.model_version?"active":"idle",signal.model_version||"No model attached to signal");this.setNode("transformer",transformer.model_version?"shadow":"idle",transformer.model_version?`${transformer.predicted_direction} · ${number(transformer.inference_latency_ms)} ms`:"No Transformer inference");
    this.setNode("council",signal.timestamp?"active":"idle",signal.decision||"NO DATA");this.setNode("risk",safety.circuit_open||safety.entry_frozen?"blocked":"active",safety.reason||"Risk gates clear");this.setNode("decision",signal.decision&&signal.decision!=="NO_TRADE"?"active":"idle",signal.decision||"NO DATA");
    this.setNode("orders",episodes.length?"active":"idle",`${episodes.length} open episode${episodes.length===1?"":"s"}`);this.setNode("alpaca",paper?"active":"idle",paper?"Paper process connected":"Paper process offline");this.setNode("llm",llm?"active":"idle",llm?`${llm.name} running`:`${data.provider||"LLM"} advisory idle`);
    this.setNode("sqlite",data.databaseAvailable?"active":"idle",data.databaseAvailable?`${number(counts.signals)} signals stored`:"Database unavailable");this.setNode("journal",Number(counts.fills)>0?"active":"idle",`${number(counts.fills)} fills stored`);this.setNode("outcomes",Number(counts.outcomes)>0?"active":"idle",`${number(counts.outcomes)} outcomes stored`);
  }
  setNode(id,status,metric){const item=this.nodes.get(id);if(!item)return;item.group.userData.status=status;item.group.userData.metric=metric;item.activity=status==="active" ? 1 : status==="blocked" ? .85 : status==="shadow" ? .55 : .08;const color=status==="blocked"?COLORS.execution:item.baseColor;item.mesh.material.color.setHex(color);item.mesh.material.emissive.setHex(color);item.halo.material.color.setHex(color);if(this.selectedId===id)this.emitSelection(item);}
  selectNode(id){const item=this.nodes.get(id);if(!item)return;this.selectedId=id;this.nodes.forEach((nodeItem,nodeId)=>{nodeItem.halo.material.opacity=nodeId===id ? .72 : .18;nodeItem.label.material.opacity=nodeId===id ? 1 : .72;});this.emitSelection(item);}
  emitSelection(item){dispatchEvent(new CustomEvent("hud-node-selected",{detail:{id:item.definition.id,label:item.definition.label,category:item.definition.category,status:item.group.userData.status,description:item.definition.description,metric:item.group.userData.metric}}));}
  pickNode(){this.raycaster.setFromCamera(this.pointer,this.camera);const hits=this.raycaster.intersectObjects([...this.nodes.values()].map(item=>item.mesh),false);if(hits.length)this.selectNode(hits[0].object.userData.nodeId);}
  setFocus(focused){this.focused=focused;if(focused&&this.selectedId)this.emitSelection(this.nodes.get(this.selectedId));}
  resize(){this.camera.aspect=innerWidth/innerHeight;this.camera.updateProjectionMatrix();this.renderer.setSize(innerWidth,innerHeight,false);}
  animate(){
    const speed=this.reduced?0:.0035;this.graph.rotation.x+=(this.rotationTarget.x-this.graph.rotation.x)*.04;this.graph.rotation.y+=(this.rotationTarget.y-this.graph.rotation.y)*.04;
    this.nodes.forEach((item,id)=>{const pulse=1+Math.sin(this.frame*.035+id.length)*.07*item.activity,target=1+item.activity*.24;item.mesh.scale.setScalar(pulse*target);item.halo.rotation.z+=speed*(id.length%2?1:-1);item.halo.material.opacity+=(Math.max(id===this.selectedId ? .68 : .12,item.activity*.38)-item.halo.material.opacity)*.05;});
    this.edges.forEach(edge=>{const active=Math.max(this.nodes.get(edge.fromId).activity,this.nodes.get(edge.toId).activity);edge.line.material.opacity+=(.08+active*.38-edge.line.material.opacity)*.04;});this.flow.forEach(item=>{const progress=(this.frame*.0025+item.offset)%1;item.particle.position.lerpVectors(item.from,item.to,progress);});
    this.stars.rotation.y-=speed*.05;this.camera.position.z+=(this.zoom-this.camera.position.z)*.08;this.camera.lookAt(0,-.25,0);this.renderer.render(this.scene,this.camera);this.frame+=1;if(this.frame%30===0)this.canvas.dataset.frame=String(this.frame);requestAnimationFrame(()=>this.animate());
  }
}

function node(id,label,category,position,description){return{id,label,category,position,description};}
function number(value){const parsed=Number(value);return Number.isFinite(parsed)?parsed.toLocaleString(undefined,{maximumFractionDigits:2}):"--";}
function ageLabel(value){const age=Math.max(0,(Date.now()-new Date(value).getTime())/1000);return age<2?"live":`${number(age)}s old`;}

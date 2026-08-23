import * as THREE from "/static/vendor/three.module.min.js";

export class HudScene {
  constructor() {
    this.canvas=document.createElement("canvas"); this.canvas.className="hud-3d";
    this.canvas.setAttribute("aria-label","Interactive GLD decision-field visualization"); document.body.prepend(this.canvas);
    this.scene=new THREE.Scene(); this.camera=new THREE.PerspectiveCamera(42,1,.1,100); this.camera.position.set(0,.4,8.5);
    this.renderer=new THREE.WebGLRenderer({canvas:this.canvas,alpha:true,antialias:true,powerPreference:"low-power"});
    this.renderer.setPixelRatio(Math.min(devicePixelRatio||1,1.5)); this.group=new THREE.Group(); this.scene.add(this.group);
    this.pointer={x:0,y:0}; this.target={long:.33,short:.33,abstain:.34,volatility:.2}; this.focused=false; this.frame=0;
    this.reduced=matchMedia("(prefers-reduced-motion: reduce)").matches; this.build();
    addEventListener("resize",()=>this.resize()); addEventListener("pointermove",event=>{this.pointer.x=(event.clientX/innerWidth-.5)*.45;this.pointer.y=(event.clientY/innerHeight-.5)*.28;},{passive:true});
    this.resize(); this.animate();
  }
  build() {
    const green=new THREE.LineBasicMaterial({color:0x39f49a,transparent:true,opacity:.36}),cyan=new THREE.LineBasicMaterial({color:0x42d9ff,transparent:true,opacity:.25}),amber=new THREE.LineBasicMaterial({color:0xffc857,transparent:true,opacity:.24});
    this.rings=[]; [2.25,2.9,3.55,4.18].forEach((radius,index)=>{const geometry=new THREE.TorusGeometry(radius,.012+index*.003,4,128),ring=new THREE.LineSegments(new THREE.WireframeGeometry(geometry),[green,cyan,amber][index%3]);ring.rotation.set(.25+index*.34,index*.47,index*.18);this.group.add(ring);this.rings.push(ring);});
    this.coreMaterial=new THREE.MeshBasicMaterial({color:0x39f49a,wireframe:true,transparent:true,opacity:.62}); this.core=new THREE.Mesh(new THREE.IcosahedronGeometry(1.32,2),this.coreMaterial); this.group.add(this.core);
    this.core.add(new THREE.Mesh(new THREE.OctahedronGeometry(.7,1),new THREE.MeshBasicMaterial({color:0x42d9ff,wireframe:true,transparent:true,opacity:.45})));
    this.marketBars=[]; const barGeometry=new THREE.BoxGeometry(.075,1,.075);
    for(let index=0;index<56;index++){const angle=index/56*Math.PI*2,material=new THREE.MeshBasicMaterial({color:index%3===0?0x42d9ff:0x39f49a,transparent:true,opacity:.34}),bar=new THREE.Mesh(barGeometry,material);bar.position.set(Math.cos(angle)*4.65,-2.25,Math.sin(angle)*4.65);bar.rotation.y=-angle;bar.scale.y=.18+((index*17)%23)/32;this.group.add(bar);this.marketBars.push(bar);}
    this.traces=[]; for(let traceIndex=0;traceIndex<4;traceIndex++){const points=[];for(let index=0;index<40;index++)points.push(new THREE.Vector3(-5.8+index*.3,-1.25+traceIndex*.36+Math.sin(index*.62+traceIndex)*.12,-1.4-traceIndex*.22));const trace=new THREE.Line(new THREE.BufferGeometry().setFromPoints(points),traceIndex%2?cyan:green);this.scene.add(trace);this.traces.push(trace);}
    const grid=new THREE.GridHelper(24,30,0x17637a,0x0d2b20);grid.material.transparent=true;grid.material.opacity=.24;grid.position.y=-2.8;this.scene.add(grid);
  }
  update({signal={},transformer={},quote={}}={}) {this.target.long=bounded(transformer.probability_long??Number(signal.bullish_score||0)/100);this.target.short=bounded(transformer.probability_short??Number(signal.bearish_score||0)/100);this.target.abstain=bounded(transformer.probability_no_trade??Number(signal.no_trade_score||0)/100);this.target.volatility=bounded(Number(quote.spread_pct||0)*500);const decision=String(signal.decision||"");this.coreMaterial.color.setHex(decision.includes("SHORT")?0xff5267:decision.includes("LONG")?0x39f49a:0x42d9ff);}
  setFocus(focused){this.focused=focused;}
  resize(){this.camera.aspect=innerWidth/innerHeight;this.camera.updateProjectionMatrix();this.renderer.setSize(innerWidth,innerHeight,false);}
  animate(){const speed=this.reduced?0:(this.focused ? .0023 : .00055);this.group.rotation.y+=speed;this.group.rotation.x+=(this.pointer.y-this.group.rotation.x)*.018;this.group.rotation.z+=(this.pointer.x-this.group.rotation.z)*.018;this.core.rotation.x-=speed*1.8;this.core.rotation.z+=speed*1.3;this.rings.forEach((ring,index)=>{ring.rotation.z+=speed*(index%2?-1:1)*(.35+index*.14);});this.marketBars.forEach((bar,index)=>{const probability=index%3===0?this.target.long:index%3===1?this.target.short:this.target.abstain,wave=.18+Math.abs(Math.sin(this.frame*.025+index*.31))*.48;bar.scale.y+=((.18+probability*1.7+wave*this.target.volatility)-bar.scale.y)*.035;bar.material.opacity=this.focused ? .48 : .18;});this.camera.position.x+=(this.pointer.x*2.2-this.camera.position.x)*.012;this.camera.position.y+=((.4-this.pointer.y*1.6)-this.camera.position.y)*.012;this.camera.lookAt(0,0,0);this.renderer.render(this.scene,this.camera);this.frame+=1;if(this.frame%30===0)this.canvas.dataset.frame=String(this.frame);requestAnimationFrame(()=>this.animate());}
}
function bounded(value){const number=Number(value);return Number.isFinite(number)?Math.max(0,Math.min(1,number)):0;}

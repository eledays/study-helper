// Exercise the actual gesture handlers with a minimal DOM, including failure paths.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('study/static/navigation.js','utf8');
const gestures=source.slice(source.indexOf(' // Follow the finger'),source.indexOf(' return {navigate,prefetch,clearCache'));
function harness({rejectAnimation=false,waitForNetwork=false}={}){
 const handlers=new Map(),layers=new Set(),navigations=[];
 let captured=false;
 class Node{
  constructor(){this.style={};this.children=[]}
  querySelectorAll(){return []}
  removeAttribute(){} setAttribute(){}
  append(...nodes){this.children.push(...nodes)} replaceChildren(...nodes){this.children=nodes}
  cloneNode(){return new Node()}
  remove(){layers.delete(this)}
  setPointerCapture(){captured=true} hasPointerCapture(){return captured} releasePointerCapture(){captured=false}
  animate(){const finished=rejectAnimation?Promise.reject(new Error('cancelled')):Promise.resolve();return {finished,cancel(){}}}
 }
 const main=new Node();main.closest=selector=>selector==='main'?main:null;
 const links=[0,1,2,3].map(n=>({href:`http://study.test/${n}`,hasAttribute:()=>n===1}));
 const listen=(type,fn)=>{const list=handlers.get(type)||[];list.push(fn);handlers.set(type,list)};
 const document={hidden:false,documentElement:{dataset:{}},body:{append(node){layers.add(node)}},createElement:()=>new Node(),querySelector:()=>main,querySelectorAll:()=>links,addEventListener:listen};
 const context={document,window:{addEventListener:listen},ready:true,mutation:false,editing:()=>false,matchMedia:()=>({matches:true}),innerWidth:390,scrollY:0,performance:{now:()=>100},setTimeout,clearTimeout,Promise,reducedMotion:()=>false,canonical:x=>x,getCache:()=>null,prefetch:async()=>{},navigate:async url=>{navigations.push(url);if(waitForNetwork)await new Promise(()=>{})}};
 vm.runInNewContext(gestures,context);
 const emit=(type,x=250,y=300)=>Promise.all((handlers.get(type)||[]).map(fn=>fn({target:main,pointerId:1,isPrimary:true,button:0,clientX:x,clientY:y,cancelable:true,preventDefault(){}})));
 return {emit,layers,navigations};
}
(async()=>{
 const interrupted=harness({rejectAnimation:true});
 await interrupted.emit('pointerdown');await interrupted.emit('pointermove',100);
 assert.equal(interrupted.layers.size,1);
 await interrupted.emit('pointerup',100);assert.equal(interrupted.layers.size,0,'Cancelled animation must release the overlay');
 await interrupted.emit('pointerdown');await interrupted.emit('pointermove',100);await interrupted.emit('pointercancel');assert.equal(interrupted.layers.size,0);
 const offline=harness({waitForNetwork:true});
 await offline.emit('pointerdown');await offline.emit('pointermove',100);
 void offline.emit('pointerup',100);await new Promise(resolve=>setImmediate(resolve));
 assert.equal(offline.layers.size,0,'Overlay must disappear before waiting on a slow network');assert.equal(offline.navigations.length,1);
 await offline.emit('pointerdown');await offline.emit('pointermove',280);await offline.emit('pointerup',280);
 assert.equal(offline.layers.size,0,'A short subsequent gesture must also clean up');assert.equal(offline.navigations.length,1);
 const vertical=harness();await vertical.emit('pointerdown');await vertical.emit('pointermove',251,150);await vertical.emit('pointerup',251,150);
 assert.equal(vertical.layers.size,0);assert.equal(vertical.navigations.length,0);
 console.log('Gesture regressions passed: interrupted animation, cancellation, slow network, short drag, vertical scroll');
})().catch(error=>{console.error(error);process.exitCode=1});

const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('study/static/app.js','utf8');
const chart=source.slice(source.indexOf('function chart('),source.indexOf("document.querySelectorAll('form:not"));
const counts={};
class Node{constructor(){this.style={};this.clientWidth=500}setAttribute(){}append(){}}
const document={createElementNS(ns,tag){counts[tag]=(counts[tag]||0)+1;return new Node()},createElement:()=>new Node(),createTextNode:()=>new Node()};
const rows=Array.from({length:10000},(_,i)=>({day:'2026-10-31',result:i%100,trend:i%80}));
vm.runInNewContext(chart+"chart(container,rows,[['result','Result','gray'],['trend','Trend','green']],100)",{document,rows,container:new Node()},{timeout:1000});
assert.equal(counts.circle,480,'SVG size must stay bounded for long histories');
assert.ok(counts.text<=8,'Date labels must not repeat for every result');
console.log('Chart stress regression passed: 10,000 results use 480 points across two series');

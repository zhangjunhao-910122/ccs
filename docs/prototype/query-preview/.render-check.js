// 一次性渲染自检：用最小 DOM 桩跑原型脚本，验证三个变体都能渲染且裁剪/空态分支正确
const fs = require('fs');
const path = require('path');
const file = path.resolve('docs/prototype/query-preview/index.html');
const html = fs.readFileSync(file, 'utf8');
const js = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function stubEl(){ return { innerHTML:"", textContent:"", dataset:{}, setAttribute(){}, classList:{toggle(){},add(){},remove(){}}, querySelectorAll: ()=>[] }; }
const els = {};
const roleButtons = ["TRADER","RISK","AUDITOR"].map(r=>({dataset:{role:r}, setAttribute(){}, onclick:null}));
global.document = {
  getElementById(id){ if(!els[id]) els[id]=stubEl(); return els[id]; },
  querySelectorAll(sel){ return sel.indexOf("roleset")>=0 ? roleButtons : []; },
  addEventListener(){},
  body:{ appendChild(){} }
};
global.location = { search:"?variant=A", href:"file:///G:/DSH/index.html?variant=A" };
global.history = { replaceState(){} };

const checks = [];
function ok(name, cond, extra){ checks.push({name, pass: !!cond, extra: extra||""}); }

function load(variant){
  global.location.search = "?variant=" + variant;
  global.location.href = "file:///G:/DSH/index.html?variant=" + variant;
  els.root = stubEl();
  new Function(js)();
  return els.root.innerHTML;
}

for(const v of ["A","B","C"]){
  const h = load(v);
  ok("变体 "+v+" 渲染非空", h.length > 800, "len="+h.length);
  ok("变体 "+v+" 含口径 as_of", h.indexOf("as_of")>=0);
  ok("变体 "+v+" 含 coverage_ratio", h.indexOf("coverage_ratio")>=0);
  ok("变体 "+v+" 含掩码值 MASK-H61ef", h.indexOf("MASK-H61ef")>=0);
  ok("变体 "+v+" 含审计事件号", h.indexOf("E004211")>=0);
  ok("变体 "+v+" 声明无真实交易写通道", h.indexOf("READ")>=0);
  ok("变体 "+v+" 显示变体键", els["cur"].innerHTML.indexOf(">"+v+"<")>=0);
}

// 角色切换：RISK 已获双人会签授权 → 数值可见（不再是掩码串）
roleButtons[1].onclick();
const hRisk = els.root.innerHTML;
ok("RISK 身份下数值可见", hRisk.indexOf("MASK-H61ef")<0);
ok("RISK 身份下仍保留掩码说明口径", hRisk.indexOf("masked_fields")>=0 || hRisk.indexOf("掩码")>=0);

// 切到不可用数据集 D02 → 显式无权限空态
els.ds.onchange({target:{value:"D02"}});
const hForbidden = els.root.innerHTML;
ok("D02 显示无权限空态", hForbidden.indexOf("无权限")>=0);
ok("D02 带 reason_code", hForbidden.indexOf("HIGHLY_REQUIRES_TWO_APPROVERS")>=0);
ok("D02 不泄露字段值", hForbidden.indexOf("MASK-H61ef")<0);

let bad = 0;
for(const c of checks){ if(!c.pass) bad++; console.log((c.pass?"[PASS] ":"[FAIL] ")+c.name+(c.extra?" ("+c.extra+")":"")); }
console.log("Summary: " + (checks.length-bad) + "/" + checks.length + " PASS");
process.exit(bad?1:0);

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务2 布局重构结构验证：顶部节点导航 + 单面板网格。"""
import subprocess, sys
from pathlib import Path

JS = r'''
const {JSDOM} = require('jsdom');
const fs = require('fs');
const p = 'D:/heygem_data/gpt_sovits/rewrite_studio.html';
const html = fs.readFileSync(p,'utf8');
const dom = new JSDOM(html, {runScripts:'outside-only', pretendToBeVisual:true});
const doc = dom.window.document;
const css = [...doc.querySelectorAll('style')].map(s=>s.textContent).join('\n');

function check(name, cond){ console.log((cond?'✅':'❌')+' '+name); return cond; }

let ok = true;
ok &= check('顶部节点导航 .node-nav 存在', !!doc.querySelector('.node-nav'));
ok &= check('导航含 9 个流程节点', doc.querySelectorAll('.node-nav .steps li').length===9);
ok &= check('9 个节点 data-step 齐全', (()=>{
  const need=['topic','edit','audio','model','render','subtitle','qc','publish','queue'];
  const got=[...doc.querySelectorAll('.node-nav .steps li')].map(li=>li.dataset.step);
  return need.every(n=>got.includes(n));
})());
ok &= check('原左栏 .col-left 已移除', !doc.querySelector('.col-left'));
ok &= check('9 个面板 panel 齐全', doc.querySelectorAll('.panel').length===9);
ok &= check('CSS 布局改为两栏(1fr 344px)', css.includes('grid-template-columns:1fr 344px'));
ok &= check('nav 跨整行 grid-column:1 / -1', css.includes('grid-column:1 / -1'));
ok &= check('nav 横向 flex 布局', css.includes('.node-nav .steps{display:flex'));
ok &= check('nav sticky 常驻顶部', css.includes('position:sticky;top:64px'));
ok &= check('active 高亮节点(横向渐变)', css.includes('.steps li.active{background:linear-gradient(90deg,#2563eb,#1d4ed8)'));

// gotoStep 逻辑：模拟调用，确认切换 panel.active + nav li.active
const gotostep_src = (()=>{
  const m = html.match(/function gotoStep\(step\)\{[\s\S]*?\n\}/);
  return m ? m[0] : '';
})();
ok &= check('gotoStep 仍切换 .panel.active (逻辑保留)', gotostep_src.includes("panel.classList.add(\"active\")") && gotostep_src.includes("document.querySelectorAll(\".panel\")"));
ok &= check('gotoStep 仍切换 nav li.active', gotostep_src.includes("li.classList.toggle(\"active\", li.dataset.step===step)"));
ok &= check('gotoStep 已去掉整页 scrollIntoView', !gotostep_src.includes('scrollIntoView'));

console.log(ok ? '\\n✅ 布局重构结构验证全部通过' : '\\n❌ 存在失败项');
process.exit(ok?0:1);
'''
out = subprocess.run([sys.executable, "-c", "import subprocess,sys; "
                      "code=sys.argv[1]; open('_verify.js','w',encoding='utf-8').write(code)"
                      ] if False else ["node", "-e", JS],
                      cwd="D:/heygem_data/gpt_sovits",
                      capture_output=True, text=True,
                      env={**__import__('os').environ,
                           "NODE_PATH": "C:/Users/lenovo/.workbuddy/binaries/node/workspace/node_modules"})
print("RC:", out.returncode)
print(out.stdout)
if out.stderr.strip():
    print("STDERR:", out.stderr[:1500])

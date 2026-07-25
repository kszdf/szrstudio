// 重现"点操作指南没反应"——真实点击 h-guide 后查 modal 状态
const { JSDOM } = require('jsdom');
const fs = require('fs');
const path = require('path');
const html = fs.readFileSync('D:/heygem_data/gpt_sovits/rewrite_studio.html','utf-8');

const errors = [];
const dom = new JSDOM(html, {
  url:'http://localhost:8385/',
  runScripts:'dangerously',
  pretendToBeVisual:true,
  beforeParse(window){
    window.fetch = async (u,o)=>{
      const url = typeof u==='string'?u:u.url;
      const ok = (body)=>({ ok:true, status:200, statusText:'OK', json:()=>Promise.resolve(body) });
      if(url.includes('/api/projects')) return ok([]);
      if(url.includes('/api/models')) return ok([]);
      if(url.includes('/api/thirdparty/info')) return ok({configured:false,avatars:[],voice_modes:['official','brand']});
      if(url.includes('/api/guidance')) return ok({});
      return ok({});
    };
    window.console.error = (...a)=>errors.push('console.error: '+a.map(x=>String(x)).join(' '));
    window.addEventListener('error', e => errors.push('window.error: '+e.message+' @ '+(e.filename||'')+':'+e.lineno));
    window.addEventListener('unhandledrejection', e => errors.push('unhandledrejection: '+(e.reason&&e.reason.message||e.reason)));
  }
});
const { window } = dom;
const { document } = window;

// 等待脚本执行完
setTimeout(()=>{
  try{
    console.log('=== STEP 1: 检查 h-guide 元素 ===');
    const hg = document.querySelector('.h-guide');
    console.log('h-guide 存在:', !!hg, 'onclick:', hg ? hg.getAttribute('onclick') : 'n/a');
    console.log('h-guide 文本:', hg ? hg.textContent : 'n/a');

    console.log('\n=== STEP 2: 检查 guideModal 元素 ===');
    const gm = document.getElementById('guideModal');
    console.log('guideModal 存在:', !!gm, 'display:', gm ? window.getComputedStyle(gm).display : 'n/a', 'inline:', gm ? gm.style.display : 'n/a');

    console.log('\n=== STEP 3: 检查 STEP_GUIDES.overview ===');
    console.log('STEP_GUIDES 存在:', typeof window.STEP_GUIDES, 'overview 键存在:', !!(window.STEP_GUIDES && window.STEP_GUIDES.overview));
    // 注意 const 不挂 window，闭包内可见；onclick 走 showGuide（function 挂 window）→ showGuide 内可访问 STEP_GUIDES

    console.log('\n=== STEP 4: 检查 showGuide / renderGuide 函数 ===');
    console.log('showGuide typeof:', typeof window.showGuide);
    console.log('renderGuide typeof:', typeof window.renderGuide);

    console.log('\n=== STEP 5: 直接调用 showGuide("overview") ===');
    try{
      window.showGuide('overview');
      console.log('showGuide 调用成功');
      console.log('guideTitle 文本:', document.getElementById('guideTitle').textContent.substring(0,40));
      console.log('guideBody innerHTML 长度:', document.getElementById('guideBody').innerHTML.length);
      console.log('guideModal style.display:', gm.style.display);
      console.log('guideModal computed display:', window.getComputedStyle(gm).display);
    }catch(e){ console.log('showGuide 抛错:', e.message); }

    console.log('\n=== STEP 6: 真实点击 h-guide（模拟用户） ===');
    try{
      // 直接执行 onclick 字符串
      const onclickCode = hg.getAttribute('onclick');
      console.log('onclick 代码:', onclickCode);
      // 用 Function 构造并调用，模拟真实 onclick
      const fn = new window.Function('event', onclickCode);
      const fakeEvent = { stopPropagation: ()=>console.log('  stopPropagation 被调用') };
      fn(fakeEvent);
      const cs = window.getComputedStyle(gm);
      console.log('onclick 执行后 guideModal.style.display:', gm.style.display);
      console.log('onclick 执行后 computed display:', cs.display);
      console.log('【关键】computed opacity:', cs.opacity, '(修复前应为 0，修复后应为 1)');
      console.log('【关键】computed pointer-events:', cs.pointerEvents, '(修复前应为 none，修复后应为 auto)');
      console.log('【关键】computed z-index:', cs.zIndex);
      console.log('guideTitle:', document.getElementById('guideTitle').textContent.substring(0,40));
      console.log('guideBody 长度:', document.getElementById('guideBody').innerHTML.length);
      console.log('guideModal class:', gm.className);
    }catch(e){ console.log('点击模拟抛错:', e.message); }

    console.log('\n=== 收集到的 JS 错误 ===');
    if(errors.length===0) console.log('(无)');
    else errors.forEach(e=>console.log('  -',e));
  }catch(e){
    console.log('外层抛错:', e.message, e.stack);
  }
}, 600);
# -*- coding: utf-8 -*-
"""
批量出片队列监控页（独立、零侵入）
=================================
- 端口 8386，绑定 0.0.0.0（局域网同事也能开）
- 复用现有 8385 的全局队列：后端 GET /api/queue 由本服务代理转发，浏览器只与本服务同源通信，无 CORS 问题
- 不触发任何出片，纯观测 + 提醒，绝不抢 HEYGEM 显卡，与原 8385 互不影响
- 补齐现有前端缺的两样：① 预计时长(ETA) ② 完成/失败主动弹窗提醒（可选响铃）

运行（无需管理员、不动 HGTStudio）：
  C:/Users/lenovo/.workbuddy/binaries/python/versions/3.13.12/python.exe queue_monitor.py
然后浏览器开 http://localhost:8386  （同事开 http://<你局域网IP>:8386）
"""
import json
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = "http://localhost:8385"   # 现有 8385 生产服务
PORT = 8386

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>批量出片队列监控</title>
<style>
  :root{
    --bg:#f4f6fb; --panel:#fff; --ink:#1f2430; --muted:#7a8194; --line:#e6e9f2;
    --blue:#2f54eb; --green:#18a058; --amber:#f0a020; --red:#e5484d; --chip:#eef2ff;
  }
  *{box-sizing:border-box}
  body{margin:0;font-family:"Microsoft YaHei",system-ui,sans-serif;background:var(--bg);color:var(--ink)}
  .wrap{max-width:880px;margin:0 auto;padding:20px 16px 60px}
  header{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px}
  h1{font-size:19px;margin:0}
  .dot{width:9px;height:9px;border-radius:50%;background:var(--green);display:inline-block;box-shadow:0 0 0 3px rgba(24,160,88,.18)}
  .dot.off{background:var(--red);box-shadow:0 0 0 3px rgba(229,72,77,.18)}
  .stat{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
  .pill{background:var(--panel);border:1px solid var(--line);border-radius:999px;padding:6px 12px;font-size:13px;font-weight:600}
  .pill b{color:var(--blue)}
  .tools{display:flex;gap:10px;align-items:center;flex-wrap:wrap;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:10px 12px;margin-bottom:16px}
  .tools label{font-size:13px;color:var(--muted)}
  .tools input[type=text]{padding:6px 10px;border:1px solid var(--line);border-radius:8px;font-size:13px;min-width:160px}
  .tools input[type=number]{width:64px;padding:6px 8px;border:1px solid var(--line);border-radius:8px;font-size:13px}
  .list{display:flex;flex-direction:column;gap:10px}
  .item{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px;display:grid;grid-template-columns:34px 1fr auto;gap:12px;align-items:center;transition:.2s}
  .item.watch{border-color:var(--blue);box-shadow:0 0 0 3px var(--chip)}
  .item.done{opacity:.75}
  .item.error{border-color:var(--red)}
  .pos{font-size:18px;font-weight:800;color:var(--muted);text-align:center}
  .pos .mini{display:block;font-size:10px;font-weight:600;color:var(--muted)}
  .main .nm{font-weight:700;font-size:15px}
  .main .sub{font-size:12px;color:var(--muted);margin-top:2px}
  .bar{height:6px;border-radius:999px;background:var(--line);margin-top:8px;overflow:hidden}
  .bar>i{display:block;height:100%;background:var(--blue);width:0;transition:width .4s}
  .right{text-align:right;min-width:120px}
  .badge{font-size:12px;font-weight:700;padding:3px 9px;border-radius:999px;display:inline-block}
  .b-waiting{background:var(--chip);color:var(--blue)}
  .b-rendering{background:#e7f7ee;color:var(--green)}
  .b-done{background:#eef0f3;color:var(--muted)}
  .b-error{background:#fdeaea;color:var(--red)}
  .eta{font-size:12px;color:var(--muted);margin-top:4px}
  .empty{color:var(--muted);text-align:center;padding:40px 0;font-size:14px}
  .tip{font-size:12px;color:var(--muted);margin-top:10px;line-height:1.6}
  /* toast */
  #toasts{position:fixed;right:18px;bottom:18px;display:flex;flex-direction:column;gap:10px;z-index:99}
  .toast{background:#1f2430;color:#fff;padding:12px 16px;border-radius:12px;font-size:14px;box-shadow:0 8px 24px rgba(0,0,0,.25);animation:in .25s ease;max-width:320px}
  .toast.ok{background:#18a058}.toast.err{background:#e5484d}
  @keyframes in{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <span class="dot" id="liveDot"></span>
    <h1>批量出片队列监控</h1>
    <span id="liveTxt" style="font-size:13px;color:var(--muted)">连接中…</span>
  </header>

  <div class="stat">
    <span class="pill">等待 <b id="cWait">0</b></span>
    <span class="pill">渲染中 <b id="cRun">0</b></span>
    <span class="pill">完成 <b id="cDone">0</b></span>
    <span class="pill">失败 <b id="cErr">0</b></span>
    <span class="pill">上限 <b id="cMax">10</b></span>
  </div>

  <div class="tools">
    <label>每条预计</label>
    <input type="number" id="avg" value="180" min="30" step="30"> 秒
    <label style="margin-left:8px">关注项目名</label>
    <input type="text" id="watch" placeholder="输入你的项目名，出完会高亮+提醒">
    <label style="margin-left:8px"><input type="checkbox" id="beep" checked> 完成响铃</label>
  </div>

  <div class="list" id="list">
    <div class="empty">队列为空。在 8385 主界面的「出片」步骤点「加入批量队列」即可。</div>
  </div>

  <div class="tip">
    本页是独立监控台，只读、不触发出片，与原 8385 互不影响。<br>
    每 3 秒自动刷新；某条从「渲染中」变「完成」会弹窗提醒（关注的项目名会高亮）。<br>
    出片仍在 8385 主界面操作；HEYGEM 一次仅渲一条，队列自动串行，多人同时提交也不会撞车。
  </div>
</div>

<div id="toasts"></div>

<script>
const $ = s => document.querySelector(s);
let seen = new Set();          // 已提醒过的 id
let lastData = null;

function fmtETA(sec){
  if(sec==null || isNaN(sec)) return "—";
  sec = Math.max(0, Math.round(sec));
  if(sec < 60) return "约 "+sec+" 秒";
  const m = Math.floor(sec/60), s = sec%60;
  return "约 "+m+" 分"+(s?(" "+s+" 秒"):"");
}

function beep(){
  try{
    if(!$("#beep").checked) return;
    const ctx = new (window.AudioContext||window.webkitAudioContext)();
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.connect(g); g.connect(ctx.destination);
    o.type="sine"; o.frequency.value=880; g.gain.value=0.001;
    o.frequency.exponentialRampToValueAtTime(1320, ctx.currentTime+0.12);
    g.gain.exponentialRampToValueAtTime(0.25, ctx.currentTime+0.02);
    g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime+0.4);
    o.start(); o.stop(ctx.currentTime+0.42);
  }catch(e){}
}

function toast(msg, kind){
  const t = document.createElement("div");
  t.className = "toast"+(kind?(" "+kind):"");
  t.textContent = msg;
  $("#toasts").appendChild(t);
  setTimeout(()=>{ t.style.opacity="0"; t.style.transition=".3s"; setTimeout(()=>t.remove(),300); }, 4000);
}

function render(d){
  const q = (d.queue||[]).filter(x=>x.status!=="done" || true); // 显示全部，已完成置灰
  const wait = q.filter(x=>x.status==="waiting");
  const run  = q.filter(x=>x.status==="rendering");
  const done = q.filter(x=>x.status==="done");
  const errs = q.filter(x=>x.status==="error");
  $("#cWait").textContent=wait.length; $("#cRun").textContent=run.length;
  $("#cDone").textContent=done.length; $("#cErr").textContent=errs.length;
  $("#cMax").textContent=d.max||10;

  const avg = Math.max(30, parseInt($("#avg").value||"180",10));
  const watch = ($("#watch").value||"").trim();

  // 计算每条 ETA：排在它前面(含渲染中剩余)的耗时之和
  const ordered = [...q].sort((a,b)=>(a.pos||0)-(b.pos||0));
  let acc = 0;
  const etaOf = {};
  for(const it of ordered){
    if(it.status==="rendering"){
      const p = Math.max(0, Math.min(100, it.progress||0));
      const rem = (100-p)/100*avg;
      etaOf[it.id] = acc + rem;
      acc += rem;
    } else if(it.status==="waiting"){
      etaOf[it.id] = acc + avg;
      acc += avg;
    } else {
      etaOf[it.id] = 0;
    }
  }

  const list = $("#list");
  if(!q.length){
    list.innerHTML = '<div class="empty">队列为空。在 8385 主界面的「出片」步骤点「加入批量队列」即可。</div>';
  } else {
    list.innerHTML = ordered.map((it,i)=>{
      const stCls = it.status==="rendering"?"b-rendering":(it.status==="done"?"b-done":(it.status==="error"?"b-error":"b-waiting"));
      const stTxt = it.status==="rendering"?"渲染中":(it.status==="done"?"已完成":(it.status==="error"?"失败":"等待"));
      const watchOn = watch && it.name===watch;
      const prog = it.progress||0;
      const eta = it.status==="done" ? "已出片" : (it.status==="error" ? "—" : fmtETA(etaOf[it.id]));
      const sub = [it.model_label||"", "加入 "+ (it.added_at||"")].filter(Boolean).join(" · ");
      const step = it.step ? `<div class="sub">${it.step}</div>` : "";
      const errLine = it.status==="error" && it.error ? `<div class="sub" style="color:var(--red)">${it.error}</div>` : "";
      const vid = it.status==="done" && it.video_url ? `<a class="sub" href="${it.video_url}" target="_blank" style="color:var(--blue)">▶ 打开成片</a>` : "";
      return `<div class="item ${it.status==="done"?"done":""} ${it.status==="error"?"error":""} ${watchOn?"watch":""}">
        <div class="pos">#${i+1}<span class="mini">${stTxt}</span></div>
        <div class="main">
          <div class="nm">${esc(it.name)}</div>
          <div class="sub">${esc(sub)}</div>
          ${step}${errLine}${vid}
          <div class="bar"><i style="width:${prog}%"></i></div>
        </div>
        <div class="right">
          <span class="badge ${stCls}">${stTxt}</span>
          <div class="eta">${eta}</div>
          <div class="eta">进度 ${prog}%</div>
        </div>
      </div>`;
    }).join("");
  }

  // 完成/失败提醒（仅对从未提醒过的）
  for(const it of q){
    if((it.status==="done"||it.status==="error") && !seen.has(it.id)){
      seen.add(it.id);
      const isWatch = watch && it.name===watch;
      if(it.status==="done"){
        toast((isWatch?"★ ":"")+ "✅ 「"+it.name+"」出片完成", "ok");
        if(isWatch) beep();
      } else {
        toast("❌ 「"+it.name+"」出片失败：" + (it.error||"").slice(0,40), "err");
        if(isWatch) beep();
      }
    }
  }
  // 队列清空或项被移除时，清理 seen 避免内存无限增长
  const ids = new Set(q.map(x=>x.id));
  seen.forEach(id=>{ if(!ids.has(id)) seen.delete(id); });
}

function esc(s){ return String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }

async function poll(){
  try{
    const r = await fetch("/api/queue", {cache:"no-store"});
    const d = await r.json();
    lastData = d;
    $("#liveDot").classList.remove("off");
    $("#liveTxt").textContent = "已连接 8385 · 每 3 秒刷新";
    render(d);
  }catch(e){
    $("#liveDot").classList.add("off");
    $("#liveTxt").textContent = "无法连接 8385（确认 HGTStudio 运行中）";
  }
}

poll();
setInterval(poll, 3000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "QueueMonitor/1.0"

    def _send(self, code, ctype, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/queue":
            self._proxy_queue()
            return
        # 其余路径都返回监控页（方便刷新）
        self._send(200, "text/html; charset=utf-8", HTML.encode("utf-8"))

    def _proxy_queue(self):
        try:
            req = urllib.request.urlopen(UPSTREAM + "/api/queue", timeout=5)
            data = req.read()
            self._send(200, "application/json; charset=utf-8", data)
        except Exception as e:  # 8385 没起 / 网络不通
            self._send(502, "application/json; charset=utf-8",
                       json.dumps({"error": "upstream_unreachable: %s" % e}).encode("utf-8"))

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"队列监控页已启动: http://localhost:{PORT}  (代理自 {UPSTREAM})")
    srv.serve_forever()

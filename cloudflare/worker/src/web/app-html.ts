/**
 * stage-cloud-27b — Worker 托管前端（对齐本地 order_platform 界面与功能）。
 *
 * 深色 Tabler 风格 + 页面结构复刻本地版：
 *   首页橱窗（hero + 商品卡 + 统计卡）/ 下单向导 / 我的订单（按账号分组）/
 *   订单详情（执行记录 + 入队）/ 查单（访客前缀）/ 批量下单。
 * 差异（设计使然）：密码不明文回传（enc-v2 托管，仅 executor 租约期解封）；
 * 课程查询工具与暂停/恢复为本地引擎进程能力，云端不入此版。
 */
export const APP_HTML = `<!DOCTYPE html>
<html lang="zh" data-bs-theme="dark">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>自动任务平台</title>
<style>
:root{--bg:#171b26;--card:#1f2433;--card2:#232b3b;--line:rgba(255,255,255,.08);--tx:#dfe5f1;--mut:#8b94a7;--pri:#6366f1;--cyan:#22d3ee}
*{box-sizing:border-box;margin:0;padding:0}
body{font:15px/1.6 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--tx)}
a{color:var(--cyan);text-decoration:none}
header.nav{background:var(--card);border-bottom:1px solid var(--line);padding:0 18px;height:56px;display:flex;align-items:center;gap:6px;position:sticky;top:0;z-index:9}
.nav .brand{font-size:18px;font-weight:800;margin-right:14px;white-space:nowrap}
.nav a.nl{padding:6px 12px;border-radius:8px;color:var(--mut);font-size:14.5px;cursor:pointer;border:0;background:none;font-family:inherit}
.nav a.nl:hover{color:var(--tx)}
.nav a.nl.on{color:#fff;background:rgba(99,102,241,.22)}
.nav .sp{flex:1}
.container{max-width:1060px;margin:0 auto;padding:0 16px}
.hero{padding:2.2rem 0 .6rem}
.hero h1{font-size:34px;font-weight:800;background:linear-gradient(90deg,#fff 30%,#a5b4fc 70%,#67e8f9);-webkit-background-clip:text;background-clip:text;color:transparent}
.hero p{color:var(--mut);margin-top:6px}
.row{display:flex;flex-wrap:wrap;gap:14px;margin:14px 0}
.col{flex:1 1 280px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px}
.card.sm{padding:14px}
.glow{box-shadow:0 8px 30px rgba(99,102,241,.25)}
.card h3{font-size:17px;margin:6px 0 4px}
.muted,.text-secondary{color:var(--mut)}
.small{font-size:13px}
.avatar{width:40px;height:40px;border-radius:10px;background:rgba(99,102,241,.18);display:grid;place-items:center;font-size:20px}
.badge{display:inline-block;padding:2px 10px;border-radius:99px;font-size:12.5px;font-weight:600}
.b-yellow{background:rgba(247,103,7,.15);color:#ff9f43}.b-blue{background:rgba(32,110,180,.25);color:#7cb8ec}
.b-green{background:rgba(47,179,68,.16);color:#6cd982}.b-red{background:rgba(214,57,57,.18);color:#f08a8a}
.b-purple{background:rgba(174,109,255,.16);color:#c3a1fa}.b-gray{background:rgba(255,255,255,.09);color:var(--mut)}
.b-orange{background:rgba(255,159,67,.15);color:#ffb45e}
.btn{border:0;border-radius:8px;padding:9px 18px;cursor:pointer;font:inherit;font-size:14.5px;background:linear-gradient(135deg,var(--pri),#8b5cf6);color:#fff}
.btn:disabled{opacity:.5;cursor:default}
.btn.out{background:transparent;border:1px solid var(--line);color:var(--tx)}
.btn.sm{padding:4px 10px;font-size:13px}
.subheader{color:var(--mut);font-size:13px}
.h1{font-size:28px;font-weight:700}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mut);font-weight:600;font-size:12.5px;white-space:nowrap}
details.acc summary{cursor:pointer;padding:10px;list-style:none}
details.acc summary:hover{background:rgba(255,255,255,.03)}
label{display:block;font-size:13px;color:var(--mut);margin:12px 0 4px}
input,select,textarea{width:100%;padding:9px 11px;border:1px solid var(--line);border-radius:8px;font:inherit;background:#171b26;color:var(--tx)}
input:focus,select:focus,textarea:focus{outline:1px solid var(--pri)}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:13px}
.wiz-num{width:26px;height:26px;border-radius:50%;background:rgba(255,255,255,.08);display:inline-grid;place-items:center;font-size:13px;font-weight:700;color:#c9d3e3;margin-right:8px}
.wiz-num.on{background:linear-gradient(135deg,#6366f1,#22d3ee);color:#fff}
.empty{text-align:center;padding:60px 0}
.empty-header{font-size:44px}.empty-title{font-size:18px;margin:8px 0 2px}
footer{padding:1.6rem 0;color:var(--mut);font-size:.8rem;text-align:center}
.toast{position:fixed;bottom:22px;right:22px;background:#2b3245;color:#fff;padding:10px 20px;border-radius:10px;font-size:14px;opacity:0;transition:.25s;pointer-events:none;z-index:99;box-shadow:0 8px 30px rgba(0,0,0,.4)}
.toast.show{opacity:1}
.stat-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px}
</style>
</head>
<body>
<header class="nav">
  <span class="brand">⚡ 自动任务平台</span>
  <a class="nl" data-v="home" onclick="go('home')">首页</a>
  <a class="nl" data-v="my" onclick="go('my')">我的订单</a>
  <a class="nl" data-v="query" onclick="go('query')">查单</a>
  <a class="nl" data-v="batch" onclick="go('batch')">批量下单</a>
  <span class="sp"></span>
  <span class="small muted" id="who"></span>
  <a class="nl" id="authBtn" onclick="go('auth')">登录 / 注册</a>
  <a class="nl" id="outBtn" style="display:none" onclick="logout()">退出</a>
</header>
<div class="container">

<div id="v-home">
  <div class="hero"><h1>任务交给自动化，时间留给自己</h1>
  <p>选择商品下单，云端自动排队执行；全程日志可查、进度实时可见。凭据加密托管（enc-v2），仅执行期按租约解封。</p></div>
  <div class="row" id="productCards"><div class="card col muted">加载中…</div></div>
  <div class="stat-row" id="statCards" style="display:none"></div>
  <p class="text-secondary" style="margin:14px 0">没有账号？<a onclick="go('auth')" style="cursor:pointer">注册</a> 后下单 · 已有订单？<a onclick="go('query')" style="cursor:pointer">凭单号查单</a></p>
</div>

<div id="v-auth" style="display:none">
  <div class="row justify-content-center"><div class="card col" style="max-width:460px">
    <h3>登录 / 注册</h3>
    <label>用户名（3-32 位字母数字_-）</label><input id="lg-user" autocomplete="username">
    <label>密码（≥8 位）</label><input id="lg-pass" type="password" autocomplete="current-password">
    <div style="display:flex;gap:10px;margin-top:16px">
      <button class="btn" onclick="doLogin()">登录</button>
      <button class="btn out" onclick="doRegister()">注册新账号</button>
    </div>
    <p class="small muted" style="margin-top:12px">注册即视为知悉并接受免责条款：仅供个人学习研究使用。</p>
  </div></div>
</div>

<div id="v-my" style="display:none">
  <h3 style="margin:20px 0 6px">📒 我的订单</h3>
  <p class="small muted">同账号多单已合并分组，点击展开；凭据密文托管，明文不出解封通道</p>
  <div class="card" style="padding:0;overflow:auto;margin-top:12px">
    <table><thead><tr><th>单号</th><th>商品 / 课程</th><th>账号</th><th>状态</th><th>时间</th><th></th></tr></thead>
    <tbody id="myOrders"><tr><td colspan="6" class="muted">加载中…</td></tr></tbody></table>
  </div>
  <div id="emptyMy"></div>
</div>

<div id="v-order" style="display:none"></div>

<div id="v-buy" style="display:none"></div>

<div id="v-query" style="display:none">
  <h3 style="margin:20px 0 6px">🔎 查单</h3>
  <div class="card">
    <p class="small muted" style="margin-bottom:8px">输入订单号（支持前缀），无需登录。返回脱敏的单号前缀。</p>
    <div style="display:flex;gap:10px">
      <input id="g-code" placeholder="订单号或前缀，如 cloud-real" class="mono">
      <button class="btn" style="flex:0 0 auto" onclick="guestQuery()">查询</button>
    </div>
    <table style="margin-top:12px"><thead><tr><th>单号前缀</th><th>商品</th><th>状态</th><th>更新时间</th></tr></thead>
    <tbody id="g-res"><tr><td colspan="4" class="muted">—</td></tr></tbody></table>
  </div>
</div>

<div id="v-batch" style="display:none">
  <h3 style="margin:20px 0 6px">📦 批量下单</h3>
  <div class="card">
    <label>每行一条：平台,账号,课程ID（课程可空；密码由管理员加密录入后自动关联）</label>
    <textarea id="b-lines" rows="6" class="mono" placeholder="chaoxing,13800000000,254722149&#10;chaoxing,13900000000,"></textarea>
    <label>执行路径</label>
    <select id="b-path"><option value="local">local（本机真实引擎）</option><option value="internal">internal（云端沙箱）</option></select>
    <button class="btn" style="width:100%;margin-top:14px" id="b-btn" onclick="batchSubmit()">批量提交</button>
    <pre id="b-out" style="display:none;margin-top:12px"></pre>
  </div>
</div>

</div>
<footer>自动任务平台 · 仅供个人本地测试与学习研究使用 · 不提供对外服务 · 进度数据仅供参考，一切以学习平台官方为准 · 使用即视为知悉并接受全部免责条款</footer>
<div class="toast" id="toast"></div>
<script>
const $=id=>document.getElementById(id);
const PICON={"chaoxing":"🚀","zhs":"🌿","zhsqr":"📷"};
const BADGE={pending:["b-yellow","排队中"],queued:["b-yellow","排队中"],processing:["b-blue","执行中"],
  leased:["b-purple","已认领"],running:["b-blue","执行中"],succeeded:["b-green","已完成"],
  done:["b-green","已完成"],failed:["b-red","失败"],canceled:["b-gray","已取消"],retry_wait:["b-orange","等待重试"]};
function badge(s){const b=BADGE[s]||["b-gray",s];return '<span class="badge '+b[0]+'">'+b[1]+'</span>'}
function esc(v){return String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
function fmt(t){return t?new Date(t).toLocaleString("zh-CN",{hour12:false}):"—"}
function toast(m){const t=$("toast");t.textContent=m;t.classList.add("show");setTimeout(()=>t.classList.remove("show"),2200)}
let me=null,products=[],pollTimer=null,currentOrder=null;

async function api(path,opt={}){opt.headers=Object.assign({"Content-Type":"application/json"},opt.headers||{});
  const r=await fetch(path,opt);let b={};try{b=await r.json()}catch(e){}
  if(!r.ok)throw new Error(b.error&&b.error.message?b.error.message:"HTTP "+r.status);return b}

function go(v,arg){location.hash="#"+v+(arg?"/"+arg:"");render(v,arg)}
function render(v,arg){
  document.querySelectorAll(".nl[data-v]").forEach(a=>a.classList.toggle("on",a.dataset.v===v||(v==="buy"&&a.dataset.v==="home")));
  for(const x of["home","auth","my","order","buy","query","batch"])$("v-"+x).style.display=x===v?"":"none";
  clearInterval(pollTimer);
  if(v==="home")drawHome();
  if(v==="my"){drawMy();pollTimer=setInterval(drawMy,5000)}
  if(v==="order")drawOrder(arg);if(v==="buy")drawBuy(arg);
  if(v==="query")setTimeout(()=>$("g-code").focus(),50);
  if(v==="auth"&&me)go("my");
}
window.addEventListener("hashchange",()=>{const parts=(location.hash.slice(1)||"home").split("/");render(parts[0],parts[1])});

async function boot(){try{const b=await api("/api/v1/me");me=b.user}catch(e){me=null}
  $("authBtn").style.display=me?"none":"";$("outBtn").style.display=me?"":"none";
  $("who").textContent=me?"👋 "+me.username:"未登录";
  try{const p=await api("/api/v1/products");products=p.products||[]}catch(e){products=[]}
  const parts=(location.hash.slice(1)||"home").split("/");render(parts[0],parts[1])}

/* ---- 首页橱窗 ---- */
async function drawHome(){
  $("productCards").innerHTML=products.length?products.map(p=>
    '<div class="card col glow" style="min-height:190px;display:flex;flex-direction:column">'+
    '<div class="avatar">'+(PICON[p.platform]||"⚙")+'</div><h3>'+esc(p.name)+'</h3>'+
    '<p class="text-secondary small" style="flex:1">'+esc(p.description||"")+'</p>'+
    '<div style="display:flex;align-items:center;justify-content:space-between;margin-top:10px">'+
    '<span class="badge b-green">'+esc(p.platform)+'</span>'+
    '<button class="btn sm" onclick="go(\\'buy\\',\\''+esc(p.code)+'\\')">立即下单</button></div></div>').join("")
    :'<div class="card col muted">暂无在售商品（管理端自助上架未开放）</div>';
  if(me){try{const b=await api("/api/v1/my/orders");const L=b.orders||[];
    const nAll=L.length,nDone=L.filter(o=>["succeeded","done"].includes(o.status)).length,
          nRun=L.filter(o=>["pending","processing","leased","running","retry_wait"].includes(o.status)).length;
    $("statCards").style.display="";$("statCards").innerHTML=
      stat("累计订单",nAll)+stat("已完成",nDone,"color:#6cd982")+stat("排队/执行中",nRun,"color:#7cb8ec")+stat("在售商品",products.length);
  }catch(e){$("statCards").style.display="none"}}else $("statCards").style.display="none";
  function stat(l,n,c){return '<div class="card sm glow"><div class="subheader">'+l+'</div><div class="h1" style="'+(c||"")+'">'+n+'</div></div>'}}

/* ---- 登录注册 ---- */
async function doRegister(){try{await api("/api/v1/auth/register",{method:"POST",body:JSON.stringify({username:$("lg-user").value.trim(),password:$("lg-pass").value})});toast("注册成功，自动登录中");await doLogin(true)}catch(e){toast("注册失败："+e.message)}}
async function doLogin(silent){try{await api("/api/v1/auth/login",{method:"POST",body:JSON.stringify({username:$("lg-user").value.trim(),password:$("lg-pass").value})});await boot();go("my");if(!silent)toast("登录成功")}catch(e){if(!silent)toast("登录失败："+e.message)}}
async function logout(){try{await api("/api/v1/auth/logout",{method:"POST"})}catch(e){}me=null;go("home");boot()}

/* ---- 下单向导（三步：商品 → 账号密码+查课选课 → 提交；课表经本机 Executor 真实查询） ---- */
let buyCourses = []; // 查课结果缓存 [{id,name}]

function drawBuy(code){
  const p=products.find(x=>x.code===code);
  buyCourses=[];
  $("v-buy").innerHTML='<h3 style="margin:20px 0 6px"><span class="wiz-num on">1</span>确认商品'+
    '<span class="wiz-num" style="margin-left:16px">2</span>账号密码 & 查课选课'+
    '<span class="wiz-num" style="margin-left:16px">3</span>提交</h3>'+
    (p?'<div class="card"><div class="avatar">'+(PICON[p.platform]||"⚙")+'</div><h3>'+esc(p.name)+'</h3>'+
      '<p class="text-secondary small">'+esc(p.description||"")+'</p>'+
      '<label>网课账号（手机号/学号）</label><input id="bw-acc" placeholder="13800000000">'+
      '<label>密码（enc-v2 加密托管，执行期才按租约解封）</label><input id="bw-pass" type="password">'+
      '<div style="display:flex;gap:10px;margin-top:12px;align-items:center">'+
        '<button class="btn out" id="bw-qbtn" onclick="queryCourses(\\''+esc(p.code)+'\\')">🔍 查询课表（本机真实登录）</button>'+
        '<span class="small muted" id="bw-qstat"></span></div>'+
      '<div id="bw-courses"></div>'+
      '<label>执行路径</label><select id="bw-path"><option value="local">local（本机真实引擎）</option><option value="internal">internal（云端沙箱）</option></select>'+
      '<button class="btn" style="margin-top:14px" id="bw-btn" onclick="buySubmit(\\''+esc(p.code)+'\\')">提交订单</button></div>'
     :'<div class="card muted">未找到商品 '+esc(code||"")+'。'+(products.length?'可选：<a onclick="go(\\'home\\')" style="cursor:pointer">回首页</a>':'商品未上架。')+'</div>');
}

async function queryCourses(code){
  const acc=$("bw-acc").value.trim(),pass=$("bw-pass").value,path=$("bw-path").value;
  if(!acc||!pass)return toast("请先填写账号和密码");
  const stat=$("bw-qstat");stat.textContent="创建订单并入队查询…";
  try{
    // 先建订单（幂等键），凭据 enc-v2 随查课请求加密入库
    const o=await api("/api/v1/orders",{method:"POST",headers:{"Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({product_code:code,platform:"chaoxing",account:acc})});
    stat.textContent="任务已入队，本机 Executor 真实登录查询中（约 30-90 秒）…";
    const q=await api("/api/v1/orders/"+o.order_id+"/query-courses",{method:"POST",body:JSON.stringify({account:acc,password:pass,platform:"chaoxing",execution_path:path})});
    // 轮询任务直至终态
    let task=null;
    for(let i=0;i<60;i++){await new Promise(r=>setTimeout(r,3000));
      const d=await api("/api/v1/tasks/"+q.task_id);task=d.task;
      if(["succeeded","failed","canceled"].includes(task.status))break;}
    if(!task||task.status!=="succeeded"){stat.textContent="查询失败："+(task&&task.error_code||"超时/引擎未在线");return}
    // 取 result_json 工件
    const d2=await api("/api/v1/tasks/"+q.task_id);
    const art=(d2.artifacts||[]).find(a=>a.artifact_type==="result_json");
    if(!art){stat.textContent="查询完成但无课程结果";return}
    const r=await fetch("/api/v1/orders/"+o.order_id+"/artifacts/"+art.id);
    const res=await r.json();
    buyCourses=(res&&res.courses)||[];
    window._buyOrder=o.order_id;window._buyAcc=acc;
    stat.textContent="查询到 "+buyCourses.length+" 门课程（订单 "+o.order_id.slice(0,8)+" 已创建，勾选后提交即入队）";
    $("bw-courses").innerHTML=buyCourses.length?'<label>勾选要刷的课程</label><div class="card sm" style="max-height:280px;overflow:auto">'+
      buyCourses.map((c,i)=>'<div style="padding:4px 2px"><label style="display:flex;gap:8px;align-items:center;margin:0;color:var(--tx)">'+
      '<input type="checkbox" class="bw-c" style="width:auto" value="'+esc(String(c.id??c.course_id??c))+'"'+(i===0?" checked":"")+'> '+esc(c.name??c.title??String(c))+'</label></div>').join("")+'</div>'
      :'<p class="small muted">课程列表为空（可能全部已完成）</p>';
  }catch(e){stat.textContent="查询失败："+e.message}
}

async function buySubmit(code){
  const acc=$("bw-acc").value.trim(),path=$("bw-path").value;
  if(!acc)return toast("请填写账号");
  const btn=$("bw-btn");btn.disabled=true;
  try{
    let oid=window._buyOrder;
    if(!oid){const o=await api("/api/v1/orders",{method:"POST",headers:{"Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({product_code:code,platform:"chaoxing",account:acc})});oid=o.order_id}
    // 收集勾选课程 → 入队 chaoxing.run
    const sel=[...document.querySelectorAll(".bw-c:checked")].map(x=>x.value);
    let tmsg="（未选课程，稍后可从详情页入队）";
    if(sel.length){const t=await api("/api/v1/orders/"+oid+"/tasks",{method:"POST",body:JSON.stringify({execution_path:path,task_type:"chaoxing.run",required_capabilities:["chaoxing"],payload:{courses:sel.join(","),speed:2.0,timeout_seconds:1800}})});tmsg=" 任务已入队："+t.task_id.slice(0,8)}
    window._buyOrder=null;buyCourses=[];
    toast("订单 "+oid.slice(0,8)+" 已提交。"+tmsg);go("order",oid);
  }catch(e){toast("下单失败："+e.message);btn.disabled=false}}

/* ---- 我的订单（按账号分组，仿本地） ---- */
async function drawMy(){if(!me)return;
  try{const b=await api("/api/v1/my/orders");const L=b.orders||[];
  if(!L.length){$("myOrders").innerHTML="";$("emptyMy").innerHTML='<div class="empty"><div class="empty-header">📭</div><p class="empty-title">还没有订单</p><p class="empty-subtitle muted">选择一个商品，立即开始自动化</p><button class="btn" onclick="go(\\'home\\')">去下单</button></div>';return}
  $("emptyMy").innerHTML="";
  const groups={};L.forEach(o=>{(groups[o.account||"(未填账号)"]=groups[o.account||"(未填账号)"]||[]).push(o)});
  let rows="";
  for(const acc of Object.keys(groups)){const items=groups[acc];
    const row=o=>'<tr><td class="mono muted">'+esc(o.order_id.slice(0,8))+'</td>'+
      '<td>'+(PICON[o.platform]||"⚙")+' '+esc(o.product_code)+'</td>'+
      '<td>'+esc(o.account||"—")+'</td><td>'+badge(o.status)+'</td>'+
      '<td class="small text-secondary">'+fmt(o.created_at)+'<br>→ '+fmt(o.updated_at)+'</td>'+
      '<td><button class="btn out sm" onclick="go(\\'order\\',\\''+o.order_id+'\\')">详情</button></td></tr>';
    if(items.length===1)rows+=row(items[0]);
    else{const doneN=items.filter(o=>["succeeded","done"].includes(o.status)).length;
      rows+='<tr><td colspan="6" style="padding:0"><details class="acc"><summary><b>'+(PICON[items[0].platform]||"⚙")+' '+esc(acc)+
        '</b> <span class="badge b-blue">'+items.length+' 单</span> <span class="badge b-green">'+doneN+' 完成</span>'+
        '<span class="small muted" style="margin-left:8px">点击展开</span></summary>'+
        '<div style="padding:0 6px 6px"><table>'+items.map(row).join("")+'</table></div></details></tr>'}}
  $("myOrders").innerHTML=rows}catch(e){}}

/* ---- 订单详情（含 stage-cloud-28 控制：暂停/恢复/优先级 + 凭据明文 + 工件下载） ---- */
async function drawOrder(id){currentOrder=id;
  try{const b=await api("/api/v1/orders/"+id);const o=b.order||{};const at=b.attempts||[];
  const running=["processing","pending"].includes(o.status);
  const paused=o.control==="paused";
  let html='<h3 style="margin:20px 0 6px">📄 订单 <span class="mono">'+esc(o.order_id)+'</span> '+badge(o.status)+
    (paused?' <span class="badge b-orange">已暂停</span>':'')+
    (o.priority>0?' <span class="badge b-blue">优先级 '+o.priority+'</span>':'')+'</h3>';
  html+='<div class="card"><p class="small muted">商品 '+esc(o.product_code)+' ｜ 平台 '+esc(o.platform)+' ｜ 账号 '+esc(o.account||"(托管)")+
   ' ｜ 创建 '+fmt(o.created_at)+' ｜ 更新 '+fmt(o.updated_at)+'</p>';
  html+='<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">';
  if(running)html+=paused
    ?'<button class="btn sm" onclick="ctlOrder(\\'resume\\')">▶ 恢复执行</button>'
    :'<button class="btn out sm" onclick="ctlOrder(\\'pause\\')">⏸ 暂停（挂起引擎进程，进度保留）</button>';
  if(running)html+='<button class="btn out sm" onclick="ctlOrder(\\'priority\\',9)">⏫ 插队（优先级 9）</button>';
  html+='<button class="btn out sm" onclick="showCred()">🔐 查看 / 显示凭据明文（仅本人）</button>';
  html+='</div><p class="small muted" id="credBox" style="margin-top:8px"></p>';
  html+='<h3 style="margin:14px 0 8px">执行记录</h3>'+
   (at.length?'<table><thead><tr><th>#</th><th>状态</th><th>错误码</th><th>开始</th><th>结束</th></tr></thead><tbody>'+
     at.map(a=>'<tr><td class="mono">'+a.attempt_no+'</td><td>'+badge(a.status)+'</td><td class="mono">'+esc(a.error_code||"—")+
     '</td><td class="small text-secondary">'+fmt(a.started_at)+'</td><td class="small text-secondary">'+fmt(a.finished_at)+'</td></tr>').join("")+'</tbody></table>'
    :'<p class="muted">暂无执行记录</p>');
  html+='<div id="artBox"></div>';
  html+=(running?'<details style="margin-top:12px" open><summary style="cursor:pointer;color:#22d3ee">➕ 入队任务</summary>'+
     '<label>任务类型</label><select id="t-type"><option value="chaoxing.run">chaoxing.run（超星刷课）</option><option value="demo.echo">demo.echo（连通性测试）</option></select>'+
     '<label>执行路径</label><select id="t-path"><option value="local">local（本机真实引擎）</option><option value="internal">internal（云端沙箱）</option></select>'+
     '<label>Payload（JSON）</label><textarea id="t-payload" rows="4" class="mono">{"courses":"254722149","speed":2.0,"timeout_seconds":1800}</textarea>'+
     '<button class="btn" style="margin-top:12px" onclick="enqueue()">入队</button></details>'
    :'<p class="small muted" style="margin-top:12px">订单已终态（不可再入队）。</p>')+
   '<div style="margin-top:16px"><button class="btn out sm" onclick="go(\\'my\\')">← 返回我的订单</button></div></div>';
  $("v-order").innerHTML=html;
  loadArtifacts(id);
  }catch(e){$("v-order").innerHTML='<div class="card muted" style="margin-top:20px">加载失败：'+esc(e.message)+'</div>'}}

async function ctlOrder(action,priority){
  try{const body=priority?{action,priority}:{action};
    const b=await api("/api/v1/orders/"+currentOrder+"/control",{method:"POST",body:JSON.stringify(body)});
    toast(action==="pause"?"已暂停（引擎进程挂起中）":action==="resume"?"已恢复执行":"优先级已更新（排队任务热生效）");
    drawOrder(currentOrder)}catch(e){toast("操作失败："+e.message)}}

async function showCred(){
  try{const c=await api("/api/v1/orders/"+currentOrder+"/credentials");
    $("credBox").innerHTML='账号 <span class="mono">'+esc(c.account||"—")+'</span> ｜ 密码 <span class="mono">'+esc(c.password||"—（未托管）")+'</span>'+
      ' <span class="small muted">（CREDENTIAL_VIEWED 已审计；仅在您本人查看时解密）</span>'}
  catch(e){$("credBox").textContent="凭据查看失败："+e.message}}

async function loadArtifacts(oid){
  try{
    const b=await api("/api/v1/orders/"+oid);
    const at=b.attempts||[];if(!at.length)return;
    const tids=[...new Set(at.map(a=>String(a.id||"").split("#")[0]).filter(Boolean))];
    let html="";
    for(const tid of tids){
      const d=await api("/api/v1/tasks/"+tid);
      const arts=(d.artifacts||[]).filter(a=>a.artifact_type==="stdout"||a.artifact_type==="result_json");
      if(!arts.length)continue;
      html+='<h3 style="margin:14px 0 8px">日志 / 结果工件</h3><table><thead><tr><th>类型</th><th>大小</th><th>时间</th><th></th></tr></thead><tbody>'+
        arts.map(a=>'<tr><td class="mono">'+esc(a.artifact_type)+'</td><td class="small text-secondary">'+(a.size_bytes||0)+' B</td>'+
        '<td class="small text-secondary">'+fmt(a.created_at)+'</td>'+
        '<td><a class="btn out sm" style="text-decoration:none" href="/api/v1/orders/'+oid+'/artifacts/'+a.id+'" target="_blank">下载</a></td></tr>').join("")+'</tbody></table>';
    }
    const box=$("artBox");if(box)box.innerHTML=html;
  }catch(e){}}
async function enqueue(){try{let payload;try{payload=JSON.parse($("t-payload").value)}catch(e){throw new Error("payload 不是合法 JSON")}
  const b=await api("/api/v1/orders/"+currentOrder+"/tasks",{method:"POST",body:JSON.stringify({execution_path:$("t-path").value,task_type:$("t-type").value,required_capabilities:[$("t-type").value.split(".")[0]],payload})});
  toast("任务已入队："+b.task_id.slice(0,8));drawOrder(currentOrder)}catch(e){toast("入队失败："+e.message)}}

/* ---- 查单 ---- */
async function guestQuery(){const code=$("g-code").value.trim();if(!code)return toast("请输入订单号");
  try{const b=await api("/api/v1/guest/query",{method:"POST",body:JSON.stringify({code})});const L=b.orders||[];
  $("g-res").innerHTML=L.length?L.map(o=>'<tr><td class="mono">'+esc(o.order_id_prefix||"")+'…</td><td>'+esc(o.product_code)+'</td><td>'+badge(o.status)+'</td><td class="small text-secondary">'+fmt(o.updated_at)+'</td></tr>').join("")
    :'<tr><td colspan="4" class="muted">无匹配订单</td></tr>'}catch(e){toast("查询失败："+e.message)}}

/* ---- 批量下单 ---- */
async function batchSubmit(){const btn=$("b-btn");btn.disabled=true;const out=$("b-out");out.style.display="";out.textContent="";
  const lines=$("b-lines").value.trim().split("\\n").filter(x=>x.trim());let okN=0;
  for(const ln of lines){const parts=ln.split(",").map(x=>(x||"").trim());const plat=parts[0],acc=parts[1],courses=parts[2];
    if(!plat||!acc){out.textContent+="跳过（格式：平台,账号,课程ID）："+ln+"\\n";continue}
    try{const o=await api("/api/v1/orders",{method:"POST",headers:{"Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({product_code:"E2E",platform:plat.toLowerCase(),account:acc})});
      if(courses){await api("/api/v1/orders/"+o.order_id+"/tasks",{method:"POST",body:JSON.stringify({execution_path:$("b-path").value,task_type:"chaoxing.run",required_capabilities:["chaoxing"],payload:{courses:courses,speed:2.0,timeout_seconds:1800}})})}
      okN++;out.textContent+="OK "+acc+" -> "+o.order_id+"\\n"}
    catch(e){out.textContent+="FAIL "+acc+"："+e.message+"\\n"}}
  out.textContent+="\\n已受理 "+okN+"/"+lines.length+" 单";toast("批量提交完成："+okN+" 单");btn.disabled=false}

boot();
</script>
</body></html>`;

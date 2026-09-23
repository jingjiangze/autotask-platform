/**
 * stage-cloud-27 — Worker 托管前端（单文件 SPA，零外部依赖）。
 *
 * 由 index.ts 对所有非 /api、非 /health 的 GET 请求返回。
 * 调用面：auth register/login/logout、me、products、my/orders、
 *         orders/{id}、orders/{id}/tasks、guest/query（§ 路由契约见 router.ts）。
 */
export const APP_HTML = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>自动任务平台 · Autotask Central</title>
<style>
  :root{--bg:#f5f7fa;--card:#fff;--line:#e4e8ee;--tx:#1f2937;--mut:#6b7280;--pri:#2563eb;--ok:#16a34a;--warn:#d97706;--err:#dc2626}
  *{box-sizing:border-box;margin:0;padding:0}
  body{font:15px/1.6 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--tx)}
  header{background:var(--card);border-bottom:1px solid var(--line);padding:14px 22px;display:flex;align-items:center;gap:18px;position:sticky;top:0;z-index:5}
  header h1{font-size:18px;font-weight:700}
  header .sp{flex:1}
  .who{color:var(--mut);font-size:14px}
  main{max-width:980px;margin:22px auto;padding:0 16px}
  .tabs{display:flex;gap:8px;margin-bottom:16px}
  .tabs button{border:1px solid var(--line);background:var(--card);padding:8px 18px;border-radius:8px;cursor:pointer;font-size:15px}
  .tabs button.on{background:var(--pri);color:#fff;border-color:var(--pri)}
  .card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin-bottom:16px}
  .card h2{font-size:16px;margin-bottom:10px}
  input,select,textarea{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:8px;font:inherit;background:#fff}
  label{display:block;font-size:13px;color:var(--mut);margin:10px 0 4px}
  .row{display:flex;gap:10px}.row>*{flex:1}
  button.pri{background:var(--pri);color:#fff;border:0;border-radius:8px;padding:9px 18px;cursor:pointer;font:inherit}
  button.pri:disabled{opacity:.55;cursor:default}
  button.ghost{background:transparent;border:1px solid var(--line);border-radius:8px;padding:8px 14px;cursor:pointer;font:inherit}
  table{width:100%;border-collapse:collapse;font-size:14px}
  th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line)}
  th{color:var(--mut);font-weight:600;font-size:13px}
  tr.click{cursor:pointer}tr.click:hover{background:#f0f5ff}
  .st{display:inline-block;padding:2px 10px;border-radius:99px;font-size:12.5px}
  .st.pending{background:#fef3c7;color:#92400e}.st.processing{background:#dbeafe;color:#1d4ed8}
  .st.succeeded{background:#dcfce7;color:#166534}.st.failed,.st.canceled{background:#fee2e2;color:#991b1b}
  .st.leased,.st.running{background:#e0e7ff;color:#3730a3}.st.retry_wait{background:#ffedd5;color:#9a3412}
  .toast{position:fixed;bottom:22px;left:50%;transform:translateX(-50%);background:#111827;color:#fff;padding:10px 20px;border-radius:8px;font-size:14px;opacity:0;transition:.25s;pointer-events:none;max-width:80vw;z-index:9}
  .toast.show{opacity:1}
  .mono{font-family:ui-monospace,Consolas,monospace;font-size:13px}
  .muted{color:var(--mut);font-size:13px}
  pre{background:#0f172a;color:#e2e8f0;padding:12px;border-radius:8px;overflow:auto;font-size:12.5px}
  details summary{cursor:pointer;color:var(--pri);font-size:14px}
</style>
</head>
<body>
<header>
  <h1>⚙️ 自动任务平台</h1>
  <span class="who" id="who"></span>
  <span class="sp"></span>
  <button class="ghost" id="authBtn" onclick="openAuth()">登录 / 注册</button>
  <button class="ghost" id="outBtn" style="display:none" onclick="logout()">退出</button>
</header>
<main>
  <div class="tabs">
    <button class="on" id="tab-o" onclick="tab('o')">我的订单</button>
    <button id="tab-g" onclick="tab('g')">访客查单</button>
  </div>

  <section id="view-o">
    <div class="card" id="authCard" style="display:none">
      <h2>登录已有账号，或注册新账号</h2>
      <div class="row">
        <div><label>用户名</label><input id="lg-user" autocomplete="username"></div>
        <div><label>密码（≥8 位）</label><input id="lg-pass" type="password" autocomplete="current-password"></div>
      </div>
      <div style="margin-top:14px;display:flex;gap:10px">
        <button class="pri" onclick="login()">登录</button>
        <button class="ghost" onclick="register()">注册</button>
      </div>
      <p class="muted" style="margin-top:10px">登录后可下单、入队任务并查看执行结果与日志。</p>
    </div>

    <div id="userArea" style="display:none">
      <div class="card">
        <h2>新建订单</h2>
        <div class="row">
          <div><label>商品（来自商品表）</label><select id="no-product"></select></div>
          <div><label>执行平台</label><select id="no-platform"><option value="chaoxing">超星 chaoxing</option><option value="xuexi">学习通</option><option value="other">其他</option></select></div>
        </div>
        <label>网课账号（选填，凭据经管理员加密录入）</label>
        <input id="no-account" placeholder="账号或手机号">
        <div style="margin-top:14px"><button class="pri" id="createBtn" onclick="createOrder()">创建订单</button></div>
      </div>

      <div class="card">
        <div style="display:flex;align-items:center">
          <h2 style="flex:1">我的订单</h2>
          <button class="ghost" onclick="loadOrders()">刷新</button>
        </div>
        <table><thead><tr><th>订单号</th><th>商品</th><th>平台</th><th>状态</th><th>更新时间</th></tr></thead>
        <tbody id="orders"><tr><td colspan="5" class="muted">加载中…</td></tr></tbody></table>
      </div>

      <div class="card" id="detail" style="display:none"></div>
    </div>
  </section>

  <section id="view-g" style="display:none">
    <div class="card">
      <h2>访客查单</h2>
      <p class="muted" style="margin-bottom:8px">输入订单号（支持前缀），无需登录。</p>
      <div class="row">
        <div><input id="g-code" placeholder="订单号或前缀，如 cloud-real" class="mono"></div>
        <div style="flex:0 0 auto"><button class="pri" onclick="guestQuery()">查询</button></div>
      </div>
      <table style="margin-top:12px"><thead><tr><th>订单号</th><th>商品</th><th>状态</th><th>更新时间</th></tr></thead>
      <tbody id="g-res"><tr><td colspan="4" class="muted">—</td></tr></tbody></table>
    </div>
  </section>
</main>
<div class="toast" id="toast"></div>
<script>
const $=id=>document.getElementById(id);
const stLabel=s=>'<span class="st '+s+'">'+s+'</span>';
const fmt=t=>t?new Date(t).toLocaleString('zh-CN',{hour12:false}):'—';
let me=null, pollTimer=null, currentOrder=null;

function toast(m){const t=$('toast');t.textContent=m;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2600)}
async function api(path,opt={}){opt.headers=Object.assign({'Content-Type':'application/json'},opt.headers||{});
  const r=await fetch(path,opt);let b={};try{b=await r.json()}catch(e){}
  if(!r.ok)throw new Error((b.error&&b.error.message?b.error.message:'HTTP '+r.status));
  return b}
function tab(k){for(const x of['o','g']){$('tab-'+x).classList.toggle('on',x===k);$('view-'+x).style.display=x===k?'':'none'}}

async function boot(){try{const b=await api('/api/v1/me');me=b.user||b}catch(e){me=null}
  renderAuth();
  if(me){$('userArea').style.display='';loadProducts();loadOrders();startPoll()}
  else{$('authCard').style.display=''}}
function renderAuth(){if(me){$('who').textContent='已登录：'+me.username;$('authBtn').style.display='none';$('outBtn').style.display='';$('authCard').style.display='none';$('userArea').style.display=''}
  else{$('who').textContent='未登录';$('authBtn').style.display='';$('outBtn').style.display='none';$('authCard').style.display='';$('userArea').style.display='none'}}

async function register(){try{await api('/api/v1/auth/register',{method:'POST',body:JSON.stringify({username:$('lg-user').value.trim(),password:$('lg-pass').value})});toast('注册成功，自动登录中');await login(true)}catch(e){toast('注册失败：'+e.message)}}
async function login(silent){try{await api('/api/v1/auth/login',{method:'POST',body:JSON.stringify({username:$('lg-user').value.trim(),password:$('lg-pass').value})});await boot();if(!silent)toast('登录成功')}catch(e){if(!silent)toast('登录失败：'+e.message)}}
async function logout(){try{await api('/api/v1/auth/logout',{method:'POST'})}catch(e){}me=null;renderAuth();toast('已退出')}

async function loadProducts(){try{const b=await api('/api/v1/products');const sel=$('no-product');sel.innerHTML='';
  (b.products||[]).forEach(p=>{const o=document.createElement('option');o.value=p.code;o.textContent=p.name+' ('+p.code+')';sel.appendChild(o)});
  if(!sel.children.length){const o=document.createElement('option');o.textContent='（暂无启用商品）';sel.appendChild(o)}}catch(e){}}
async function createOrder(){const btn=$('createBtn');btn.disabled=true;
  try{const b=await api('/api/v1/orders',{method:'POST',body:JSON.stringify({product_code:$('no-product').value,platform:$('no-platform').value,account:$('no-account').value.trim()})});
  toast('订单已创建：'+b.order_id);loadOrders()}catch(e){toast('创建失败：'+e.message)}finally{btn.disabled=false}}

async function loadOrders(){if(!me)return;try{const b=await api('/api/v1/my/orders');
  const tb=$('orders');const list=b.orders||[];
  tb.innerHTML=list.length?list.map(o=>'<tr class="click" onclick="showDetail(\\''+o.order_id+'\\')"><td class="mono">'+o.order_id+'</td><td>'+o.product_code+'</td><td>'+o.platform+'</td><td>'+stLabel(o.status)+'</td><td class="muted">'+fmt(o.updated_at)+'</td></tr>').join('')
    :'<tr><td colspan="5" class="muted">暂无订单 —— 先在上方创建一个</td></tr>'}catch(e){}}
function startPoll(){clearInterval(pollTimer);pollTimer=setInterval(()=>{if(me)loadOrders();if(currentOrder)refreshDetail(false)},5000)}

async function showDetail(id){currentOrder=id;$('detail').style.display='';await refreshDetail(true)}
async function refreshDetail(full){try{const b=await api('/api/v1/orders/'+currentOrder);const o=b.order||{};const at=b.attempts||[];
  let html='<h2>订单详情 <span class="mono muted">'+currentOrder+'</span> '+stLabel(o.status)+'</h2>';
  html+='<p class="muted">商品 '+o.product_code+' ｜ 平台 '+o.platform+' ｜ 账号 '+(o.account||'（托管）')+' ｜ 创建 '+fmt(o.created_at)+' ｜ 更新 '+fmt(o.updated_at)+'</p>';
  html+='<h2 style="margin-top:14px">执行记录</h2>';
  html+=at.length?'<table><thead><tr><th>Attempt</th><th>状态</th><th>错误码</th><th>开始</th><th>结束</th></tr></thead><tbody>'
    +at.map(a=>'<tr><td class="mono">#'+a.attempt_no+'</td><td>'+stLabel(a.status)+'</td><td class="mono">'+(a.error_code||'—')+'</td><td class="muted">'+fmt(a.started_at)+'</td><td class="muted">'+fmt(a.finished_at)+'</td></tr>').join('')+'</tbody></table>'
    :'<p class="muted">暂无执行记录 —— 入队一个任务开始执行</p>';
  const canEnqueue=['pending','processing'].includes(o.status);
  html+='<details style="margin-top:14px"'+(canEnqueue?' open':'')+'><summary>'+(canEnqueue?'入队任务':'（订单已终态，不能再入队）')+'</summary>';
  if(canEnqueue){html+='<label>任务类型</label><select id="t-type"><option value="chaoxing.run">chaoxing.run（超星刷课）</option><option value="demo.echo">demo.echo（连通性测试）</option></select>';
    html+='<label>执行路径</label><select id="t-path"><option value="local">local（本机真实引擎）</option><option value="internal">internal（云端沙箱）</option></select>';
    html+='<label>Payload（JSON）</label><textarea id="t-payload" rows="4" class="mono">{"courses":"254722149","speed":2.0,"timeout_seconds":600}</textarea>';
    html+='<div style="margin-top:10px"><button class="pri" onclick="enqueue()">入队</button></div>'}
  html+='</details>';
  $('detail').innerHTML=html}catch(e){if(full)toast('详情加载失败：'+e.message)}}
async function enqueue(){try{let payload;try{payload=JSON.parse($('t-payload').value)}catch(e){throw new Error('payload 不是合法 JSON')}
  const b=await api('/api/v1/orders/'+currentOrder+'/tasks',{method:'POST',body:JSON.stringify({execution_path:$('t-path').value,task_type:$('t-type').value,required_capabilities:[$('t-type').value.split('.')[0]],payload})});
  toast('任务已入队：'+b.task_id);refreshDetail(false)}catch(e){toast('入队失败：'+e.message)}}

async function guestQuery(){const code=$('g-code').value.trim();if(!code)return toast('请输入订单号');
  try{const b=await api('/api/v1/guest/query',{method:'POST',body:JSON.stringify({code})});
  const list=b.orders||b.results||[];
  $('g-res').innerHTML=list.length?list.map(o=>'<tr><td class="mono">'+(o.order_id_prefix||o.order_id||'')+'…</td><td>'+o.product_code+'</td><td>'+stLabel(o.status)+'</td><td class="muted">'+fmt(o.updated_at)+'</td></tr>').join('')
    :'<tr><td colspan="4" class="muted">无匹配订单（仅返回前缀匹配的非敏感字段）</td></tr>'}catch(e){toast('查询失败：'+e.message)}}

boot();
</script>
</body>
</html>`;

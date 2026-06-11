// 殿御命 2026-06-04 cmd_478: 全 page 共通 SSE 受信 + Push onboarding (D 案)
(function(){
  if (window._scoreNotifClientLoaded) return;
  window._scoreNotifClientLoaded = true;

  function _log(...a){ try{ console.log('[score-notif]', ...a);}catch(e){} }
  function _safeOrigin(){ return location.protocol === 'https:' || location.hostname === 'localhost' || location.hostname === '127.0.0.1'; }

  // ===== Toast 表示 (SSE 受信時) =====
  let _toastContainer = null;
  function _ensureToastContainer(){
    if (_toastContainer) return _toastContainer;
    _toastContainer = document.createElement('div');
    _toastContainer.id = 'score-toast-container';
    _toastContainer.style.cssText = 'position:fixed!important;bottom:20px!important;right:20px!important;z-index:2147483647!important;display:flex!important;flex-direction:column;gap:8px;max-width:380px;pointer-events:none;';
    document.body.appendChild(_toastContainer);
    return _toastContainer;
  }
  function _showToast(title, body, url){
    console.warn('[score-notif] _showToast called:', title, body);
    const c = _ensureToastContainer();
    const t = document.createElement('div');
    t.style.cssText = 'background:white!important;border-left:6px solid #6366f1!important;border-radius:12px;padding:14px 18px;box-shadow:0 8px 32px rgba(0,0,0,0.25)!important;cursor:pointer;font-family:system-ui,sans-serif;animation:scoreToastIn .25s ease;min-width:280px;pointer-events:auto;color:#1e293b!important;';
    t.innerHTML = `<div style="font-weight:900;font-size:13px;color:#1e293b;margin-bottom:4px;">${_esc(title||'通知')}</div><div style="font-size:11px;color:#64748b;white-space:pre-wrap;max-height:60px;overflow:hidden;">${_esc((body||'').slice(0,200))}</div>`;
    if (url) t.onclick = () => { window.location.href = url; };
    c.appendChild(t);
    setTimeout(() => { t.style.opacity = '0'; t.style.transition='opacity .3s'; setTimeout(()=>t.remove(), 350); }, 8000);
  }
  // 殿御命 2026-06-04: window 公開 (手動 console テスト用)
  window._showScoreToast = _showToast;
  function _esc(s){ return String(s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

  // ===== SSE 接続 =====
  let _es = null;
  async function _connectSSE(){
    if (_es) return;
    try {
      const prefs = await _loadPrefs();
      if (!(prefs.channels && prefs.channels.sse)) { _log('SSE disabled by user prefs'); return; }
      _es = new EventSource('/api/bff/notifications/stream');
      _es.addEventListener('hello', (e) => console.warn('[score-notif] SSE hello:', e.data));
      _es.addEventListener('notif', (e) => {
        console.warn('[score-notif] SSE notif event received:', e.data);
        try {
          const d = JSON.parse(e.data);
          _showToast(d.title, d.body, d.url);
        } catch (err) { console.error('[score-notif] SSE parse err:', err, e.data); }
      });
      // generic message fallback (event 名が default の場合)
      _es.onmessage = (e) => console.warn('[score-notif] SSE generic message:', e.data);
      _es.onerror = (ev) => {
        _log('SSE error · readyState=' + _es.readyState);
        if (_es.readyState === EventSource.CLOSED) {
          _es = null;
          setTimeout(_connectSSE, 5000);  // 自動再接続
        }
      };
    } catch (e) { _log('SSE connect exception:', e); }
  }

  async function _loadPrefs(){
    try {
      const r = await fetch('/api/bff/notif/prefs', {credentials:'include'});
      if (r.ok) return await r.json();
    } catch (e) {}
    return { channels: { push: true, sse: true, badge: true } };
  }

  // ===== Push onboarding banner =====
  function _showOnboardBanner(){
    if (localStorage.getItem('score_push_onboarded') === 'dismissed') return;
    if (!_safeOrigin()) return;
    if (Notification.permission !== 'default') return;
    const b = document.createElement('div');
    b.id = 'score-push-onboard';
    b.style.cssText = 'position:fixed;top:0;left:0;right:0;background:linear-gradient(90deg,#2563eb,#1d4ed8);color:white;padding:10px 16px;display:flex;align-items:center;justify-content:center;gap:16px;z-index:9998;font-family:\'Outfit\',\'Noto Sans JP\',system-ui,sans-serif;font-size:13px;font-weight:bold;box-shadow:0 2px 8px rgba(0,0,0,0.15);';
    b.innerHTML = '<span>🔔 重要通知を OS で受け取る</span><button id="score-push-yes" style="background:white;color:#2563eb;border:none;padding:6px 14px;border-radius:8px;font-weight:900;cursor:pointer;">有効化</button><button id="score-push-no" style="background:rgba(255,255,255,0.2);color:white;border:none;padding:6px 12px;border-radius:8px;cursor:pointer;">あとで</button>';
    document.body.appendChild(b);
    document.getElementById('score-push-yes').onclick = async () => {
      if (window.scorePushEnable) {
        const ok = await window.scorePushEnable();
        if (ok) { localStorage.setItem('score_push_onboarded', 'dismissed'); b.remove(); }
      } else { alert('push-register.js 未ロード'); }
    };
    document.getElementById('score-push-no').onclick = () => {
      localStorage.setItem('score_push_onboarded', 'dismissed'); b.remove();
    };
  }

  // ===== granted user は silent re-subscribe =====
  async function _silentResub(){
    if (!_safeOrigin()) return;
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
    if (Notification.permission !== 'granted') return;
    try {
      const prefs = await _loadPrefs();
      if (!(prefs.channels && prefs.channels.push)) return;
      const reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
      await navigator.serviceWorker.ready;
      let sub = await reg.pushManager.getSubscription();
      if (!sub) {
        const m = await (await fetch('/api/bff/push/meta')).json();
        if (!m.vapid_public_key) return;
        const k = m.vapid_public_key;
        const padding = '='.repeat((4 - k.length % 4) % 4);
        const b64 = (k + padding).replace(/-/g,'+').replace(/_/g,'/');
        const raw = atob(b64);
        const arr = new Uint8Array(raw.length);
        for (let i=0;i<raw.length;i++) arr[i]=raw.charCodeAt(i);
        sub = await reg.pushManager.subscribe({ userVisibleOnly:true, applicationServerKey: arr });
        await fetch('/api/bff/push/subscribe', {method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify(sub)});
        _log('silent re-subscribed');
      }
    } catch (e) { _log('silent resub exception:', e); }
  }

  // ===== keyframes 注入 =====
  const sty = document.createElement('style');
  sty.textContent = '@keyframes scoreToastIn{from{transform:translateY(20px);opacity:0;}to{transform:translateY(0);opacity:1;}}@keyframes scoreDrawerIn{from{transform:translateX(100%);}to{transform:translateX(0);}}';
  document.head.appendChild(sty);

  // ===== 殿御命 2026-06-05: 共通 Thread Drawer (右からスライドイン) =====
  let _drawerEl = null;
  function _ensureDrawer() {
    if (_drawerEl) return _drawerEl;
    const wrap = document.createElement('div');
    wrap.id = 'score-thread-drawer';
    wrap.style.cssText = 'position:fixed;inset:0;z-index:9997;display:none;';
    wrap.innerHTML = `
      <div id="score-drawer-backdrop" style="position:absolute;inset:0;background:rgba(0,0,0,0.3);backdrop-filter:blur(4px);" onclick="window._closeThreadDrawer()"></div>
      <div id="score-drawer-panel" style="position:absolute;top:0;right:0;width:min(480px,90vw);height:100vh;background:linear-gradient(135deg,#E0F2FE 0%,#F3E8FF 50%,#FCE7F3 100%);box-shadow:-8px 0 32px rgba(0,0,0,0.15);display:flex;flex-direction:column;animation:scoreDrawerIn .25s ease;font-family:system-ui,sans-serif;">
        <div style="padding:16px 20px;border-bottom:1px solid rgba(255,255,255,0.6);background:rgba(255,255,255,0.4);backdrop-filter:blur(12px);display:flex;align-items:center;gap:12px;">
          <div id="score-drawer-icon" style="width:40px;height:40px;border-radius:50%;background:#a78bfa20;display:flex;align-items:center;justify-content:center;font-size:18px;">💬</div>
          <div style="flex:1;min-width:0;">
            <p id="score-drawer-title" style="font-weight:900;color:#1e293b;font-size:14px;margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">thread を読込中...</p>
            <p id="score-drawer-meta" style="font-size:11px;color:#64748b;margin:2px 0 0;"></p>
          </div>
          <a id="score-drawer-fullview" href="#" style="font-size:11px;color:#6366f1;text-decoration:underline;flex-shrink:0;">全画面で開く</a>
          <button onclick="window._closeThreadDrawer()" style="background:none;border:none;font-size:24px;color:#64748b;cursor:pointer;line-height:1;padding:0 4px;">×</button>
        </div>
        <div id="score-drawer-messages" style="flex:1;overflow-y:auto;padding:16px 20px;display:flex;flex-direction:column;gap:12px;"></div>
        <div style="padding:12px 20px;border-top:1px solid rgba(255,255,255,0.6);background:rgba(255,255,255,0.3);">
          <div style="display:flex;gap:8px;">
            <input id="score-drawer-input" type="text" placeholder="メッセージを入力 (Enter で送信)" style="flex:1;background:rgba(255,255,255,0.6);border:1px solid rgba(255,255,255,0.6);border-radius:12px;padding:10px 14px;font-size:13px;outline:none;">
            <button onclick="window._drawerSendMessage()" style="background:#6366f1;color:white;border:none;border-radius:12px;padding:10px 16px;font-weight:bold;font-size:13px;cursor:pointer;">送信</button>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(wrap);
    _drawerEl = wrap;
    // Enter で送信
    const inp = document.getElementById('score-drawer-input');
    if (inp) inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); window._drawerSendMessage(); } });
    return wrap;
  }

  window._openThreadDrawer = async function(threadId) {
    _ensureDrawer();
    document.getElementById('score-thread-drawer').style.display = 'block';
    document.getElementById('score-drawer-title').textContent = 'thread ' + threadId + ' を読込中...';
    document.getElementById('score-drawer-meta').textContent = '';
    document.getElementById('score-drawer-messages').innerHTML = '<p style="text-align:center;color:#94a3b8;font-size:12px;margin-top:24px;">📥 読込中...</p>';
    document.getElementById('score-drawer-fullview').href = '/messages?thread=' + threadId;
    window._drawerCurrentThread = threadId;
    try {
      // thread meta (participants)
      const metaResp = await fetch('/api/bff/dm/threads_meta', { credentials: 'include' });
      let participants = [];
      let lastMsg = '';
      if (metaResp.ok) {
        const metaData = await metaResp.json();
        const t = (metaData.threads || []).find(x => String(x.thread_id) === String(threadId));
        if (t) {
          // 単に thread_id + updated_at しか持たぬので 別経路で詳細取れぬ → messages 取得後 sender_id で推測
        }
      }
      // messages 全件
      const resp = await fetch('/api/bff/dm/threads/' + threadId + '/messages', { credentials: 'include' });
      if (resp.ok) {
        const messages = await resp.json() || [];
        const myCuid = parseInt(document.body.dataset.myCuid || '0', 10);
        const senders = new Set();
        messages.forEach(m => { if (m.sender_id != null) senders.add(parseInt(m.sender_id, 10)); });
        document.getElementById('score-drawer-title').textContent = 'thread ' + threadId;
        document.getElementById('score-drawer-meta').textContent = `${messages.length} 件 · 参加 ${senders.size} 名`;
        const area = document.getElementById('score-drawer-messages');
        area.innerHTML = messages.map(m => {
          const isMe = (m.sender_id != null) && (parseInt(m.sender_id, 10) === myCuid);
          const time = (m.created_at || '').slice(11, 16);
          const date = (m.created_at || '').slice(0, 10);
          return `
            <div style="display:flex;${isMe ? 'justify-content:flex-end' : 'justify-content:flex-start'};">
              <div style="max-width:85%;">
                <p style="font-size:10px;color:#94a3b8;margin:0 0 4px;${isMe ? 'text-align:right' : ''}">uid ${m.sender_id} · ${_esc(date)} ${_esc(time)}</p>
                <div style="background:${isMe ? '#eef2ff' : 'white'};border:1px solid ${isMe ? '#c7d2fe' : '#e2e8f0'};border-radius:12px;${isMe ? 'border-bottom-right-radius:4px' : 'border-bottom-left-radius:4px'};padding:10px 14px;font-size:13px;color:#334155;white-space:pre-wrap;">${_renderDrawerBody(m.body || '')}</div>
              </div>
            </div>`;
        }).join('') || '<p style="text-align:center;color:#94a3b8;">(まだメッセージなし)</p>';
        area.scrollTop = area.scrollHeight;
        // 既読 mark
        fetch('/api/bff/dm/threads/' + threadId + '/read', { method: 'POST', credentials: 'include' }).catch(() => {});
        try { window.dispatchEvent(new CustomEvent('score:dm-read-updated')); } catch(e) {}
      } else {
        document.getElementById('score-drawer-messages').innerHTML = '<p style="text-align:center;color:#dc2626;">取得失敗 HTTP ' + resp.status + '</p>';
      }
    } catch (e) {
      document.getElementById('score-drawer-messages').innerHTML = '<p style="text-align:center;color:#dc2626;">例外: ' + _esc(e.message) + '</p>';
    }
  };

  window._closeThreadDrawer = function() {
    if (_drawerEl) _drawerEl.style.display = 'none';
  };

  window._drawerSendMessage = async function() {
    const inp = document.getElementById('score-drawer-input');
    if (!inp || !window._drawerCurrentThread) return;
    const text = inp.value.trim();
    if (!text) return;
    try {
      const resp = await fetch('/api/bff/dm', {
        method: 'POST', credentials: 'include',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ thread_id: window._drawerCurrentThread, body: text })
      });
      if (resp.ok) {
        inp.value = '';
        // 再 fetch でリスト更新
        window._openThreadDrawer(window._drawerCurrentThread);
      }
    } catch(e) {}
  };

  function _renderDrawerBody(text) {
    const esc = _esc(text);
    const lines = esc.split('\n');
    const urls = [];
    const remaining = [];
    for (const ln of lines) {
      const m = ln.match(/^(https?:\/\/[^\s<]+|\/[\w/?=&%.\-]+)$/);
      if (m) urls.push(m[1]); else remaining.push(ln);
    }
    let html = remaining.join('\n');
    if (urls.length) {
      html += '\n\n' + urls.map(u => `<a href="${u}" target="_blank" rel="noopener" style="display:inline-block;margin-top:8px;background:#10b981;color:white;font-weight:bold;padding:6px 14px;border-radius:10px;text-decoration:none;font-size:11px;">▶ 開く</a>`).join(' ');
    }
    return html;
  }

  // 殿御命 2026-06-05: /messages?thread=N link を event delegation で intercept
  // (現在 page が /messages でなければ drawer open)
  if (!location.pathname.startsWith('/messages')) {
    console.log('[score-notif] thread drawer event delegation 登録 (path=' + location.pathname + ')');
    document.addEventListener('click', (ev) => {
      // selector を緩く: thread= param 含む全 link 対象
      const a = ev.target.closest('a[href*="thread="]');
      if (!a) return;
      console.log('[score-notif] thread link click detected:', a.href);
      let tid = null;
      try {
        const url = new URL(a.href, location.origin);
        tid = url.searchParams.get('thread');
      } catch(e) {
        // fallback: href 文字列 parse
        const m = (a.getAttribute('href') || '').match(/thread=([^&]+)/);
        if (m) tid = m[1];
      }
      if (tid && /^\d+$/.test(tid)) {
        ev.preventDefault();
        ev.stopPropagation();
        console.log('[score-notif] opening drawer for thread=' + tid);
        window._openThreadDrawer(tid);
      } else {
        console.log('[score-notif] thread id not numeric, fall through:', tid);
      }
    }, true);  // capture phase で interception
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') window._closeThreadDrawer(); });
  } else {
    console.log('[score-notif] thread drawer skipped (path=/messages)');
  }

  // ===== 起動 =====
  function _init(){
    _showOnboardBanner();
    _silentResub();
    _connectSSE();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _init);
  else _init();
  _log('notif-client loaded · origin=' + location.origin);
})();

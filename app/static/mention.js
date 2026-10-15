// cmd_326d: 入力欄に @メンション候補を付ける共通部品(/api/bff/users)。
// 使い方: ScoreMention.attach('#id') / ScoreMention.attach('.cls textarea')
// 委譲方式ゆえ、後から動的に生成される欄(マーカー毎コメント等)にも効く。
// 候補は body 直下の fixed 要素に描く(モーダル/iframe/overflow に切られぬ)。
(function () {
  if (window.ScoreMention) return;
  let _users = null, _dd = null, _target = null, _sel = [];
  function esc(s) { const d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }
  async function users() {
    if (_users) return _users;
    try {
      const r = await fetch('/api/bff/users', { credentials: 'include' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      _users = (await r.json()).map(u => ({ name: u.name || u.email || ('user_' + u.id), email: u.email || '' }));
    } catch (e) { return []; }  // 失敗は保持しない(次の入力で再取得)
    return _users;
  }
  function dd() {
    if (_dd) return _dd;
    _dd = document.createElement('div');
    _dd.id = 'score-mention-dropdown';
    _dd.style.cssText = 'display:none;position:fixed;z-index:2147483000;max-height:176px;overflow-y:auto;background-color:white;border:1px solid #e2e8f0;border-radius:12px;box-shadow:0 8px 24px rgba(0,0,0,0.25);';
    document.body.appendChild(_dd);
    return _dd;
  }
  function hide() { if (_dd) _dd.style.display = 'none'; }
  function matches(el) { return el && _sel.some(s => { try { return el.matches(s); } catch (e) { return false; } }); }
  function pick(name) {
    const el = _target; if (!el) return;
    const cur = el.selectionStart, at = el.value.lastIndexOf('@', cur - 1);
    if (at < 0) return;
    el.value = el.value.substring(0, at) + '@' + name + ' ' + el.value.substring(cur);
    const pos = at + name.length + 2;
    el.focus(); el.setSelectionRange(pos, pos);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    hide();
  }
  async function onInput(ev) {
    const el = ev.target; if (!matches(el)) return;
    _target = el;
    const text = el.value.substring(0, el.selectionStart), at = text.lastIndexOf('@');
    if (at < 0 || /\s/.test(text.substring(at + 1))) { hide(); return; }
    const q = text.substring(at + 1).toLowerCase();
    const c = (await users()).filter(u => u.name.toLowerCase().includes(q) || u.email.toLowerCase().includes(q)).slice(0, 8);
    if (!c.length) { hide(); return; }
    const d = dd(), r = el.getBoundingClientRect();
    // 候補の地は常に白・文字は inline 濃色(暗色テーマの上書きで白地白文字にならぬよう !important で固定)
    d.innerHTML = c.map((u, i) => '<div data-mention-idx="' + i + '" style="padding:8px 12px;cursor:pointer;font-size:13px;background-color:white;"><span style="font-weight:bold;color:#0f172a !important;" translate="no">' + esc(u.name) + '</span>' + (u.email ? ' <span style="font-size:10px;color:#64748b !important;" translate="no">' + esc(u.email) + '</span>' : '') + '</div>').join('');
    d.querySelectorAll('[data-mention-idx]').forEach(n => {
      n.addEventListener('mousedown', e => e.preventDefault());  // 欄のfocusを失わぬ
      n.addEventListener('click', () => pick(c[parseInt(n.dataset.mentionIdx, 10)].name));
    });
    d.style.left = r.left + 'px'; d.style.width = Math.max(r.width, 220) + 'px';
    d.style.display = 'block';
    const h = d.offsetHeight;
    // 下に収まらねば欄の上へ
    d.style.top = (r.bottom + h > window.innerHeight && r.top > h ? r.top - h : r.bottom) + 'px';
  }
  function onKeydown(ev) {
    if (!_dd || _dd.style.display === 'none' || !matches(ev.target)) return;
    if (ev.key === 'Escape') { hide(); ev.stopPropagation(); return; }
    if (ev.key === 'Enter' && !ev.shiftKey) {
      const f = _dd.querySelector('[data-mention-idx]');
      if (f) { f.click(); ev.preventDefault(); ev.stopPropagation(); }
    }
  }
  document.addEventListener('input', onInput, true);
  document.addEventListener('keydown', onKeydown, true);
  document.addEventListener('click', e => { if (_dd && !_dd.contains(e.target) && e.target !== _target) hide(); });
  window.ScoreMention = { attach: function (selector) { if (_sel.indexOf(selector) < 0) _sel.push(selector); } };
})();

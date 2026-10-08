const $ = id => document.getElementById(id);
const DEFAULT_DOMAIN = (typeof window !== 'undefined' && window.DEFAULT_DOMAIN && !window.DEFAULT_DOMAIN.startsWith('__'))
  ? window.DEFAULT_DOMAIN
  : 'example.com';
function resolveMailbox(input){
  let val = String(input || '').trim().toLowerCase();
  if(!val) return '';
  if(!val.includes('@')){
    const dom = (DEFAULT_DOMAIN && !DEFAULT_DOMAIN.startsWith('__')) ? DEFAULT_DOMAIN : 'example.com';
    val = `${val}@${dom}`;
  }
  return val;
}
let currentMailbox = '', currentMsgId = null, pollTimer = null;
let currentMessageData = null;
let headerDetailsExpanded = false;
let linksPopoverOpen = false;

function esc(s){
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function showToast(msg){
  const t = $('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._tid);
  t._tid = setTimeout(() => t.classList.remove('show'), 2000);
}

function fmtTime(iso){
  if(!iso) return '';
  const d = new Date(iso), now = new Date(), diff = (now - d) / 1000;
  if(diff < 55) return '刚刚';
  if(diff < 3600) return `${Math.floor(diff/60)}分钟前`;
  if(diff < 86400 && d.getDate() === now.getDate()) {
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
  }
  if(d.getFullYear() === now.getFullYear()) {
    return `${d.getMonth() + 1}月${d.getDate()}日`;
  }
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
}

function fmtTimeFull(iso){
  if(!iso) return '';
  return new Date(iso).toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
    hour12: false
  });
}

function formatSender(raw){
  raw = String(raw || '').trim();
  const match = raw.match(/^([^<]+)<.*>$/);
  if (match && match[1].trim()) {
    return match[1].trim().replace(/^["']|["']$/g, '');
  }
  if (raw.includes('@')) {
    return raw.split('@')[0];
  }
  return raw || '未知发件人';
}

function extractEmail(raw){
  raw = String(raw || '').trim();
  const match = raw.match(/<([^>]+)>/);
  if (match && match[1]) return match[1];
  return raw;
}

function getDomain(url){
  try { return new URL(url).hostname; } catch{ return url.slice(0, 32); }
}

function getPath(url){
  try {
    const p = new URL(url).pathname;
    return p.length > 1 ? (p.length > 28 ? p.slice(0, 25) + '…' : p) : '';
  } catch{ return ''; }
}

function extractLinks(html){
  const links = [], seen = new Set();
  const re = /href=["'](https?:\/\/[^"'\s>]+)["']/gi;
  let m;
  while((m = re.exec(html)) !== null){
    const u = m[1];
    if(!seen.has(u)){ seen.add(u); links.push(u); }
  }
  return links.filter(u => !u.match(/spacer\.gif|1x1|pixel|tracking|unsubscribe/i));
}

function htmlToText(html){
  if (!html) return '';
  let s = html
    .replace(/<style[\s\S]*?<\/style>/gi, '')
    .replace(/<script[\s\S]*?<\/script>/gi, '');
  s = s.replace(/<(?:br|hr)\s*\/?>/gi, '\n')
       .replace(/<\/?(?:p|div|tr|h[1-6]|table|blockquote|section|article)\b[^>]*>/gi, '\n')
       .replace(/<li\b[^>]*>/gi, '\n• ')
       .replace(/<\/li>/gi, '\n')
       .replace(/<td\b[^>]*>/gi, ' ')
       .replace(/<\/td>/gi, ' ');
  s = s.replace(/<[^>]+>/g, '');
  s = s.replace(/&nbsp;/g, ' ')
       .replace(/&amp;/g, '&')
       .replace(/&lt;/g, '<')
       .replace(/&gt;/g, '>')
       .replace(/&quot;/g, '"')
       .replace(/&#39;/g, "'")
       .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(n))
       .replace(/&#x([0-9a-fA-F]+);/g, (_, n) => String.fromCharCode(parseInt(n, 16)));

  const rawLines = s.split('\n');
  const lines = [];
  for (let i = 0; i < rawLines.length; i++) {
    const line = rawLines[i].replace(/[ \t\u00a0\u3000]+/g, ' ').trim();
    lines.push(line);
  }

  const cleaned = [];
  let prevEmpty = true;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (!line) {
      if (!prevEmpty) {
        cleaned.push('');
        prevEmpty = true;
      }
    } else {
      cleaned.push(line);
      prevEmpty = false;
    }
  }

  while (cleaned.length && !cleaned[cleaned.length - 1]) {
    cleaned.pop();
  }

  return cleaned.join('\n');
}

function prepareEmailHtml(rawHtml){
  let html = String(rawHtml || '').trim();
  const baseTag = '<base target="_blank">';
  const helperStyle = `<style>
    html, body {
      margin: 0 !important;
      padding: 0 !important;
      width: 100% !important;
      min-height: 100% !important;
      overflow-y: auto !important;
      overflow-x: hidden !important;
      -webkit-overflow-scrolling: touch;
      background-color: #ffffff;
    }
    img { max-width: 100% !important; height: auto; }
    a { cursor: pointer; }
    ::-webkit-scrollbar { width: 7px; height: 7px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: rgba(0,0,0,0.18); border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: rgba(0,0,0,0.35); }
  </style>`;

  if(/<head\b[^>]*>/i.test(html)){
    return html.replace(/<head\b[^>]*>/i, `$&${baseTag}${helperStyle}`);
  } else if(/<html\b[^>]*>/i.test(html)){
    return html.replace(/<html\b[^>]*>/i, `$&<head>${baseTag}${helperStyle}</head>`);
  } else {
    return `<!DOCTYPE html><html><head><meta charset="utf-8">${baseTag}${helperStyle}<style>body{padding:24px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;font-size:14px;line-height:1.6;color:#222;background:#fff;}</style></head><body>${html}</body></html>`;
  }
}

async function loadInbox(quiet = false){
  const rawInput = $('emailInput').value.trim();
  if(!rawInput){ if(!quiet) showToast('请输入邮箱地址或前缀'); return; }
  const email = resolveMailbox(rawInput);
  $('emailInput').value = email;
  currentMailbox = email;
  $('mailboxAddr').textContent = currentMailbox;
  $('sidebarHead').style.display = 'block';

  try{
    const r = await fetch(`/api/ui/messages?mailbox=${encodeURIComponent(currentMailbox)}`);
    const data = await r.json();
    if(data.error){ if(!quiet) showToast(data.error); return; }
    renderList(data.messages || []);
    history.replaceState({}, '', `/?email=${encodeURIComponent(currentMailbox)}`);
    startPoll();
  } catch(e){
    if(!quiet) showToast('请求失败');
  }
}

function manualRefresh(){
  const btn = $('refreshBtn');
  btn.classList.add('spinning');
  setTimeout(() => btn.classList.remove('spinning'), 600);
  loadInbox(false);
}

function renderList(msgs){
  $('msgCount').textContent = msgs.length;
  const list = $('msgList');
  if(!msgs.length){
    list.innerHTML = '<div class="empty-list">暂无邮件</div>';
    return;
  }
  list.innerHTML = msgs.map((m, idx) => `
    <div class="mail-item ${m.id === currentMsgId ? 'active' : ''} ${idx === 0 ? 'latest' : ''}" data-id="${m.id}" onclick="loadDetail(${m.id})">
      <div class="mail-item-top">
        <span class="mail-item-subject" title="${esc(m.subject || '（无主题）')}">${esc(m.subject || '（无主题）')}</span>
        <span class="mail-item-time">${fmtTime(m.received_at)}</span>
      </div>
      <div class="mail-item-bottom">
        <span class="mail-item-sender">${esc(formatSender(m.sender))}</span>
        ${m.code ? `<span class="mail-item-code">${esc(m.code)}</span>` : ''}
      </div>
    </div>
  `).join('');
}

async function loadDetail(id){
  currentMsgId = id;
  document.querySelectorAll('.mail-item').forEach(el => el.classList.remove('active'));
  const el = document.querySelector(`.mail-item[data-id="${id}"]`);
  if(el) el.classList.add('active');

  $('detail').innerHTML = '<div class="detail-placeholder">加载中…</div>';

  try{
    const r = await fetch(`/api/ui/message/${id}`);
    const m = await r.json();
    if(m.error){ showToast(m.error); return; }
    currentMessageData = m;
    headerDetailsExpanded = false;
    linksPopoverOpen = false;
    renderDetail(m);
  } catch(e){
    showToast('加载失败');
  }
}

function renderDetail(m){
  const isHTML = /<html|<body|<div|<table/i.test(m.body_text || '');
  const allLinks = extractLinks(m.body_text || '');
  const plainText = htmlToText(m.body_text || '');
  const fullDate = fmtTimeFull(m.received_at);
  const briefDate = fmtTime(m.received_at);

  $('detail').innerHTML = `
    <div class="mail-header-section">
      <div class="mail-subject-row">
        <h1 class="mail-subject">${esc(m.subject || '（无主题）')}</h1>
        <span class="mail-date-brief" title="${esc(fullDate)}">${esc(briefDate)}</span>
      </div>
      <div class="mail-meta-line">
        <div class="sender-group">
          <span class="sender-display-name">${esc(formatSender(m.sender))}</span>
          <span class="sender-recipient-summary" onclick="toggleHeaderDetails()" title="展开收发详情">
            <span>${esc(extractEmail(m.sender))} → ${esc(m.mailbox)}</span>
            <svg class="chevron-icon" id="detailChevron" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M6 9l6 6 6-6"/></svg>
          </span>
        </div>
        ${m.code ? `
        <div class="header-code-pill" onclick="copyCode(this, '${esc(m.code)}')" title="点击一键复制验证码">
          <span class="code-pill-label">验证码</span>
          <span class="code-pill-val">${esc(m.code)}</span>
          <span class="code-pill-btn">复制</span>
        </div>` : ''}
      </div>
      <div id="expandedDetails" class="header-expanded-details" style="display:none">
        <div class="detail-grid">
          <span class="dt-lbl">发件人：</span><span class="dt-val">${esc(m.sender)}</span>
          <span class="dt-lbl">收件人：</span><span class="dt-val">${esc(m.mailbox)}</span>
          <span class="dt-lbl">时　间：</span><span class="dt-val">${esc(fullDate)}</span>
        </div>
      </div>
    </div>

    <div class="reader-toolbar">
      <div class="view-tabs">
        ${isHTML ? `<button class="tab-btn active" onclick="switchTab('html', this)">HTML 渲染</button>` : ''}
        <button class="tab-btn ${!isHTML ? 'active' : ''}" onclick="switchTab('text', this)">纯文本</button>
      </div>
      <div class="toolbar-actions">
        ${allLinks.length ? `
        <div class="links-popover-wrap">
          <button class="toolbar-link-btn" onclick="toggleLinksPopover(event)" id="linksBtn">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6M15 3h6v6M10 14L21 3"/></svg>
            <span>${allLinks.length} 个外部链接</span>
            <svg class="chevron-icon" id="linksChevron" width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg>
          </button>
          <div id="linksDropdown" class="links-dropdown" style="display:none">
            <div class="dropdown-header">外部链接 (${allLinks.length})</div>
            <div class="dropdown-list">
              ${allLinks.map(u => `
                <a href="${esc(u)}" target="_blank" rel="noopener noreferrer" class="dropdown-item">
                  <div class="dropdown-item-info">
                    <div class="dropdown-item-domain">${esc(getDomain(u))}</div>
                    <div class="dropdown-item-path">${esc(getPath(u) || '/')}</div>
                  </div>
                  <svg class="dropdown-item-arrow" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M7 17l9.2-9.2M17 17V8H8"/></svg>
                </a>
              `).join('')}
            </div>
          </div>
        </div>` : ''}
      </div>
    </div>

    <div class="reader-canvas" id="readerCanvas">
      <div class="email-paper">
        ${isHTML ? `<iframe id="bodyFrame" class="email-iframe" sandbox="allow-popups allow-popups-to-escape-sandbox allow-same-origin"></iframe>` : ''}
        <div id="bodyText" class="email-text-view" style="${isHTML ? 'display:none' : ''}">${esc(plainText)}</div>
      </div>
    </div>
  `;

  if(isHTML){
    const f = $('bodyFrame');
    if(f){
      f.contentDocument.open();
      f.contentDocument.write(prepareEmailHtml(m.body_text));
      f.contentDocument.close();
    }
  }
}

function toggleHeaderDetails(){
  headerDetailsExpanded = !headerDetailsExpanded;
  const el = $('expandedDetails');
  const ch = $('detailChevron');
  if(el){ el.style.display = headerDetailsExpanded ? 'block' : 'none'; }
  if(ch){ ch.classList.toggle('open', headerDetailsExpanded); }
}

function toggleLinksPopover(e){
  if(e) e.stopPropagation();
  linksPopoverOpen = !linksPopoverOpen;
  const menu = $('linksDropdown');
  const btn = $('linksBtn');
  const ch = $('linksChevron');
  if(menu){ menu.style.display = linksPopoverOpen ? 'flex' : 'none'; }
  if(btn){ btn.classList.toggle('active', linksPopoverOpen); }
  if(ch){ ch.classList.toggle('open', linksPopoverOpen); }
}

document.addEventListener('click', e => {
  if(linksPopoverOpen){
    const wrap = document.querySelector('.links-popover-wrap');
    if(wrap && !wrap.contains(e.target)){
      linksPopoverOpen = false;
      const menu = $('linksDropdown');
      const btn = $('linksBtn');
      const ch = $('linksChevron');
      if(menu) menu.style.display = 'none';
      if(btn) btn.classList.remove('active');
      if(ch) ch.classList.remove('open');
    }
  }
});

function switchTab(type, btn){
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  const frame = $('bodyFrame');
  const text = $('bodyText');
  if(type === 'html'){
    if(frame) frame.style.display = 'block';
    if(text) text.style.display = 'none';
  } else {
    if(frame) frame.style.display = 'none';
    if(text) text.style.display = 'block';
  }
}

function copyText(t, msg){
  navigator.clipboard.writeText(t).then(() => showToast(msg || '已复制'));
}

function copyCode(btn, code){
  copyText(code, '已复制验证码');
  const actionEl = btn ? btn.querySelector('.code-pill-btn') : null;
  if(actionEl && !actionEl._busy){
    actionEl._busy = true;
    const oldText = actionEl.textContent;
    actionEl.textContent = '已复制 ✓';
    setTimeout(() => {
      actionEl.textContent = oldText;
      actionEl._busy = false;
    }, 1200);
  }
}

function copyAddr(){
  if(currentMailbox) copyText(currentMailbox, '已复制邮箱地址');
}

function startPoll(){
  if(pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(() => {
    loadInbox(true);
  }, 8000);
}

// Search Enter Key
$('emailInput').addEventListener('keydown', e => {
  if(e.key === 'Enter') loadInbox();
});

// URL Param Auto Load
const urlEmail = new URLSearchParams(location.search).get('email');
if(urlEmail){
  const email = resolveMailbox(urlEmail);
  $('emailInput').value = email;
  loadInbox();
}

// Theme Handlers
function applyTheme(t){
  const isLight = t === 'light';
  document.documentElement.classList.toggle('light', isLight);
  const icon = $('themeIcon');
  if(icon){
    if(isLight){
      // 月亮图标
      icon.innerHTML = '<path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"/>';
    } else {
      // 太阳图标
      icon.innerHTML = '<circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>';
    }
  }
}

function toggleTheme(){
  const next = document.documentElement.classList.contains('light') ? 'dark' : 'light';
  localStorage.setItem('theme', next);
  applyTheme(next);
}

applyTheme(localStorage.getItem('theme') || 'dark');

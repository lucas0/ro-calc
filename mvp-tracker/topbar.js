/**
 * topbar.js — shared navigation bar for RO Tools
 * Injects the global tool-nav into any page that includes this script.
 * Pages listen to 'topbar:servertype' and 'topbar:logout' / 'topbar:auth' events.
 */
(function () {
  'use strict';

  const API_BASE = (location.port === '8766' || location.port === '8080') ? 'http://localhost:9500' : '';

  // ── CSS ────────────────────────────────────────────────────────────────────
  const CSS = `
.tool-nav{display:flex;align-items:center;background:#1a1d2e;border-bottom:2px solid #5a7de8;padding:0 20px;height:42px;position:sticky;top:0;z-index:300;gap:0;box-shadow:0 2px 8px rgba(0,0,0,.18)}
.tool-nav-brand{font-size:14px;font-weight:800;color:#fff;padding-right:20px;border-right:1px solid rgba(255,255,255,.15);letter-spacing:.4px;white-space:nowrap;user-select:none;text-decoration:none;flex-shrink:0}
.tool-nav-links{display:flex;align-items:stretch;height:100%;padding-left:8px;gap:2px}
.tool-nav-item{display:flex;align-items:center;padding:0 14px;font-size:12px;font-weight:600;color:rgba(255,255,255,.5);text-decoration:none;border-bottom:2px solid transparent;cursor:pointer;transition:color .15s,background .15s;white-space:nowrap;user-select:none;border-radius:4px 4px 0 0;margin-bottom:-2px}
.tool-nav-item:hover{color:rgba(255,255,255,.85);background:rgba(255,255,255,.08)}
.tool-nav-item.active{color:#fff;background:rgba(90,125,232,.2);border-bottom-color:#5a7de8}
/* Coffee — absolutely centered */
.tnr-coffee{position:absolute;left:50%;transform:translateX(-50%);display:flex;align-items:center;gap:5px;padding:5px 13px;background:#FFDD00;border:none;border-radius:20px;color:#000;text-decoration:none;font-size:11px;font-weight:800;white-space:nowrap;cursor:pointer;z-index:1;transition:background .15s,transform .15s}
.tnr-coffee:hover{background:#FFC700;transform:translateX(-50%) scale(1.04)}
/* Right controls */
.tool-nav-right{display:flex;align-items:center;gap:6px;margin-left:auto;padding-left:12px;flex-shrink:0}
.tnr-divider{width:1px;height:18px;background:rgba(255,255,255,.15);flex-shrink:0}
.tnr-sel-wrap{display:flex;align-items:center;gap:5px}
.tnr-sel-lbl{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.4px;color:rgba(255,255,255,.5);white-space:nowrap}
.tnr-sel{font-size:12px;font-weight:600;color:#fff;background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.2);border-radius:5px;padding:4px 8px;cursor:pointer;outline:none}
.tnr-sel option{background:#1a1d2e;color:#fff}
.tnr-user{display:flex;align-items:center;gap:6px;padding:4px 9px;border-radius:5px;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);font-size:11px;font-weight:600;color:rgba(255,255,255,.75);flex-shrink:0}
.tnr-avatar{width:20px;height:20px;border-radius:50%;background:#5a7de8;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:700;color:#fff;flex-shrink:0;overflow:hidden}
/* Icon buttons (feedback, theme, sign-out) */
.tnr-icon-btn{display:flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:6px;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);font-size:13px;color:rgba(255,255,255,.7);cursor:pointer;transition:background .15s;text-decoration:none;flex-shrink:0;user-select:none;padding:0}
.tnr-icon-btn:hover{background:rgba(255,255,255,.18);color:#fff}
.tnr-signout{background:rgba(200,40,40,.7);border-color:rgba(200,40,40,.4);color:#fff}
.tnr-signout:hover{background:rgba(220,50,50,.95)}
/* Tooltips */
.tnr-tip{position:relative}
.tnr-tip[data-tip]::after{content:attr(data-tip);position:absolute;top:calc(100% + 7px);left:50%;transform:translateX(-50%);background:rgba(15,17,30,.93);color:#e8eaf6;font-size:10px;font-weight:600;white-space:nowrap;padding:4px 8px;border-radius:4px;pointer-events:none;opacity:0;transition:opacity .15s;z-index:2000;letter-spacing:.2px}
.tnr-tip[data-tip]:hover::after{opacity:1}
`;

  // ── Helpers ────────────────────────────────────────────────────────────────
  function isMvpPage() {
    return location.pathname === '/mvp' ||
           location.pathname.startsWith('/mvp/') ||
           location.pathname.includes('mvp-tracker');
  }

  function injectCSS() {
    if (document.getElementById('topbar-css')) return;
    const s = document.createElement('style');
    s.id = 'topbar-css';
    s.textContent = CSS;
    document.head.insertBefore(s, document.head.firstChild);
  }

  function buildNav() {
    const nav = document.createElement('div');
    nav.className = 'tool-nav';
    nav.id = 'tool-nav';
    const mvp = isMvpPage();

    // Server type — shared across tools
    const storedType = getStoredServerType();

    nav.innerHTML =
      '<a class="tool-nav-brand" href="/">⚔\uFE0F RO Tools</a>' +
      '<div class="tool-nav-links">' +
        '<a class="tool-nav-item' + (mvp ? '' : ' active') + '" href="/">🔧 Refine Calculator</a>' +
        '<a class="tool-nav-item' + (mvp ? ' active' : '') + '" href="/mvp">⏱ MVP Tracker</a>' +
      '</div>' +
      // Coffee — centered absolutely
      '<a href="https://buymeacoffee.com/lucazedo" target="_blank" class="tnr-coffee tnr-tip" data-tip="Support the project">☕ Buy me a coffee</a>' +
      // Right controls
      '<div class="tool-nav-right" id="tnr-right">' +
        '<div class="tnr-sel-wrap">' +
          '<span class="tnr-sel-lbl">Type</span>' +
          '<select id="tnr-type-sel" class="tnr-sel">' +
            '<option value="pre-re"' + (storedType === 'pre-re' ? ' selected' : '') + '>Pre-Renewal</option>' +
            '<option value="hercules"' + (storedType === 'hercules' ? ' selected' : '') + '>Hercules</option>' +
            '<option value="re"' + (storedType === 're' ? ' selected' : '') + '>Renewal</option>' +
          '</select>' +
        '</div>' +
        '<div class="tnr-divider"></div>' +
        '<div id="tnr-auth-slot"></div>' +
        '<div class="tnr-divider" id="tnr-post-auth-div"></div>' +
        '<a href="https://forms.gle/Y5v2BzeCTe7XeErUA" target="_blank" class="tnr-icon-btn tnr-tip" data-tip="Send Feedback">💬</a>' +
        '<div class="tnr-icon-btn tnr-tip" id="tnr-theme-btn" data-tip="Toggle Theme" onclick="TopBar.toggleTheme()">🌙</div>' +
      '</div>';

    // Wire TYPE selector
    nav.querySelector('#tnr-type-sel').addEventListener('change', function () {
      setServerTypeInternal(this.value);
    });

    return nav;
  }

  // ── Server Type ────────────────────────────────────────────────────────────
  function getStoredServerType() {
    return localStorage.getItem('ro_server_type') ||
           localStorage.getItem('roCalcServerMode') ||
           localStorage.getItem('mvp_server_type') ||
           'pre-re';
  }

  function setServerTypeInternal(val) {
    localStorage.setItem('ro_server_type', val);
    document.dispatchEvent(new CustomEvent('topbar:servertype', { detail: val }));
  }

  // ── Theme ──────────────────────────────────────────────────────────────────
  function getStoredTheme() {
    return localStorage.getItem('theme') ||
           localStorage.getItem('mvp_theme') ||
           localStorage.getItem('ro-calc-theme') ||
           'light';
  }

  function applyThemeInternal(theme) {
    document.documentElement.setAttribute('data-theme', theme === 'dark' ? 'dark' : 'light');
    const btn = document.getElementById('tnr-theme-btn');
    if (btn) btn.textContent = theme === 'dark' ? '☀️' : '🌙';
  }

  function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme') || 'light';
    const next = current === 'dark' ? 'light' : 'dark';
    localStorage.setItem('theme', next);
    applyThemeInternal(next);
    document.dispatchEvent(new CustomEvent('topbar:theme', { detail: next }));
  }

  // ── Auth ───────────────────────────────────────────────────────────────────
  function renderUser(user) {
    const slot = document.getElementById('tnr-auth-slot');
    const postDiv = document.getElementById('tnr-post-auth-div');
    if (!slot) return;
    const name = user.discordUsername || user.characterName || '?';
    const initials = name[0].toUpperCase();
    let avatarInner = initials;
    if (user.discordAvatar && user.discordId) {
      avatarInner = '<img src="https://cdn.discordapp.com/avatars/' + user.discordId + '/' + user.discordAvatar + '.png?size=32" style="width:20px;height:20px;border-radius:50%;object-fit:cover;display:block">';
    }
    slot.innerHTML =
      '<div class="tnr-user tnr-tip" data-tip="' + name + '">' +
        '<div class="tnr-avatar">' + avatarInner + '</div>' +
        '<span>' + name + '</span>' +
      '</div>' +
      '<button class="tnr-icon-btn tnr-signout tnr-tip" data-tip="Sign Out" onclick="TopBar.logout()">✕</button>';
    if (postDiv) postDiv.style.display = '';
  }

  function renderSignIn() {
    const slot = document.getElementById('tnr-auth-slot');
    const postDiv = document.getElementById('tnr-post-auth-div');
    if (!slot) return;
    slot.innerHTML = '<a href="/mvp" class="tnr-icon-btn tnr-tip" data-tip="Sign In" style="width:auto;padding:0 9px;font-size:11px;font-weight:600;color:rgba(255,255,255,.8)">🔑 Sign In</a>';
    if (postDiv) postDiv.style.display = '';
  }

  async function checkAuth() {
    const token = localStorage.getItem('mvp_token');
    if (!token) {
      renderSignIn();
      document.dispatchEvent(new CustomEvent('topbar:auth', { detail: null }));
      return;
    }
    try {
      const r = await fetch(API_BASE + '/api/auth/me', { headers: { 'X-Session-Token': token } });
      if (r.ok) {
        const user = await r.json();
        renderUser(user);
        document.dispatchEvent(new CustomEvent('topbar:auth', { detail: user }));
        return;
      }
    } catch (e) {}
    localStorage.removeItem('mvp_token');
    renderSignIn();
    document.dispatchEvent(new CustomEvent('topbar:auth', { detail: null }));
  }

  // ── Public API ─────────────────────────────────────────────────────────────
  window.TopBar = {
    apiBase: API_BASE,

    getServerType: function () { return getStoredServerType(); },

    setServerType: function (val) {
      const sel = document.getElementById('tnr-type-sel');
      if (sel) sel.value = val;
      setServerTypeInternal(val);
    },

    showUser: function (user) { renderUser(user); },
    showSignIn: function () { renderSignIn(); },

    toggleTheme: toggleTheme,

    logout: async function () {
      const token = localStorage.getItem('mvp_token');
      if (token) {
        try {
          await fetch(API_BASE + '/api/auth/logout', {
            method: 'POST', headers: { 'X-Session-Token': token }
          });
        } catch (e) {}
        localStorage.removeItem('mvp_token');
      }
      renderSignIn();
      document.dispatchEvent(new CustomEvent('topbar:logout', {}));
    }
  };

  // ── Init ───────────────────────────────────────────────────────────────────
  function init() {
    injectCSS();
    const nav = buildNav();
    document.body.insertBefore(nav, document.body.firstChild);
    applyThemeInternal(getStoredTheme());
    checkAuth();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

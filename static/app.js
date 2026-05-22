/**
 * Fibank Fraud Detection Demo — app.js  (Phase 4 — Unified Engine)
 *
 * DEMO SCENARIOS:
 *  S1 LOW  (~1.0)  — all-safe defaults, 5 000 ALL → green + confetti
 *  S2 MED  (~4.3)  — presenter: hist=off fido=off tz neobank mouse=0.65, 85 000 ALL
 *                  → amber + ID Card + ID Photo upload required
 *  S3 HIGH (~7.5)  — presenter: mule bot screensharing tension tz mouse/typing=0.9
 *                  → full-screen CRITICAL LOCKDOWN overlay + FIDO fail + 24h lock
 */

'use strict';

/* ── Session tracking ── */
const SESSION = {
  loginTime: Date.now(),
  pagesVisited: parseInt(sessionStorage.getItem('pagesVisited') || '1', 10),
  passwordPasted: false,
};
sessionStorage.setItem('pagesVisited', SESSION.pagesVisited);

/* ── Transfer state ── */
let pendingTransfer   = null;
let accountLocked     = false;
let lockCountdownInterval = null;

/* ── Password field timing ── */
let passwordFocusTime = null;
let passwordTotalMs   = 0;

/* ── Form start timing ── */
let formStartTime = null;

/* ═══════════════════════════════════════════════════════════════
   TOAST
═══════════════════════════════════════════════════════════════ */
window.showToast = function (message, type = 'success', icon = '') {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.innerHTML = icon
    ? `<span class="toast-icon">${icon}</span>${message}`
    : message;
  container.appendChild(toast);
  // Trigger animation
  requestAnimationFrame(() => {
    requestAnimationFrame(() => toast.classList.add('show'));
  });
  setTimeout(() => {
    toast.classList.remove('show');
    setTimeout(() => toast.remove(), 350);
  }, 3200);
};

/* ═══════════════════════════════════════════════════════════════
   TIRANA CLOCK (GMT+2 / CEST +2h)
═══════════════════════════════════════════════════════════════ */
function tickTiranaClock() {
  const el = document.getElementById('tirana-time');
  if (!el) return;
  // Tirana is UTC+2 in winter (CET), UTC+3 in summer (CEST).
  // We compute local Tirana time using the UTC offset +2 always for simplicity.
  const now = new Date();
  const utc = now.getTime() + now.getTimezoneOffset() * 60000;
  const tirana = new Date(utc + 2 * 3600000);
  const hh = String(tirana.getHours()).padStart(2, '0');
  const mm = String(tirana.getMinutes()).padStart(2, '0');
  const ss = String(tirana.getSeconds()).padStart(2, '0');
  el.textContent = `${hh}:${mm}:${ss}`;
}

/* ═══════════════════════════════════════════════════════════════
   FORMAT HELPERS
═══════════════════════════════════════════════════════════════ */
function formatALL(n) {
  return Number(n).toLocaleString('sq-AL');
}
function formatDate(iso) {
  try {
    return new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
  } catch { return iso; }
}
function riskBadge(level) {
  const map = { low: 'badge-low', medium: 'badge-medium', high: 'badge-high' };
  const lbl = { low: 'LOW', medium: 'MEDIUM', high: 'HIGH' };
  return `<span class="badge ${map[level] || 'badge-low'}">${lbl[level] || level.toUpperCase()}</span>`;
}
function statusBadge(status) {
  const icons = { completed: '✓', blocked: '✕', pending: '⏳' };
  return `<span class="status-badge status-${status}">${icons[status] || ''} ${status}</span>`;
}

/* ═══════════════════════════════════════════════════════════════
   LOAD USER DATA
═══════════════════════════════════════════════════════════════ */
async function loadUser() {
  try {
    const res  = await fetch('/api/user');
    const data = await res.json();
    const u    = data.user;

    document.getElementById('user-name').textContent     = u.name.split(' ')[0];
    document.getElementById('user-iban').textContent     = u.iban;
    document.getElementById('balance').textContent       = formatALL(u.balance);
    document.getElementById('stat-txns').textContent    = data.transactions.length;
    document.getElementById('stat-age').textContent     = u.account_age_days + ' days';
    document.getElementById('stat-location').textContent = u.location.split(',')[0] + ', AL';

    renderTxnTable(data.transactions);
  } catch (e) {
    console.error('loadUser:', e);
  }
}

function renderTxnTable(txns) {
  const tbody = document.getElementById('txn-table-body');
  if (!txns || txns.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:2rem;">No transactions yet.</td></tr>';
    return;
  }
  tbody.innerHTML = txns.slice(0, 8).map(t => `
    <tr>
      <td style="font-weight:600;">${t.recipient_name}</td>
      <td>${formatALL(t.amount)}</td>
      <td style="color:var(--muted);font-size:0.82rem;">${formatDate(t.timestamp)}</td>
      <td>${riskBadge(t.risk_level)}</td>
      <td>${statusBadge(t.status)}</td>
    </tr>`).join('');
}

/* ═══════════════════════════════════════════════════════════════
   ADDRESS BOOK
═══════════════════════════════════════════════════════════════ */
async function loadRecipients() {
  try {
    const res  = await fetch('/api/recipients');
    const data = await res.json();
    const sel  = document.getElementById('recipient-select');
    if (!sel || !data.recipients) return;
    // Remove any previously added options (beyond the placeholder)
    while (sel.options.length > 1) sel.remove(1);
    data.recipients.forEach(r => {
      const opt = document.createElement('option');
      opt.value = JSON.stringify({ name: r.name, iban: r.iban });
      opt.textContent = r.name;
      sel.appendChild(opt);
    });
  } catch (e) {
    console.warn('loadRecipients:', e);
  }
}

window.fillRecipient = function (sel) {
  if (!sel.value) return;
  try {
    const r = JSON.parse(sel.value);
    document.getElementById('recipient-name').value = r.name;
    document.getElementById('recipient-iban').value = r.iban;
    if (!formStartTime) formStartTime = Date.now();
  } catch { /* ignore */ }
};

/* ── Add recipient panel ── */
window.toggleAddRecipientPanel = function () {
  const form = document.getElementById('add-recipient-form');
  form.classList.toggle('visible');
  if (form.classList.contains('visible')) {
    document.getElementById('new-recip-name').focus();
  }
};

window.saveNewRecipient = async function () {
  const name = document.getElementById('new-recip-name').value.trim();
  const iban = document.getElementById('new-recip-iban').value.trim();
  if (!name || !iban) { showToast('Please enter both name and IBAN.', 'warn', '⚠️'); return; }

  const btn = document.getElementById('save-recip-btn');
  btn.innerHTML = '<span class="btn-spinner"></span> Saving…';
  btn.disabled = true;

  try {
    const res  = await fetch('/api/recipients/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, iban }),
    });
    const data = await res.json();
    if (data.success) {
      // Add to dropdown
      const sel = document.getElementById('recipient-select');
      const opt = document.createElement('option');
      opt.value = JSON.stringify({ name, iban });
      opt.textContent = name;
      sel.appendChild(opt);

      // Clear form + hide
      document.getElementById('new-recip-name').value = '';
      document.getElementById('new-recip-iban').value = '';
      document.getElementById('add-recipient-form').classList.remove('visible');

      showToast(`${name} added to address book.`, 'success', '✓');
    } else {
      showToast(data.error || 'Could not save recipient.', 'error', '✕');
    }
  } catch (e) {
    showToast('Network error.', 'error', '✕');
  } finally {
    btn.innerHTML = 'Save';
    btn.disabled = false;
  }
};

/* ── Scroll to transfer panel ── */
window.scrollToTransfer = function () {
  document.getElementById('transfer-section').scrollIntoView({ behavior: 'smooth' });
};

/* ═══════════════════════════════════════════════════════════════
   PASSWORD TRACKING
═══════════════════════════════════════════════════════════════ */
function initPasswordTracking() {
  const pw = document.getElementById('password-field');
  if (!pw) return;
  pw.addEventListener('focus', () => { passwordFocusTime = Date.now(); });
  pw.addEventListener('blur', () => {
    if (passwordFocusTime) {
      passwordTotalMs += Date.now() - passwordFocusTime;
      passwordFocusTime = null;
    }
  });
  pw.addEventListener('paste', () => {
    SESSION.passwordPasted = true;
    fetch('/api/report-paste', { method: 'POST' }).catch(() => {});
  });
}

function initFormTracking() {
  ['recipient-name', 'recipient-iban', 'amount', 'password-field'].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('focus', () => { if (!formStartTime) formStartTime = Date.now(); });
  });
}

/* ═══════════════════════════════════════════════════════════════
   TRANSFER BUTTON
═══════════════════════════════════════════════════════════════ */
function initTransferButton() {
  const btn = document.getElementById('transfer-btn');
  if (!btn) return;

  btn.addEventListener('click', async () => {
    if (accountLocked) return;

    const recipientName = document.getElementById('recipient-name').value.trim();
    const recipientIban = document.getElementById('recipient-iban').value.trim();
    const amount        = parseFloat(document.getElementById('amount').value);

    if (!recipientName || !recipientIban || !amount || amount <= 0) {
      showToast('Please fill in all transfer fields.', 'warn', '⚠️');
      return;
    }

    // 1.5 s loading state for dramatic effect
    btn.disabled  = true;
    btn.innerHTML = '<span class="btn-spinner"></span>Analysing…';

    await new Promise(r => setTimeout(r, 1500));

    // Collect timing
    if (passwordFocusTime) passwordTotalMs += Date.now() - passwordFocusTime;
    const formCompletionSec = formStartTime ? (Date.now() - formStartTime) / 1000 : 15;
    const timeLoginSec      = (Date.now() - SESSION.loginTime) / 1000;

    const payload = {
      amount,
      recipient_name: recipientName,
      recipient_iban: recipientIban,
      form_completion_time_sec:    formCompletionSec,
      password_entry_ms:           passwordTotalMs || 2000,
      pages_visited_pre_transfer:  SESSION.pagesVisited,
      time_login_to_transfer_sec:  timeLoginSec,
    };

    try {
      const res    = await fetch('/api/score', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(payload),
      });
      const result = await res.json();

      pendingTransfer = {
        amount,
        recipient_name:    recipientName,
        recipient_iban:    recipientIban,
        risk_score:        result.risk_score,
        risk_level:        result.risk_level,
        triggered_signals: result.triggered_signals,
      };

      handleRiskResponse(result);
    } catch (e) {
      console.error('Score API error:', e);
      btn.disabled = false;
      btn.textContent = 'Transfer Funds →';
      showToast('Connection error. Please try again.', 'error', '✕');
    }
  });
}

/* ═══════════════════════════════════════════════════════════════
   RISK RESPONSE HANDLERS
═══════════════════════════════════════════════════════════════ */
function handleRiskResponse({ risk_level, explanation }) {
  if (risk_level === 'low')         handleLow();
  else if (risk_level === 'medium') handleMedium(explanation);
  else                              handleHigh(explanation);
}

/* ── LOW ── */
async function handleLow() {
  document.body.classList.add('risk-low');
  confetti({ particleCount: 150, spread: 80, origin: { y: 0.6 },
             colors: ['#2E7D32','#43A047','#66bb6a','#fff'] });

  const r = await saveTransfer('completed');
  if (r && r.new_balance !== null) {
    document.getElementById('balance').textContent        = formatALL(r.new_balance);
    document.getElementById('new-balance-low').textContent = formatALL(r.new_balance);
  }

  openModal('modal-low');
  await loadUser();

  setTimeout(() => {
    document.body.classList.remove('risk-low');
    resetTransferForm();
  }, 3000);
}

/* ── MEDIUM ── */
function handleMedium(explanation) {
  document.body.classList.add('risk-medium');

  document.getElementById('medium-explanation').textContent =
    explanation || 'Unusual patterns detected. Please verify your identity.';

  // Reset fields + Verify button
  document.getElementById('verify-id-number').value = '';
  document.getElementById('verify-id-photo').value  = '';
  document.getElementById('upload-area').classList.remove('has-file');
  document.getElementById('upload-text').textContent = 'Click to upload front of ID card';
  document.getElementById('upload-text').classList.remove('active');
  document.getElementById('verify-btn').disabled = true;

  openModal('modal-medium');
}

/* ── HIGH ── */
function handleHigh(explanation) {
  document.body.classList.add('risk-high');

  document.getElementById('high-explanation').textContent =
    explanation || 'Multiple critical threat signals detected. Immediate action required.';

  // Reset lockdown screen state
  document.getElementById('high-btn-wrap').style.display    = 'block';
  document.getElementById('passkey-spinner').style.display  = 'none';
  document.getElementById('passkey-fail').style.display     = 'none';

  // Show full-screen overlay
  document.getElementById('lockdown-screen').classList.add('active');
}

/* ═══════════════════════════════════════════════════════════════
   MEDIUM: enable Verify when both fields filled
═══════════════════════════════════════════════════════════════ */
window.checkMediumReady = function () {
  const id    = document.getElementById('verify-id-number').value.trim();
  const photo = document.getElementById('verify-id-photo').files.length > 0;
  document.getElementById('verify-btn').disabled = !(id && photo);
};

window.onPhotoSelected = function (input) {
  const area = document.getElementById('upload-area');
  const text = document.getElementById('upload-text');
  if (input.files && input.files[0]) {
    area.classList.add('has-file');
    text.textContent = `✓ ${input.files[0].name}`;
    text.classList.add('active');
  } else {
    area.classList.remove('has-file');
    text.textContent = 'Click to upload front of ID card';
    text.classList.remove('active');
  }
  checkMediumReady();
};

window.completeMediumVerify = async function () {
  const btn = document.getElementById('verify-btn');
  btn.innerHTML = '<span class="btn-spinner"></span>Verifying…';
  btn.disabled  = true;

  await new Promise(r => setTimeout(r, 1500));

  closeModal('modal-medium');
  document.body.classList.remove('risk-medium');

  const r = await saveTransfer('completed');
  if (r && r.new_balance !== null) {
    document.getElementById('balance').textContent        = formatALL(r.new_balance);
    document.getElementById('new-balance-low').textContent = formatALL(r.new_balance);
  }

  openModal('modal-low');
  await loadUser();

  setTimeout(resetTransferForm, 2000);
};

/* ═══════════════════════════════════════════════════════════════
   HIGH: passkey attempt (always fails for demo)
═══════════════════════════════════════════════════════════════ */
window.attemptPasskey = async function () {
  document.getElementById('high-btn-wrap').style.display   = 'none';
  document.getElementById('passkey-spinner').style.display = 'block';

  await new Promise(r => setTimeout(r, 2000));

  document.getElementById('passkey-spinner').style.display = 'none';
  document.getElementById('passkey-fail').style.display    = 'block';

  await saveTransfer('blocked');
  await loadUser();

  // Lock transfer form
  accountLocked = true;
  const btn = document.getElementById('transfer-btn');
  if (btn) { btn.disabled = true; btn.textContent = '🔒 Account Locked'; }
  ['recipient-name','recipient-iban','amount','password-field'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.disabled = true;
  });

  // Show lockdown notice behind the overlay (visible once overlay is dismissed or after reload)
  const notice = document.getElementById('lockdown-notice');
  if (notice) notice.style.display = 'block';
  startLockdownTimer();

  showToast('Account locked for 24 hours.', 'error', '🔒');
};

/* ═══════════════════════════════════════════════════════════════
   SAVE TRANSFER
═══════════════════════════════════════════════════════════════ */
async function saveTransfer(status) {
  if (!pendingTransfer) return null;
  try {
    const res = await fetch('/api/transfer', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ ...pendingTransfer, status }),
    });
    return await res.json();
  } catch (e) {
    console.error('saveTransfer:', e);
    return null;
  }
}

/* ═══════════════════════════════════════════════════════════════
   LOCKDOWN COUNTDOWN
═══════════════════════════════════════════════════════════════ */
function startLockdownTimer() {
  let remaining = 24 * 3600;
  const el = document.getElementById('lockdown-timer');
  if (lockCountdownInterval) clearInterval(lockCountdownInterval);
  lockCountdownInterval = setInterval(() => {
    remaining--;
    if (remaining <= 0) { clearInterval(lockCountdownInterval); if (el) el.textContent = '00:00:00'; return; }
    const h = Math.floor(remaining / 3600);
    const m = Math.floor((remaining % 3600) / 60);
    const s = remaining % 60;
    if (el) el.textContent =
      `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  }, 1000);
}

/* ═══════════════════════════════════════════════════════════════
   MODAL HELPERS
═══════════════════════════════════════════════════════════════ */
function openModal(id) {
  document.getElementById(id).classList.add('active');
}
window.closeModal = function (id) {
  document.getElementById(id).classList.remove('active');
  const btn = document.getElementById('transfer-btn');
  if (btn && !accountLocked) {
    btn.disabled = false;
    btn.textContent = 'Transfer Funds →';
  }
};

function resetTransferForm() {
  if (accountLocked) return;
  ['recipient-name','recipient-iban','amount','password-field'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = '';
  });
  formStartTime   = null;
  passwordTotalMs = 0;
  passwordFocusTime = null;
  pendingTransfer = null;
  const btn = document.getElementById('transfer-btn');
  if (btn) { btn.disabled = false; btn.textContent = 'Transfer Funds →'; }
}

/* ═══════════════════════════════════════════════════════════════
   LOGOUT
═══════════════════════════════════════════════════════════════ */
window.doLogout = async function () {
  await fetch('/api/logout', { method: 'POST' }).catch(() => {});
  window.location.href = '/';
};

/* ═══════════════════════════════════════════════════════════════
   BOOT
═══════════════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
  if (!document.getElementById('transfer-btn')) return;

  loadUser();
  loadRecipients();
  initPasswordTracking();
  initFormTracking();
  initTransferButton();

  // Tirana clock
  tickTiranaClock();
  setInterval(tickTiranaClock, 1000);
});

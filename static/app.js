/**
 * Fibank Fraud Detection Demo — app.js
 *
 * DEMO SCENARIOS:
 *  S1 LOW  (~1.0)  — all-safe defaults, 5 000 ALL
 *  S2 MED  (~4.3)  — presenter: no-hist no-fido tz neobank mouse=0.65, 85 000 ALL
 *  S3 HIGH (~7.2)  — presenter: session_from_link bot screensharing tz mouse/typing=0.9
 */

'use strict';

/* ═══════════════════════════════════════════════════════════════
   SESSION TRACKING
═══════════════════════════════════════════════════════════════ */
const SESSION = {
  loginTime:     Date.now(),
  pagesVisited:  parseInt(sessionStorage.getItem('pagesVisited') || '1', 10),
  passwordPasted: false,
};
sessionStorage.setItem('pagesVisited', SESSION.pagesVisited);

/* ── Transfer state ── */
let pendingTransfer       = null;
let accountLocked         = false;
let lockCountdownInterval = null;
let mediumAuthDone        = false;   // set true after any biometric auth passes
let mediumAuthMethod      = null;    // last auth method used ('faceid','touchid',etc.)
let ibanFromDropdown      = false;   // true when IBAN filled via address-book dropdown
let knownIBANs            = new Set();

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
  toast.innerHTML = icon ? `<span class="toast-icon">${icon}</span>${message}` : message;
  container.appendChild(toast);
  requestAnimationFrame(() => requestAnimationFrame(() => toast.classList.add('show')));
  setTimeout(() => {
    toast.classList.remove('show');
    setTimeout(() => toast.remove(), 350);
  }, 3200);
};

/* ═══════════════════════════════════════════════════════════════
   TIRANA CLOCK (GMT+2)
═══════════════════════════════════════════════════════════════ */
function tickTiranaClock() {
  const el = document.getElementById('tirana-time');
  if (!el) return;
  const now  = new Date();
  const utc  = now.getTime() + now.getTimezoneOffset() * 60000;
  const t    = new Date(utc + 2 * 3600000);
  el.textContent =
    `${String(t.getHours()).padStart(2,'0')}:${String(t.getMinutes()).padStart(2,'0')}:${String(t.getSeconds()).padStart(2,'0')}`;
}

/* ═══════════════════════════════════════════════════════════════
   FORMAT HELPERS
═══════════════════════════════════════════════════════════════ */
function formatALL(n)  { return Number(n).toLocaleString('sq-AL'); }
function formatDate(iso) {
  try { return new Date(iso).toLocaleDateString('en-GB', { day:'2-digit', month:'short', year:'numeric' }); }
  catch { return iso; }
}
function riskBadge(level) {
  const cls = { low:'badge-low', medium:'badge-medium', high:'badge-high' };
  const lbl = { low:'LOW', medium:'MEDIUM', high:'HIGH' };
  return `<span class="badge ${cls[level]||'badge-low'}">${lbl[level]||level.toUpperCase()}</span>`;
}
function statusBadge(status) {
  const icons = { completed:'✓', blocked:'✕', pending:'⏳' };
  return `<span class="status-badge status-${status}">${icons[status]||''} ${status}</span>`;
}

/* ═══════════════════════════════════════════════════════════════
   LOAD USER DATA
═══════════════════════════════════════════════════════════════ */
async function loadUser() {
  try {
    const res  = await fetch('/api/user');
    const data = await res.json();
    const u    = data.user;
    document.getElementById('user-name').textContent      = u.name.split(' ')[0];
    document.getElementById('user-iban').textContent      = u.iban;
    document.getElementById('balance').textContent        = formatALL(u.balance);
    document.getElementById('stat-txns').textContent     = data.transactions.length;
    document.getElementById('stat-age').textContent      = u.account_age_days + ' days';
    document.getElementById('stat-location').textContent = u.location.split(',')[0] + ', AL';
    renderTxnTable(data.transactions);
  } catch (e) { console.error('loadUser:', e); }
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
    while (sel.options.length > 1) sel.remove(1);
    knownIBANs.clear();
    data.recipients.forEach(r => {
      knownIBANs.add(r.iban.replace(/\s/g, '').toLowerCase());
      const opt = document.createElement('option');
      opt.value = JSON.stringify({ name: r.name, iban: r.iban });
      opt.textContent = r.name;
      sel.appendChild(opt);
    });
  } catch (e) { console.warn('loadRecipients:', e); }
}

window.fillRecipient = function (sel) {
  if (!sel.value) return;
  try {
    const r = JSON.parse(sel.value);
    document.getElementById('recipient-name').value = r.name;
    document.getElementById('recipient-iban').value = r.iban;
    ibanFromDropdown = true;
    if (!formStartTime) formStartTime = Date.now();
  } catch { /* ignore */ }
};

/* ── Add recipient panel ── */
window.toggleAddRecipientPanel = function () {
  const form = document.getElementById('add-recipient-form');
  form.classList.toggle('visible');
  if (form.classList.contains('visible')) document.getElementById('new-recip-name').focus();
};

window.saveNewRecipient = async function () {
  const name = document.getElementById('new-recip-name').value.trim();
  const iban = document.getElementById('new-recip-iban').value.trim();
  if (!name || !iban) { showToast('Please enter both name and IBAN.', 'warn', '⚠️'); return; }

  const btn = document.getElementById('save-recip-btn');
  btn.innerHTML = '<span class="btn-spinner"></span> Saving…';
  btn.disabled  = true;

  try {
    const res  = await fetch('/api/recipients/add', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, iban }),
    });
    const data = await res.json();
    if (data.success) {
      const normalised = iban.replace(/\s/g, '').toLowerCase();
      knownIBANs.add(normalised);   // add to known set immediately
      const sel = document.getElementById('recipient-select');
      const opt = document.createElement('option');
      opt.value = JSON.stringify({ name, iban });
      opt.textContent = name;
      sel.appendChild(opt);
      document.getElementById('new-recip-name').value = '';
      document.getElementById('new-recip-iban').value = '';
      document.getElementById('add-recipient-form').classList.remove('visible');
      showToast(`${name} added to address book.`, 'success', '✓');
    } else {
      showToast(data.error || 'Could not save recipient.', 'error', '✕');
    }
  } catch (e) { showToast('Network error.', 'error', '✕'); }
  finally {
    btn.innerHTML = 'Save';
    btn.disabled  = false;
  }
};

window.scrollToTransfer = function () {
  document.getElementById('transfer-section').scrollIntoView({ behavior: 'smooth' });
};

/* ═══════════════════════════════════════════════════════════════
   BEHAVIORAL SIGNAL DETECTORS
═══════════════════════════════════════════════════════════════ */

/* ── Password field timing + paste detection (dashboard) ── */
function initPasswordTracking() {
  const pw = document.getElementById('password-field');
  if (!pw) return;
  pw.addEventListener('focus', () => { passwordFocusTime = Date.now(); });
  pw.addEventListener('blur',  () => {
    if (passwordFocusTime) { passwordTotalMs += Date.now() - passwordFocusTime; passwordFocusTime = null; }
  });
  pw.addEventListener('paste', () => {
    SESSION.passwordPasted = true;
    fetch('/api/report-paste', { method: 'POST' }).catch(() => {});
  });
}

function initFormTracking() {
  ['recipient-name','recipient-iban','amount','password-field'].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('focus', () => { if (!formStartTime) formStartTime = Date.now(); });
  });
  // Track if IBAN is typed manually (not from dropdown)
  const ibanInput = document.getElementById('recipient-iban');
  if (ibanInput) {
    ibanInput.addEventListener('input', () => { ibanFromDropdown = false; });
  }
}

/* ── Clipboard paste detection on IBAN / amount fields ── */
function initClipboardDetection() {
  ['recipient-iban', 'amount'].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('paste', () => {
      fetch('/api/report-clipboard', { method: 'POST' }).catch(() => {});
    });
  });
}

/* ── Tab-switch detection ── */
function initTabSwitchDetection() {
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      fetch('/api/report-tab-switch', { method: 'POST' }).catch(() => {});
    }
  });
}

/* ── Session-from-link detection (email / SMS referrer or ?from= param) ── */
function checkSessionSource() {
  const params      = new URLSearchParams(window.location.search);
  const fromParam   = params.get('from');
  const fromExternal = fromParam === 'email' || fromParam === 'sms' || fromParam === 'link';
  // External referrer = not same origin and not empty
  let   fromReferrer = false;
  try {
    if (document.referrer) {
      const refHost = new URL(document.referrer).hostname;
      fromReferrer  = refHost !== window.location.hostname;
    }
  } catch { /* ignore */ }

  if (fromExternal || fromReferrer) {
    fetch('/api/report-referrer', { method: 'POST' }).catch(() => {});
    showToast('Session opened from external link — additional verification may apply.', 'warn', '⚠️');
  }
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
      showToast('Please fill in all transfer fields.', 'warn', '⚠️'); return;
    }

    btn.disabled  = true;
    btn.innerHTML = '<span class="btn-spinner"></span>Analysing…';

    // Unknown-IBAN check — typed directly rather than selected from address book
    const normIban = recipientIban.replace(/\s/g, '').toLowerCase();
    if (!ibanFromDropdown || !knownIBANs.has(normIban)) {
      await fetch('/api/report-unknown-iban', { method: 'POST' }).catch(() => {});
    }

    await new Promise(r => setTimeout(r, 1500));

    if (passwordFocusTime) passwordTotalMs += Date.now() - passwordFocusTime;
    const formCompletionSec = formStartTime ? (Date.now() - formStartTime) / 1000 : 15;
    const timeLoginSec      = (Date.now() - SESSION.loginTime) / 1000;

    const payload = {
      amount,
      recipient_name:              recipientName,
      recipient_iban:              recipientIban,
      form_completion_time_sec:    formCompletionSec,
      password_entry_ms:           passwordTotalMs || 2000,
      pages_visited_pre_transfer:  SESSION.pagesVisited,
      time_login_to_transfer_sec:  timeLoginSec,
    };

    try {
      const res    = await fetch('/api/score', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const result = await res.json();

      pendingTransfer = {
        audit_id:          result.audit_id,
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
      btn.disabled    = false;
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
  if (r?.new_balance != null) {
    document.getElementById('balance').textContent         = formatALL(r.new_balance);
    document.getElementById('new-balance-low').textContent = formatALL(r.new_balance);
  }
  openModal('modal-low');
  await loadUser();
  setTimeout(() => { document.body.classList.remove('risk-low'); resetTransferForm(); }, 3000);
}

/* ── MEDIUM ── */
function handleMedium(explanation) {
  document.body.classList.add('risk-medium');
  document.getElementById('medium-explanation').textContent =
    explanation || 'Unusual patterns detected. Please verify your identity.';

  // Reset all auth state
  mediumAuthDone = false;
  document.getElementById('verify-btn').disabled = true;
  const badge = document.getElementById('auth-verified-badge');
  if (badge) badge.classList.remove('visible');

  // Reset panels to Face ID tab
  const firstTab = document.querySelector('.auth-tab');
  if (firstTab) switchAuthTab('faceid', firstTab);

  // Reset face scan
  const frame = document.getElementById('face-scan-frame');
  if (frame) { frame.classList.remove('scanning','success'); frame.textContent = '👤'; }
  const fp = document.getElementById('fingerprint-display');
  if (fp)   { fp.classList.remove('scanning','success'); fp.textContent = '👆'; }
  const pk = document.getElementById('passkey-display');
  if (pk)   { pk.classList.remove('scanning'); }

  // Reset ID card fallback
  const idNum = document.getElementById('verify-id-number');
  if (idNum) idNum.value = '';
  const idPhoto = document.getElementById('verify-id-photo');
  if (idPhoto) idPhoto.value = '';
  const uploadArea = document.getElementById('upload-area');
  if (uploadArea) uploadArea.classList.remove('has-file');
  const uploadText = document.getElementById('upload-text');
  if (uploadText) { uploadText.textContent = 'Click to upload front of ID card'; uploadText.classList.remove('active'); }
  const idVerifyBtn = document.getElementById('id-verify-btn');
  if (idVerifyBtn) { idVerifyBtn.disabled = true; idVerifyBtn.innerHTML = 'Verify ID Card'; }

  // Update push amount label
  const pushLabel = document.getElementById('push-amount-label');
  if (pushLabel && pendingTransfer) pushLabel.textContent = `Amount: ${formatALL(pendingTransfer.amount)} ALL`;

  const pushMock = document.getElementById('push-mock');
  if (pushMock) pushMock.classList.remove('sent');

  document.getElementById('auth-spinner').style.display = 'none';

  openModal('modal-medium');
}

/* ── HIGH ── */
function handleHigh(explanation) {
  document.body.classList.add('risk-high');
  document.getElementById('high-explanation').textContent =
    explanation || 'Multiple critical threat signals detected. Immediate action required.';

  // Reset overlay to Step 1 (FIDO entry)
  document.getElementById('fido-step').style.display      = 'block';
  document.getElementById('biometric-step').style.display = 'none';
  document.getElementById('high-spinner').style.display   = 'none';
  document.getElementById('passkey-fail').style.display   = 'none';
  const codeInput = document.getElementById('fido-code-input');
  if (codeInput) { codeInput.value = ''; codeInput.classList.remove('error'); }
  const errEl = document.getElementById('fido-error');
  if (errEl) errEl.style.display = 'none';

  document.getElementById('lockdown-screen').classList.add('active');
}

/* ═══════════════════════════════════════════════════════════════
   MEDIUM AUTH — TAB SWITCHING
═══════════════════════════════════════════════════════════════ */
window.switchAuthTab = function (method, btn) {
  document.querySelectorAll('.auth-tab').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  ['faceid','touchid','passkey','push'].forEach(m => {
    const p = document.getElementById('auth-' + m);
    if (p) p.style.display = m === method ? 'flex' : 'none';
  });
  document.getElementById('auth-spinner').style.display = 'none';
};

/* ── Simulate biometric auth (Face ID / Touch ID / Passkey / Push) ── */
window.simulateAuth = async function (method) {
  mediumAuthMethod = method;   // capture for resolution logging

  // Hide panel content, show spinner
  ['faceid','touchid','passkey','push'].forEach(m => {
    const p = document.getElementById('auth-' + m);
    if (p) p.style.display = 'none';
  });
  document.getElementById('auth-spinner').style.display = 'block';

  // Start animations
  if (method === 'faceid') {
    const f = document.getElementById('face-scan-frame');
    if (f) f.classList.add('scanning');
  } else if (method === 'touchid') {
    const fp = document.getElementById('fingerprint-display');
    if (fp) fp.classList.add('scanning');
  } else if (method === 'passkey') {
    const pk = document.getElementById('passkey-display');
    if (pk) pk.classList.add('scanning');
  } else if (method === 'push') {
    const mock = document.getElementById('push-mock');
    if (mock) mock.classList.add('sent');
  }

  await new Promise(r => setTimeout(r, 1800));

  document.getElementById('auth-spinner').style.display = 'none';

  // Success state on icon
  if (method === 'faceid') {
    const f = document.getElementById('face-scan-frame');
    if (f) { f.classList.remove('scanning'); f.classList.add('success'); f.textContent = '✅'; }
  } else if (method === 'touchid') {
    const fp = document.getElementById('fingerprint-display');
    if (fp) { fp.classList.remove('scanning'); fp.classList.add('success'); fp.textContent = '✅'; }
  } else if (method === 'passkey') {
    const pk = document.getElementById('passkey-display');
    if (pk) { pk.classList.remove('scanning'); pk.textContent = '✅'; }
  } else if (method === 'push') {
    const mock = document.getElementById('push-mock');
    if (mock) {
      mock.innerHTML = '<div style="font-weight:700;font-size:0.8rem;color:#2E7D32;">✓ Approved</div><div style="font-size:0.74rem;color:#4a7c59;">Transfer authorised via push notification</div>';
    }
  }

  // Show auth panel again with success icon visible
  const panel = document.getElementById('auth-' + method);
  if (panel) panel.style.display = 'flex';

  // Mark done + enable proceed
  mediumAuthDone = true;
  const badge = document.getElementById('auth-verified-badge');
  if (badge) badge.classList.add('visible');
  document.getElementById('verify-btn').disabled = false;
};

/* ── Albanian ID card fallback ── */
window.checkIdCardReady = function () {
  const id    = document.getElementById('verify-id-number').value.trim();
  const photo = document.getElementById('verify-id-photo').files.length > 0;
  document.getElementById('id-verify-btn').disabled = !(id && photo);
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
  checkIdCardReady();
};

window.completeIdVerify = async function () {
  const btn = document.getElementById('id-verify-btn');
  btn.innerHTML = '<span class="btn-spinner"></span>Verifying…';
  btn.disabled  = true;
  await new Promise(r => setTimeout(r, 1200));
  mediumAuthDone   = true;
  mediumAuthMethod = 'id_card';   // capture for resolution logging
  const badge = document.getElementById('auth-verified-badge');
  if (badge) badge.classList.add('visible');
  document.getElementById('verify-btn').disabled = false;
  btn.innerHTML = '✓ Verified';
};

/* ── Proceed after any MEDIUM auth passes ── */
window.completeMediumVerify = async function () {
  if (!mediumAuthDone) return;
  const btn = document.getElementById('verify-btn');
  btn.innerHTML = '<span class="btn-spinner"></span>Processing…';
  btn.disabled  = true;

  await new Promise(r => setTimeout(r, 800));

  closeModal('modal-medium');
  document.body.classList.remove('risk-medium');

  const r = await saveTransfer('completed', mediumAuthMethod);
  if (r?.new_balance != null) {
    document.getElementById('balance').textContent         = formatALL(r.new_balance);
    document.getElementById('new-balance-low').textContent = formatALL(r.new_balance);
  }

  confetti({ particleCount: 100, spread: 70, origin: { y: 0.6 },
             colors: ['#1565C0','#42a5f5','#fff'] });
  openModal('modal-low');
  await loadUser();
  setTimeout(resetTransferForm, 2000);
};

/* ═══════════════════════════════════════════════════════════════
   HIGH: FIDO code verification
═══════════════════════════════════════════════════════════════ */
window.verifyFido = async function () {
  const codeInput = document.getElementById('fido-code-input');
  const errEl     = document.getElementById('fido-error');
  const code      = codeInput.value.trim();

  codeInput.classList.remove('error');
  errEl.style.display = 'none';

  if (code === '12345') {
    // Correct — proceed to biometric
    document.getElementById('fido-step').style.display      = 'none';
    document.getElementById('biometric-step').style.display = 'block';
  } else {
    // Wrong — shake + show error + lockdown
    codeInput.classList.add('error');
    errEl.style.display = 'block';
    await new Promise(r => setTimeout(r, 1200));
    document.getElementById('fido-step').style.display = 'none';
    errEl.style.display = 'none';
    document.getElementById('passkey-fail').style.display = 'block';
    await triggerLockdown();
  }
};

/* ── Biometric step after correct FIDO ── */
window.simulateBiometricHigh = async function (method) {
  document.getElementById('biometric-step').style.display = 'none';
  document.getElementById('high-spinner').style.display   = 'block';

  await new Promise(r => setTimeout(r, 2000));

  document.getElementById('high-spinner').style.display = 'none';

  // SUCCESS — save transfer as completed
  const r = await saveTransfer('completed');
  if (r?.new_balance != null) {
    document.getElementById('balance').textContent         = formatALL(r.new_balance);
    document.getElementById('new-balance-low').textContent = formatALL(r.new_balance);
  }

  document.getElementById('lockdown-screen').classList.remove('active');
  document.body.classList.remove('risk-high');

  confetti({ particleCount: 180, spread: 90, origin: { y: 0.6 },
             colors: ['#2E7D32','#43A047','#66bb6a','#1565C0','#fff'] });
  openModal('modal-low');
  await loadUser();
  setTimeout(resetTransferForm, 2000);
};

/* ── Lock account (wrong FIDO or other failure) ── */
async function triggerLockdown() {
  await saveTransfer('blocked');
  await loadUser();
  accountLocked = true;

  const btn = document.getElementById('transfer-btn');
  if (btn) { btn.disabled = true; btn.textContent = '🔒 Account Locked'; }
  ['recipient-name','recipient-iban','amount','password-field'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.disabled = true;
  });

  const notice = document.getElementById('lockdown-notice');
  if (notice) notice.style.display = 'block';
  startLockdownTimer();
  showToast('Account locked for 24 hours.', 'error', '🔒');
}

/* ═══════════════════════════════════════════════════════════════
   SAVE TRANSFER
═══════════════════════════════════════════════════════════════ */
async function saveTransfer(status, resolutionMethod = null) {
  if (!pendingTransfer) return null;
  try {
    const res = await fetch('/api/transfer', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...pendingTransfer,
        status,
        ...(resolutionMethod ? { resolution_method: resolutionMethod } : {}),
      }),
    });
    return await res.json();
  } catch (e) { console.error('saveTransfer:', e); return null; }
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
function openModal(id) { document.getElementById(id).classList.add('active'); }
window.closeModal = function (id) {
  document.getElementById(id).classList.remove('active');
  const btn = document.getElementById('transfer-btn');
  if (btn && !accountLocked) { btn.disabled = false; btn.textContent = 'Transfer Funds →'; }
};

function resetTransferForm() {
  if (accountLocked) return;
  ['recipient-name','recipient-iban','amount','password-field'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = '';
  });
  formStartTime    = null;
  passwordTotalMs  = 0;
  passwordFocusTime = null;
  pendingTransfer  = null;
  mediumAuthMethod = null;
  ibanFromDropdown = false;
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
  initClipboardDetection();
  initTabSwitchDetection();
  checkSessionSource();
  initTransferButton();

  tickTiranaClock();
  setInterval(tickTiranaClock, 1000);
});

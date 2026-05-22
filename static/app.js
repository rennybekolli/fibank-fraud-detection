/**
 * Fibank Fraud Detection Demo — app.js
 *
 * DEMO SCENARIOS — use the presenter "Quick Scenario" buttons or set manually:
 *
 * SCENARIO 1 — LOW RISK (~1.9):
 *   Presenter: click [Scenario 1] button (all defaults)
 *   User: transfer 5,000 ALL to Elton Hoxha
 *   Result: green background + confetti + instant completion
 *
 * SCENARIO 2 — MEDIUM RISK (~3.6–4.2):
 *   Presenter: click [Scenario 2] button (auto-applies)
 *     Sets: is_historical_payee=OFF, used_fido_passkey=OFF,
 *           timezone_mismatch=ON, is_neobank_routing=ON, mouse_linearity=0.65
 *   Optional: user visits Profile and edits location (adds profile_updated signal)
 *   User: transfer 85,000 ALL to any new recipient
 *   Result: amber background + Albanian ID verification popup
 *
 * SCENARIO 3 — HIGH RISK (~7.1–7.6):
 *   Presenter: click [Scenario 3] button (auto-applies)
 *     Sets: mule_potential=ON, bot_agility_index=ON, is_screensharing_active=ON,
 *           timezone_mismatch=ON, session_tension=ON, mouse_linearity=0.9, typing=0.9
 *   User: transfer 50,000 ALL to unknown recipient
 *   Result: red background + siren + FIDO passkey fails + 24hr lockdown
 *   NOTE: is_in_active_call/coached_fraud_index are available for visual drama
 *         but mule+bot+screen are the primary model drivers for HIGH score
 */

'use strict';

/* ── Session tracking ── */
const SESSION = {
  loginTime: Date.now(),
  pagesVisited: parseInt(sessionStorage.getItem('pagesVisited') || '1', 10),
  passwordPasted: false,
};

// Increment page counter on each load
sessionStorage.setItem('pagesVisited', SESSION.pagesVisited);

/* ── Pending transfer state (filled by /api/score, used by /api/transfer) ── */
let pendingTransfer = null;

/* ── Lock state ── */
let accountLocked = false;
let lockCountdownInterval = null;

/* ── Format helpers ── */
function formatALL(n) {
  return Number(n).toLocaleString('sq-AL');
}

function formatDate(iso) {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
  } catch { return iso; }
}

function riskBadge(level) {
  const map = { low: 'badge-low', medium: 'badge-medium', high: 'badge-high' };
  const labels = { low: 'LOW', medium: 'MEDIUM', high: 'HIGH' };
  return `<span class="badge ${map[level] || 'badge-low'}">${labels[level] || level.toUpperCase()}</span>`;
}

function statusBadge(status) {
  const cls = `status-${status}`;
  const icons = { completed: '✓', blocked: '✕', pending: '⏳' };
  return `<span class="status-badge ${cls}">${icons[status] || ''} ${status}</span>`;
}

/* ── Load user data ── */
async function loadUser() {
  try {
    const res = await fetch('/api/user');
    const data = await res.json();
    const u = data.user;
    const txns = data.transactions;

    document.getElementById('user-name').textContent = u.name.split(' ')[0];
    document.getElementById('user-iban').textContent = u.iban;
    document.getElementById('balance').textContent = formatALL(u.balance);

    document.getElementById('stat-txns').textContent = txns.length;
    document.getElementById('stat-age').textContent = u.account_age_days + ' days';
    document.getElementById('stat-location').textContent = u.location.split(',')[0] + ', AL';

    renderTxnTable(txns);
  } catch (e) {
    console.error('Failed to load user:', e);
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
    </tr>
  `).join('');
}

/* ── Password field timing ── */
let passwordFocusTime = null;
let passwordTotalMs = 0;

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

/* ── Form completion timing ── */
let formStartTime = null;

function initFormTracking() {
  const fields = ['recipient-name', 'recipient-iban', 'amount', 'password-field'];
  fields.forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('focus', () => {
      if (!formStartTime) formStartTime = Date.now();
    });
  });
}

/* ── Transfer button ── */
function initTransferButton() {
  const btn = document.getElementById('transfer-btn');
  if (!btn) return;

  btn.addEventListener('click', async () => {
    if (accountLocked) return;

    const recipientName = document.getElementById('recipient-name').value.trim();
    const recipientIban = document.getElementById('recipient-iban').value.trim();
    const amount = parseFloat(document.getElementById('amount').value);

    if (!recipientName || !recipientIban || !amount || amount <= 0) {
      alert('Please fill in all fields.');
      return;
    }

    btn.disabled = true;
    btn.textContent = 'Analysing…';

    // Collect timing signals
    if (passwordFocusTime) {
      passwordTotalMs += Date.now() - passwordFocusTime;
    }
    const formCompletionSec = formStartTime
      ? (Date.now() - formStartTime) / 1000
      : 15;
    const timeLoginSec = (Date.now() - SESSION.loginTime) / 1000;

    const payload = {
      amount,
      recipient_name: recipientName,
      recipient_iban: recipientIban,
      form_completion_time_sec: formCompletionSec,
      password_entry_ms: passwordTotalMs || 2000,
      pages_visited_pre_transfer: SESSION.pagesVisited,
      time_login_to_transfer_sec: timeLoginSec,
    };

    try {
      const res = await fetch('/api/score', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const result = await res.json();

      pendingTransfer = {
        amount,
        recipient_name: recipientName,
        recipient_iban: recipientIban,
        risk_score: result.risk_score,
        risk_level: result.risk_level,
        triggered_signals: result.triggered_signals,
      };

      handleRiskResponse(result);
    } catch (e) {
      console.error('Score API error:', e);
      btn.disabled = false;
      btn.textContent = 'Transfer Funds →';
      alert('Connection error. Please try again.');
    }
  });
}

/* ── Risk response handlers ── */
function handleRiskResponse(result) {
  const { risk_level, explanation } = result;

  if (risk_level === 'low') {
    handleLow();
  } else if (risk_level === 'medium') {
    handleMedium(explanation);
  } else {
    handleHigh(explanation);
  }
}

async function handleLow() {
  // Green background flash
  document.body.classList.add('risk-low');

  // Fire confetti
  confetti({ particleCount: 150, spread: 80, origin: { y: 0.6 }, colors: ['#2E7D32', '#43A047', '#66bb6a', '#fff'] });

  // Save transaction
  const r = await saveTransfer('completed');
  if (r && r.new_balance !== null) {
    document.getElementById('balance').textContent = formatALL(r.new_balance);
    document.getElementById('new-balance-low').textContent = formatALL(r.new_balance);
  }

  openModal('modal-low');
  await loadUser();

  setTimeout(() => {
    document.body.classList.remove('risk-low');
    resetTransferForm();
  }, 3000);
}

function handleMedium(explanation) {
  // Amber background
  document.body.classList.add('risk-medium');

  document.getElementById('medium-explanation').textContent = explanation
    || 'Unusual patterns detected. Please verify your identity.';

  document.getElementById('verify-q1').value = '';
  document.getElementById('verify-q2').value = '';

  openModal('modal-medium');
}

function handleHigh(explanation) {
  // Dark red background
  document.body.classList.add('risk-high');

  document.getElementById('high-explanation').textContent = explanation
    || 'Multiple critical threat signals detected. Transfer blocked.';

  // Reset high modal state
  document.getElementById('high-btn-wrap').style.display = 'block';
  document.getElementById('passkey-spinner').style.display = 'none';
  document.getElementById('passkey-fail').style.display = 'none';

  openModal('modal-high');
}

/* ── Medium: verify & proceed ── */
window.completeMediumVerify = async function () {
  const q1 = document.getElementById('verify-q1').value.trim();
  const q2 = document.getElementById('verify-q2').value.trim();
  if (!q1 || !q2) {
    alert('Please answer both security questions.');
    return;
  }

  closeModal('modal-medium');
  document.body.classList.remove('risk-medium');

  const r = await saveTransfer('completed');
  if (r && r.new_balance !== null) {
    document.getElementById('balance').textContent = formatALL(r.new_balance);
    document.getElementById('new-balance-low').textContent = formatALL(r.new_balance);
  }

  openModal('modal-low');
  await loadUser();

  setTimeout(() => {
    resetTransferForm();
  }, 2000);
};

/* ── High: passkey attempt ── */
window.attemptPasskey = function () {
  document.getElementById('high-btn-wrap').style.display = 'none';
  document.getElementById('passkey-spinner').style.display = 'block';

  setTimeout(async () => {
    document.getElementById('passkey-spinner').style.display = 'none';
    document.getElementById('passkey-fail').style.display = 'block';

    await saveTransfer('blocked');
    await loadUser();

    // Lock the transfer form
    accountLocked = true;
    document.getElementById('transfer-btn').disabled = true;
    document.getElementById('transfer-btn').textContent = '🔒 Account Locked';
    ['recipient-name', 'recipient-iban', 'amount', 'password-field'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.disabled = true;
    });

    // Show lockdown notice
    const notice = document.getElementById('lockdown-notice');
    notice.style.display = 'block';
    startLockdownTimer();
  }, 2000);
};

window.closeHighModal = function () {
  closeModal('modal-high');
};

/* ── Save transfer to DB ── */
async function saveTransfer(status) {
  if (!pendingTransfer) return null;
  try {
    const res = await fetch('/api/transfer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...pendingTransfer, status }),
    });
    return await res.json();
  } catch (e) {
    console.error('Transfer save error:', e);
    return null;
  }
}

/* ── Lockdown countdown ── */
function startLockdownTimer() {
  let remaining = 24 * 3600; // 24 hours in seconds
  const el = document.getElementById('lockdown-timer');

  lockCountdownInterval = setInterval(() => {
    remaining--;
    if (remaining <= 0) {
      clearInterval(lockCountdownInterval);
      el.textContent = '00:00:00';
      return;
    }
    const h = Math.floor(remaining / 3600);
    const m = Math.floor((remaining % 3600) / 60);
    const s = remaining % 60;
    el.textContent = `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  }, 1000);
}

/* ── Modal helpers ── */
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
  document.getElementById('recipient-name').value = '';
  document.getElementById('recipient-iban').value = '';
  document.getElementById('amount').value = '';
  document.getElementById('password-field').value = '';
  formStartTime = null;
  passwordTotalMs = 0;
  passwordFocusTime = null;
  pendingTransfer = null;
  const btn = document.getElementById('transfer-btn');
  if (btn) { btn.disabled = false; btn.textContent = 'Transfer Funds →'; }
}

/* ── Boot ── */
document.addEventListener('DOMContentLoaded', () => {
  // Only run on index page
  if (!document.getElementById('transfer-btn')) return;
  loadUser();
  initPasswordTracking();
  initFormTracking();
  initTransferButton();
});

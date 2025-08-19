/* Minimal SPA to interact with Helios API */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function fmtTime(iso) {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleString(); } catch { return String(iso); }
}

function toast(msg, ok=true) {
  const el = $('#toast');
  el.textContent = msg;
  el.style.borderColor = ok ? 'rgba(49,196,141,.5)' : 'rgba(239,68,68,.5)';
  el.hidden = false;
  clearTimeout(el._t);
  el._t = setTimeout(()=> el.hidden = true, 2200);
}

async function api(path, opts={}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers||{}) },
    ...opts
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('application/json')) return res.json();
  return res.text();
}

async function refreshHealth() {
  try {
    await api('/health');
    const el = $('#health-indicator');
    el.textContent = 'Health: ok';
    el.className = 'chip chip-ok';
  } catch {
    const el = $('#health-indicator');
    el.textContent = 'Health: error';
    el.className = 'chip chip-bad';
  }
}

async function refreshStatus() {
  try {
    const s = await api('/status');
    $('#automation-paused').textContent = s.automation_paused ? 'yes' : 'no';
    $('#last-recalc').textContent = fmtTime(s.last_recalc_at);
    $('#last-control').textContent = fmtTime(s.last_control_at);
    const btn = $('#pause-resume');
    btn.textContent = s.automation_paused ? 'Resume' : 'Pause';
    btn.dataset.paused = String(s.automation_paused);
  } catch (e) {
    toast('Failed to load status', false);
  }
}

function slotClass(action) {
  if (action === 'charge_from_grid') return 'charge';
  if (action === 'export_to_grid') return 'export';
  return 'idle';
}

async function refreshPlan() {
  const container = $('#plan-container');
  try {
    const p = await api('/plan');
    const now = Date.now();
    container.innerHTML = '';
    p.slots.forEach(slot => {
      const start = new Date(slot.start).getTime();
      const end = new Date(slot.end).getTime();
      const current = start <= now && now < end;
      const div = document.createElement('div');
      div.className = `slot ${slotClass(slot.action)} ${current ? 'current':''}`;
      const setpoint = slot.target_grid_setpoint_w;
      const sp = (setpoint > 0 ? `+${setpoint}` : setpoint);
      div.innerHTML = `
        <div class="time">${new Date(start).toLocaleTimeString()} – ${new Date(end).toLocaleTimeString()}</div>
        <div class="action">${slot.action}</div>
        <div class="muted">setpoint: <strong>${sp} W</strong></div>
      `;
      container.appendChild(div);
    });
  } catch (e) {
    container.innerHTML = '<div class="muted">Plan not ready</div>';
  }
}

function coerceValue(key, value, current) {
  if (value === '' || value === null || value === undefined) return null;
  if (typeof current === 'number') return Number(value);
  if (typeof current === 'boolean') return value === 'true' || value === true;
  return String(value);
}

async function refreshConfig() {
  try {
    const resp = await api('/config');
    const cfg = resp.data || {};

    // Fill quick fields
    const quickKeys = ['grid_import_limit_w','grid_export_limit_w','grid_sell_enabled','price_hysteresis_eur_per_kwh','executor_backend','price_provider','assumed_current_soc_percent'];
    quickKeys.forEach(k => {
      const el = document.getElementById(k);
      if (!el) return;
      const v = cfg[k];
      if (el.tagName === 'SELECT') el.value = String(v);
      else el.value = v === null || v === undefined ? '' : v;
    });

    // Secrets info
    const tibberPresent = !!cfg.tibber_token_present;
    const owPresent = !!cfg.openweather_api_key_present;
    $('#secrets-info').textContent = `tibber_token set: ${tibberPresent} · openweather_api_key set: ${owPresent}`;

    // Build all settings grid (editable except read-only derived fields)
    const container = $('#all-settings');
    container.innerHTML = '';
    const readOnly = new Set(['tibber_token_present','openweather_api_key_present']);
    Object.keys(cfg).sort().forEach(key => {
      const value = cfg[key];
      const item = document.createElement('div');
      item.className = 'item';
      item.dataset.key = key;
      let inputHtml = '';
      if (typeof value === 'boolean') {
        inputHtml = `<select><option value="false" ${!value?'selected':''}>false</option><option value="true" ${value?'selected':''}>true</option></select>`;
      } else if (typeof value === 'number') {
        inputHtml = `<input type="number" value="${value}">`;
      } else if (value === null || value === undefined) {
        inputHtml = `<input type="text" value="">`;
      } else {
        inputHtml = `<input type="text" value="${String(value)}">`;
      }
      if (readOnly.has(key)) {
        inputHtml = `<input type="text" value="${String(value)}" disabled>`;
      }
      item.innerHTML = `<label>${key}${inputHtml}</label>`;
      container.appendChild(item);
    });
    container._rawConfig = cfg;
  } catch (e) {
    toast('Failed to load config', false);
  }
}

async function saveQuick() {
  const payload = {
    grid_import_limit_w: $('#grid_import_limit_w').value ? Number($('#grid_import_limit_w').value) : null,
    grid_export_limit_w: $('#grid_export_limit_w').value ? Number($('#grid_export_limit_w').value) : null,
    grid_sell_enabled: $('#grid_sell_enabled').value,
    price_hysteresis_eur_per_kwh: $('#price_hysteresis_eur_per_kwh').value ? Number($('#price_hysteresis_eur_per_kwh').value) : null,
    executor_backend: $('#executor_backend').value,
    price_provider: $('#price_provider').value,
    assumed_current_soc_percent: $('#assumed_current_soc_percent').value ? Number($('#assumed_current_soc_percent').value) : null,
  };
  // Clean nulls (server ignores None)
  Object.keys(payload).forEach(k => payload[k] === null && delete payload[k]);
  try {
    await api('/config', { method: 'PUT', body: JSON.stringify(payload)});
    toast('Saved');
    await Promise.all([refreshConfig(), refreshPlan(), refreshStatus()]);
  } catch (e) {
    toast('Save failed: ' + e.message, false);
  }
}

async function saveSecrets() {
  const payload = {};
  const tt = $('#tibber_token').value.trim();
  const th = $('#tibber_home_id').value.trim();
  const ow = $('#openweather_api_key').value.trim();
  if (tt) payload.tibber_token = tt;
  if (th) payload.tibber_home_id = th;
  if (ow) payload.openweather_api_key = ow;
  if (Object.keys(payload).length === 0) { toast('Nothing to save'); return; }
  try {
    await api('/config', { method: 'PUT', body: JSON.stringify(payload)});
    toast('Secrets saved');
    $('#tibber_token').value = '';
    $('#openweather_api_key').value = '';
    await refreshConfig();
  } catch (e) {
    toast('Save failed: ' + e.message, false);
  }
}

async function saveAll() {
  const container = $('#all-settings');
  const raw = container._rawConfig || {};
  const updates = {};
  $$('#all-settings .item').forEach(item => {
    const key = item.dataset.key;
    if (key === 'tibber_token_present' || key === 'openweather_api_key_present') return;
    const input = item.querySelector('input,select');
    const current = raw[key];
    const value = input.tagName === 'SELECT' ? input.value : input.value;
    const coerced = coerceValue(key, value, current);
    if (coerced === null) return; // skip nulls
    // Do not send unchanged values to keep payload small
    if (String(coerced) !== String(current)) updates[key] = coerced;
  });
  if (Object.keys(updates).length === 0) { toast('No changes'); return; }
  try {
    await api('/config', { method: 'PUT', body: JSON.stringify(updates)});
    toast('Settings saved');
    await Promise.all([refreshConfig(), refreshPlan(), refreshStatus()]);
  } catch (e) {
    toast('Save failed: ' + e.message, false);
  }
}

async function doPause() {
  try { await api('/pause', { method: 'POST' }); toast('Paused'); } catch (e) { toast('Pause failed', false); }
  await refreshStatus();
}
async function doResume() {
  try { await api('/resume', { method: 'POST' }); toast('Resumed'); } catch (e) { toast('Resume failed', false); }
  await refreshStatus();
}

async function loadMetrics() {
  try { const text = await api('/metrics'); $('#metrics').textContent = text; }
  catch (e) { toast('Failed to load metrics', false); }
}

function initTabs() {
  $$('.tab').forEach(btn => {
    btn.addEventListener('click', () => {
      $$('.tab').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const id = btn.dataset.tab;
      $$('.tab-panel').forEach(p => p.classList.remove('active'));
      $('#tab-' + id).classList.add('active');
    });
  });
}

function init() {
  initTabs();
  refreshHealth();
  refreshStatus();
  refreshPlan();
  refreshConfig();

  // Actions
  $('#btn-pause').addEventListener('click', doPause);
  $('#btn-resume').addEventListener('click', doResume);
  $('#btn-refresh').addEventListener('click', () => { refreshStatus(); refreshPlan(); });
  $('#btn-save-quick').addEventListener('click', saveQuick);
  $('#btn-save-secrets').addEventListener('click', saveSecrets);
  $('#btn-save-all').addEventListener('click', saveAll);
  $('#btn-load-metrics').addEventListener('click', loadMetrics);

  // Header pause/resume
  $('#pause-resume').addEventListener('click', async (e) => {
    const paused = e.currentTarget.dataset.paused === 'true';
    if (paused) await doResume(); else await doPause();
  });

  // Auto refresh
  const auto = $('#auto-refresh');
  let timer = setInterval(refreshPlan, 15000);
  auto.addEventListener('change', () => {
    clearInterval(timer);
    if (auto.checked) timer = setInterval(refreshPlan, 15000);
  });
}

document.addEventListener('DOMContentLoaded', init);


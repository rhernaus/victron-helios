/* Minimal SPA to interact with Helios API */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// Global UI/app state for synchronized X-axis
const __appState = {
  xDomainMode: 'auto', // 'auto' | 'plan' | 'prices' | 'today' | 'yesterday' | 'last24h' | 'next24h' | 'custom'
  xFromMs: null,
  xToMs: null,
};

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
    if ($('#current-action')) $('#current-action').textContent = s.current_action || '—';
    if ($('#current-setpoint')) $('#current-setpoint').textContent = (s.current_setpoint_w ?? '—') + (s.current_setpoint_w != null ? ' W' : '');
    if ($('#current-reason')) $('#current-reason').textContent = s.current_reason || '—';
    if ($('#telemetry-soc')) $('#telemetry-soc').textContent = (s.soc_percent != null) ? `${s.soc_percent}%` : '—';
    if ($('#telemetry-load')) $('#telemetry-load').textContent = (s.load_w != null) ? `${s.load_w} W` : '—';
    if ($('#telemetry-solar')) $('#telemetry-solar').textContent = (s.solar_w != null) ? `${s.solar_w} W` : '—';
    if ($('#telemetry-ev')) $('#telemetry-ev').textContent = s.ev_charger_status ? JSON.stringify(s.ev_charger_status) : '—';
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
    // Also fetch price series for graphs
    let priceData = null;
    try { priceData = await api('/prices'); } catch (_) { priceData = null; }
    const now = Date.now();
    if ($('#plan-summary')) $('#plan-summary').textContent = p.summary || '—';
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
        ${slot.reason ? `<div class="muted">reason: ${slot.reason}</div>` : ''}
      `;
      container.appendChild(div);
    });

    // Persist last loaded datasets and render charts
    window.__lastPlan = p;
    window.__lastPrices = priceData;
    // Render charts when plan and optional prices are available
    try { renderCharts(p, priceData); } catch(e) { /* best-effort; keep UI */ }
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
    const quickKeys = ['grid_import_limit_w','grid_export_limit_w','grid_sell_enabled','price_hysteresis_eur_per_kwh','executor_backend','telemetry_backend','price_provider','assumed_current_soc_percent'];
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
    telemetry_backend: $('#telemetry_backend').value,
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

async function doRecalcNow() {
  try { await api('/recalc', { method: 'POST' }); toast('Recalculated'); }
  catch (e) { toast('Recalc failed: ' + e.message, false); }
  await Promise.all([refreshPlan(), refreshStatus()]);
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
      const panel = document.getElementById('tab-' + id) || document.querySelector(`#tab-${id}`) || document.getElementById(`tab-${id}`);
      if (panel) panel.classList.add('active');
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
  const recalcBtn = $('#btn-recalc-now'); if (recalcBtn) recalcBtn.addEventListener('click', doRecalcNow);

  // Range selector for synchronized X-axis
  const rangeSel = $('#x-range');
  const customWrap = $('#x-range-custom');
  const fromInp = $('#x-from');
  const toInp = $('#x-to');
  const applyBtn = $('#btn-apply-range');
  if (rangeSel) {
    const refreshFromState = () => { if (customWrap) customWrap.style.display = (rangeSel.value === 'custom') ? 'inline-flex' : 'none'; };
    refreshFromState();
    rangeSel.addEventListener('change', () => {
      __appState.xDomainMode = rangeSel.value;
      refreshFromState();
      try { renderCharts(window.__lastPlan, window.__lastPrices); } catch(_) {}
    });
    if (applyBtn) {
      applyBtn.addEventListener('click', () => {
        const from = fromInp && fromInp.value ? new Date(fromInp.value).getTime() : null;
        const to = toInp && toInp.value ? new Date(toInp.value).getTime() : null;
        if (from && to && to > from) { __appState.xFromMs = from; __appState.xToMs = to; __appState.xDomainMode = 'custom'; }
        try { renderCharts(window.__lastPlan, window.__lastPrices); } catch(_) {}
      });
    }
  }

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

// ----- Charts (lightweight canvas rendering; no external deps) -----
function renderCharts(plan, prices) {
  const domain = computeGlobalDomain(plan, prices);
  drawPriceChart($('#chart-prices'), prices, domain);
  drawEnergyChart($('#chart-energy'), plan, domain);
  drawCostsChart($('#chart-costs'), plan, prices, domain);
}

function makeScale(domainMin, domainMax, rangeMin, rangeMax) {
  const span = domainMax - domainMin || 1;
  const k = (rangeMax - rangeMin) / span;
  return (v) => rangeMin + (v - domainMin) * k;
}

function clearCanvas(canvas) {
  if (!canvas) return { ctx: null, W: 0, H: 0 };
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const W = Math.min(1200, canvas.clientWidth || canvas.width);
  const rawH = (canvas.clientHeight || canvas.height || 500);
  const H = Math.min(500, rawH);
  canvas.width = Math.floor(W * dpr);
  canvas.height = Math.floor(H * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  return { ctx, W, H };
}

// simple diagonal stripe pattern cache per color
const __stripeCache = new Map();
function getStripePattern(ctx, color) {
  const key = color || '#fff';
  if (__stripeCache.has(key)) return __stripeCache.get(key);
  const c = document.createElement('canvas');
  c.width = 8; c.height = 8;
  const g = c.getContext('2d');
  g.strokeStyle = color; g.lineWidth = 2; g.globalAlpha = 0.9;
  g.beginPath();
  g.moveTo(-2, 8); g.lineTo(8, -2); // diagonal
  g.stroke();
  const pat = ctx.createPattern(c, 'repeat');
  __stripeCache.set(key, pat);
  return pat;
}

function drawAxes(ctx, W, H, yZero) {
  ctx.strokeStyle = '#1f2a37';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(40, 10);
  ctx.lineTo(40, H - 24);
  ctx.lineTo(W - 10, H - 24);
  ctx.stroke();
  // Y ticks and labels
  ctx.fillStyle = 'rgba(255,255,255,.6)';
  ctx.font = '12px system-ui, sans-serif';
  for (let i = 0; i <= 4; i++) {
    const gy = 10 + (H - 34) * (i / 4);
    ctx.fillRect(37, gy, 3, 1);
  }
  if (yZero !== null && yZero !== undefined) {
    ctx.strokeStyle = 'rgba(255,255,255,.15)';
    ctx.beginPath();
    ctx.moveTo(40, yZero);
    ctx.lineTo(W - 10, yZero);
    ctx.stroke();
  }
}

function timeDomainFromPlan(plan) {
  if (!plan || !plan.slots || plan.slots.length === 0) {
    const now = Date.now();
    return [now, now + 3600_000];
  }
  const start = new Date(plan.slots[0].start).getTime();
  const end = new Date(plan.slots[plan.slots.length - 1].end).getTime();
  return [start, end];
}

function timeDomainFromPrices(prices) {
  const items = (prices && prices.items) ? prices.items : [];
  if (!items.length) { const now = Date.now(); return [now, now + 3600_000]; }
  const start = new Date(items[0].t).getTime();
  const end = new Date(items[items.length - 1].t).getTime() + 3600_000;
  return [start, end];
}

function computeGlobalDomain(plan, prices) {
  const hour = 3600_000;
  const [p0, p1] = timeDomainFromPlan(plan);
  const [r0, r1] = timeDomainFromPrices(prices);
  const union = [Math.min(p0, r0), Math.max(p1, r1)];
  const now = Date.now();
  switch (__appState.xDomainMode) {
    case 'plan': return [p0, p1];
    case 'prices': return [r0, r1];
    case 'today': {
      const d = new Date(); d.setHours(0,0,0,0); const from = d.getTime(); return [from, from + 24*hour];
    }
    case 'yesterday': {
      const d = new Date(); d.setHours(0,0,0,0); const to = d.getTime(); return [to - 24*hour, to];
    }
    case 'last24h': return [now - 24*hour, now];
    case 'next24h': return [now, now + 24*hour];
    case 'custom': {
      if (__appState.xFromMs && __appState.xToMs && __appState.xToMs > __appState.xFromMs) return [__appState.xFromMs, __appState.xToMs];
      return union;
    }
    case 'auto':
    default: return union;
  }
}

function formatLocalDate(d) {
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`;
}

function drawHourTicks(ctx, x, tmin, tmax, H, padL, padR) {
  const hour = 3600_000;
  let t = Math.ceil(tmin / hour) * hour;
  ctx.fillStyle = 'rgba(255,255,255,.6)';
  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';
  ctx.font = '12px system-ui, sans-serif';
  ctx.strokeStyle = 'rgba(255,255,255,.06)';
  ctx.lineWidth = 1;
  for (; t <= tmax; t += hour) {
    const tx = x(t);
    ctx.beginPath(); ctx.moveTo(tx, 10); ctx.lineTo(tx, H - 24); ctx.stroke();
    const d = new Date(t);
    const isMidnight = d.getHours() === 0;
    const label = isMidnight ? formatLocalDate(d) : d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', hour12:false});
    ctx.save();
    ctx.translate(tx, H); // place below the axis
    ctx.rotate(-Math.PI / 4);
    ctx.fillText(label, 4, 0);
    ctx.restore();
  }
}

function drawPriceChart(canvas, prices, domainOverride) {
  if (!canvas) return;
  const { ctx, W, H } = clearCanvas(canvas);
  if (!ctx) return;
  const padL = 40, padB = 24, padT = 10, padR = 10;

  const items = (prices && prices.items) ? prices.items : [];
  let tmin = items.length ? new Date(items[0].t).getTime() : Date.now();
  let tmax = items.length ? new Date(items[items.length-1].t).getTime() + 3600_000 : tmin + 3600_000;
  if (domainOverride) { tmin = domainOverride[0]; tmax = domainOverride[1]; }
  const ymin = Math.min(0, ...items.map(i => Math.min(i.buy ?? i.raw, i.sell ?? i.raw)));
  const ymax = Math.max(0.01, ...items.map(i => Math.max(i.buy ?? i.raw, i.sell ?? i.raw)));
  const x = makeScale(tmin, tmax, padL, W - padR);
  const y = makeScale(ymin, ymax, H - padB, padT);
  drawAxes(ctx, W, H, y(0));

  // gridlines and Y labels
  ctx.strokeStyle = 'rgba(255,255,255,.06)';
  for (let g = 0; g <= 4; g++) {
    const gy = y(ymin + (ymax - ymin) * g / 4);
    ctx.beginPath(); ctx.moveTo(padL, gy); ctx.lineTo(W - padR, gy); ctx.stroke();
    ctx.fillStyle = 'rgba(255,255,255,.6)';
    ctx.font = '12px system-ui, sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText((ymin + (ymax - ymin) * g / 4).toFixed(2), padL - 6, gy + 4);
  }

  function drawLine(key, color) {
    const now = Date.now();
    ctx.lineWidth = 2;
    let segment = [];
    const flush = (predicted) => {
      if (segment.length < 2) { segment = []; return; }
      ctx.beginPath();
      ctx.setLineDash(predicted ? [6, 4] : []);
      ctx.strokeStyle = color;
      segment.forEach((pt, i) => { if (i===0) ctx.moveTo(pt[0], pt[1]); else ctx.lineTo(pt[0], pt[1]); });
      ctx.stroke();
      ctx.setLineDash([]);
      segment = [];
    };
    items.forEach((it, idx) => {
      const tt = new Date(it.t).getTime();
      const tx = x(tt);
      const val = it[key] ?? it.raw;
      const ty = y(val);
      const future = tt > now;
      // start new segment if needed
      if (segment.length && segment._future !== future) { flush(segment._future); }
      segment._future = future; // attach flag
      segment.push([tx, ty]);
    });
    flush(segment._future);
  }

  drawLine('buy', '#ef4444');
  drawLine('sell', '#84cc16');

  // Shared hour ticks
  drawHourTicks(ctx, x, tmin, tmax, H, padL, padR);

  // legend
  const legend = $('#legend-prices');
  if (legend) legend.innerHTML = `
    <div class="key"><span class="swatch" style="background:#ef4444"></span> buy price</div>
    <div class="key"><span class="swatch" style="background:#84cc16"></span> sell price</div>
  `;

  // Hover tooltip
  attachHover(canvas, (mx, my) => {
    // find nearest hour
    let nearest = null;
    let best = Infinity;
    for (const it of items) {
      const tt = new Date(it.t).getTime();
      const dx = Math.abs(mx - x(tt));
      if (dx < best) { best = dx; nearest = it; }
    }
    if (!nearest || best > 30) return null;
    const tt = new Date(nearest.t);
    return {
      x: x(new Date(nearest.t).getTime()),
      y: my,
      html: `${tt.toLocaleString()}<br>Buy: <b>${(nearest.buy ?? nearest.raw).toFixed(3)}</b><br>Sell: <b>${(nearest.sell ?? nearest.raw).toFixed(3)}</b>`
    };
  });
}

function drawEnergyChart(canvas, plan, domainOverride) {
  if (!canvas) return;
  const { ctx, W, H } = clearCanvas(canvas);
  if (!ctx) return;
  const padL = 40, padB = 24, padT = 10, padR = 10;

  let [tmin, tmax] = timeDomainFromPlan(plan);
  if (domainOverride) { tmin = domainOverride[0]; tmax = domainOverride[1]; }
  const x = makeScale(tmin, tmax, padL, W - padR);
  const ymin = -1; // dynamic below
  const ymax = 1;

  // Build stacked series per slot using annotated energy flows from the plan
  const slotSecs = plan.planning_window_seconds || 900;
  const streams = [
    { key: 'batt_grid', color: '#60a5fa', sign: 1 },
    { key: 'solar_grid', color: '#f59e0b', sign: 1 },
    { key: 'solar_batt', color: '#22c55e', sign: 1 },
    { key: 'solar_use', color: '#fde047', sign: 1 },
    { key: 'batt_use', color: '#3b82f6', sign: -1 },
    { key: 'grid_use', color: '#ef4444', sign: -1 },
    { key: 'grid_batt', color: '#ec4899', sign: -1 },
  ];

  // Compute values (kWh per slot)
  const bars = plan.slots.map(s => {
    const t0 = new Date(s.start).getTime();
    const obj = { t: t0 };
    streams.forEach(st => obj[st.key] = 0);
    obj['batt_grid'] = s.battery_to_grid_kwh || 0;
    obj['solar_grid'] = s.solar_to_grid_kwh || 0;
    obj['solar_batt'] = s.solar_to_battery_kwh || 0;
    obj['solar_use'] = s.solar_to_usage_kwh || 0;
    obj['batt_use'] = s.battery_to_usage_kwh || 0;
    obj['grid_use'] = s.grid_to_usage_kwh || 0;
    obj['grid_batt'] = s.grid_to_battery_kwh || 0;
    return obj;
  });

  const vmax = Math.max(0.01, ...bars.map(b => streams.reduce((acc, st) => acc + (st.sign > 0 ? b[st.key] : 0), 0)));
  const vmin = -Math.max(0.01, ...bars.map(b => streams.reduce((acc, st) => acc + (st.sign < 0 ? b[st.key] : 0), 0)));
  const y = makeScale(vmin, vmax, H - padB, padT);
  drawAxes(ctx, W, H, y(0));

  const slotMs = (plan.planning_window_seconds || 900) * 1000;
  const barW = Math.max(2, x(tmin + slotMs) - x(tmin) - 2);

  // draw stacked bars
  const now = Date.now();
  bars.forEach((b, idx) => {
    const cx = x(b.t) + 1;
    const predicted = b.t > now;
    let yPos = y(0);
    // positive stacks
    streams.filter(s => s.sign > 0).forEach(st => {
      const h = y(0) - y(b[st.key]);
      if (h <= 0) return;
      ctx.fillStyle = st.color; ctx.globalAlpha = predicted ? 0.5 : 0.9;
      ctx.fillRect(cx, yPos - h, barW, h);
      yPos -= h;
    });
    // negative stacks
    yPos = y(0);
    streams.filter(s => s.sign < 0).forEach(st => {
      const h = y(-b[st.key]) - y(0);
      if (h <= 0) return;
      ctx.fillStyle = st.color; ctx.globalAlpha = predicted ? 0.5 : 0.9;
      ctx.fillRect(cx, yPos, barW, h);
      yPos += h;
    });
  });

  const legend = $('#legend-energy');
  if (legend) legend.innerHTML = `
    <div class="key"><span class="swatch" style="background:#60a5fa"></span> From battery to grid</div>
    <div class="key"><span class="swatch" style="background:#f59e0b"></span> From solar to grid</div>
    <div class="key"><span class="swatch" style="background:#22c55e"></span> From solar to battery</div>
    <div class="key"><span class="swatch" style="background:#fde047"></span> From solar to usage</div>
    <div class="key"><span class="swatch" style="background:#3b82f6"></span> From battery to usage</div>
    <div class="key"><span class="swatch" style="background:#ef4444"></span> From grid to usage</div>
    <div class="key"><span class="swatch" style="background:#ec4899"></span> From grid to battery</div>
  `;

  // Shared hour ticks
  drawHourTicks(ctx, x, tmin, tmax, H, padL, padR);

  // Hover tooltip (stacked totals)
  attachHover(canvas, (mx, my) => {
    // map x to bar index
    const idx = Math.round((mx - padL) / ((W - padL - padR) / bars.length));
    const b = bars[idx];
    if (!b) return null;
    const t = new Date(b.t);
    const lines = [
      `From battery to grid: <b>${(b.batt_grid).toFixed(3)} kWh</b>`,
      `From solar to grid: <b>${(b.solar_grid).toFixed(3)} kWh</b>`,
      `From solar to battery: <b>${(b.solar_batt).toFixed(3)} kWh</b>`,
      `From solar to usage: <b>${(b.solar_use).toFixed(3)} kWh</b>`,
      `From battery to usage: <b>${(b.batt_use).toFixed(3)} kWh</b>`,
      `From grid to usage: <b>${(b.grid_use).toFixed(3)} kWh</b>`,
      `From grid to battery: <b>${(b.grid_batt).toFixed(3)} kWh</b>`,
    ];
    return {
      x: x(b.t) + barW/2,
      y: my,
      html: `${t.toLocaleString()}<br>${lines.join('<br>')}`
    };
  });
}

function drawCostsChart(canvas, plan, prices, domainOverride) {
  if (!canvas) return;
  const { ctx, W, H } = clearCanvas(canvas);
  if (!ctx) return;
  const padL = 40, padB = 24, padT = 10, padR = 10;

  // derive per-slot cost using buy/sell price at hour of slot midpoint
  const items = (prices && prices.items) ? prices.items : [];
  const priceAt = (timeMs) => {
    if (!items.length) return { buy: 0, sell: 0 };
    const hour = new Date(timeMs).setMinutes(0,0,0);
    let best = items[0];
    let bestDiff = Math.abs(new Date(best.t).getTime() - hour);
    for (const it of items) {
      const diff = Math.abs(new Date(it.t).getTime() - hour);
      if (diff < bestDiff) { best = it; bestDiff = diff; }
    }
    return { buy: best.buy ?? best.raw, sell: best.sell ?? best.raw };
  };

  const slotSecs = plan.planning_window_seconds || 900;
  const bars = plan.slots.map(s => {
    const mid = new Date(s.start).getTime() + (slotSecs*500);
    const pr = priceAt(mid);
    // Prefer server-computed costs if present
    let gridCost = s.grid_cost_eur ?? 0;
    let gridSave = s.grid_savings_eur ?? 0;
    let battCost = s.battery_cost_eur ?? 0;
    if (s.grid_cost_eur == null || s.grid_savings_eur == null || s.battery_cost_eur == null) {
      // Fallback from energy flows and prices
      const impKwh = (s.grid_to_usage_kwh || 0) + (s.grid_to_battery_kwh || 0);
      const expKwh = (s.battery_to_grid_kwh || 0) + (s.solar_to_grid_kwh || 0);
      gridCost = impKwh * pr.buy;
      gridSave = expKwh * pr.sell;
      // no local estimate for battCost when missing
    }
    return { t: new Date(s.start).getTime(), gridCost, gridSave, battCost };
  });
  let tmin = bars.length ? bars[0].t : Date.now();
  let tmax = bars.length ? bars[bars.length-1].t + slotSecs*1000 : tmin + 3600_000;
  if (domainOverride) { tmin = domainOverride[0]; tmax = domainOverride[1]; }

  const vmax = Math.max(0.01, ...bars.map(b => b.gridSave));
  const vmin = -Math.max(0.01, ...bars.map(b => Math.max(b.gridCost, b.battCost)));
  const x = makeScale(tmin, tmax, padL, W - padR);
  const y = makeScale(vmin, vmax, H - padB, padT);
  drawAxes(ctx, W, H, y(0));

  const barW = Math.max(2, x(tmin + slotSecs*1000) - x(tmin) - 4);
  const now = Date.now();
  bars.forEach(b => {
    const cx = x(b.t) + 2;
    const predicted = b.t > now;
    // negative bars
    let y0 = y(0);
    const drawNeg = (val, color) => {
      const h = y(-val) - y(0);
      if (h <= 0) return;
      ctx.fillStyle = color; ctx.globalAlpha = predicted ? 0.5 : 0.9; ctx.fillRect(cx, y0, barW, h);
      y0 += h;
    };
    drawNeg(b.gridCost, '#ef4444');
    drawNeg(b.battCost, '#60a5fa');
    // positive bar
    const hp = y(0) - y(b.gridSave);
    if (hp > 0) { ctx.fillStyle = '#84cc16'; ctx.globalAlpha = predicted ? 0.5 : 0.9; ctx.fillRect(cx, y(0) - hp, barW, hp); }
  });

  const legend = $('#legend-costs');
  if (legend) legend.innerHTML = `
    <div class=\"key\"><span class=\"swatch\" style=\"background:#ef4444\"></span> Grid costs</div>
    <div class=\"key\"><span class=\"swatch\" style=\"background:#84cc16\"></span> Grid savings</div>
    <div class=\"key\"><span class=\"swatch\" style=\"background:#60a5fa\"></span> Battery costs</div>
  `;

  // Shared hour ticks
  drawHourTicks(ctx, x, tmin, tmax, H, padL, padR);

  // Hover tooltip
  attachHover(canvas, (mx, my) => {
    const idx = Math.round((mx - padL) / ((W - padL - padR) / bars.length));
    const b = bars[idx];
    if (!b) return null;
    const t = new Date(b.t);
    return {
      x: x(b.t) + barW/2,
      y: my,
      html: `${t.toLocaleString()}<br>Grid cost: <b>€${b.gridCost.toFixed(2)}</b><br>Grid savings: <b>€${b.gridSave.toFixed(2)}</b><br>Battery cost: <b>€${b.battCost.toFixed(2)}</b>`
    };
  });
}

// Attach hover helper to a canvas; cb returns {x,y,html} or null
function attachHover(canvas, compute) {
  if (!canvas) return;
  const tip = document.getElementById('chart-tooltip');
  if (!tip) return;
  let over = false;
  const show = (evt) => {
    const rect = canvas.getBoundingClientRect();
    const mx = evt.clientX - rect.left;
    const my = evt.clientY - rect.top;
    const res = compute(mx, my);
    if (!res) { tip.hidden = true; return; }
    tip.innerHTML = res.html;
    tip.style.left = `${evt.clientX + 12}px`;
    tip.style.top = `${evt.clientY + 12}px`;
    tip.hidden = false;
  };
  canvas.addEventListener('mousemove', show);
  canvas.addEventListener('mouseleave', () => { tip.hidden = true; });
}


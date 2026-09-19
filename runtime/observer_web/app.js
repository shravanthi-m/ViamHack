'use strict';
const $ = id => document.getElementById(id);
let state = null, frameId = null, busy = false, connected = false;
let liveSession = null, heartbeatAt = 0, refreshing = false, showingDemo = false;
let errorTimer = null, liveRetryAt = 0, liveStartError = null;
let scanning = false, scanSession = null;
let operatorGate = null, operatorSending = false;
let recoveryId = null, recovering = false;
let completionShown = false;
const taskButtons = document.querySelectorAll('[data-task]');

function fail(message) {
  $('error').textContent = message;
  $('error').hidden = false;
  clearTimeout(errorTimer);
  errorTimer = setTimeout(() => { $('error').hidden = true; }, 9000);
}
async function post(route, body = {}) {
  const response = await fetch(route, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const result = await response.json();
  if (response.status === 404 && route === '/api/request') {
    throw new Error('The website server is outdated. Restart with sh demos/start_ui.sh, then reload this page.');
  }
  if (!response.ok) throw new Error(result.error || 'This action could not be completed.');
  return result;
}
function controls() {
  const taskBlocked = !connected || busy || !!state?.demo?.active || state?.demo?.status === 'failed';
  taskButtons.forEach(button => { button.disabled = taskBlocked || (!!state?.demo?.reset_required && button.dataset.task !== 'reset'); });
  document.querySelector('#request-form button[type="submit"]').disabled = taskBlocked;
  $('continue-reset').disabled = taskBlocked || !state?.demo?.reset_required;
  $('scan').disabled = taskBlocked || !state?.vision_configured || !state?.live?.active
    || !state.live.frames || state.live.frame_age_s == null || state.live.frame_age_s > 3;
  $('scan').textContent = scanning ? 'Scanning…' : 'Scan objects';
  const gateBlocked = !connected || operatorSending || !state?.demo?.gate_id;
  $('operator-continue').disabled = $('operator-abort').disabled = gateBlocked;
  $('recovery-clear').disabled = !connected || busy || recovering || !!state?.demo?.active || !state?.demo?.failure_id;
  $('recovery-clear').textContent = recovering ? 'Checking station…' : 'Clear stopped task';
}
async function action(callback) {
  if (busy || !connected) return;
  busy = true; controls();
  try { await callback(); }
  catch (error) { fail(error.message); }
  finally { busy = false; await refresh(); controls(); }
}
function renderScene() {
  const live = state.live || {};
  if (scanSession && scanSession !== live.session) clearScan();
  if (!scanning && !state.vision_configured) {
    $('scan-status').textContent = 'Object scanning needs an OpenRouter API key in the station configuration.';
  }
  const active = !!live.active;
  const visible = active && !document.hidden;
  if (visible && liveSession !== live.session) {
    liveSession = live.session;
    $('live-scene').src = '/api/live.mjpg?session=' + encodeURIComponent(liveSession);
  } else if (!visible && liveSession) {
    $('live-scene').removeAttribute('src');
    liveSession = null;
  }
  if (state.frame && state.frame.id !== frameId) {
    frameId = state.frame.id;
    $('scene').src = '/api/frame?id=' + encodeURIComponent(frameId);
  }
  $('live-scene').hidden = !visible;
  $('scene').hidden = active || !state.frame;
  $('empty-state').hidden = active || !!state.frame;
  if (active) {
    const delayed = live.frame_age_s != null && live.frame_age_s > 3;
    $('image-status').textContent = live.status === 'connecting' ? 'CONNECTING' : delayed ? 'CAMERA DELAYED' : 'LIVE CAMERA';
    $('source-label').textContent = (state.view || 'Station') + ' · LIVE';
    $('age-label').textContent = live.captured_at ? new Date(live.captured_at).toLocaleTimeString() : 'WAITING FOR CAMERA';
    $('camera-status').textContent = live.error || (live.status === 'connecting' ? 'Connecting to camera…' : `${live.fps || 0} fps · ${live.frame_age_s ?? '—'}s since latest frame`);
  } else {
    $('image-status').textContent = state.frame ? 'SNAPSHOT · NOT LIVE' : 'WAITING FOR CAMERA';
    $('source-label').textContent = state.frame?.source?.toUpperCase() || 'STATION VISION';
    const stamp = state.frame?.captured_at || state.frame?.loaded_at;
    $('age-label').textContent = stamp ? new Date(stamp).toLocaleTimeString() : 'WAITING FOR CAMERA';
    $('camera-status').textContent = liveStartError || live.error || (state.demo?.active
      ? 'Camera will reconnect when the routine finishes.'
      : state.live_enabled ? 'Connecting to the overhead camera…' : 'Overhead camera is not configured.');
  }
  if (visible && Date.now() - heartbeatAt > 5000) {
    heartbeatAt = Date.now();
    post('/api/live/heartbeat', {session: live.session}).catch(() => {});
  }
}
function clearScan() {
  scanSession = null;
  $('scan-result').hidden = true;
  $('scan-image').removeAttribute('src');
  $('scan-boxes').replaceChildren();
}
function showScan(result) {
  const {width, height} = result.image;
  const overlay = $('scan-boxes');
  overlay.setAttribute('viewBox', `0 0 ${width} ${height}`);
  overlay.replaceChildren();
  function svg(tag, attributes, text) {
    const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value));
    if (text) element.textContent = text;
    overlay.appendChild(element);
  }
  for (const detection of result.detections) {
    const [left, top, right, bottom] = detection.bbox;
    const label = detection.object_id.replaceAll('_', ' ') + (detection.visibility === 'clear' ? '' : ` · ${detection.visibility}`);
    const x = left * width, y = top * height;
    const fontSize = Math.max(14, width / 48), labelHeight = fontSize * 1.5;
    const labelWidth = Math.min(width, label.length * fontSize * .62 + 12);
    const labelX = Math.max(0, Math.min(x, width - labelWidth));
    const labelY = Math.max(0, y - labelHeight);
    svg('rect', {x, y, width: (right - left) * width, height: (bottom - top) * height,
      class: 'object-box', 'vector-effect': 'non-scaling-stroke'});
    svg('rect', {x: labelX, y: labelY, width: labelWidth, height: labelHeight, class: 'object-label-bg'});
    svg('text', {x: labelX + 6, y: labelY + fontSize * 1.1, 'font-size': fontSize, class: 'object-label'}, label);
  }
  scanSession = result.session;
  $('scan-image').src = '/api/live/identified?id=' + encodeURIComponent(result.frame_id);
  $('scan-time').textContent = new Date(result.captured_at).toLocaleTimeString();
  $('scan-summary').textContent = result.summary;
  $('scan-status').textContent = result.detections.length
    ? `${result.detections.length} objects detected. Boxes show the scanned frame below.`
    : 'No configured objects detected. Try scanning again with a clearer view.';
  $('scan-result').hidden = false;
}
async function scanObjects() {
  if ($('scan').disabled) return;
  await action(async () => {
    scanning = true;
    clearScan();
    controls();
    $('scan-status').textContent = 'Scanning the overhead view…';
    try {
      const result = await post('/api/scan');
      if (!connected || result.session !== state?.live?.session) throw new Error('The camera session changed. Scan again.');
      showScan(result);
    } catch (error) {
      clearScan();
      $('scan-status').textContent = error.message;
      throw error;
    } finally { scanning = false; }
  });
}
async function ensureLive() {
  if (document.hidden || busy || state.demo?.active || !state.live_enabled || state.live?.active
      || Date.now() < liveRetryAt) return;
  // Retry camera failures at a bounded rate; the normal refresh cannot overlap starts.
  liveRetryAt = Date.now() + 10000;
  try {
    state.live = await post('/api/live/start', {identify: false});
    liveStartError = null;
  } catch (error) {
    liveStartError = error.message;
  }
}
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const response = await fetch('/api/state');
    if (!response.ok) throw new Error('Varista is unavailable');
    state = await response.json();
    if (state.api_version !== 2) {
      throw new Error('The website server is outdated. Restart with sh demos/start_ui.sh, then reload.');
    }
    connected = true;
    const demo = state.demo || {};
    const taskName = {signature: 'Signature pour', reset: 'Reset', shake: 'Shake'}[demo.task] || 'Task';
    $('recovery-next').hidden = !demo.reset_required || demo.active || demo.status === 'failed';
    const complete = demo.status === 'completed' && !demo.active;
    if (!complete) completionShown = false;
    const newCompletion = complete && !completionShown;
    $('task-readiness').textContent = demo.status === 'failed'
      ? 'Task stopped · Clear the stopped task before continuing.'
      : demo.active
        ? demo.status === 'waiting_operator' ? 'Waiting for your operator check.'
          : demo.status === 'completed' ? 'Finishing the task…' : `${taskName} is running…`
        : demo.reset_required ? 'Ready for Reset.'
        : complete ? `${taskName} complete · Ready for another task.` : 'Ready · Choose a task.';
    $('recovery-form').hidden = demo.status !== 'failed' || !demo.failure_id;
    if (recoveryId !== demo.failure_id) {
      recoveryId = demo.failure_id;
      $('recovery-held').value = '';
      $('recovery-inspected').checked = false;
      $('recovery-status').textContent = '';
    }
    $('recovery-reason').textContent = demo.failure_reason || 'The routine stopped. Inspect the station before starting a fresh task.';
    $('station-mode').textContent = demo.enabled ? 'SUPERVISED ROBOT MODE' : 'PREVIEW MODE';
    $('mode-note').textContent = demo.enabled
      ? 'Real arm actions enabled · Complete operator checks here. Shake starts and finishes holding.'
      : 'Preview mode · No robot motion. Start supervised mode to execute fixed tasks.';
    if (demo.reset_required) $('mode-note').textContent = 'Reset is required before another Pour or Shake. Complete Reset with the observed held state.';
    $('operator-form').hidden = !demo.gate_id;
    if (operatorGate !== demo.gate_id) {
      operatorGate = demo.gate_id;
      $('operator-answer').value = '';
      $('operator-status').textContent = '';
    }
    $('operator-prompt').textContent = demo.prompt || '';
    if (!busy && newCompletion) {
      completionShown = true;
      showingDemo = false;
      $('error').hidden = true;
      $('draft-text').hidden = true;
      $('draft-text').textContent = '';
      $('operator-answer').value = '';
      $('operator-status').textContent = '';
    }
    if ((!busy || demo.active) && (showingDemo || demo.active || (demo.enabled && demo.status !== 'idle' && (!complete || newCompletion)))) {
      const progress = {
        waiting_operator: ['WAITING FOR OPERATOR', demo.browser_gates ? 'Complete the operator check above to continue.' : 'Claudia is waiting for the operator’s check in the terminal.'],
        running: ['TASK RUNNING', 'Claudia is running the selected fixed task.'],
        completed: complete
          ? ['READY FOR NEXT TASK', `${taskName} completed and outcome confirmed. Choose another task above.`]
          : ['FINISHING TASK', 'Waiting for the routine to finish cleanup.'],
        failed: ['TASK STOPPED', demo.failure_reason || 'Inspect the station and use Clear stopped task above.'],
      }[demo.status];
      if (progress) {
        $('request-status').textContent = progress[0];
        $('reply').textContent = progress[1];
      }
    }
    await ensureLive();
    renderScene();
  } catch (error) {
    $('reply').textContent = error.message;
    connected = false;
    $('station-mode').textContent = 'DISCONNECTED';
    $('image-status').textContent = 'DISCONNECTED';
    $('camera-status').textContent = 'Connection lost. The scene may be out of date.';
    $('mode-note').textContent = 'Connection lost. Task status may be out of date.';
    $('live-scene').removeAttribute('src');
    liveSession = null;
    clearScan();
    $('operator-form').hidden = true;
    $('recovery-form').hidden = true;
    $('recovery-next').hidden = true;
    $('task-readiness').textContent = 'Disconnected · Waiting for task status.';
  } finally {
    refreshing = false;
    controls();
  }
}
async function clearStoppedTask() {
  const failureId = state?.demo?.failure_id;
  const held = $('recovery-held').value;
  if (!failureId || busy || recovering || !connected) return;
  if (!$('recovery-inspected').checked || !['empty', 'coconut_water', 'pitcher'].includes(held)) {
    $('recovery-status').textContent = 'Select the observed held state and confirm your inspection first.';
    return;
  }
  await action(async () => {
    recovering = true;
    controls();
    $('recovery-status').textContent = 'Reading the arm and gripper. No motion is commanded.';
    try {
      const result = await post('/api/recover', {failure_id: failureId, held, inspected: true});
      showingDemo = false;
      $('request-status').textContent = result.reset_required ? 'RESET REQUIRED' : 'READY FOR A FRESH TASK';
      $('reply').textContent = result.message;
      $('error').hidden = true;
    } catch (error) {
      $('recovery-status').textContent = error.message;
      throw error;
    } finally { recovering = false; }
  });
}
async function answerOperator(value) {
  const gate = state?.demo?.gate_id;
  if (!connected || operatorSending || !gate || !value.trim()) return;
  operatorSending = true;
  controls();
  try {
    await post('/api/operator', {gate_id: gate, answer: value.trim()});
    // Keep the old check disabled until the next server state arrives.
    if (state.demo.gate_id === gate) state.demo.gate_id = null;
    $('operator-status').textContent = 'Answer received.';
  } catch (error) {
    $('operator-status').textContent = error.message;
  } finally {
    operatorSending = false;
    await refresh();
    controls();
  }
}
async function submitRequest(text, task = null) {
  text = text.trim();
  if (!text || busy || !connected || state?.demo?.active) return;
  await action(async () => {
    showingDemo = false;
    $('request-status').textContent = 'SENDING REQUEST';
    try {
      const result = await post('/api/request', {text, review_only: false});
      showingDemo = ['signature', 'reset', 'shake'].includes(result.intent) && result.status === 'waiting_operator';
      if (showingDemo) completionShown = false;
      const unsupported = result.intent === 'unsupported' || result.intent === 'scene';
      $('request-status').textContent = unsupported ? 'TASK NOT CONNECTED'
        : result.status === 'preview' ? 'PREVIEW · NO ROBOT MOTION'
        : showingDemo ? 'WAITING FOR OPERATOR' : 'CLAUDIA';
      $('reply').textContent = unsupported
        ? 'Choose signature pour, reset, or shake held object. This request is unsupported; no motion was started.'
        : result.message;
      $('draft-text').textContent = text;
      $('draft-text').hidden = false;
      // Keep unsupported custom requests available for editing.
      if (!unsupported) $('request').value = '';
    } catch (error) {
      $('request-status').textContent = 'REQUEST NOT CONFIRMED';
      $('reply').textContent = error.message;
      throw error;
    }
  });
}
$('request-form').addEventListener('submit', event => { event.preventDefault(); submitRequest($('request').value); });
$('operator-form').addEventListener('submit', event => { event.preventDefault(); answerOperator($('operator-answer').value); });
$('operator-abort').addEventListener('click', () => answerOperator('q'));
$('recovery-form').addEventListener('submit', event => { event.preventDefault(); clearStoppedTask(); });
$('continue-reset').addEventListener('click', () => {
  if (!$('continue-reset').disabled) submitRequest('Reset station', 'reset');
});
taskButtons.forEach(button => button.addEventListener('click', () => submitRequest(button.dataset.prompt, button.dataset.task)));
$('scan').addEventListener('click', scanObjects);
$('scan-image').addEventListener('error', () => {
  clearScan();
  $('scan-status').textContent = 'The scanned frame is unavailable. Scan again.';
});
$('fullscreen').addEventListener('click', async () => {
  try { if (document.fullscreenElement) await document.exitFullscreen(); else await document.documentElement.requestFullscreen(); }
  catch (_) { fail('Full screen is unavailable in this browser.'); }
});
function detachLive() { $('live-scene').removeAttribute('src'); liveSession = null; }
document.addEventListener('visibilitychange', () => { if (document.hidden) detachLive(); else refresh(); });
window.addEventListener('pagehide', detachLive);
refresh();
setInterval(() => { if (!document.hidden) refresh(); }, 1500);

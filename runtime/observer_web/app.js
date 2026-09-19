'use strict';
const $ = id => document.getElementById(id);
let state = null, frameId = null, busy = false, voice = false, recognition = null;
let errorTimer = null, runSignature = null;
let showingDemo = false;
let recorder = null, recordingStream = null, recordingTimer = null, openingMic = false;
let listening = false, recognizing = false, recognitionFailed = false, finalTranscript = '', selectedVoice = null;
const names = {coconut_water: 'Coconut water', pitcher: 'Pitcher', coffee: 'Coffee', cup: 'Cup', spoon: 'Spoon'};
const human = value => names[value] || String(value || '').replaceAll('_', ' ');

function fail(message) {
  $('error').textContent = message;
  $('error').hidden = false;
  clearTimeout(errorTimer);
  errorTimer = setTimeout(() => { $('error').hidden = true; }, 9000);
}
function say(text) {
  $('reply').textContent = text;
  if (voice && !listening && !openingMic && !recordingStream && 'speechSynthesis' in window) {
    speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.voice = selectedVoice;
    utterance.lang = selectedVoice?.lang || 'en-US';
    utterance.rate = 1.04;
    utterance.pitch = 1.08;
    utterance.onerror = event => {
      if (!['canceled', 'interrupted'].includes(event.error)) fail('Claudia’s voice could not play. Try another voice; her reply is on screen.');
    };
    speechSynthesis.speak(utterance);
  }
}
function setVoice(enabled) {
  voice = enabled && 'speechSynthesis' in window;
  $('sound').textContent = voice ? 'Sassy voice on' : 'Enable sassy voice';
  $('sound').setAttribute('aria-pressed', String(voice));
  if (!voice && 'speechSynthesis' in window) speechSynthesis.cancel();
}
function loadVoices() {
  if (!('speechSynthesis' in window)) {
    $('voice-choice').disabled = true;
    return;
  }
  const voices = speechSynthesis.getVoices().filter(item => /^en(?:[-_]|$)/i.test(item.lang));
  const preferred = ['Samantha', 'Google UK English Female', 'Microsoft Aria', 'Microsoft Jenny', 'Karen', 'Moira'];
  selectedVoice = voices.find(item => item.voiceURI === selectedVoice?.voiceURI)
    || preferred.map(name => voices.find(item => item.name.includes(name))).find(Boolean)
    || voices.find(item => item.default) || voices[0] || null;
  $('voice-choice').replaceChildren();
  if (!voices.length) $('voice-choice').append(new Option('Browser default', ''));
  for (const item of voices) $('voice-choice').append(new Option(item.name, item.voiceURI, false, item === selectedVoice));
}
$('voice-choice').addEventListener('change', () => {
  selectedVoice = speechSynthesis.getVoices().find(item => item.voiceURI === $('voice-choice').value) || null;
  setVoice(true);
  say('One signature pour. Plenty of attitude. Darling, you’ve chosen well.');
});
loadVoices();
if ('speechSynthesis' in window) speechSynthesis.addEventListener('voiceschanged', loadVoices);
const canRecord = () => !!(state?.vision_configured && window.MediaRecorder && navigator.mediaDevices?.getUserMedia);
async function post(route, body = {}) {
  const response = await fetch(route, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'This action could not be completed.');
  return result;
}
function controls() {
  const inputActive = openingMic || listening || recognizing || !!recordingStream;
  $('capture').disabled = busy || inputActive || !state?.capture_enabled;
  $('scan').disabled = busy || inputActive || !state?.frame || !state?.vision_configured;
  $('upload').disabled = busy || inputActive;
  $('scanline').hidden = !busy;
  $('mic').disabled = busy || openingMic || (!canRecord() && !recognition);
  document.querySelector('#request-form button[type="submit"]').disabled = busy || inputActive || !!state?.demo?.active;
  document.querySelectorAll('[data-prompt]').forEach(button => { button.disabled = busy || inputActive || !!state?.demo?.active; });
}
async function action(callback) {
  if (busy) return;
  busy = true; controls();
  try { await callback(); } catch (error) { fail(error.message); }
  finally { busy = false; await refresh(); controls(); }
}
function drawObservation() {
  $('boxes').replaceChildren(); $('objects').replaceChildren();
  const observation = state?.observation;
  const matches = observation && observation.frame_id === frameId;
  const items = matches ? observation.detections : [];
  $('object-count').textContent = matches ? String(items.length).padStart(2, '0') : '—';
  $('summary').textContent = matches ? observation.summary : '';
  if (!items.length) {
    const empty = document.createElement('span'); empty.className = 'empty-copy';
    empty.textContent = matches ? 'No allowed objects identified in this image.' : 'Object observations will appear here.';
    $('objects').append(empty);
  }
  for (const item of items) {
    const [left, top, right, bottom] = item.bbox;
    const box = document.createElement('div');
    box.className = 'box' + (item.visibility === 'clear' ? '' : ' uncertain');
    Object.assign(box.style, {left: `${left * 100}%`, top: `${top * 100}%`, width: `${(right-left)*100}%`, height: `${(bottom-top)*100}%`});
    const label = document.createElement('span'); label.textContent = human(item.object_id); box.append(label);
    box.title = item.note; $('boxes').append(box);
    const pill = document.createElement('span'); pill.className = 'object-pill';
    pill.textContent = human(item.object_id);
    const visibility = document.createElement('small'); visibility.textContent = item.visibility;
    pill.append(visibility); pill.title = item.note; $('objects').append(pill);
  }
  const positions = matches ? Object.values(observation.localization || {}) : [];
  $('position-status').textContent = positions.length
    ? positions.filter(item => item.status === 'target_estimated').length + ' measured targets / ' + positions.length + ' recognized objects. ' + (positions.find(item => item.status === 'blocked')?.reason || 'Operator scene checks still apply.')
    : 'Grasp positions need measured object geometry.';
}
function renderRun(run) {
  const signature = JSON.stringify(run);
  if (signature === runSignature) return;
  runSignature = signature;
  $('run-mode').textContent = ({unattached: 'NOT ATTACHED', mock: 'MOCK RUN', execute: 'ROBOT RUN', recorded: 'RECORDED RUN'})[run.mode] || 'RECORDED RUN';
  const statuses = {idle: 'Waiting for a run', waiting: 'Waiting for run evidence', running: 'Run in progress', completed: 'Run reported complete', aborted: 'Run stopped', failed: 'Run stopped'};
  $('run-status').textContent = statuses[run.status] || human(run.status);
  $('timeline').replaceChildren();
  for (const event of run.events.slice(-7).reverse()) {
    const row = document.createElement('li');
    row.textContent = human(event.step) + (event.object ? ' · ' + human(event.object) : '');
    const details = document.createElement('small');
    const time = event.time || event.timestamp;
    details.textContent = [event.status ? human(event.status) : 'Recorded event', time ? new Date(time).toLocaleTimeString() : ''].filter(Boolean).join(' · ');
    row.append(details); $('timeline').append(row);
  }
  if (!run.events.length) {
    const row = document.createElement('li'); row.className = 'placeholder-event';
    row.textContent = run.mode === 'unattached' ? 'Attach a run to follow its recorded steps.' : 'No recorded steps yet.';
    $('timeline').append(row);
  }
}
async function refresh() {
  try {
    const response = await fetch('/api/state');
    if (!response.ok) throw new Error('Varista is unavailable');
    state = await response.json();
    const demo = state.demo || {};
    $('station-mode').textContent = demo.enabled ? 'SUPERVISED ROBOT MODE' : 'PREVIEW MODE';
    const demoStatuses = {idle: demo.enabled ? 'Claudia is ready · Operator checks required' : 'Preview mode · Requests show the routine without moving Claudia.',
      waiting_operator: 'Claudia is waiting for the operator’s check in the terminal.',
      running: 'Claudia is following her taught routine.',
      completed: 'Routine completed · Pour and return confirmed by the operator.',
      failed: 'Claudia stopped · Operator inspection and station reset required.'};
    $('demo-status').textContent = demoStatuses[demo.status] || demoStatuses.idle;
    if (showingDemo && (demo.active || ['completed', 'failed'].includes(demo.status))) {
      $('request-status').textContent = ({waiting_operator: 'WAITING FOR OPERATOR', running: 'ROUTINE RUNNING', completed: 'ROUTINE COMPLETE', failed: 'ROUTINE STOPPED'})[demo.status];
    }
    if (state.frame && state.frame.id !== frameId) {
      frameId = state.frame.id;
      $('scene').src = '/api/frame?id=' + frameId;
      $('empty-state').hidden = true; $('image-wrap').hidden = false;
    }
    $('image-status').textContent = state.frame ? 'SNAPSHOT · NOT LIVE' : 'AWAITING IMAGE';
    $('source-label').textContent = state.frame?.source?.toUpperCase() || 'STATION VISION';
    if (state.frame) {
      const stamp = state.frame.captured_at || state.frame.loaded_at;
      $('age-label').textContent = (state.frame.captured_at ? 'CAPTURED ' : 'LOADED ') + new Date(stamp).toLocaleTimeString();
    }
    $('vision-helper').textContent = state.vision_configured
      ? 'Analyze sends this image to OpenRouter. Boxes are approximate; they are not grasp targets.'
      : 'Vision is optional. Add OPENROUTER_API_KEY locally to enable scene analysis.';
    if (!recordingStream && !openingMic && !listening && !recognizing && !busy) {
      $('voice-note').textContent = canRecord()
        ? 'Tap to talk, tap to finish. Audio goes to OpenRouter. Drink requests need review and send.'
        : recognition ? 'Tap to talk. Uses your browser’s speech service. Drink requests need review and send.' : 'Voice input is unavailable. Type to chat with Claudia.';
    }
    drawObservation(); renderRun(state.run); controls();
  } catch (_) {
    $('image-status').textContent = 'VARISTA DISCONNECTED';
    $('capture').disabled = true; $('scan').disabled = true;
    $('run-status').textContent = 'Connection lost · activity may be stale';
    runSignature = null;
  }
}
async function scan() {
  if (!state?.vision_configured) return say('Scene analysis needs an OpenRouter key. You can still load or capture an image.');
  if (!state?.frame) return say('Let’s capture a snapshot or load a photo first.');
  await action(async () => {
    say('Taking a closer look at this image.');
    const result = await post('/api/scan');
    say(result.summary || 'The image analysis is ready.');
  });
}
async function submitRequest(text, reviewOnly = false) {
  if (openingMic || listening || recognizing || recordingStream) return;
  text = text.trim(); if (!text) return;
  let scene = false;
  await action(async () => {
    const result = await post('/api/request', {text, review_only: reviewOnly});
    if (result.intent === 'scene') { scene = true; return; }
    showingDemo = result.intent === 'signature' && result.status === 'waiting_operator';
    $('draft-text').textContent = text;
    $('request-status').textContent = result.status === 'review' ? 'REVIEW REQUEST · PRESS SEND' : result.status === 'preview' ? 'PREVIEW · NO ROBOT MOTION' : result.intent === 'chat' ? 'CHATTING WITH CLAUDIA' : result.intent === 'unsupported' ? 'TRY THE SIGNATURE POUR' : 'WAITING FOR OPERATOR';
    $('request-card').hidden = false;
    $('copy').textContent = 'Copy request ↗';
    say(result.message);
    $('request').value = result.status === 'review' ? text : '';
  });
  if (scene) await scan();
}
$('request-form').addEventListener('submit', event => { event.preventDefault(); submitRequest($('request').value); });
document.querySelectorAll('[data-prompt]').forEach(button => button.addEventListener('click', () => submitRequest(button.dataset.prompt)));
$('capture').addEventListener('click', () => action(() => post('/api/capture')));
$('scan').addEventListener('click', scan);
$('upload').addEventListener('change', () => {
  const file = $('upload').files[0]; if (!file) return;
  if (file.size > 8 * 1024 * 1024) { fail('Choose an image smaller than 8 MB.'); $('upload').value = ''; return; }
  action(async () => {
    const data = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result.split(',')[1]);
      reader.onerror = () => reject(new Error('Could not read this image.'));
      reader.readAsDataURL(file);
    });
    await post('/api/image', {data});
    $('upload').value = '';
  });
});
$('sound').addEventListener('click', () => {
  if (!('speechSynthesis' in window)) return fail('Speech output is unavailable in this browser.');
  setVoice(!voice);
  if (voice) say('Well, hello. I’m Claudia. One signature pour, plenty of personality. Tap the mic and say hello.');
});
$('copy').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText($('draft-text').textContent); $('copy').textContent = 'Copied ✓'; }
  catch (_) { fail('Select the request text to copy it.'); }
});
$('fullscreen').addEventListener('click', async () => {
  try { if (document.fullscreenElement) await document.exitFullscreen(); else await document.documentElement.requestFullscreen(); }
  catch (_) { fail('Full screen is unavailable in this browser.'); }
});
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
if (SpeechRecognition) {
  recognition = new SpeechRecognition(); recognition.lang = 'en-US'; recognition.interimResults = true; recognition.continuous = false;
  recognition.onresult = event => {
    const results = Array.from(event.results);
    $('request').value = results.map(result => result[0].transcript).join(' ');
    finalTranscript = results.filter(result => result.isFinal).map(result => result[0].transcript).join(' ');
  };
  recognition.onstart = () => { listening = true; controls(); $('mic').classList.add('listening'); $('mic').setAttribute('aria-label', 'Stop voice input'); $('voice-note').textContent = 'Listening… Say hello, ask for the menu, or try “tell me a joke.”'; };
  recognition.onend = () => {
    listening = recognizing = false; controls();
    $('mic').classList.remove('listening'); $('mic').setAttribute('aria-label', 'Start voice input');
    if (!recognitionFailed && finalTranscript.trim()) submitRequest(finalTranscript, true);
    else $('voice-note').textContent = 'No complete speech captured. Try again, or type your request.';
  };
  recognition.onerror = event => { recognitionFailed = true; fail('Voice input: ' + event.error + '. You can type your request.'); };
  $('voice-note').textContent = 'Tap the mic, speak, then review and send. Uses your browser’s speech service.';
} else {
  $('mic').disabled = true;
  $('voice-note').textContent = 'Voice input is unavailable in this browser. Type a request, or try Chrome.';
}
function stopRecording() {
  clearTimeout(recordingTimer);
  if (recorder && recorder.state !== 'inactive') recorder.stop();
  recordingStream?.getTracks().forEach(track => track.stop());
  recordingStream = null;
  $('mic').classList.remove('listening');
  $('mic').setAttribute('aria-label', 'Start voice input');
}
async function recordVoice() {
  if (recordingStream) { stopRecording(); return; }
  if (openingMic || busy) return;
  openingMic = true; controls();
  try {
    const types = [['audio/webm;codecs=opus', 'webm'], ['audio/webm', 'webm'], ['audio/mp4', 'm4a'], ['audio/ogg;codecs=opus', 'ogg']];
    const supported = types.find(([mime]) => MediaRecorder.isTypeSupported(mime));
    if (!supported) throw new Error('This browser cannot record a supported audio format. Please type your request.');
    recordingStream = await navigator.mediaDevices.getUserMedia({audio: true});
    if ('speechSynthesis' in window) speechSynthesis.cancel();
    recorder = new MediaRecorder(recordingStream, {mimeType: supported[0]});
    const chunks = []; let bytes = 0, recordingError = false;
    recorder.ondataavailable = event => {
      bytes += event.data.size; chunks.push(event.data);
      if (bytes > 8 * 1024 * 1024) { recordingError = true; stopRecording(); fail('Recording is too large. Try a shorter request.'); }
    };
    recorder.onerror = () => { recordingError = true; stopRecording(); fail('Microphone recording failed. Please type your request.'); };
    recorder.onstop = async () => {
      if (recordingError) return;
      let transcript = '';
      await action(async () => {
        $('voice-note').textContent = 'Transcribing your request with OpenRouter…';
        const data = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(reader.result.split(',')[1]);
          reader.onerror = () => reject(new Error('The recording could not be read.'));
          reader.readAsDataURL(new Blob(chunks, {type: supported[0]}));
        });
        const result = await post('/api/transcribe', {data, format: supported[1]});
        $('request').value = result.text;
        transcript = result.text;
      });
      if (transcript) await submitRequest(transcript, true);
    };
    recorder.start(250);
    $('mic').classList.add('listening');
    $('mic').setAttribute('aria-label', 'Stop voice input');
    $('voice-note').textContent = 'Recording… Tap the mic to finish (30-second maximum). Audio goes to OpenRouter.';
    recordingTimer = setTimeout(stopRecording, 30_000);
  } catch (error) { stopRecording(); fail(error.message); }
  finally { openingMic = false; controls(); }
}
$('mic').addEventListener('click', () => {
  if (busy || openingMic) return;
  setVoice(true);
  if (canRecord()) return recordVoice();
  if (!recognition) return fail('Microphone recording is unavailable. Please type your request.');
  if (recognizing) recognition.stop();
  else {
    if ('speechSynthesis' in window) speechSynthesis.cancel();
    finalTranscript = ''; recognitionFailed = false;
    try { recognition.start(); recognizing = true; controls(); }
    catch (_) { recognizing = false; controls(); fail('Voice input could not start. Try again or type your request.'); }
  }
});
window.addEventListener('pagehide', () => {
  recognitionFailed = true;
  if (recorder) recorder.onstop = null;
  stopRecording();
  recognition?.abort();
  if ('speechSynthesis' in window) speechSynthesis.cancel();
});
$('scene').addEventListener('error', () => fail('The image could not be displayed. Load a valid station photo.'));
refresh(); setInterval(refresh, 2000);

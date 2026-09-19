// Offline browser-controller tests. No microphone, network, or robot is used.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('runtime/observer_web/app.js', 'utf8');
const settle = () => new Promise(resolve => setImmediate(resolve));
function setup({recognition = true, synthesis = true, recording = false, live = null} = {}) {
  const elements = new Map(), posts = [], spoken = [], windowEvents = {};
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      value: '', textContent: '', disabled: false, hidden: false, style: {}, events: {},
      classList: {add() {}, remove() {}},
      addEventListener(name, handler) { this.events[name] = handler; },
      setAttribute(name, value) { this[name] = value; },
      removeAttribute(name) { delete this[name]; },
      children: [], replaceChildren() { this.children = []; }, append(child) { this.children.push(child); },
    });
    return elements.get(id);
  }
  let recognizer, mediaRecorder, stoppedTracks = 0, cancelled = 0;
  class Recorder {
    static isTypeSupported() { return true; }
    constructor() { mediaRecorder = this; this.state = 'inactive'; }
    start() { this.state = 'recording'; }
    stop() { this.state = 'inactive'; queueMicrotask(() => this.onstop?.()); }
  }
  class Reader {
    readAsDataURL() { this.result = 'data:audio/webm;base64,AAAA'; this.onload(); }
  }
  class Recognition {
    constructor() { recognizer = this; }
    start() { this.started = true; }
    stop() { this.onend(); }
    abort() { this.onend(); }
  }
  const voices = [{name: 'Daniel', voiceURI: 'daniel', lang: 'en-GB'},
                  {name: 'Samantha', voiceURI: 'sam', lang: 'en-US'}];
  const synth = {getVoices: () => voices, addEventListener() {},
    cancel() { cancelled++; }, speak(value) { spoken.push(value); }};
  const state = {live, live_enabled: true, vision_configured: recording, demo: {enabled: false, active: false}, run: {mode: 'unattached', status: 'idle', events: []}};
  const context = vm.createContext({
    document: {addEventListener() {}, getElementById: element, querySelector: element, querySelectorAll: () => [], createElement: element},
    window: {...(recording ? {MediaRecorder: Recorder} : {}), ...(recognition ? {SpeechRecognition: Recognition} : {}),
      ...(synthesis ? {speechSynthesis: synth} : {}), addEventListener(name, fn) { windowEvents[name] = fn; }},
    navigator: recording ? {mediaDevices: {getUserMedia: async () => ({getTracks: () => [{stop() { stoppedTracks++; }}]})}} : {},
    MediaRecorder: Recorder, FileReader: Reader, Blob, speechSynthesis: synth,
    SpeechSynthesisUtterance: class {constructor(text) {this.text = text;}},
    Option: class {}, setTimeout, clearTimeout, setInterval() {},
    fetch: async (route, options) => {
      if (!options) return {ok: true, json: async () => state};
      const body = JSON.parse(options.body); posts.push({route, body});
      if (route.startsWith('/api/live/')) return {ok: true, json: async () => ({ok: true})};
      if (route === '/api/transcribe') return {ok: true, json: async () => ({text: 'pour a drink'})};
      const drink = body.text.includes('drink');
      return {ok: true, json: async () => drink
        ? {intent: 'signature', status: body.review_only ? 'review' : 'preview', message: 'Review your drink request.'}
        : {intent: 'chat', message: 'Well, hello.'}};
    },
  });
  vm.runInContext(source, context);
  return {element, posts, state, spoken, recognizer, windowEvents, context, cancelled: () => cancelled, stoppedTracks: () => stoppedTracks, recorder: () => mediaRecorder};
}
function transcript(recognizer, text, isFinal = true) {
  const result = [{transcript: text}]; result.isFinal = isFinal;
  recognizer.onresult({results: [result]});
}

test('voice input interrupts output, speaks chat, and uses the preferred voice', async () => {
  const app = setup(); await settle();
  app.element('sound').events.click();
  assert.equal(app.spoken[0].voice.name, 'Samantha');
  assert.ok(app.spoken[0].pitch > 1);
  const before = app.cancelled();
  app.element('mic').events.click();
  assert.ok(app.cancelled() > before);
  app.recognizer.onstart();
  assert.equal(app.element('#request-form button[type="submit"]').disabled, true);
  transcript(app.recognizer, 'hello Claudia'); app.recognizer.onend(); await settle();
  assert.equal(app.posts[0].body.review_only, true);
  assert.equal(app.spoken.at(-1).text, 'Well, hello.');
  assert.equal(app.element('#request-form button[type="submit"]').disabled, false);
});

test('spoken drinks stay in review until a separate form submission', async () => {
  const app = setup(); await settle();
  app.element('mic').events.click(); app.recognizer.onstart();
  transcript(app.recognizer, 'pour a drink'); app.recognizer.onend(); await settle();
  assert.equal(app.posts.length, 1);
  assert.equal(app.posts[0].body.review_only, true);
  assert.equal(app.element('request').value, 'pour a drink');
  assert.equal(app.element('request-status').textContent, 'REVIEW REQUEST · PRESS SEND');
  app.element('request-form').events.submit({preventDefault() {}}); await settle();
  assert.equal(app.posts[1].body.review_only, false);
});

test('interim transcripts, recognition errors, and page exit never dispatch', async () => {
  for (const outcome of ['interim', 'error', 'exit']) {
    const app = setup(); await settle();
    app.element('mic').events.click(); app.recognizer.onstart();
    transcript(app.recognizer, 'pour a drink', outcome !== 'interim');
    if (outcome === 'error') app.recognizer.onerror({error: 'aborted'});
    if (outcome === 'exit') app.windowEvents.pagehide(); else app.recognizer.onend();
    await settle(); assert.equal(app.posts.length, 0);
  }
});

test('missing browser speech capabilities keep typing available', async () => {
  const app = setup({recognition: false, synthesis: false}); await settle();
  assert.equal(app.element('mic').disabled, true);
  assert.equal(app.element('voice-choice').disabled, true);
  app.element('request').value = 'hello Claudia';
  app.element('request-form').events.submit({preventDefault() {}}); await settle();
  assert.equal(app.element('reply').textContent, 'Well, hello.');
});


test('recorded audio releases the microphone and dispatches only for review', async () => {
  const app = setup({recording: true}); await settle();
  await app.element('mic').events.click();
  assert.equal(app.recorder().state, 'recording');
  assert.equal(app.element('#request-form button[type="submit"]').disabled, true);
  await app.element('mic').events.click(); await settle();
  assert.equal(app.stoppedTracks(), 1);
  assert.equal(app.posts[0].route, '/api/transcribe');
  assert.equal(app.posts[1].body.review_only, true);
  assert.equal(app.element('request').value, 'pour a drink');
  assert.equal(app.spoken.at(-1).text, 'Review your drink request.');
});


test('live identification boxes are committed only after their matching image loads', async () => {
  const app = setup({live: {active: true, status: 'streaming', session: 'abc', fps: 5,
    frame_age_s: .1, identify: true, observation: {frame_id: 'exact', age_s: 2, latency_s: 2,
      detections: [{object_id: 'cup', bbox: [.1, .2, .3, .4], visibility: 'clear'}]}}});
  await settle();
  assert.equal(app.element('live-scene').src, '/api/live.mjpg?session=abc');
  assert.equal(app.element('identified-scene').src, '/api/live/identified?id=exact');
  assert.equal(app.element('identified-boxes').children.length, 0);
  app.element('identified-scene').onload();
  assert.equal(app.element('identified-boxes').children.length, 1);
  assert.equal(app.element('boxes').children.length, 0);
  assert.equal(app.element('identified-scene').hidden, false);
  app.state.live.active = false;
  await vm.runInContext('refresh()', app.context);
  assert.equal(app.element('live-scene').src, undefined);
  assert.equal(app.element('identified-panel').hidden, true);
});

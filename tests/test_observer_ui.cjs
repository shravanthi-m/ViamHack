// Offline UI-controller checks. No network, camera, microphone, or robot is used.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('runtime/observer_web/app.js', 'utf8');
const html = fs.readFileSync('runtime/observer_web/index.html', 'utf8');
const settle = () => new Promise(resolve => setImmediate(resolve));
function setup({autoLive = false} = {}) {
  const elements = new Map(), posts = [], windowEvents = {}, documentEvents = {};
  function makeElement() {
    return {value: '', textContent: '', disabled: false, hidden: false, events: {}, children: [],
      replaceChildren() { this.children = []; },
      appendChild(child) { this.children.push(child); },
      addEventListener(name, callback) { this.events[name] = callback; },
      setAttribute(name, value) { this[name] = value; },
      removeAttribute(name) { delete this[name]; }, click() { this.events.click?.(); }};
  }
  for (const match of html.matchAll(/\bid="([^"]+)"/g)) elements.set(match[1], makeElement());
  const element = id => {
    assert.ok(elements.has(id), `Controller references missing element: ${id}`);
    return elements.get(id);
  };
  const buttons = [...html.matchAll(/data-task="([^"]+)" data-prompt="([^"]+)"/g)].map(match =>
    Object.assign(makeElement(), {dataset: {task: match[1], prompt: match[2]}}));
  const send = makeElement();
  const state = {api_version: 2, live_enabled: autoLive, capture_enabled: true, demo: {enabled: false, active: false, status: 'idle'}};
  let disconnected = false, requestHandler = null;
  const context = vm.createContext({
    document: {hidden: false, getElementById: element, createElementNS: () => makeElement(),
      querySelector: selector => { assert.equal(selector, '#request-form button[type="submit"]'); return send; },
      querySelectorAll: selector => { assert.equal(selector, '[data-task]'); return buttons; },
      addEventListener(name, callback) { documentEvents[name] = callback; }},
    window: {addEventListener(name, callback) { windowEvents[name] = callback; }},
    setInterval() {}, setTimeout() {}, clearTimeout() {},
    fetch: async (route, options) => {
      if (!options) {
        if (disconnected) throw new Error('Offline');
        return {ok: true, json: async () => state};
      }
      const body = JSON.parse(options.body); posts.push({route, body});
      if (requestHandler) return requestHandler(route, body);
      if (route === '/api/live/start') {
        state.live = {active: true, session: 'auto-camera-session', status: 'connecting'};
        return {ok: true, json: async () => state.live};
      }
      return {ok: true, json: async () => route === '/api/request'
        ? body.text === 'Claudia, help pour a drink'
          ? {intent: 'signature', status: 'preview', message: 'Signature preview; no robot motion.'}
          : body.text === 'Reset station' || body.text === 'Shake held object'
            ? {intent: body.text === 'Reset station' ? 'reset' : 'shake', status: 'preview', message: 'Fixed task preview; no robot motion.'}
            : {intent: 'unsupported', message: 'Unsupported'}
        : {ok: true}};
    },
  });
  vm.runInContext(source, context);
  return {element, buttons, send, posts, state, context, windowEvents, documentEvents,
    disconnect() { disconnected = true; }, setHandler(handler) { requestHandler = handler; }};
}

test('signature preset uses the existing request endpoint and displays preview honestly', async () => {
  const app = setup(); await settle();
  app.buttons[0].click(); await settle();
  assert.deepEqual(app.posts, [{route: '/api/request', body: {text: 'Claudia, help pour a drink', review_only: false}}]);
  assert.equal(app.element('request-status').textContent, 'PREVIEW · NO ROBOT MOTION');
  assert.equal(app.element('reply').textContent, 'Signature preview; no robot motion.');
});

test('reset and held-object shake select their fixed tasks in preview', async () => {
  const app = setup(); await settle();
  for (const button of app.buttons.slice(1)) {
    button.click(); await settle();
    assert.equal(app.posts.at(-1).body.text, button.dataset.prompt);
    assert.equal(app.element('request-status').textContent, 'PREVIEW · NO ROBOT MOTION');
    assert.match(app.element('reply').textContent, /no robot motion/);
  }
  assert.equal(app.posts[0].body.text, 'Reset station');
  assert.equal(app.posts[1].body.text, 'Shake held object');
});

test('unsupported custom requests stay editable and blank submissions do nothing', async () => {
  const app = setup(); await settle();
  app.element('request').value = '   ';
  app.element('request-form').events.submit({preventDefault() {}}); await settle();
  assert.equal(app.posts.length, 0);
  app.element('request').value = 'Bring me a cup';
  app.element('request-form').events.submit({preventDefault() {}}); await settle();
  assert.equal(app.posts[0].body.text, 'Bring me a cup');
  assert.equal(app.element('request').value, 'Bring me a cup');
  assert.equal(app.element('request-status').textContent, 'TASK NOT CONNECTED');
});

test('busy and active routines block duplicate task requests', async () => {
  const app = setup(); await settle();
  let complete;
  app.setHandler(() => new Promise(resolve => { complete = resolve; }));
  app.buttons[0].click(); app.buttons[1].click();
  assert.equal(app.posts.length, 1);
  assert.ok(app.buttons.every(button => button.disabled));
  complete({ok: true, json: async () => ({intent: 'signature', status: 'waiting_operator', message: 'Waiting'})});
  app.state.demo = {enabled: true, active: true, status: 'waiting_operator'};
  await settle();
  app.buttons[2].click(); await settle();
  assert.equal(app.posts.length, 1);
  assert.equal(app.element('request-status').textContent, 'WAITING FOR OPERATOR');
  assert.equal(app.send.disabled, true);
});

test('failed requests show unconfirmed status and keep the custom text', async () => {
  const app = setup(); await settle();
  app.setHandler(async () => { throw new Error('Connection lost'); });
  app.element('request').value = 'My task';
  app.element('request-form').events.submit({preventDefault() {}}); await settle();
  assert.equal(app.element('request-status').textContent, 'REQUEST NOT CONFIRMED');
  assert.equal(app.element('request').value, 'My task');
});

test('disconnect disables dispatch and removes a stale live stream', async () => {
  const app = setup(); await settle();
  app.state.live = {active: true, session: 'camera-session'};
  await vm.runInContext('refresh()', app.context);
  assert.match(app.element('live-scene').src, /camera-session/);
  app.disconnect(); await vm.runInContext('refresh()', app.context);
  assert.equal(app.element('live-scene').src, undefined);
  assert.equal(app.element('image-status').textContent, 'DISCONNECTED');
  assert.equal(app.send.disabled, true);
  const count = app.posts.length;
  app.buttons[0].click(); await settle();
  assert.equal(app.posts.length, count);
});

test('live view starts automatically without inference and reuses the active session', async () => {
  const app = setup({autoLive: true}); await settle();
  assert.deepEqual(app.posts[0], {route: '/api/live/start', body: {identify: false}});
  assert.match(app.element('live-scene').src, /auto-camera-session/);
  await vm.runInContext('refresh()', app.context);
  assert.equal(app.posts.filter(post => post.route === '/api/live/start').length, 1);
  assert.doesNotMatch(html, /id="(?:live-toggle|capture|load-image|upload)"/);
  app.windowEvents.pagehide();
  assert.equal(app.element('live-scene').src, undefined);
});

test('hidden pages wait, then automatically resume an expired stream when visible', async () => {
  const app = setup(); await settle();
  app.state.live_enabled = true;
  app.context.document.hidden = true;
  await vm.runInContext('refresh()', app.context);
  assert.equal(app.posts.length, 0);
  app.context.document.hidden = false;
  app.documentEvents.visibilitychange(); await settle();
  assert.equal(app.posts[0].route, '/api/live/start');
  app.state.live = {active: false, status: 'stopped'};
  vm.runInContext('liveRetryAt = 0', app.context);
  await vm.runInContext('refresh()', app.context);
  assert.equal(app.posts.filter(post => post.route === '/api/live/start').length, 2);
});

test('camera start failures are retried without disconnecting task controls', async () => {
  const app = setup(); await settle();
  app.state.live_enabled = true;
  app.setHandler(async () => ({ok: false, json: async () => ({error: 'Camera unavailable'})}));
  await vm.runInContext('refresh()', app.context);
  assert.equal(app.element('camera-status').textContent, 'Camera unavailable');
  assert.equal(app.send.disabled, false);
  await vm.runInContext('refresh()', app.context);
  assert.equal(app.posts.length, 1);
  vm.runInContext('liveRetryAt = 0', app.context);
  await vm.runInContext('refresh()', app.context);
  assert.equal(app.posts.length, 2);
});

test('automatic camera starts wait until an active routine finishes', async () => {
  const app = setup(); await settle();
  app.state.live_enabled = true;
  app.state.demo.active = true;
  await vm.runInContext('refresh()', app.context);
  assert.equal(app.posts.length, 0);
  app.state.demo.active = false;
  await vm.runInContext('refresh()', app.context);
  assert.equal(app.posts[0].route, '/api/live/start');
});

 test('old backend disables dispatch and gives restart instructions', async () => {
  const app = setup(); await settle();
  delete app.state.api_version;
  await vm.runInContext('refresh()', app.context);
  assert.ok(app.buttons.every(button => button.disabled));
  assert.match(app.element('reply').textContent, /server is outdated.*start_ui/);
  app.buttons[0].click(); await settle();
  assert.equal(app.posts.length, 0);
});

test('missing request endpoint gives actionable restart error', async () => {
  const app = setup(); await settle();
  app.setHandler(async () => ({ok: false, status: 404, json: async () => ({error: 'Not found.'})}));
  app.buttons[0].click(); await settle();
  assert.equal(app.element('request-status').textContent, 'REQUEST NOT CONFIRMED');
  assert.match(app.element('reply').textContent, /server is outdated.*start_ui/);
});

test('scan displays boxes on the exact scanned frame while the live feed continues', async () => {
  const app = setup(); await settle();
  app.state.vision_configured = true;
  app.state.live = {active: true, session: 'scan-session', frames: 3, frame_age_s: 0};
  await vm.runInContext('refresh()', app.context);
  const result = {session: 'scan-session', frame_id: 'exact-frame', image: {width: 960, height: 540},
    captured_at: '2026-09-19T15:00:00Z', summary: 'A cup.',
    detections: [{object_id: 'cup', bbox: [.2, .3, .5, .8], visibility: 'clear'}]};
  app.setHandler(async () => ({ok: true, json: async () => result}));
  await app.element('scan').events.click();
  assert.equal(app.posts.at(-1).route, '/api/scan');
  assert.equal(app.element('scan-image').src, '/api/live/identified?id=exact-frame');
  assert.equal(app.element('scan-boxes').viewBox, '0 0 960 540');
  const box = app.element('scan-boxes').children[0];
  assert.equal(box.x, 192); assert.equal(box.y, 162);
  assert.equal(box.width, 288); assert.equal(box.height, 270);
  assert.equal(app.element('scan-boxes').children[2].textContent, 'cup');
  assert.equal(app.element('scan-result').hidden, false);
  assert.match(app.element('live-scene').src, /scan-session/);
  app.state.live.session = 'new-session';
  await vm.runInContext('refresh()', app.context);
  assert.equal(app.element('scan-result').hidden, true);
});

test('scan handles empty detections and failure without leaving stale boxes', async () => {
  const app = setup(); await settle();
  app.state.vision_configured = true;
  app.state.live = {active: true, session: 'scan-session', frames: 1, frame_age_s: 0};
  await vm.runInContext('refresh()', app.context);
  app.setHandler(async () => ({ok: true, json: async () => ({session: 'scan-session', frame_id: 'empty',
    image: {width: 960, height: 540}, captured_at: '2026-09-19T15:00:00Z', detections: [], summary: 'No objects.'})}));
  await app.element('scan').events.click();
  assert.match(app.element('scan-status').textContent, /No configured objects/);
  assert.equal(app.element('scan-boxes').children.length, 0);
  app.setHandler(async () => ({ok: false, json: async () => ({error: 'Scan unavailable'})}));
  await app.element('scan').events.click();
  assert.equal(app.element('scan-result').hidden, true);
  assert.equal(app.element('scan-status').textContent, 'Scan unavailable');
  assert.equal(app.element('scan').textContent, 'Scan objects');
});

test('scan requires a fresh live frame and configured vision', async () => {
  const app = setup(); await settle();
  assert.equal(app.element('scan').disabled, true);
  app.state.vision_configured = true;
  app.state.live = {active: true, session: 'scan-session', frames: 1, frame_age_s: 4};
  await vm.runInContext('refresh()', app.context);
  assert.equal(app.element('scan').disabled, true);
  app.state.live.frame_age_s = 0;
  await vm.runInContext('refresh()', app.context);
  assert.equal(app.element('scan').disabled, false);
});

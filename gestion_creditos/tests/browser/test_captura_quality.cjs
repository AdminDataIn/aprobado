// Standalone synthetic browser regression. No Django, camera, identity or external network.
const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
const drawDocument = require('./document_fixture.cjs');
const root = path.resolve(__dirname, '../../..');
const qualityPath = path.join(root, 'static/js/captura_quality.js');
let browser, context, page, baselineSource;
before(async () => {
  if (process.env.CAPTURE_QUALITY_BASELINE_REF) baselineSource = execFileSync('git',
    ['show', `${process.env.CAPTURE_QUALITY_BASELINE_REF}:static/js/captura_quality.js`], {cwd: root, encoding: 'utf8'});
  browser = await chromium.launch({channel: process.env.CAPTURE_BROWSER_CHANNEL || 'msedge', headless: true});
  context = await browser.newContext({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true});
  await context.route('**/*', route => route.abort());
  page = await context.newPage();
  await page.addScriptTag({path: path.join(root, 'static/js/vendor/jsfeat-0.0.8.min.js')});
  await page.addScriptTag({content: `window.drawDocumentFixture = ${drawDocument.toString()};`});
});
after(async () => { if (browser) await browser.close(); });
async function analyze(scene, {baseline = false, diagnostics = true, exif = false, ...geometry} = {}) {
  const source = baseline ? baselineSource : fs.readFileSync(qualityPath, 'utf8');
  await page.addScriptTag({content: source});
  return page.evaluate(async ({scene, diagnostics, geometry, exif}) => {
    const canvas = document.createElement('canvas'); canvas.width = 1080; canvas.height = 1920;
    window.drawDocumentFixture(canvas, scene);
    let photo = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', .95));
    if (exif) {
      // Minimal synthetic EXIF orientation 6 (90 degrees clockwise), no camera metadata.
      const app1 = new Uint8Array([255,225,0,34,69,120,105,102,0,0,73,73,42,0,8,0,0,0,
        1,0,18,1,3,0,1,0,0,0,6,0,0,0,0,0,0,0]);
      const bytes = new Uint8Array(await photo.arrayBuffer());
      photo = new Blob([bytes.slice(0,2), app1, bytes.slice(2)], {type: 'image/jpeg'});
    }
    const result = await DocumentCaptureQuality.analyze(photo, {aspect: 1080 / 1920, diagnostics, ...geometry});
    canvas.width = canvas.height = 0;
    return result;
  }, {scene, diagnostics, geometry, exif});
}
const matrix = [
  ['sharp', null], ['white-card', null], ['dark-background', null], ['dim', null],
  ['low-contrast', null], ['rotated', null], ['worn', null], ['alternative', null],
  ['blur', 'blur'], ['dark', 'dark'], ['bright', 'bright'], ['wall', 'missing'],
  ['keyboard', 'missing'], ['blank', 'missing'], ['textureless', 'missing'],
  ['small', 'small'], ['outside', 'missing'], ['offcenter', 'framing'],
  ['cut-left', 'missing'], ['cut-right', 'missing'], ['cut-top', 'missing'], ['cut-bottom', 'missing'],
  ['low-contrast-blank', 'missing'], ['low-contrast-blur', 'blur'],
  ['rotated-blank', 'missing'], ['rotated-blur', 'blur'], ['rotated-small', 'small'],
  ['texture-overload', 'missing'],
];
for (const [scene, reason] of matrix) test('synthetic quality: ' + scene, async t => {
  const old = baselineSource ? await analyze(scene, {baseline: true}) : null;
  const result = await analyze(scene);
  t.diagnostic(JSON.stringify({scene, before: old?.decision, beforeReasons: old?.reasons,
    after: result.decision, afterReasons: result.reasons, detail: result.rejectionCode,
    candidates: result.diagnostics?.candidates, detection: result.diagnostics?.detection}));
  assert.equal(result.decision, reason ? 'reject' : 'pass');
  if (old && ['low-contrast', 'rotated', 'alternative'].includes(scene)) assert.equal(old.decision, 'reject');
  if (reason) {
    assert.equal(result.reasons[0], reason);
    if (old) assert.equal(old.decision, 'reject', 'negative control must remain rejected');
  }
});
for (const [scene, detail] of [['wall', 'missing_no_contour'], ['blank', 'missing_low_contrast'], ['textureless', 'missing_texture']])
  test('missing remains compatible with subcode: ' + detail, async () => {
    const result = await analyze(scene);
    assert.deepEqual(result.reasons, ['missing']); assert.equal(result.rejectionCode, detail);
    assert.equal(result.diagnostics.rejectionCode, detail);
  });
test('detailed metrics are opt-in and never persisted or transmitted', async () => {
  let requests = 0, logs = 0;
  const onRequest = () => requests++, onLog = () => logs++;
  page.on('request', onRequest); page.on('console', onLog);
  try {
    const normal = await analyze('sharp', {diagnostics: false});
    assert.equal('diagnostics' in normal, false);
    assert.equal('diagnostics' in await analyze('sharp', {diagnostics: 'true'}), false);
    const debug = await analyze('sharp');
    assert.deepEqual(Object.keys(debug.diagnostics).sort(), ['candidates', 'capture', 'detection',
      'documentOrientation', 'frame', 'height', 'interior', 'occupancy', 'offsetX', 'offsetY',
      'orientation', 'rejectionCode', 'width'].sort());
    assert.ok(debug.diagnostics.interior.contrast >= 9);
    assert.ok(debug.diagnostics.interior.blur >= 24);
    assert.ok(debug.diagnostics.occupancy >= .3);
    assert.ok(Math.abs(debug.diagnostics.offsetX) < .16);
    assert.equal(debug.diagnostics.width, 360); assert.equal(debug.diagnostics.height, 640);
    assert.equal(requests, 0); assert.equal(logs, 0);
    assert.equal(await page.evaluate(() => document.querySelectorAll('img,canvas').length), 0);
  } finally { page.off('request', onRequest); page.off('console', onLog); }
});
test('native aspect mismatch uses fallback without inventing preview coordinates', async () => {
  const result = await analyze('sharp', {aspect: 1.6, expected: {x: 0, y: 0, w: .1, h: .1}});
  assert.equal(result.decision, 'pass'); assert.equal(result.diagnostics.capture.frameMapping, 'fallback');
});
test('matching preview aspect preserves normalized frame mapping', async () => {
  const result = await analyze('sharp', {expected: {x: .05, y: .3402, w: .9, h: .3196}});
  assert.equal(result.decision, 'pass'); assert.equal(result.diagnostics.capture.frameMapping, 'preview');
});
test('browser EXIF orientation is decoded once, not double-rotated', async () => {
  const result = await analyze('sharp', {exif: true});
  assert.equal(result.decision, 'pass');
  assert.equal(result.diagnostics.capture.width, 1920); assert.equal(result.diagnostics.capture.height, 1080);
  assert.equal(result.diagnostics.documentOrientation, 'portrait');
});

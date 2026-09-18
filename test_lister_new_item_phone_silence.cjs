// The phone page's microphone when nobody speaks: silence sent to the speech service comes back as
// made-up words (on 2026-09-18 a draft was named after the spelling hint's example pan), so the page
// keeps a clip its level meter never heard a voice in. Edge's fake microphone plays a silent WAV here.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');

const root = fs.existsSync(path.join(__dirname, 'templates')) ? __dirname : path.join(__dirname, '..');
const TOKEN = 'tok_quiet';
const ORIGIN = 'https://pi.example';

function page() {
  return fs.readFileSync(path.join(root, 'templates', 'lister_new_item_mobile.html'), 'utf8')
    .replace("{{ (token or '')|tojson }}", JSON.stringify(TOKEN))
    .replace("{{ (step or '')|tojson }}", JSON.stringify(''))
    .replace("{{ (defects or [])|tojson }}", JSON.stringify(['Missing pieces', 'Broken', 'Box damage', 'Replacement', 'Other']));
}

// 3 s of 16-bit mono silence with a faint hiss, the way a real room sounds to a phone.
function quietWav(file) {
  const rate = 16000, samples = rate * 3;
  const buf = Buffer.alloc(44 + samples * 2);
  buf.write('RIFF', 0); buf.writeUInt32LE(36 + samples * 2, 4); buf.write('WAVE', 8);
  buf.write('fmt ', 12); buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22);
  buf.writeUInt32LE(rate, 24); buf.writeUInt32LE(rate * 2, 28); buf.writeUInt16LE(2, 32); buf.writeUInt16LE(16, 34);
  buf.write('data', 36); buf.writeUInt32LE(samples * 2, 40);
  for (let i = 0; i < samples; i++) buf.writeInt16LE(Math.round((Math.random() - 0.5) * 200), 44 + i * 2);
  fs.writeFileSync(file, buf);
}

(async () => {
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'lister-quiet-'));
  const wav = path.join(userDataDir, 'quiet.wav');
  quietWav(wav);
  const context = await chromium.launchPersistentContext(userDataDir, {
    channel: 'msedge', headless: false,
    viewport: { width: 390, height: 844 },
    permissions: ['microphone', 'camera'],
    args: ['--headless=new', '--no-first-run', '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
           `--use-file-for-fake-audio-capture=${wav}`],
  });
  const dictation = [];
  const draft = {
    id: 5, token: TOKEN, upc: '', upcKind: '', title: '', titleSource: '', description: '',
    stage: 'title', status: 'draft', linkKind: 'intake', linkSent: true, photos: [], ready: false,
    missing: ['barcode', 'name'], phoneUrl: `${ORIGIN}/items-to-list/new-item/${TOKEN}`,
  };
  try {
    await context.route(`${ORIGIN}/**`, async route => {
      const url = new URL(route.request().url());
      const json = body => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
      if (url.pathname === `/items-to-list/new-item/${TOKEN}`) return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: page() });
      if (url.pathname === '/api/warehouse/name-dictation') {
        dictation.push(1);
        return json({ success: true, text: 'Calphalon nonstick 12-inch frying pan, black' });
      }
      if (url.pathname === `/api/lister/new/t/${TOKEN}` && route.request().method() === 'GET') return json({ success: true, draft });
      return json({ success: true, draft });
    });

    const phone = await context.newPage();
    const broken = [];
    phone.on('pageerror', error => broken.push(String(error)));
    await phone.goto(`${ORIGIN}/items-to-list/new-item/${TOKEN}`);
    await phone.waitForFunction(() => !document.getElementById('stepTitle').hidden);

    await phone.click('#titleMic');
    await phone.waitForFunction(() => document.getElementById('titleMic').dataset.state === 'listening', null, { timeout: 10000 });
    await phone.waitForTimeout(1500);
    await phone.click('#titleMic', { force: true });
    await phone.waitForFunction(() => document.getElementById('titleMic').dataset.state === 'idle', null, { timeout: 15000 });

    assert.equal(dictation.length, 0, 'a clip with no voice in it is never sent to be written down');
    assert.equal(await phone.$eval('#titleInput', el => el.value), '', 'nothing lands in the name box');
    assert.match(await phone.textContent('#titleStatus'), /Didn't hear anything/);
    assert.deepEqual(broken, []);
    console.log('lister new item phone silence test passed');
  } finally {
    await context.close();
  }
})().catch(error => { console.error(error); process.exit(1); });

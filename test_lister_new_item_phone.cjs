// The phone page the Lister's "+ NEW" Telegram link opens: name it, say what it is like, photograph
// it. Serves templates/lister_new_item_mobile.html through request interception (the template has
// only two Jinja expressions, both substituted here) with a fake Sweet Shelves server behind it, and
// drives the real microphone and camera code through Edge's fake media devices.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');

const root = fs.existsSync(path.join(__dirname, 'templates')) ? __dirname : path.join(__dirname, '..');
const TOKEN = 'tok_phone';
const ORIGIN = 'https://pi.example';

// The template is served by Flask; here only its two placeholders need filling.
function page(step) {
  return fs.readFileSync(path.join(root, 'templates', 'lister_new_item_mobile.html'), 'utf8')
    .replace("{{ (token or '')|tojson }}", JSON.stringify(TOKEN))
    .replace("{{ (step or '')|tojson }}", JSON.stringify(step || ''))
    .replace("{{ (defects or [])|tojson }}", JSON.stringify(['Missing pieces', 'Broken', 'Box damage', 'Replacement', 'Other']));
}

(async () => {
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'lister-phone-'));
  const context = await chromium.launchPersistentContext(userDataDir, {
    channel: 'msedge', headless: false,
    viewport: { width: 390, height: 844 },
    permissions: ['microphone', 'camera'],
    args: ['--headless=new', '--no-first-run', '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'],
  });
  const calls = { steps: [], photos: [], dictation: [], opened: 0 };
  let draft = {
    id: 4, token: TOKEN, upc: '', upcKind: '', title: '', titleSource: '', description: '',
    stage: 'title', status: 'draft', linkKind: 'intake', linkSent: true, photos: [], ready: false,
    missing: ['barcode', 'name'], phoneUrl: `${ORIGIN}/items-to-list/new-item/${TOKEN}`,
  };
  try {
    await context.route(`${ORIGIN}/**`, async route => {
      const request = route.request();
      const url = new URL(request.url());
      const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

      if (url.pathname === `/items-to-list/new-item/${TOKEN}`) {
        return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: page(url.searchParams.get('step')) });
      }
      if (url.pathname === '/api/warehouse/name-dictation') {
        const kind = /name="kind"\r?\n\r?\nnote/.test(request.postData() || '') ? 'note' : 'name';
        // Only the details ask for English; the name is already transcribed as English.
        const english = /name="english"\r?\n\r?\n1/.test(request.postData() || '');
        calls.dictation.push(kind + (english ? '+en' : ''));
        return json({ success: true, text: kind === 'name' ? 'Ninja blender 1000 watt black' : 'Jug is clean, lid is scratched' });
      }
      if (url.pathname === `/api/lister/new/t/${TOKEN}` && request.method() === 'GET') return json({ success: true, draft });
      if (url.pathname === `/api/lister/new/t/${TOKEN}/step`) {
        const body = request.postDataJSON();
        calls.steps.push(body);
        draft = { ...draft, ...(body.title !== undefined ? { title: body.title, titleSource: 'voice' } : {}),
                  ...(body.description !== undefined ? { description: body.description } : {}),
                  ...(body.stage ? { stage: body.stage } : {}) };
        return json({ success: true, draft });
      }
      if (url.pathname === `/api/lister/new/t/${TOKEN}/photos`) {
        calls.photos.push(((request.postData() || '').match(/filename="/g) || []).length);
        draft = { ...draft, photos: [{ id: 9, url: '/static/items_prep/new_9.jpg', markedUrl: '', note: '', createdAt: 'x' }] };
        return json({ success: true, draft, added: 1 }, 201);
      }
      if (url.pathname === `/api/lister/new/t/${TOKEN}/opened`) { calls.opened += 1; return json({ success: true, deleted: 1 }); }
      if (url.pathname.startsWith('/static/')) return route.fulfill({ status: 200, contentType: 'image/gif', body: Buffer.from('R0lGODlhAQABAAAAACw=', 'base64') });
      return json({ success: true });
    });

    const phone = await context.newPage();
    const broken = [];
    phone.on('pageerror', error => broken.push(String(error)));
    await phone.goto(`${ORIGIN}/items-to-list/new-item/${TOKEN}`);

    // Opening the link is what takes the bot's own message back down.
    await phone.waitForFunction(() => !document.getElementById('stepTitle').hidden);
    await phone.waitForTimeout(400);
    assert.equal(calls.opened, 1, 'opening the page tells the server, which deletes the Telegram message');
    assert.ok(await phone.$eval('#titleNext', el => el.disabled), 'nothing to go on with yet');

    // Dictating, the way a person does it: tap to start, then either the page hears the pause and
    // stops by itself or you tap again. Both endings have to reach the same place.
    const micState = which => phone.$eval(`#${which}Mic`, el => el.dataset.state);
    const dictate = async (which, expected) => {
      await phone.click(`#${which}Mic`);
      await phone.waitForFunction(id => document.getElementById(id).dataset.state !== 'idle', `${which}Mic`, { timeout: 10000 });
      try {
        await phone.waitForFunction(id => document.getElementById(id).dataset.state !== 'listening', `${which}Mic`, { timeout: 4000 });
      } catch {
        // force: the button is live, and waiting for it to be enabled would tap a fresh recording.
        await phone.click(`#${which}Mic`, { force: true });
      }
      await phone.waitForFunction(([id, text]) => document.getElementById(id).value.includes(text),
                                  [`${which}Input`, expected], { timeout: 25000 });
      await phone.waitForFunction(id => document.getElementById(id).dataset.state === 'idle', `${which}Mic`, { timeout: 15000 });
    };

    // 1. The name, written down by the same route the receiving screens dictate through.
    await dictate('title', 'Ninja');
    assert.deepEqual(calls.dictation, ['name'], 'the name goes up as a name, not as a note');
    assert.equal(await micState('title'), 'idle', 'the microphone is let go of again');
    assert.ok(!(await phone.$eval('#titleNext', el => el.disabled)));

    await phone.click('#titleNext');
    await phone.waitForFunction(() => !document.getElementById('stepDetails').hidden, null, { timeout: 10000 });
    assert.equal(calls.steps.at(-1).title, 'Ninja blender 1000 watt black');
    assert.equal(calls.steps.at(-1).stage, 'details');

    // 2. The details. A longer clip goes up as a note, which is the kind that gets translated.
    await dictate('details', 'scratched');
    assert.deepEqual(calls.dictation, ['name', 'note+en'], 'the details ask for English');
    // The bubbles live on the side panel only; the phone reads the words in the background.
    assert.equal(await phone.$('#statusBubbles'), null, 'no bubbles on the phone');
    assert.equal(await phone.$('#defectBubbles'), null);
    const readVerdict = text => phone.evaluate(t => readVerdict(t), text);
    assert.deepEqual(await readVerdict(''), { status: 'good', defects: [] }, 'nothing said is GOOD');
    assert.deepEqual(await readVerdict('Brand new, no scratches, nothing is missing.'), { status: 'good', defects: [] });
    assert.deepEqual(await readVerdict('The charger is missing and the screen is cracked. Box is torn.'),
                     { status: 'bad', defects: ['Missing pieces', 'Broken', 'Box damage'] });
    assert.deepEqual(await readVerdict('Customer return, works fine.'), { status: 'return', defects: [] });
    assert.deepEqual(await readVerdict('This is a replacement unit.'), { status: 'bad', defects: ['Replacement'] });

    // 3. The camera opens by itself, and the shutter is the whole of that screen.
    await phone.click('#detailsNext');
    await phone.waitForFunction(() => document.getElementById('live').classList.contains('on'), null, { timeout: 20000 });
    assert.equal(calls.steps.at(-1).description, 'Jug is clean, lid is scratched');
    // "lid is scratched" goes up as Bad + Other, marked as the phone's reading.
    assert.equal(calls.steps.at(-1).prepStatus, 'bad');
    assert.deepEqual(calls.steps.at(-1).defects, ['Other']);
    assert.equal(calls.steps.at(-1).verdictAuto, true);
    assert.ok(await phone.$eval('#liveDone', el => el.disabled), 'nothing photographed yet');
    assert.ok((await phone.textContent('#liveNote')).includes('shutter'));

    await phone.click('#liveShutter');
    await phone.waitForFunction(() => document.getElementById('liveShots').children.length === 1, null, { timeout: 20000 });
    assert.ok((await phone.textContent('#liveDone')).includes('1'), 'the button counts what is waiting');

    await phone.click('#liveDone');
    await phone.waitForFunction(() => !document.getElementById('stepDone').hidden, null, { timeout: 20000 });
    assert.deepEqual(calls.photos, [1], 'one upload carrying one photo');
    assert.ok(!(await phone.$eval('#live', el => el.classList.contains('on'))), 'the camera is let go of');

    // The second link, the one sent once the barcode has given us the name, opens on the camera and
    // never asks again for a name that is already settled.
    const second = await context.newPage();
    await second.goto(`${ORIGIN}/items-to-list/new-item/${TOKEN}?step=photos`);
    await second.waitForFunction(() => !document.getElementById('stepPhotos').hidden, null, { timeout: 20000 });
    assert.ok(await second.$eval('#stepTitle', el => el.hidden), 'the name is settled: do not ask for it again');
    assert.equal(await second.textContent('#photosSub'), 'Ninja blender 1000 watt black');

    // The first link (sent before anything was known) opened after the name is settled skips it;
    // the "from the beginning" pill's link (start=1) asks for the name again.
    const late = await context.newPage();
    await late.goto(`${ORIGIN}/items-to-list/new-item/${TOKEN}?step=title`);
    await late.waitForFunction(() => !document.getElementById('stepPhotos').hidden, null, { timeout: 20000 });
    await late.close();
    const again = await context.newPage();
    await again.goto(`${ORIGIN}/items-to-list/new-item/${TOKEN}?step=title&start=1`);
    await again.waitForFunction(() => !document.getElementById('stepTitle').hidden, null, { timeout: 20000 });
    assert.equal(await again.$eval('#titleInput', el => el.value), 'Ninja blender 1000 watt black', 'the name is there to redo');

    // Skip moves on without saying anything; a step bar jumps to its step and keeps what was typed.
    await again.click('#titleSkip');
    await again.waitForFunction(() => !document.getElementById('stepDetails').hidden, null, { timeout: 10000 });
    assert.deepEqual(calls.steps.at(-1), { stage: 'details' }, 'Skip saves nothing but the step');
    await again.fill('#detailsInput', 'The box is crushed');
    await again.click('#steps [data-step="photos"]');
    await again.waitForFunction(() => !document.getElementById('stepPhotos').hidden, null, { timeout: 10000 });
    assert.deepEqual(calls.steps.at(-1), { description: 'The box is crushed', prepStatus: 'bad', defects: ['Box damage'],
                                           verdictAuto: true, stage: 'photos' }, 'the bar kept the typed details');
    await again.waitForFunction(() => document.getElementById('live').classList.contains('on'), null, { timeout: 20000 });
    await again.click('#liveSkip');
    await again.waitForFunction(() => !document.getElementById('stepDone').hidden, null, { timeout: 10000 });
    assert.equal(calls.steps.at(-1).stage, 'done');
    assert.ok(!(await again.$eval('#live', el => el.classList.contains('on'))), 'the camera is let go of');
    await again.close();

    // The bars go backwards too.
    const back = await context.newPage();
    await back.goto(`${ORIGIN}/items-to-list/new-item/${TOKEN}?step=photos`);
    await back.waitForFunction(() => !document.getElementById('stepPhotos').hidden, null, { timeout: 20000 });
    await back.click('#liveClose');
    await back.click('#steps [data-step="title"]');
    await back.waitForFunction(() => !document.getElementById('stepTitle').hidden, null, { timeout: 10000 });
    assert.ok(!(await back.$eval('#live', el => el.classList.contains('on'))));
    await back.click('#steps [data-step="details"]');
    await back.waitForFunction(() => !document.getElementById('stepDetails').hidden, null, { timeout: 10000 });
    await back.close();

    assert.deepEqual(broken, [], 'the page threw nothing');
    console.log('lister new-item phone page test passed');
  } finally {
    await context.close();
    fs.rmSync(userDataDir, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exit(1); });

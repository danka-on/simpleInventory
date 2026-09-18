// The Lister's "+ NEW" card in a real browser: load the unpacked extension, serve a fake Sweet
// Shelves server through request interception, and walk the flow the warehouse actually does -
// open a new item, scan a barcode the server already has a name for, wait for the phone's photo,
// circle the damage on it, and submit it onto Items to List.
// Uses the system Edge like the other Playwright tests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');

const extensionPath = fs.existsSync(path.join(__dirname, 'lister-extension')) ? path.join(__dirname, 'lister-extension')
  : path.join(__dirname, '..', 'lister-extension');

const TOKEN = 'tok_abc123';
const UPC = '012345678905';
// A plain 96x96 PNG: big enough for the editor to draw a real circle on.
const PHOTO_PNG = 'iVBORw0KGgoAAAANSUhEUgAAAGAAAABgCAIAAABt+uBvAAAApUlEQVR4nO3QMQ0AIBDAwPevjJkZMThgpcMlFdDcrH30aL4fxAMECBAgQOEAAQIECFA4QIAAAQIUDhAgQIAAhQMECBAgQOEAAQIECFA4QIAAAQIUDhAgQIAAhQMECBAgQOEAAQIECFA4QIAAAQIUDhAgQIAAhQMECBAgQOEAAQIECFA4QIAAAQIUDhAgQIAAhQMECBAgQOEAAQIECFA4QIAAAfrZBXbNnSnx9WwoAAAAAElFTkSuQmCC';

const draft = {
  id: 4, token: TOKEN, upc: '', upcKind: '', title: '', titleSource: '', description: '',
  stage: 'title', status: 'draft', actor: '', createdAt: '2026-09-17T10:00:00', updatedAt: '2026-09-17T10:00:00',
  submittedAt: '', linkKind: 'intake', linkSent: true,
  phoneUrl: 'https://pi.nexuscentralhq.org/items-to-list/new-item/' + TOKEN,
  photos: [], ready: false, missing: ['barcode', 'name'],
};

(async () => {
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'lister-new-item-'));
  const context = await chromium.launchPersistentContext(userDataDir, {
    channel: 'msedge', headless: false,
    args: ['--headless=new', `--disable-extensions-except=${extensionPath}`, `--load-extension=${extensionPath}`, '--no-first-run'],
  });
  const calls = { create: 0, barcode: [], fields: [], mark: [], submit: [], link: 0, linkBodies: [], generate: 0, print: [], cancel: 0, createBodies: [] };
  let current = { ...draft };
  try {
    await context.route('https://pi.nexuscentralhq.org/**', async route => {
      const request = route.request();
      const url = new URL(request.url());
      const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
      const state = (extra = {}) => json({ success: true, draft: current, ...extra });

      if (url.pathname === '/api/lister/ping') return json({ success: true, version: '0.2.0', user: 'dan@example.com' });
      if (url.pathname === '/api/lister/queue') return json({ success: true, items: [], counts: { queued: 0, listed: 0, hidden: 0 } });
      if (url.pathname === '/api/lister/links') return json({ success: true, links: [] });

      if (url.pathname === '/api/lister/new' && request.method() === 'POST') {
        calls.create += 1;
        calls.createBodies.push(request.postDataJSON());
        current = { ...draft };
        return json({ success: true, draft: current, link: { kind: 'intake', stage: 'title', sent: ['Danka'], errors: [] } }, 201);
      }
      if (url.pathname === `/api/lister/new/${draft.id}` && request.method() === 'GET') return state();
      if (url.pathname === `/api/lister/new/${draft.id}/link`) {
        calls.link += 1;
        calls.linkBodies.push(request.postDataJSON() || {});
        return state({ kind: 'intake', sent: ['Danka'] });
      }
      if (url.pathname === `/api/lister/new/${draft.id}/barcode`) {
        const body = request.postDataJSON();
        calls.barcode.push(body);
        // A code we already have a name for: the title comes back filled and the phone's first
        // link is replaced by one that opens straight on the camera.
        current = { ...current, upc: body.barcode, upcKind: body.kind, title: 'Lenox Butterfly Meadow Plate',
                    titleSource: 'system', stage: 'photos', linkKind: 'photos', missing: [], ready: true };
        return state({ systemTitle: 'Lenox Butterfly Meadow Plate', link: { kind: 'photos', stage: 'photos', sent: ['Danka'], errors: [] } });
      }
      if (url.pathname === `/api/lister/new/${draft.id}/fields`) {
        const body = request.postDataJSON();
        calls.fields.push(body);
        current = { ...current, ...(body.title !== undefined ? { title: body.title, titleSource: 'typed' } : {}),
                    ...(body.description !== undefined ? { description: body.description } : {}),
                    ...(body.prepStatus !== undefined ? { prepStatus: body.prepStatus, prepStatusChosen: body.prepStatus } : {}),
                    ...(body.defects !== undefined ? { defects: body.prepStatus === 'good' ? [] : body.defects } : {}) };
        return state();
      }
      if (url.pathname === `/api/lister/new/${draft.id}/photos/9`) {
        const body = request.postDataJSON();
        calls.mark.push(body);
        current = { ...current, photos: current.photos.map(p => p.id !== 9 ? p
          : { ...p, markedUrl: body.image ? '/static/items_prep/new_9_marked.jpg' : '', note: body.note || '' }) };
        return state();
      }
      if (url.pathname === `/api/lister/new/${draft.id}/submit`) {
        calls.submit.push(request.postDataJSON());
        current = { ...current, status: 'submitted', stage: 'done' };
        return json({ success: true, upc: current.upc, title: current.title, draft: current,
                      queued: { id: 88, status: 'queued' }, photos: 2, notes: 1 }, 201);
      }
      if (url.pathname === `/api/lister/new/${draft.id}/cancel`) { calls.cancel += 1; current = { ...current, status: 'cancelled' }; return state(); }
      if (url.pathname === '/api/items-prep/generate-barcode') { calls.generate += 1; return json({ success: true, barcode: '777000000042' }); }
      if (url.pathname === '/api/printer/print-barcode') { calls.print.push(request.postDataJSON()); return json({ success: true }); }
      // The editor reads the bytes through the API, never off the image URL, so the canvas is not tainted.
      if (url.pathname === '/api/lister/photos/fetch') return json({ success: true, name: 'new_9.png', mime: 'image/png', base64: PHOTO_PNG });
      if (url.pathname.startsWith('/static/')) return route.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from(PHOTO_PNG, 'base64') });
      return json({ success: true });
    });

    let [worker] = context.serviceWorkers();
    if (!worker) worker = await context.waitForEvent('serviceworker');
    const extensionId = new URL(worker.url()).host;

    const panel = await context.newPage();
    await panel.goto(`chrome-extension://${extensionId}/sidepanel.html`);
    await panel.waitForFunction(() => document.getElementById('connStatus').classList.contains('ok'));

    // 1. "+ NEW" opens a draft and the phone is told about it straight away.
    await panel.click('#storeNew');
    await panel.waitForSelector('#newBarcode');
    assert.equal(calls.create, 1, 'pressing + NEW opens exactly one draft');
    // The draft opens without waiting on Telegram; the phone link goes out on its own call.
    assert.equal(calls.createBodies[0].send, false, 'the draft does not wait for the phone link');
    for (let i = 0; i < 50 && calls.link < 1; i += 1) await panel.waitForTimeout(100);
    assert.equal(calls.link, 1, 'the phone link goes out beside the draft');
    assert.equal(await panel.$eval('#storeNew', el => el.getAttribute('aria-selected')), 'true');
    assert.ok(await panel.$eval('#listCard', el => el.hidden), 'the queue steps aside while a new item is open');
    assert.ok((await panel.textContent('#newFields')).includes('the phone is naming it'), 'step 2 says what the phone is doing');
    assert.equal(await panel.$eval('#newFields .step', el => el.classList.contains('wait')), true, 'and waits on it');
    assert.equal(await panel.$eval('#newHead .step', el => el.classList.contains('need')), true, 'the barcode is on you');
    assert.equal(await panel.$('#newPhone'), null, 'no separate phone-link card any more');
    assert.ok(!(await panel.textContent('#newCard')).includes('new-item/' + TOKEN), 'and the URL is not on the card');
    assert.ok((await panel.textContent('#newPhotos')).includes('after the name'));
    // 1b. The Phone pill sends the phone its link again, in one press.
    await panel.click('#newPhoneBtn');
    for (let i = 0; i < 50 && calls.link < 2; i += 1) await panel.waitForTimeout(100);
    assert.equal(calls.link, 2, 'the Phone pill re-sends the link');
    assert.deepEqual(calls.linkBodies[0], {}, 'the first link goes wherever the draft needs the phone');
    assert.deepEqual(calls.linkBodies[1], { stage: 'title' }, 'the pill by Name & details starts from the beginning');
    await panel.waitForFunction(() => !document.getElementById('newPhoneBtn').disabled, null, { timeout: 5000 });
    // 1c. The pill by Photos sends a camera-only link.
    await panel.click('#newPhotoPhoneBtn');
    for (let i = 0; i < 50 && calls.link < 3; i += 1) await panel.waitForTimeout(100);
    assert.deepEqual(calls.linkBodies[2], { stage: 'photos' }, 'the pill by Photos goes straight to the camera');
    await panel.waitForFunction(() => !document.getElementById('newPhotoPhoneBtn').disabled, null, { timeout: 5000 });
    // 1d. Item Prep's status and defect bubbles: Good until told otherwise; a defect makes it Bad,
    // Good clears the defects again.
    assert.deepEqual(await panel.$$eval('#newStatus .vb.on', els => els.map(el => el.dataset.status)), ['good']);
    assert.deepEqual(await panel.$$eval('#newDefects .vb', els => els.map(el => el.dataset.defect)),
                     ['Missing pieces', 'Broken', 'Box damage', 'Replacement', 'Other']);
    await panel.click('#newDefects [data-defect="Broken"]');
    await panel.waitForFunction(() => document.querySelector('#newStatus [data-status="bad"]').classList.contains('on'), null, { timeout: 5000 });
    assert.deepEqual(calls.fields.at(-1), { prepStatus: 'bad', defects: ['Broken'] });
    assert.ok(await panel.$eval('#newDefects [data-defect="Broken"]', el => el.classList.contains('on')));
    await panel.click('#newStatus [data-status="return"]');
    await panel.waitForFunction(() => document.querySelector('#newStatus [data-status="return"]').classList.contains('on'), null, { timeout: 5000 });
    assert.deepEqual(calls.fields.at(-1), { prepStatus: 'return', defects: ['Broken'] }, 'Return keeps the defect');
    await panel.click('#newStatus [data-status="good"]');
    await panel.waitForFunction(() => !document.querySelector('#newDefects .vb.on'), null, { timeout: 5000 });
    assert.deepEqual(calls.fields.at(-1), { prepStatus: 'good', defects: [] }, 'Good clears the defects');
    assert.equal(await panel.textContent('#goBtn'), 'Needs a barcode and a name');
    assert.ok(await panel.$eval('#goBtn', el => el.disabled), 'nothing to submit yet');

    // 2. Generate, right beside the box (no popup), takes the next code of ours, puts it on the
    //    item and prints it without being asked.
    assert.ok(await panel.$eval('#newPrintTyped', el => el.disabled), 'nothing typed, nothing to print');
    await panel.click('#newGen');
    for (let i = 0; i < 50 && !calls.print.length; i += 1) await panel.waitForTimeout(100);
    assert.ok(await panel.$eval('#modal', el => el.hidden), 'no popup');
    assert.equal(calls.barcode.at(-1).barcode, '777000000042');
    assert.equal(calls.barcode.at(-1).kind, 'generated');
    assert.equal(calls.print.length, 1, 'a generated code prints on its own');
    assert.equal(calls.print.at(-1).upc, '777000000042', 'the label carries the generated code');
    // The card now has the big printer button for a reprint, and it says the label already went out.
    await panel.waitForSelector('#newPrint.printbtn svg');
    await panel.waitForSelector('#newPrint.printed', { timeout: 5000 });
    assert.ok((await panel.textContent('#newPrint')).includes('PRINTED'), 'the label went out on its own, so the button says PRINTED');
    assert.ok(!(await panel.$eval('#newPrint', el => el.disabled)), 'and it can still be pressed');
    await panel.click('#newPrint');
    for (let i = 0; i < 50 && calls.print.length < 2; i += 1) await panel.waitForTimeout(100);
    assert.equal(calls.print.length, 2, 'the printer button reprints');

    // 2b. Change opens the box again in place; a code typed there prints with the Print button.
    await panel.click('#newRecode');
    await panel.waitForSelector('#newBarcode');
    assert.ok(await panel.$eval('#modal', el => el.hidden), 'still no popup');
    await panel.fill('#newBarcode', '4006381333931');
    assert.ok(!(await panel.$eval('#newPrintTyped', el => el.disabled)), 'a typed code can be printed');
    await panel.click('#newPrintTyped');
    for (let i = 0; i < 50 && calls.print.length < 3; i += 1) await panel.waitForTimeout(100);
    assert.equal(calls.barcode.at(-1).kind, 'scanned', 'a typed code is the item’s own');
    assert.equal(calls.print.at(-1).upc, '4006381333931', 'a typed code prints on its own');
    await panel.waitForFunction(() => !document.getElementById('newBarcode') && document.getElementById('newRecode'), null, { timeout: 5000 });
    assert.ok((await panel.textContent('#newHead')).includes('4006381333931'), 'and the card shows it as the code again');

    // 2c. Start over: the old item goes, a blank one opens with a fresh phone link.
    await panel.click('#newRestart');
    await panel.waitForSelector('#modalOk');
    await panel.click('#modalOk');
    await panel.waitForSelector('#newBarcode', { timeout: 10000 });
    assert.equal(calls.cancel, 1, 'the old draft is thrown away');
    assert.equal(calls.create, 2, 'and a fresh one opens');
    for (let i = 0; i < 50 && calls.link < 4; i += 1) await panel.waitForTimeout(100);
    assert.equal(calls.link, 4, 'with a fresh phone link');
    assert.equal(await panel.$eval('#newTitle', el => el.value), '', 'nothing of the old item is left');

    // 2d. Start over from a blank item: the new card is the same markup as the old one, and the
    //     half-typed barcode used to survive it.
    await panel.fill('#newBarcode', '234234');
    await panel.click('#newRestart');
    for (let i = 0; i < 50 && calls.create < 3; i += 1) await panel.waitForTimeout(100);
    assert.equal(calls.create, 3, 'a blank item starts over without asking');
    await panel.waitForFunction(() => document.querySelector('#newBarcode')?.value === '', null, { timeout: 5000 });
    for (let i = 0; i < 50 && calls.link < 5; i += 1) await panel.waitForTimeout(100);

    // 3. A scanner types the code and presses Enter. The server already knows the name, so the
    //    title arrives filled in and the phone gets a photos-only link.
    await panel.fill('#newBarcode', UPC);
    await panel.press('#newBarcode', 'Enter');
    await panel.waitForFunction(() => document.querySelector('#newTitle')?.value === 'Lenox Butterfly Meadow Plate', null, { timeout: 10000 });
    assert.deepEqual(calls.barcode.at(-1).barcode, UPC);
    assert.equal(calls.barcode.at(-1).kind, 'scanned');
    assert.ok((await panel.textContent('#newHead')).includes(UPC), 'the code it is now filed under is on the card');
    assert.ok((await panel.textContent('#newFields')).includes('from our own records'), 'and where the name came from');
    assert.equal(await panel.$eval('#newHead .step', el => el.classList.contains('done')), true, 'barcode step is ticked');
    assert.equal(await panel.$eval('#newFields .step', el => el.classList.contains('done')), true, 'name step is ticked');
    assert.ok((await panel.textContent('#newPhotos')).includes('taking photos'), 'photos step says the phone is on it');
    assert.equal(await panel.textContent('#goBtn'), 'Submit to Items to List');

    // 4. The phone sends a photo; the card is watching for it.
    current = { ...current, photos: [{ id: 9, url: '/static/items_prep/new_9.png', markedUrl: '', note: '', createdAt: '2026-09-17T10:05:00' }] };
    await panel.waitForSelector('.ptile[data-photo="9"]', { timeout: 15000 });
    assert.ok((await panel.textContent('#newPhotos')).includes('mark'), 'a photo nobody has marked says so');
    assert.ok((await panel.textContent('#newPhotos')).includes('Tap a photo to circle'), 'and the card says how');

    // 5. Circle the damage and say what it is.
    await panel.click('.ptile[data-photo="9"]');
    await panel.waitForSelector('#edCanvas');
    await panel.waitForFunction(() => document.getElementById('edCanvas').width > 1, null, { timeout: 10000 });
    const box = await panel.$eval('#edCanvas', el => { const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; });
    await panel.mouse.move(box.x + box.w * 0.25, box.y + box.h * 0.25);
    await panel.mouse.down();
    await panel.mouse.move(box.x + box.w * 0.75, box.y + box.h * 0.75, { steps: 8 });
    await panel.mouse.up();
    await panel.fill('#edNote', 'Cracked corner');
    await panel.click('#edSave');
    await panel.waitForFunction(() => document.getElementById('editor').hidden, null, { timeout: 10000 });
    const mark = calls.mark.at(-1);
    assert.equal(mark.note, 'Cracked corner');
    assert.ok(String(mark.image).startsWith('data:image/jpeg;base64,'), 'the circled copy is sent as an image, not as coordinates');
    assert.ok(mark.image.length > 200, 'and it has actual pixels in it');
    await panel.waitForSelector('.ptile.marked[data-photo="9"]');
    assert.ok((await panel.textContent('#newPhotos')).includes('Cracked corner'), 'the note rides with the tile');

    // 6. Submit: the panel goes back to the queue with the new UPC selected.
    await panel.click('#goBtn');
    await panel.waitForFunction(() => document.getElementById('newCard').hidden, null, { timeout: 15000 });
    assert.equal(calls.submit.length, 1);
    assert.ok(!(await panel.$eval('#listCard', el => el.hidden)), 'the queue is back');
    assert.equal(await panel.$eval('#storeNew', el => el.getAttribute('aria-selected')), 'false');

    console.log('lister new-item panel test passed');
  } finally {
    await context.close();
    fs.rmSync(userDataDir, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exit(1); });

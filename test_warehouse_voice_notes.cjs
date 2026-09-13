const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require('playwright');

(async () => {
    const browser = await chromium.launch({channel: 'msedge', headless: true});
    try {
        const page = await browser.newPage({viewport: {width: 1100, height: 800}});
        const errors = [];
        page.on('pageerror', e => errors.push(e.message));
        const template = fs.readFileSync('templates/searchrack.html', 'utf8');
        const render = template.slice(template.indexOf('function showWarehouseNoteModal('),
            template.indexOf('async function showSuffixedItemPrepModal('));
        const script = fs.readFileSync('static/warehouse-voice-notes.js', 'utf8');
        let saved = {status: 'pending', lithuanian: '', english: ''};
        let posts = 0, fail = false;
        let writtenPosts = 0, writtenFail = false;
        const written = {
            'prep-note': {original:'ira tik trys mazi saukstai', status:'pending', english:''},
            'prep-reason': {original:'truksta lekstes', status:'pending', english:''},
            'prep-status-note': {original:'ira tik 7 puodeliai', status:'pending', english:''},
            'warehouse': {original:'There is rust on the spoon.', status:'pending', english:''}
        };
        const fixture = `<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>
            body{font:16px Arial;margin:16px} #viewContent{max-width:760px;margin:auto} button{padding:9px;cursor:pointer}
            </style><div id="viewContent"></div><script>${script}
            const esc = s => String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
            const formatUpcDisplay = s => s;
            const showViewModal = html => {document.getElementById('viewContent').innerHTML=html;};
            ${render}
            function openNote(){
                const root=document.getElementById('viewContent');
                root.innerHTML=renderSuffixedItemPrepContext({barcode:'123-1',voice_notes:[{id:1,audio_url:''}]});
                WarehouseVoiceNotes.hydrate(root);
            }
            function openWritten(){
                const root=document.getElementById('viewContent');
                root.innerHTML=renderSuffixedItemPrepContext({barcode:'123-1',
                    notes:[{id:1,note:'ira tik trys mazi saukstai'}],
                    status_entries:[{id:1,note:'ira tik 7 puodeliai',reason:'truksta lekstes'}]});
                WarehouseVoiceNotes.hydrate(root);
            }
            openNote();</script>`;
        await page.route('**/*', async route => {
            if (new URL(route.request().url()).pathname === '/')
                return route.fulfill({contentType: 'text/html', body: fixture});
            if (route.request().url().includes('/written-notes/')) {
                const kind = new URL(route.request().url()).pathname.split('/')[4];
                if (route.request().method() === 'POST') {
                    writtenPosts++;
                    assert.equal(route.request().postDataJSON().original, written[kind].original);
                    if (writtenFail) return route.fulfill({status:502,json:{success:false,error:'Please retry translation.'}});
                    written[kind].status = 'complete';
                    written[kind].english = kind === 'warehouse' ? written[kind].original : 'There are only three small spoons. <img src=x>';
                }
                return route.fulfill({json:{success:true,translation:written[kind]}});
            }
            if (route.request().method() === 'POST') {
                posts++;
                if (fail) {
                    saved = {status:'pending', lithuanian:'Neveikia.', english:''};
                    return route.fulfill({status:502, json:{success:false, error:'Translation failed. Please retry.'}});
                }
                saved = {status:'complete', lithuanian:'Dėžė pažeista. <img src=x onerror=alert(1)>',
                    english:'The box is damaged. Two parts are missing.\nCheck the lid.'};
            }
            return route.fulfill({json: {success:true, analysis:saved}});
        });
        await page.goto('http://voice.test/');
        const button = page.locator('[data-voice-analyze]');
        await page.waitForFunction(() => !document.querySelector('[data-voice-analyze]').disabled);
        assert.equal(posts, 0, 'Opening a note must never start analysis');
        await button.click();
        await page.getByText('Analyzed + translated', {exact: true}).waitFor();
        assert.equal(posts, 1);
        assert.equal(await page.locator('[lang=lt] img').count(), 0, 'AI text must not execute HTML');
        assert.match(await page.locator('[lang=lt]').textContent(), /Dėžė pažeista/);
        assert.match(await page.locator('[lang=en]').textContent(), /Two parts are missing/);
        assert.ok(await button.isDisabled());
        const player = await page.locator('audio').boundingBox();
        const original = await page.locator('[lang=lt]').boundingBox();
        const translated = await page.locator('[lang=en]').boundingBox();
        assert.ok(player.y < original.y && original.y < translated.y);
        await page.evaluate(() => openNote());
        await page.getByText('Analyzed + translated', {exact:true}).waitFor();
        assert.equal(posts, 1, 'Reopening must reuse saved analysis');
        await page.setViewportSize({width:390, height:844});
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
        if (process.env.VOICE_SCREENSHOT) await page.screenshot({path:process.env.VOICE_SCREENSHOT, fullPage:true});
        saved = {status:'pending', lithuanian:'', english:''};
        fail = true;
        await page.evaluate(() => openNote());
        await page.waitForFunction(() => !document.querySelector('[data-voice-analyze]').disabled);
        await button.click();
        await page.getByText('Retry translation', {exact:true}).waitFor();
        assert.equal(await page.locator('[lang=lt]').textContent(), 'Neveikia.');
        assert.match(await page.locator('[data-voice-status]').textContent(), /Translation failed/);
        fail = false;
        await button.click();
        await page.getByText('Analyzed + translated', {exact:true}).waitFor();
        assert.equal(posts, 3);
        await page.getByRole('button', {name:'Analyze again',exact:true}).click();
        await page.getByText('Analyzed + translated', {exact:true}).waitFor();
        assert.equal(posts, 4);
        await page.evaluate(() => openWritten());
        const noteCard = page.locator('[data-written-kind=prep-note]');
        await noteCard.getByRole('button',{name:'Translate to English',exact:true}).waitFor();
        assert.equal(writtenPosts, 0, 'Opening written notes only reads saved text');
        assert.equal(await page.locator('[data-written-translate]').count(), 3);
        await noteCard.getByRole('button').click();
        await noteCard.getByRole('button',{name:'Translated',exact:true}).waitFor();
        assert.equal(writtenPosts, 1);
        assert.match(await noteCard.locator('[lang=en]').textContent(), /only three small spoons/);
        assert.equal(await noteCard.locator('img').count(), 0);
        assert.match(await page.locator('#viewContent').textContent(), /ira tik trys mazi saukstai/);
        await page.evaluate(() => openWritten());
        await noteCard.getByRole('button',{name:'Translated',exact:true}).waitFor();
        assert.equal(writtenPosts, 1);
        writtenFail = true;
        const reason = page.locator('[data-written-kind=prep-reason]');
        await reason.getByRole('button').click();
        await reason.getByText('Please retry translation.',{exact:true}).waitFor();
        assert.ok(await reason.getByRole('button').isEnabled());
        writtenFail = false;
        await reason.getByRole('button').click();
        await reason.getByRole('button',{name:'Translated',exact:true}).waitFor();
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
        await page.evaluate(() => showWarehouseNoteModal('There is rust on the spoon.','123-1',1));
        const warehouse = page.locator('[data-written-kind=warehouse]');
        await warehouse.getByRole('button',{name:'Translate to English',exact:true}).waitFor();
        await warehouse.getByRole('button').click();
        await warehouse.getByRole('button',{name:'Translated',exact:true}).waitFor();
        assert.equal(await warehouse.locator('[lang=en]').textContent(), 'There is rust on the spoon.');
        assert.deepEqual(errors, []);
        console.log('Note UI passed: manual voice/written requests, source types, reload, safe text, mobile width, retry, and reanalysis.');
    } finally {
        await browser.close();
    }
})().catch(error => {console.error(error); process.exitCode = 1;});

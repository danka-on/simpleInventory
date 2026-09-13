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
        const render = template.slice(template.indexOf('function renderSuffixedItemPrepContext('),
            template.indexOf('async function showSuffixedItemPrepModal('));
        const script = fs.readFileSync('static/warehouse-voice-notes.js', 'utf8');
        let saved = {status: 'pending', lithuanian: '', english: ''};
        let posts = 0, fail = false;
        const fixture = `<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>
            body{font:16px Arial;margin:16px} #viewContent{max-width:760px;margin:auto} button{padding:9px;cursor:pointer}
            </style><div id="viewContent"></div><script>${script}
            const esc = s => String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
            const formatUpcDisplay = s => s;
            ${render}
            function openNote(){
                const root=document.getElementById('viewContent');
                root.innerHTML=renderSuffixedItemPrepContext({barcode:'123-1',voice_notes:[{id:1,audio_url:''}]});
                WarehouseVoiceNotes.hydrate(root);
            }
            openNote();</script>`;
        await page.route('**/*', async route => {
            if (new URL(route.request().url()).pathname === '/')
                return route.fulfill({contentType: 'text/html', body: fixture});
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
        assert.deepEqual(errors, []);
        console.log('Voice UI passed: manual requests, saved reload, safe text, bilingual layout, mobile width, and retry.');
    } finally {
        await browser.close();
    }
})().catch(error => {console.error(error); process.exitCode = 1;});

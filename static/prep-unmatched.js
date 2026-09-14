/* Unmanifested Item Prep intake. Catalog choice and manual name require worker confirmation. */
window.PrepUnmatched = (() => {
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    async function api(path, options) {
        const response = await fetch('/api/items-prep/unmatched/' + path, options);
        const result = await response.json();
        if (!response.ok || !result.success) throw new Error(result.error || 'Please try again.');
        return result;
    }
    function create({card, onAdopt, onSkip, getLot}) {
        let version = 0, active = null, photoVersion = 0, urls = [];
        const releaseUrls = () => { urls.forEach(URL.revokeObjectURL); urls = []; };
        function clear() { version++; photoVersion++; active = null; releaseUrls(); card.innerHTML = ''; card.classList.add('hidden'); }
        function heading(text) { return `<div class="prep-fallback-head">${esc(text)}</div><div class="muted">UPC ${esc(active.upc)}</div>`; }
        // Macy's search opens the product page directly for barcodes Macy's currently sells.
        function macysLink() {
            const code = /^\d{1,11}$/.test(active.upc) ? active.upc.padStart(12, '0') : active.upc;
            return `<a class="btn btn-gray" href="https://www.macys.com/shop/search?keyword=${encodeURIComponent(code)}" target="_blank" rel="noopener noreferrer" style="text-decoration:none;display:inline-flex;align-items:center">Search Macy's</a>`;
        }
        function noMatchMessage(tried) {
            const names = (tried || []).filter(t => t.result === 'no exact match').map(t => t.source);
            if (!names.length) return 'No catalog could confirm this barcode.';
            return (names.length > 1 ? names.slice(0, -1).join(', ') + ' and ' + names[names.length - 1] : names[0]) + ' did not return a verified match.';
        }
        function choices() {
            card.innerHTML = heading('No matching item') + `<p>Add the item with two photos and a name, or skip it for now.</p><div class="prep-fallback-actions"><button type="button" class="btn btn-primary" data-intake="manual">Enter manually</button><button type="button" class="btn btn-gray" data-intake="skip">Skip item</button>${macysLink()}</div>`;
        }
        async function show(upc) {
            clear(); const current = version;
            active = {upc, lot: getLot?.() || '', candidates: []}; card.classList.remove('hidden');
            card.innerHTML = heading('Not in BOL — looking for the exact UPC…');
            try {
                const result = await api('lookup?upc=' + encodeURIComponent(upc));
                if (current !== version) return;
                active.candidates = result.candidates || [];
                const unavailable = (result.tried || []).filter(t => t.result === 'unavailable').map(t => t.source);
                card.innerHTML = heading(active.candidates.length ? 'Does one of these match your item?' : 'No verified UPC match found') +
                    `<p class="muted">${active.candidates.length ? 'Compare the name, photo, model, and size before choosing.' : esc(result.message || noMatchMessage(result.tried))}</p>` +
                    (unavailable.length ? `<p role="status">${esc(unavailable.join(' and '))} could not be checked. <button type="button" class="btn btn-gray" data-intake="retry">Retry search</button></p>` : '') +
                    `<div class="prep-match-grid">${active.candidates.map((c,i) => `<div class="prep-match"><div class="prep-fallback-body">${c.image_url ? `<img src="${esc(c.image_url)}" alt="${esc(c.source_label)} product photo">` : ''}<div><b>${esc(c.title)}</b><p class="muted">${esc(c.source_label)} · exact UPC</p></div></div><button type="button" class="btn btn-primary" data-intake="adopt" data-index="${i}">Yes, use this item</button></div>`).join('')}</div>` +
                    `<div class="prep-fallback-actions"><button type="button" class="btn btn-gray" data-intake="no-match">No match</button>${macysLink()}</div><div class="prep-intake-status" role="status"></div>`;
            } catch(error) {
                if (current !== version) return;
                card.innerHTML = heading('Catalog search could not finish') + `<p>${esc(error.message)}</p><div class="prep-fallback-actions"><button type="button" class="btn" data-intake="retry">Retry search</button><button type="button" class="btn btn-gray" data-intake="no-match">No match</button>${macysLink()}</div>`;
            }
        }
        function manual() {
            releaseUrls(); active.requestId = crypto.randomUUID(); active.nameEdited = false;
            card.innerHTML = heading('Add this item') + `<form class="prep-manual-form">
                <div class="prep-intake-photos">
                    <label class="prep-photo-input">1. Photo of the name or label<span class="muted">Get the product name and model in focus.</span><input name="name_photo" type="file" accept="image/jpeg,image/png,image/webp" capture="environment" required><img data-preview="name_photo" alt="Name or label photo" hidden></label>
                    <label class="prep-photo-input">2. Photo of the item<span class="muted">Show the whole item clearly.</span><input name="item_photo" type="file" accept="image/jpeg,image/png,image/webp" capture="environment" required><img data-preview="item_photo" alt="Item photo" hidden></label>
                </div>
                <p class="prep-name-status" role="status">We’ll try to read the name from the label photo. You can also type it.</p>
                <label class="prep-name-input">Item name<input name="title" type="text" maxlength="300" placeholder="Check or enter the item name" required autocomplete="off"></label>
                <p class="muted">Check the name before saving. This creates the item so you can continue prep.</p>
                <div class="prep-fallback-actions"><button class="btn btn-primary" type="submit">Save item &amp; continue prep</button><button class="btn btn-gray" type="button" data-intake="skip">Skip item</button></div>
                <div class="prep-intake-status" role="status"></div>
            </form>`;
            const form = card.querySelector('form');
            form.elements.title.addEventListener('input', () => active && (active.nameEdited = true));
            for (const role of ['name_photo','item_photo']) form.elements[role].addEventListener('change', async () => {
                const current = version, thisPhoto = role === 'name_photo' ? ++photoVersion : photoVersion;
                const file = form.elements[role].files[0], preview = form.querySelector(`[data-preview="${role}"]`);
                if (role === 'name_photo' && !active.nameEdited) form.elements.title.value = '';
                if (!file) { preview.hidden = true; return; }
                if (file.size > 10 * 1024 * 1024) { form.querySelector('.prep-intake-status').textContent = 'Each photo must be smaller than 10 MB.'; form.elements[role].value = ''; preview.hidden = true; return; }
                const url = URL.createObjectURL(file); urls.push(url); preview.src = url; preview.hidden = false;
                if (role !== 'name_photo') return;
                const status = form.querySelector('.prep-name-status'); status.textContent = 'Reading the name from your photo…';
                const body = new FormData(); body.append('name_photo',file);
                try {
                    const result = await api('read-name',{method:'POST',body});
                    if (current !== version || thisPhoto !== photoVersion || !card.contains(form)) return;
                    if (result.name && !active.nameEdited) form.elements.title.value = result.name;
                    status.textContent = result.name && active.nameEdited ? `Label reads: ${result.name}. Your typed name has been kept.` : result.message;
                } catch(error) {
                    if (current === version && thisPhoto === photoVersion && card.contains(form)) status.textContent = 'Could not read the label. Type the name or retake the photo.';
                }
            });
            form.addEventListener('submit', async event => {
                event.preventDefault(); if (form.dataset.saving || !form.reportValidity()) return;
                const current = version, body = new FormData(form), button = form.querySelector('[type="submit"]');
                body.append('upc',active.upc); body.append('lot_number',active.lot); body.append('request_id',active.requestId); body.append('approved','true');
                form.dataset.saving = 'true'; form.querySelectorAll('button').forEach(b => b.disabled = true); button.textContent = 'Saving…';
                try {
                    const result = await api('manual',{method:'POST',body});
                    if (current !== version) return;
                    clear(); await onAdopt(result.upc);
                } catch(error) {
                    if (current !== version) return;
                    delete form.dataset.saving; form.querySelectorAll('button').forEach(b => b.disabled = false); button.textContent = 'Save item & continue prep'; form.querySelector('.prep-intake-status').textContent = error.message;
                }
            });
        }
        card.addEventListener('click', async event => {
            const button = event.target.closest('[data-intake]'); if (!button || !active) return;
            const action = button.dataset.intake;
            if (action === 'no-match') { choices(); return; }
            if (action === 'manual') { manual(); return; }
            if (action === 'skip') { clear(); onSkip?.(); return; }
            if (action === 'retry') { await show(active.upc); return; }
            if (action === 'adopt') {
                const current = version, candidate = active.candidates[Number(button.dataset.index)];
                if (!candidate) return;
                const buttons = [...card.querySelectorAll('button')]; buttons.forEach(b => b.disabled = true); button.textContent = 'Saving…';
                try {
                    const result = await api('adopt',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({upc:active.upc,lot_number:active.lot,match_id:candidate.match_id,approved:true})});
                    if (current !== version) return;
                    clear(); await onAdopt(result.upc);
                } catch(error) {
                    if (current !== version) return;
                    buttons.forEach(b => b.disabled = false); button.textContent = 'Yes, use this item'; card.querySelector('.prep-intake-status').textContent = error.message;
                }
            }
        });
        return {show,clear};
    }
    return {create};
})();

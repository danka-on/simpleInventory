/* "Last added" card for the receiving scan lists (/barcode, /multibarcode).
 *
 * LastScanPreview.mount(listEl, { lookup(code), onImage(code, url) }) inserts the
 * card right above the list and returns { show(code, title), hide() }.
 * lookup resolves the screening item ({ title, image_url }); the card shows its
 * thumbnail, or a + button that opens MediaCapture.pickPhotos and saves the first
 * photo as the thumbnail (same endpoints as warehouse-identity.js).
 * Fires "last-scan-preview:shown" on the card with { code, hasImage }.
 */
(() => {
    const STYLE_ID = 'lastScanPreviewStyles';
    const CSS = [
        '.last-scan{display:flex;align-items:center;gap:12px;box-sizing:border-box;width:100%;margin:0 0 12px;padding:10px;border-radius:12px;background:#fff;border:2px solid #2ecc71;box-shadow:0 4px 14px rgba(0,0,0,.12);color:#1f2937;overflow:hidden;opacity:0;transform:translateY(-8px);max-height:0;padding-top:0;padding-bottom:0;border-width:0;transition:opacity .22s ease,transform .22s ease,max-height .25s ease,padding .25s ease}',
        '.last-scan.is-visible{opacity:1;transform:none;max-height:140px;padding-top:10px;padding-bottom:10px;border-width:2px}',
        '.last-scan.is-swapping{opacity:.35;transform:scale(.985)}',
        '.last-scan-thumb{position:relative;flex:none;width:84px;height:84px;margin:0;padding:0;border-radius:10px;overflow:hidden;background:#f3f4f6;border:1px solid #e5e7eb}',
        '.last-scan-thumb img{display:block;width:100%;height:100%;object-fit:contain;background:#fff;opacity:0;transition:opacity .2s ease}',
        '.last-scan-thumb img.is-loaded{opacity:1}',
        '.last-scan-add{display:flex;align-items:center;justify-content:center;width:100%;height:100%;margin:0;padding:0;border:2px dashed #16a34a;border-radius:10px;background:#f0fdf4;color:#16a34a;font-size:2.4rem;font-weight:300;line-height:1;cursor:pointer;box-sizing:border-box;font-family:inherit}',
        '.last-scan-add:active{background:#dcfce7}',
        '.last-scan-add:disabled{opacity:.6;cursor:wait}',
        '.last-scan-body{flex:1;min-width:0;text-align:left}',
        '.last-scan-label{font-size:.7rem;font-weight:800;letter-spacing:.06em;text-transform:uppercase;color:#16a34a}',
        '.last-scan-title{margin:2px 0;font-size:1rem;font-weight:700;line-height:1.25;color:#1f2937;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}',
        '.last-scan-code{font-size:.85rem;color:#6b7280;overflow-wrap:anywhere}',
        '.last-scan-status{font-size:.78rem;font-weight:700;color:#b45309}',
        '.last-scan-status[data-tone="bad"]{color:#b91c1c}',
        '@media (prefers-reduced-motion:reduce){.last-scan,.last-scan-thumb img{transition:none}}'
    ].join('\n');

    function injectStyles() {
        if (document.getElementById(STYLE_ID)) return;
        const style = document.createElement('style');
        style.id = STYLE_ID;
        style.textContent = CSS;
        document.head.appendChild(style);
    }

    async function postJson(url, options, failure) {
        const response = await fetch(url, options);
        const result = await response.json().catch(() => ({}));
        if (!response.ok || result.success === false) throw new Error(result.error || failure);
        return result;
    }

    function mount(listEl, options = {}) {
        if (!listEl || !listEl.parentNode) return { show() {}, hide() {} };
        injectStyles();
        const card = document.createElement('div');
        card.className = 'last-scan';
        card.id = 'lastScanPreview';
        card.setAttribute('aria-live', 'polite');
        card.innerHTML = '<div class="last-scan-thumb"></div>'
            + '<div class="last-scan-body"><div class="last-scan-label">Just added</div>'
            + '<div class="last-scan-title"></div><div class="last-scan-code"></div>'
            + '<div class="last-scan-status"></div></div>';
        listEl.parentNode.insertBefore(card, listEl);
        const thumb = card.querySelector('.last-scan-thumb');
        const titleEl = card.querySelector('.last-scan-title');
        const codeEl = card.querySelector('.last-scan-code');
        const statusEl = card.querySelector('.last-scan-status');
        const state = { code: '', title: '', generation: 0 };

        function status(message, tone) {
            statusEl.textContent = message || '';
            if (tone) statusEl.dataset.tone = tone;
            else delete statusEl.dataset.tone;
        }

        function renderAdd() {
            thumb.innerHTML = '<button type="button" class="last-scan-add" aria-label="Add photo">+</button>';
            thumb.firstChild.addEventListener('click', addPhoto);
        }

        function renderImage(url) {
            const img = new Image();
            img.alt = state.title || state.code;
            img.addEventListener('load', () => img.classList.add('is-loaded'));
            img.addEventListener('error', () => { if (img.parentNode === thumb) renderAdd(); });
            img.src = url;
            thumb.replaceChildren(img);
        }

        async function addPhoto(event) {
            if (event) event.stopPropagation();
            const capture = window.MediaCapture;
            if (!capture || typeof capture.pickPhotos !== 'function') return;
            const generation = state.generation;
            const code = state.code;
            const button = thumb.querySelector('.last-scan-add');
            const picked = await capture.pickPhotos({
                heading: 'Add photo',
                detail: state.title || code,
                allowSkip: false
            });
            if (!picked || picked.action !== 'save' || !picked.photos || !picked.photos.length) return;
            if (button) button.disabled = true;
            status('Saving photo…');
            try {
                const saved = await postJson('/api/items-prep/temp-item', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ upc: code, item_description: state.title || code, image_data: picked.photos[0] })
                }, 'Photo was not saved.');
                const form = new FormData();
                picked.photos.forEach((photo, index) => {
                    const file = capture.photoFile(photo, 'receiving-' + (index + 1));
                    form.append('photos[]', file, file.name);
                });
                postJson('/api/items_prep/diagnostic/' + encodeURIComponent(code) + '/photos',
                    { method: 'POST', body: form }, 'Photos were not saved.')
                    .catch(err => console.error('Last-scan photo gallery save failed:', err));
                const url = saved.image_url || picked.photos[0];
                if (typeof options.onImage === 'function') options.onImage(code, url);
                if (typeof capture.playSound === 'function') capture.playSound('confirm');
                if (generation !== state.generation) return;
                status('');
                renderImage(url);
            } catch (error) {
                if (generation !== state.generation) return;
                if (button) button.disabled = false;
                status((error && error.message) || 'Photo was not saved.', 'bad');
            }
        }

        async function show(code, title) {
            const key = String(code || '').trim();
            if (!key) return;
            const generation = ++state.generation;
            const wasVisible = card.classList.contains('is-visible');
            if (wasVisible) card.classList.add('is-swapping');
            let item = null;
            try {
                item = typeof options.lookup === 'function' ? await options.lookup(key) : null;
            } catch (_) {
                item = null;
            }
            if (generation !== state.generation) return;
            state.code = key;
            state.title = String(title || (item && item.title) || '').trim();
            titleEl.textContent = state.title || 'Untitled item';
            codeEl.textContent = key;
            status('');
            const imageUrl = String(item && (item.image_url || item.image || item.thumbnail_url) || '').trim();
            if (imageUrl) renderImage(imageUrl);
            else renderAdd();
            requestAnimationFrame(() => {
                card.classList.remove('is-swapping');
                card.classList.add('is-visible');
            });
            card.dispatchEvent(new CustomEvent('last-scan-preview:shown', { detail: { code: key, hasImage: !!imageUrl } }));
        }

        function hide() {
            state.generation++;
            state.code = '';
            card.classList.remove('is-visible', 'is-swapping');
        }

        return { show, hide, element: card };
    }

    window.LastScanPreview = { mount };
})();

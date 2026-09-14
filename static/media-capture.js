/* Capture tools shared by the receiving screens and Item Prep.
 *
 * playSound(name)            'mic-on', 'mic-off' or 'confirm'; fires "media-capture:sound".
 * transcribeVoiceNote(file)  Lithuanian transcript + English translation of an unsaved
 *                            voice note (POST /api/warehouse/voice-notes/transcribe).
 * formatVoiceNote(result)    "LT: ... | EN: ..." for a note field.
 * createPhotoStrip(el, opts) Photo tiles: the first is the thumbnail; drag a tile or tap
 *                            its star to reorder, x to remove. Photos are 1600 px JPEG data URLs.
 * pickPhotos(opts)           A stand-alone photo step built on the strip; resolves
 *                            { action: 'save' | 'skip' | 'close', photos }.
 */
(() => {
    const PHOTO_EDGE_PX = 1600;
    const STYLE_ID = 'mediaCaptureStyles';
    const SOUNDS = {
        'mic-on': { wave: 'sine', notes: [660, 990], step: 0.09, length: 0.08 },
        'mic-off': { wave: 'sine', notes: [990, 660], step: 0.09, length: 0.08 },
        confirm: { wave: 'triangle', notes: [784, 1047, 1319], step: 0.1, length: 0.14 }
    };
    const CAMERA_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M9 4.5 7.6 6.5H5A2.5 2.5 0 0 0 2.5 9v9A2.5 2.5 0 0 0 5 20.5h14a2.5 2.5 0 0 0 2.5-2.5V9A2.5 2.5 0 0 0 19 6.5h-2.6L15 4.5H9Zm3 4.5a4.25 4.25 0 1 1 0 8.5 4.25 4.25 0 0 1 0-8.5Zm0 2a2.25 2.25 0 1 0 0 4.5 2.25 2.25 0 0 0 0-4.5Z"/></svg>';
    const CSS = [
        '.identity-photo-strip{display:grid;grid-template-columns:repeat(auto-fill,minmax(76px,1fr));gap:8px;margin:0 0 10px}',
        '.identity-photo-strip:empty{display:none}',
        '.identity-photo{position:relative;height:84px;margin:0;border-radius:10px;overflow:hidden;border:1px solid rgba(146,64,14,.2);background:#fff;cursor:grab;touch-action:none;-webkit-user-select:none;user-select:none}',
        '.identity-photo.is-thumbnail{border:3px solid #d97706}',
        '.identity-photo.is-dragging{z-index:3;opacity:.9;cursor:grabbing;box-shadow:0 10px 22px rgba(0,0,0,.3)}',
        '.identity-photo.is-drop-target{outline:3px dashed #1d4ed8;outline-offset:2px}',
        '.identity-photo img{display:block;width:100%;height:100%;object-fit:cover;pointer-events:none;-webkit-touch-callout:none}',
        '.identity-photo-badge{position:absolute;left:0;right:0;bottom:0;padding:3px 0;background:rgba(217,119,6,.95);color:#fff;font-size:.7rem;font-weight:800;text-align:center;pointer-events:none}',
        '.identity-photo button{position:absolute;top:4px;right:4px;width:26px;height:26px;margin:0;padding:0;border:none;border-radius:50%;background:rgba(17,24,39,.72);color:#fff;font-size:1rem;line-height:26px;cursor:pointer}',
        '.identity-photo .identity-photo-promote{left:4px;right:auto;background:rgba(217,119,6,.95);font-size:.9rem}',
        '.media-picker{position:fixed;inset:0;z-index:100050;display:flex;align-items:flex-start;justify-content:center;padding:20px 16px;background:rgba(0,0,0,.6);overflow-y:auto;-webkit-overflow-scrolling:touch;box-sizing:border-box}',
        '.media-picker[hidden],.media-picker [hidden]{display:none!important}',
        '.media-picker-card{position:relative;width:100%;max-width:430px;margin:10px auto;padding:18px 20px 16px;border-radius:16px;background:#fffdf8;border:1px solid #f3d9a2;box-shadow:0 18px 40px rgba(0,0,0,.28);box-sizing:border-box;color:#1f2937;text-align:left;font-family:inherit}',
        '.media-picker-accent{height:6px;border-radius:999px;background:linear-gradient(90deg,#f59e0b,#ef4444);margin:-18px -20px 14px}',
        '.media-picker-close{position:absolute;top:10px;right:12px;width:34px;height:34px;margin:0;padding:0;border:none;border-radius:50%;background:none;color:#9ca3af;font-size:1.3rem;line-height:34px;cursor:pointer}',
        '.media-picker-card .media-picker-heading{margin:0 36px 6px;text-align:center;font-size:1.08rem;font-weight:800;color:#1f2937!important}',
        '.media-picker-detail{margin:0 0 8px;text-align:center;font-size:.92rem;font-weight:700;color:#b45309;overflow-wrap:anywhere}',
        '.media-picker-reason{margin:0 0 12px;padding:8px 10px;border-radius:10px;background:#fef3c7;color:#92400e;font-size:.88rem;font-weight:700;text-align:center}',
        '.media-picker-current{display:block;width:100%;max-height:170px;object-fit:contain;margin:0 0 10px;border-radius:10px;border:1px solid rgba(146,64,14,.18);background:#fff}',
        '.media-picker-take{display:flex;align-items:center;justify-content:center;gap:10px;width:100%;min-height:52px;margin:0;padding:11px 14px;border-radius:12px;border:1px solid #f59e0b;background:#fff7ed;color:#92400e;font-size:1rem;font-weight:800;cursor:pointer;font-family:inherit}',
        '.media-picker-take svg{width:22px;height:22px;flex:none}',
        '.media-picker-status{min-height:1.1em;margin:8px 0 12px;text-align:center;font-size:.85rem;font-weight:700;color:#92400e;line-height:1.35}',
        '.media-picker-status[data-tone="bad"]{color:#b91c1c}',
        '.media-picker-actions{display:flex;gap:10px}',
        '.media-picker-actions button{min-height:50px;margin:0;padding:10px 14px;border:none;border-radius:10px;font-size:1.05rem;font-weight:700;cursor:pointer;font-family:inherit}',
        '.media-picker-skip{background:#e5e7eb;color:#374151}',
        '.media-picker-save{flex:1;background:#16a34a;color:#fff}',
        '.media-picker-actions button:disabled{opacity:.55;cursor:default}'
    ].join('\n');

    function injectStyles() {
        if (document.getElementById(STYLE_ID)) return;
        const style = document.createElement('style');
        style.id = STYLE_ID;
        style.textContent = CSS;
        (document.head || document.documentElement).appendChild(style);
    }

    // ---- Sounds ----------------------------------------------------------

    function unlockAudio() {
        const Context = window.AudioContext || window.webkitAudioContext;
        if (!Context) return null;
        try {
            if (!window.audioCtx) window.audioCtx = new Context();
            if (window.audioCtx.state === 'suspended') window.audioCtx.resume().catch(() => {});
            return window.audioCtx;
        } catch (_) {
            return null;
        }
    }

    // Plays a short tone sequence; returns roughly how long it lasts in ms.
    function playSound(name) {
        const sound = SOUNDS[name];
        document.dispatchEvent(new CustomEvent('media-capture:sound', { detail: name }));
        const context = unlockAudio();
        if (!context || !sound) return 0;
        try {
            const start = context.currentTime + 0.01;
            sound.notes.forEach((frequency, index) => {
                const at = start + index * sound.step;
                const osc = context.createOscillator();
                const gain = context.createGain();
                osc.type = sound.wave;
                osc.frequency.setValueAtTime(frequency, at);
                gain.gain.setValueAtTime(0.0001, at);
                gain.gain.exponentialRampToValueAtTime(0.25, at + 0.012);
                gain.gain.exponentialRampToValueAtTime(0.0001, at + sound.length);
                osc.connect(gain);
                gain.connect(context.destination);
                osc.start(at);
                osc.stop(at + sound.length + 0.02);
                osc.onended = () => {
                    osc.disconnect();
                    gain.disconnect();
                };
            });
        } catch (_) {
            return 0;
        }
        return Math.round(((sound.notes.length - 1) * sound.step + sound.length) * 1000) + 30;
    }

    // ---- Voice notes -----------------------------------------------------

    async function transcribeVoiceNote(file) {
        const form = new FormData();
        form.append('audio', file, (file && file.name) || 'voice-note.webm');
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 60000);
        try {
            const response = await fetch('/api/warehouse/voice-notes/transcribe', {
                method: 'POST',
                body: form,
                signal: controller.signal
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok || !result.success) {
                throw new Error(result.error || 'Transcription failed. The recording is still attached.');
            }
            return {
                lithuanian: String(result.lithuanian || '').trim(),
                english: String(result.english || '').trim(),
                warning: String(result.warning || '').trim()
            };
        } catch (error) {
            if (error && error.name === 'AbortError') throw new Error('Transcription timed out. The recording is still attached.');
            throw error;
        } finally {
            clearTimeout(timeout);
        }
    }

    function formatVoiceNote(result) {
        const lithuanian = String((result && result.lithuanian) || '').trim();
        const english = String((result && result.english) || '').trim();
        if (!lithuanian) return english ? `EN: ${english}` : '';
        if (!english) return `LT: ${lithuanian}`;
        // English speech comes back unchanged; one copy is enough.
        if (english.toLowerCase() === lithuanian.toLowerCase()) return `EN: ${english}`;
        return `LT: ${lithuanian} | EN: ${english}`;
    }

    // ---- Photos ----------------------------------------------------------

    function readAsDataUrl(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result || ''));
            reader.onerror = () => reject(reader.error);
            reader.readAsDataURL(file);
        });
    }

    // Phone photos are several MB; 1600 px keeps labels legible and uploads quick.
    function shrinkPhoto(file) {
        if (typeof file === 'string') return Promise.resolve(file);
        return new Promise(resolve => {
            const url = URL.createObjectURL(file);
            const image = new Image();
            image.onload = () => {
                URL.revokeObjectURL(url);
                try {
                    const scale = Math.min(1, PHOTO_EDGE_PX / Math.max(image.naturalWidth, image.naturalHeight));
                    const canvas = document.createElement('canvas');
                    canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
                    canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
                    canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
                    resolve(canvas.toDataURL('image/jpeg', 0.85));
                } catch (_) {
                    resolve(readAsDataUrl(file));
                }
            };
            image.onerror = () => {
                URL.revokeObjectURL(url);
                resolve(readAsDataUrl(file));
            };
            image.src = url;
        });
    }

    function photoBlob(dataUrl) {
        const [header, body] = String(dataUrl).split(',');
        const type = (/data:([^;,]+)/.exec(header) || [])[1] || 'image/jpeg';
        const bytes = atob(body || '');
        const buffer = new Uint8Array(bytes.length);
        for (let i = 0; i < bytes.length; i++) buffer[i] = bytes.charCodeAt(i);
        return new Blob([buffer], { type });
    }

    function photoFile(dataUrl, name) {
        const blob = photoBlob(dataUrl);
        const extension = blob.type === 'image/png' ? '.png' : '.jpg';
        return new File([blob], String(name || 'photo').replace(/\.[a-z0-9]+$/i, '') + extension, { type: blob.type });
    }

    function createPhotoStrip(strip, options = {}) {
        injectStyles();
        const settings = Object.assign({ thumbnail: true, isBusy: () => false, onChange: () => {} }, options);
        let photos = [];
        let version = 0;

        function changed() {
            render();
            settings.onChange();
        }

        function move(from, to) {
            if (settings.isBusy() || from === to || !photos[from]) return;
            const [photo] = photos.splice(from, 1);
            photos.splice(to, 0, photo);
            changed();
        }

        function remove(index) {
            if (settings.isBusy() || !photos[index]) return;
            photos.splice(index, 1);
            changed();
        }

        // Drag a tile onto another tile to move it there; the first spot is the thumbnail.
        function enableDrag(tile, index) {
            tile.addEventListener('pointerdown', event => {
                if (settings.isBusy() || event.button > 0 || (event.target.closest && event.target.closest('button'))) return;
                const start = { x: event.clientX, y: event.clientY };
                let moved = false;
                let target = index;
                try { tile.setPointerCapture(event.pointerId); } catch (_) {}
                const tiles = () => Array.from(strip.children);
                const onMove = step => {
                    const dx = step.clientX - start.x;
                    const dy = step.clientY - start.y;
                    if (!moved && Math.hypot(dx, dy) < 8) return;
                    moved = true;
                    tile.classList.add('is-dragging');
                    tile.style.transform = `translate(${dx}px, ${dy}px)`;
                    target = index;
                    tiles().forEach((other, position) => {
                        const box = other.getBoundingClientRect();
                        const over = other !== tile && step.clientX >= box.left && step.clientX <= box.right
                            && step.clientY >= box.top && step.clientY <= box.bottom;
                        other.classList.toggle('is-drop-target', over);
                        if (over) target = position;
                    });
                };
                const onEnd = () => {
                    tile.removeEventListener('pointermove', onMove);
                    tile.removeEventListener('pointerup', onEnd);
                    tile.removeEventListener('pointercancel', onEnd);
                    if (moved && target !== index) {
                        move(index, target);
                        return;
                    }
                    tile.classList.remove('is-dragging');
                    tile.style.transform = '';
                    tiles().forEach(other => other.classList.remove('is-drop-target'));
                };
                tile.addEventListener('pointermove', onMove);
                tile.addEventListener('pointerup', onEnd);
                tile.addEventListener('pointercancel', onEnd);
            });
        }

        function render() {
            if (!strip) return;
            const busy = !!settings.isBusy();
            strip.textContent = '';
            photos.forEach((src, index) => {
                const lead = index === 0 && settings.thumbnail;
                const tile = document.createElement('figure');
                tile.className = 'identity-photo' + (lead ? ' is-thumbnail' : '');
                const image = document.createElement('img');
                image.src = src;
                image.alt = lead ? 'Thumbnail photo' : `Photo ${index + 1}`;
                image.draggable = false;
                const removeButton = document.createElement('button');
                removeButton.type = 'button';
                removeButton.className = 'identity-photo-remove';
                removeButton.textContent = '\u00d7';
                removeButton.disabled = busy;
                removeButton.setAttribute('aria-label', `Remove photo ${index + 1}`);
                removeButton.addEventListener('click', () => remove(index));
                tile.append(image, removeButton);
                if (lead) {
                    const badge = document.createElement('span');
                    badge.className = 'identity-photo-badge';
                    badge.textContent = 'Thumbnail';
                    tile.appendChild(badge);
                } else if (settings.thumbnail) {
                    const promote = document.createElement('button');
                    promote.type = 'button';
                    promote.className = 'identity-photo-promote';
                    promote.textContent = '\u2605';
                    promote.disabled = busy;
                    promote.setAttribute('aria-label', `Make photo ${index + 1} the thumbnail`);
                    promote.addEventListener('click', () => move(index, 0));
                    tile.appendChild(promote);
                }
                enableDrag(tile, index);
                strip.appendChild(tile);
            });
        }

        return {
            get photos() { return photos.slice(); },
            get count() { return photos.length; },
            configure(next) { Object.assign(settings, next || {}); render(); },
            set(list) {
                version += 1;
                photos = (list || []).filter(Boolean).slice();
                render();
            },
            // Adds files (or data URLs) in order; a set() while shrinking discards the result.
            async add(files) {
                const mine = version;
                let failed = 0;
                for (const file of Array.from(files || [])) {
                    try {
                        const photo = await shrinkPhoto(file);
                        if (mine !== version) return { failed, stale: true };
                        photos.push(photo);
                    } catch (_) {
                        failed += 1;
                    }
                }
                changed();
                return { failed, stale: false };
            },
            move,
            remove,
            render,
            hint() {
                if (photos.length < 2) return '';
                return settings.thumbnail
                    ? 'Drag a photo to the first spot, or tap \u2605, to make it the thumbnail.'
                    : 'Drag photos to change their order.';
            }
        };
    }

    // ---- Stand-alone photo step ------------------------------------------

    let picker = null;

    function pickerStatus(message, tone) {
        picker.status.textContent = message || '';
        picker.status.dataset.tone = tone || '';
    }

    function pickerHint() {
        return picker.strip.hint() || (picker.strip.count ? '' : picker.options.emptyHint);
    }

    function renderPicker() {
        const count = picker.strip.count;
        picker.takeLabel.textContent = count ? 'Add another photo' : 'Take photo';
        picker.save.disabled = !count;
        picker.save.textContent = count > 1 ? `Save ${count} photos` : 'Save photo';
        picker.current.hidden = !picker.options.existingImage || count > 0;
    }

    function finishPicker(action) {
        if (!picker || picker.root.hidden) return;
        const photos = picker.strip.photos;
        if (action === 'save' && !photos.length) return;
        const resolve = picker.resolve;
        picker.resolve = null;
        picker.root.hidden = true;
        picker.strip.set([]);
        if (action === 'save') playSound('confirm');
        if (resolve) resolve({ action, photos: action === 'save' ? photos : [] });
    }

    function buildPicker() {
        injectStyles();
        const root = document.createElement('div');
        root.className = 'media-picker';
        root.id = 'mediaPickerModal';
        root.hidden = true;
        root.innerHTML = [
            '<div class="media-picker-card" role="dialog" aria-modal="true" aria-labelledby="mediaPickerHeading">',
            '<button type="button" class="media-picker-close" aria-label="Close">\u00d7</button>',
            '<div class="media-picker-accent"></div>',
            '<h3 class="media-picker-heading" id="mediaPickerHeading"></h3>',
            '<div class="media-picker-detail"></div>',
            '<div class="media-picker-reason"></div>',
            '<img class="media-picker-current" alt="Current picture">',
            '<div class="identity-photo-strip"></div>',
            '<input type="file" accept="image/*" capture="environment" multiple style="display:none">',
            '<button type="button" class="media-picker-take">' + CAMERA_ICON + '<span></span></button>',
            '<div class="media-picker-status" role="status" aria-live="polite"></div>',
            '<div class="media-picker-actions">',
            '<button type="button" class="media-picker-skip"></button>',
            '<button type="button" class="media-picker-save" disabled></button>',
            '</div>',
            '</div>'
        ].join('');
        document.body.appendChild(root);
        const find = selector => root.querySelector(selector);
        const view = {
            root,
            heading: find('.media-picker-heading'),
            detail: find('.media-picker-detail'),
            reason: find('.media-picker-reason'),
            current: find('.media-picker-current'),
            input: find('input[type="file"]'),
            take: find('.media-picker-take'),
            takeLabel: find('.media-picker-take span'),
            status: find('.media-picker-status'),
            skip: find('.media-picker-skip'),
            save: find('.media-picker-save'),
            close: find('.media-picker-close'),
            options: {},
            resolve: null
        };
        view.strip = createPhotoStrip(find('.identity-photo-strip'), {
            onChange: () => {
                renderPicker();
                pickerStatus(pickerHint());
            }
        });
        view.take.addEventListener('click', event => {
            event.preventDefault();
            view.input.click();
        });
        view.input.addEventListener('change', async () => {
            const files = Array.from(view.input.files || []);
            view.input.value = '';
            if (!files.length) return;
            pickerStatus('Preparing photo\u2026');
            const result = await view.strip.add(files);
            if (result.stale) return;
            renderPicker();
            if (result.failed) pickerStatus('A photo could not be read. Try again.', 'bad');
            else pickerStatus(pickerHint());
        });
        view.save.addEventListener('click', () => finishPicker('save'));
        view.skip.addEventListener('click', () => finishPicker('skip'));
        view.close.addEventListener('click', () => finishPicker('close'));
        root.addEventListener('pointerdown', () => unlockAudio(), true);
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape' && !root.hidden) finishPicker('close');
        });
        return view;
    }

    async function pickPhotos(options = {}) {
        if (!picker) picker = buildPicker();
        if (picker.resolve) finishPicker('close');
        const o = picker.options = Object.assign({
            heading: 'Add photos',
            detail: '',
            reason: '',
            existingImage: '',
            thumbnail: true,
            allowSkip: true,
            skipLabel: 'Skip',
            emptyHint: '',
            photos: []
        }, options);
        picker.strip.configure({ thumbnail: !!o.thumbnail });
        picker.strip.set([]);
        picker.heading.textContent = o.heading;
        picker.detail.textContent = o.detail;
        picker.detail.hidden = !o.detail;
        picker.reason.textContent = o.reason;
        picker.reason.hidden = !o.reason;
        if (o.existingImage) picker.current.src = o.existingImage;
        else picker.current.removeAttribute('src');
        picker.skip.hidden = !o.allowSkip;
        picker.skip.textContent = o.skipLabel;
        const done = new Promise(resolve => { picker.resolve = resolve; });
        renderPicker();
        pickerStatus(pickerHint());
        picker.root.hidden = false;
        if (o.photos && o.photos.length) {
            pickerStatus('Loading photos\u2026');
            const result = await picker.strip.add(o.photos);
            if (!result.stale) {
                renderPicker();
                pickerStatus(pickerHint());
            }
        }
        return done;
    }

    injectStyles();
    window.MediaCapture = {
        playSound,
        unlockAudio,
        transcribeVoiceNote,
        formatVoiceNote,
        shrinkPhoto,
        photoBlob,
        photoFile,
        createPhotoStrip,
        pickPhotos
    };
})();

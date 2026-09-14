/* Shared naming flow for both receiving screens.
 *
 * The page owns the modal and the promise its scan waits on; this file owns what
 * happens inside it. Step 1 names the item, typed or dictated through
 * /api/warehouse/name-dictation. When the page confirms a saved name with next(),
 * items that need a picture continue to step 2, where any number of photos can be
 * taken, saved or skipped. Step 2 ends by firing "warehouse-identity:complete" on
 * #missingTitleModal with { title, photos, imageUrl }.
 */
(() => {
    const MAX_RECORDING_MS = 15000;
    const QUIET_STOP_MS = 1300;
    const NO_SPEECH_STOP_MS = 8000;
    const PHOTO_EDGE_PX = 1600;
    const MIC_LABELS = {
        idle: 'Tap and say the name',
        starting: 'Starting microphone\u2026',
        listening: 'Listening\u2026',
        working: 'Writing it down\u2026'
    };
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    const byId = id => document.getElementById(id);
    const flow = { generation: 0 };
    let dictation = null;
    let recognition = null;

    function setStatus(id, message, tone) {
        const el = byId(id);
        if (!el) return;
        el.textContent = message || '';
        el.dataset.tone = tone || '';
    }
    const nameStatus = (message, tone) => setStatus('warehouseIdentityStatus', message, tone);
    const photoStatus = (message, tone) => setStatus('missingTitlePhotoStatus', message, tone);

    function fillName(text) {
        const input = byId('missingTitleInput');
        if (!input) return;
        input.value = String(text || '').replace(/\s+/g, ' ').trim().slice(0, 200);
        input.dispatchEvent(new Event('input', { bubbles: true }));
    }

    // ---- Dictation -------------------------------------------------------

    const canRecord = () => !!(window.MediaRecorder && navigator.mediaDevices && navigator.mediaDevices.getUserMedia);

    function setMic(state) {
        const button = byId('warehouseDictateBtn');
        if (!button) return;
        button.dataset.state = state;
        button.disabled = state === 'starting' || state === 'working';
        button.setAttribute('aria-pressed', state === 'listening' ? 'true' : 'false');
        button.style.setProperty('--level', '0');
        const label = byId('warehouseDictateLabel');
        if (label) label.textContent = MIC_LABELS[state];
    }

    function recordingType() {
        const supported = type => {
            try { return MediaRecorder.isTypeSupported(type); } catch (_) { return false; }
        };
        return ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'].find(supported) || '';
    }

    function fileExtension(type) {
        if (/mp4|m4a|aac/i.test(type)) return '.mp4';
        if (/ogg/i.test(type)) return '.ogg';
        if (/wav/i.test(type)) return '.wav';
        return '.webm';
    }

    function releaseSession(session) {
        clearTimeout(session.maxTimer);
        clearInterval(session.levelTimer);
        if (session.stream) session.stream.getTracks().forEach(track => track.stop());
        if (session.audio) {
            try {
                const closing = session.audio.close();
                if (closing && closing.catch) closing.catch(() => {});
            } catch (_) {}
        }
        session.stream = null;
        session.audio = null;
    }

    function cancelDictation() {
        const session = dictation;
        dictation = null;
        if (session) {
            session.stopping = true;
            if (session.controller) session.controller.abort();
            try {
                if (session.recorder && session.recorder.state !== 'inactive') session.recorder.stop();
            } catch (_) {}
            releaseSession(session);
        }
        if (recognition) {
            try { recognition.abort(); } catch (_) {}
            recognition = null;
        }
        setMic('idle');
    }

    function finishDictation(session, message, tone) {
        if (dictation !== session) return;
        dictation = null;
        setMic('idle');
        nameStatus(message, tone);
    }

    async function startDictation() {
        cancelDictation();
        // The page may still be saying "Item needs a name"; don't record it.
        try { if (window.speechSynthesis) window.speechSynthesis.cancel(); } catch (_) {}
        const session = { chunks: [] };
        dictation = session;
        setMic('starting');
        nameStatus('');
        try {
            session.stream = await navigator.mediaDevices.getUserMedia({
                audio: { echoCancellation: true, noiseSuppression: true }
            });
        } catch (error) {
            finishDictation(session, error && error.name === 'NotAllowedError'
                ? 'Microphone access is blocked. Allow it for this site, or type the name.'
                : 'Microphone unavailable. Type the name instead.', 'bad');
            return;
        }
        if (dictation !== session) {
            releaseSession(session);
            return;
        }
        try {
            const type = recordingType();
            session.recorder = new MediaRecorder(session.stream, type ? { mimeType: type } : undefined);
            session.recorder.ondataavailable = event => {
                if (event.data && event.data.size) session.chunks.push(event.data);
            };
            session.recorder.onstop = () => transcribe(session);
            session.recorder.start();
        } catch (_) {
            releaseSession(session);
            finishDictation(session, 'Recording is not supported here. Type the name instead.', 'bad');
            return;
        }
        session.startedAt = Date.now();
        session.maxTimer = setTimeout(() => stopRecording(session), MAX_RECORDING_MS);
        watchForPause(session);
        setMic('listening');
        nameStatus('Say the brand, item, size and color. Tap the mic when done.');
    }

    // Stop by itself once the speaker pauses, so dictating is a single tap.
    function watchForPause(session) {
        const Context = window.AudioContext || window.webkitAudioContext;
        if (!Context) return;
        try {
            session.audio = new Context();
            if (session.audio.state === 'suspended') session.audio.resume().catch(() => {});
            const analyser = session.audio.createAnalyser();
            analyser.fftSize = 1024;
            session.audio.createMediaStreamSource(session.stream).connect(analyser);
            const samples = new Uint8Array(analyser.fftSize);
            let noise = null;
            let spokenMs = 0;
            let quietSince = 0;
            session.levelTimer = setInterval(() => {
                analyser.getByteTimeDomainData(samples);
                let sum = 0;
                for (let i = 0; i < samples.length; i++) {
                    const value = (samples[i] - 128) / 128;
                    sum += value * value;
                }
                const level = Math.sqrt(sum / samples.length);
                if (noise === null) noise = level;
                const threshold = Math.max(0.015, noise * 2.5);
                const button = byId('warehouseDictateBtn');
                if (button) button.style.setProperty('--level', Math.min(1, level / (threshold * 2)).toFixed(2));
                const now = Date.now();
                if (level > threshold) {
                    spokenMs += 100;
                    quietSince = 0;
                } else {
                    // Learn the room only from quiet frames, or speech raises the bar.
                    noise = level < noise ? level : noise + (level - noise) * 0.05;
                    if (spokenMs >= 300) {
                        quietSince = quietSince || now;
                        if (now - quietSince >= QUIET_STOP_MS) stopRecording(session);
                    }
                }
                if (!spokenMs && now - session.startedAt >= NO_SPEECH_STOP_MS) stopRecording(session);
            }, 100);
        } catch (_) {
            // Tap-to-stop and the time limit still end the recording.
        }
    }

    function stopRecording(session) {
        if (!session || session.stopping) return;
        session.stopping = true;
        clearTimeout(session.maxTimer);
        clearInterval(session.levelTimer);
        try {
            if (session.recorder && session.recorder.state !== 'inactive') {
                session.recorder.stop();
                return;
            }
        } catch (_) {}
        transcribe(session);
    }

    async function transcribe(session) {
        if (session.transcribing) return;
        session.transcribing = true;
        releaseSession(session);
        if (dictation !== session) return;
        const type = (session.recorder && session.recorder.mimeType) || recordingType() || 'audio/webm';
        const audio = new Blob(session.chunks, { type });
        if (!audio.size) {
            finishDictation(session, "Didn't catch that. Tap the microphone and try again.", 'bad');
            return;
        }
        setMic('working');
        nameStatus('');
        session.controller = new AbortController();
        const timeout = setTimeout(() => session.controller.abort(), 25000);
        try {
            const form = new FormData();
            form.append('audio', audio, 'name' + fileExtension(type));
            const response = await fetch('/api/warehouse/name-dictation', {
                method: 'POST',
                body: form,
                signal: session.controller.signal
            });
            const result = await response.json().catch(() => ({}));
            if (dictation !== session) return;
            if (!response.ok || !result.success) throw new Error(result.error || 'Dictation failed. Type the name or try again.');
            fillName(result.text);
            finishDictation(session, 'Check it against the label, then confirm.', 'ok');
        } catch (error) {
            finishDictation(session, error && error.name === 'AbortError'
                ? 'Dictation timed out. Try again or type the name.'
                : (error && error.message) || 'Dictation failed. Type the name or try again.', 'bad');
        } finally {
            clearTimeout(timeout);
        }
    }

    // Browsers without MediaRecorder can still use their own speech service.
    function startBrowserSpeech() {
        cancelDictation();
        const speech = new SpeechRecognition();
        recognition = speech;
        speech.lang = 'en-US';
        speech.interimResults = false;
        speech.onresult = event => {
            if (recognition !== speech) return;
            fillName(event.results[0][0].transcript);
            nameStatus('Check it against the label, then confirm.', 'ok');
        };
        speech.onerror = () => {
            if (recognition === speech) nameStatus('Dictation unavailable. Type the name instead.', 'bad');
        };
        speech.onend = () => {
            if (recognition !== speech) return;
            recognition = null;
            setMic('idle');
        };
        setMic('listening');
        nameStatus('Say the brand, item, size and color. Tap the mic when done.');
        try {
            speech.start();
        } catch (_) {
            recognition = null;
            setMic('idle');
            nameStatus('Dictation unavailable. Type the name instead.', 'bad');
        }
    }

    function toggleDictation() {
        if (recognition) {
            try { recognition.stop(); } catch (_) {}
            return;
        }
        if (dictation) {
            if (dictation.recorder && !dictation.stopping) stopRecording(dictation);
            return;
        }
        if (canRecord()) startDictation();
        else if (SpeechRecognition) startBrowserSpeech();
    }

    // ---- Steps and photos ------------------------------------------------

    function showStep(step) {
        const photos = step === 'photos';
        const nameStep = byId('warehouseNameStep');
        const photoStep = byId('missingTitlePhotoSection');
        if (nameStep) nameStep.hidden = photos;
        if (photoStep) photoStep.hidden = !photos;
        const steps = byId('warehouseIdentitySteps');
        if (steps) {
            steps.hidden = !flow.photosWanted;
            steps.querySelectorAll('[data-identity-step]').forEach(item => {
                item.classList.toggle('is-active', item.dataset.identityStep === step);
                item.classList.toggle('is-done', photos && item.dataset.identityStep === 'name');
            });
        }
        const heading = byId('missingTitleHeading');
        if (heading) heading.textContent = photos ? 'Add photos' : 'Name this item';
    }

    function renderPhotos() {
        const photos = flow.photos || [];
        const strip = byId('warehousePhotoStrip');
        if (strip) {
            strip.textContent = '';
            photos.forEach((src, index) => {
                const figure = document.createElement('figure');
                figure.className = 'identity-photo';
                const image = document.createElement('img');
                image.src = src;
                image.alt = `Photo ${index + 1}`;
                const remove = document.createElement('button');
                remove.type = 'button';
                remove.textContent = '\u00d7';
                remove.disabled = !!flow.busy;
                remove.setAttribute('aria-label', `Remove photo ${index + 1}`);
                remove.addEventListener('click', () => {
                    if (flow.busy) return;
                    flow.photos.splice(index, 1);
                    renderPhotos();
                });
                figure.append(image, remove);
                strip.appendChild(figure);
            });
        }
        const current = byId('missingTitlePhotoPreview');
        if (current) {
            current.hidden = !flow.existingImage || photos.length > 0;
            if (flow.existingImage && current.getAttribute('src') !== flow.existingImage) current.src = flow.existingImage;
        }
        const take = byId('missingTitlePhotoBtn');
        if (take) {
            take.disabled = !!flow.busy;
            take.innerHTML = `<i class="fas fa-camera"></i> ${photos.length ? 'Add another photo' : 'Take photo'}`;
        }
        const done = byId('warehousePhotoDoneBtn');
        if (done) {
            done.disabled = !photos.length || !!flow.busy;
            done.textContent = photos.length > 1 ? `Save ${photos.length} photos` : 'Save photo';
        }
        const skip = byId('warehousePhotoSkipBtn');
        if (skip) {
            skip.disabled = !!flow.busy;
            skip.textContent = flow.existingImage ? 'Keep current' : 'Skip';
        }
    }

    function photoHint() {
        if (flow.photos.length) return 'The first photo becomes the thumbnail.';
        return flow.existingImage
            ? 'Current thumbnail shown. A new photo replaces it.'
            : 'Take one or more photos. The first becomes the thumbnail.';
    }

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

    async function addPhotos(input) {
        const files = Array.from(input.files || []);
        input.value = '';
        if (!files.length) return;
        const generation = flow.generation;
        photoStatus('Preparing photo\u2026');
        let failed = false;
        for (const file of files) {
            try {
                const photo = await shrinkPhoto(file);
                if (generation !== flow.generation) return;
                flow.photos.push(photo);
            } catch (_) {
                failed = true;
            }
        }
        renderPhotos();
        if (failed) photoStatus('A photo could not be read. Try again.', 'bad');
        else photoStatus(photoHint());
    }

    function dataUrlBlob(dataUrl) {
        const [header, body] = String(dataUrl).split(',');
        const type = (/data:([^;,]+)/.exec(header) || [])[1] || 'image/jpeg';
        const bytes = atob(body || '');
        const buffer = new Uint8Array(bytes.length);
        for (let i = 0; i < bytes.length; i++) buffer[i] = bytes.charCodeAt(i);
        return new Blob([buffer], { type });
    }

    async function send(url, options, failure) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 30000);
        try {
            const response = await fetch(url, Object.assign({ signal: controller.signal }, options));
            const result = await response.json().catch(() => ({}));
            if (!response.ok || result.success === false) throw new Error(result.error || failure);
            return result;
        } catch (error) {
            if (error && error.name === 'AbortError') throw new Error('Saving photos timed out. Try again or skip.');
            throw error;
        } finally {
            clearTimeout(timeout);
        }
    }

    function complete(photos, imageUrl) {
        const modal = byId('missingTitleModal');
        if (!modal) return;
        modal.dispatchEvent(new CustomEvent('warehouse-identity:complete', {
            detail: { title: flow.title, photos, imageUrl: imageUrl || '' }
        }));
    }

    // The first photo replaces the thumbnail exactly as the single-photo flow did;
    // every photo also joins the item's prep photos so listing can use them.
    async function savePhotos() {
        if (flow.busy || !flow.photos.length) return;
        const generation = flow.generation;
        const job = { upc: flow.upc, title: flow.title, photos: flow.photos.slice() };
        flow.busy = true;
        renderPhotos();
        photoStatus(job.photos.length > 1 ? `Saving ${job.photos.length} photos\u2026` : 'Saving photo\u2026');
        try {
            if (flow.thumbnail.source !== job.photos[0]) {
                const saved = await send('/api/items-prep/temp-item', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ upc: job.upc, item_description: job.title, image_data: job.photos[0] })
                }, 'Thumbnail was not saved. Try again or skip.');
                if (generation !== flow.generation) return;
                flow.thumbnail = { source: job.photos[0], url: saved.image_url || '' };
            }
            const form = new FormData();
            job.photos.forEach((photo, index) => {
                const blob = dataUrlBlob(photo);
                form.append('photos[]', blob, `receiving-${index + 1}${blob.type === 'image/png' ? '.png' : '.jpg'}`);
            });
            await send('/api/items_prep/diagnostic/' + encodeURIComponent(job.upc) + '/photos',
                { method: 'POST', body: form }, 'Photos were not saved. Try again or skip.');
            if (generation !== flow.generation) return;
            flow.busy = false;
            complete(job.photos.length, flow.thumbnail.url);
        } catch (error) {
            if (generation !== flow.generation) return;
            flow.busy = false;
            renderPhotos();
            photoStatus((error && error.message) || 'Photos were not saved. Try again or skip.', 'bad');
        }
    }

    function reset() {
        cancelDictation();
        Object.assign(flow, {
            generation: flow.generation + 1,
            upc: '',
            title: '',
            photosWanted: false,
            existingImage: '',
            photos: [],
            thumbnail: { source: '', url: '' },
            busy: false
        });
        nameStatus('');
        photoStatus('');
        showStep('name');
        renderPhotos();
    }

    window.warehouseIdentity = {
        reset,
        // Call when the modal opens. `photos` decides whether step 2 follows the name.
        begin({ upc = '', photos = false, existingImage = '' } = {}) {
            reset();
            flow.upc = String(upc || '');
            flow.photosWanted = !!photos;
            flow.existingImage = String(existingImage || '').trim();
            showStep('name');
            renderPhotos();
        },
        // On a touch screen with a microphone the name starts from the mic button;
        // opening the keyboard straight away would cover it.
        prefersVoice() {
            const button = byId('warehouseDictateBtn');
            const coarse = !!(window.matchMedia && window.matchMedia('(pointer: coarse)').matches);
            return !!(button && !button.hidden && coarse);
        },
        async save(upc, title) {
            cancelDictation();
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 10000);
            try {
                nameStatus('Saving name\u2026');
                const response = await fetch('/api/custom-item/identity', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ upc, title }),
                    signal: controller.signal
                });
                const result = await response.json();
                if (!response.ok || !result.success) throw new Error(result.error || 'Name was not saved. Try again.');
                nameStatus('');
                flow.upc = String(upc || flow.upc);
                return result;
            } catch (error) {
                if (error.name === 'AbortError') throw new Error('Saving timed out. Your name is still here; try again.');
                throw error;
            } finally {
                clearTimeout(timeout);
            }
        },
        // After the page saved a name: returns true when the photo step is now showing
        // (it will fire "warehouse-identity:complete"), false when naming is finished.
        next(title) {
            const name = String(title || '').replace(/\s+/g, ' ').trim();
            if (flow.title && flow.title !== name) flow.thumbnail = { source: '', url: '' };
            flow.title = name;
            if (!flow.photosWanted) return false;
            const confirmed = byId('warehouseConfirmedName');
            if (confirmed) confirmed.textContent = name;
            showStep('photos');
            renderPhotos();
            photoStatus(photoHint());
            return true;
        }
    };

    document.addEventListener('DOMContentLoaded', () => {
        const mic = byId('warehouseDictateBtn');
        if (mic && (canRecord() || SpeechRecognition)) {
            mic.hidden = false;
            setMic('idle');
            mic.addEventListener('click', toggleDictation);
        }
        const take = byId('missingTitlePhotoBtn');
        const input = byId('missingTitlePhotoInput');
        if (take && input) {
            take.addEventListener('click', event => {
                event.preventDefault();
                if (!flow.busy) input.click();
            });
            input.addEventListener('change', () => addPhotos(input));
        }
        const done = byId('warehousePhotoDoneBtn');
        if (done) done.addEventListener('click', savePhotos);
        const skip = byId('warehousePhotoSkipBtn');
        if (skip) skip.addEventListener('click', () => {
            if (!flow.busy) complete(0, '');
        });
        const edit = byId('warehouseEditNameBtn');
        if (edit) edit.addEventListener('click', () => {
            if (flow.busy) return;
            nameStatus('');
            showStep('name');
        });
        reset();
    });
})();

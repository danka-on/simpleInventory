/* Shared naming flow for both receiving screens.
 *
 * The page owns the modal and the promise its scan waits on; this file owns what
 * happens inside it. Step 1 names the item, typed or dictated through
 * /api/warehouse/name-dictation; the optional note can be dictated too. When the
 * page confirms a saved name with next(), items that need a picture continue to
 * step 2: take photos, reorder them (the first is the thumbnail), save or skip.
 * Step 2 ends by firing "warehouse-identity:complete" on #missingTitleModal with
 * { title, photos, imageUrl }. Tones and the photo tiles come from media-capture.js.
 */
(() => {
    const NO_SPEECH_STOP_MS = 8000;
    const DICTATION = {
        name: {
            button: 'warehouseDictateBtn',
            label: 'warehouseDictateLabel',
            field: 'missingTitleInput',
            maxMs: 15000,
            quietMs: 1300,
            labels: { idle: 'Tap and say the name', starting: 'Starting microphone\u2026', listening: 'Listening\u2026', working: 'Writing it down\u2026' },
            prompt: 'Say the brand, item, size and color. Tap the mic when done.',
            done: 'Check it against the label, then confirm.'
        },
        note: {
            button: 'warehouseNoteDictateBtn',
            label: '',
            field: 'missingTitleNoteInput',
            maxMs: 60000,
            quietMs: 2000,
            labels: { idle: 'Dictate note', starting: 'Starting microphone\u2026', listening: 'Stop dictating the note', working: 'Writing the note\u2026' },
            prompt: 'Say the note. Tap the mic when done.',
            done: 'Note added. Check it before confirming.'
        }
    };
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    const byId = id => document.getElementById(id);
    const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
    const capture = () => window.MediaCapture;
    const playSound = name => (capture() ? capture().playSound(name) : 0);
    const flow = { generation: 0 };
    let strip = null;
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

    // ---- Dictation -------------------------------------------------------

    const canRecord = () => !!(window.MediaRecorder && navigator.mediaDevices && navigator.mediaDevices.getUserMedia);

    function setMic(kind, state) {
        const config = DICTATION[kind];
        const button = byId(config.button);
        if (!button) return;
        button.dataset.state = state;
        button.disabled = state === 'starting' || state === 'working';
        button.setAttribute('aria-pressed', state === 'listening' ? 'true' : 'false');
        button.setAttribute('aria-label', config.labels[state]);
        button.style.setProperty('--level', '0');
        const label = config.label && byId(config.label);
        if (label) label.textContent = config.labels[state];
    }

    function resetMics() {
        Object.keys(DICTATION).forEach(kind => setMic(kind, 'idle'));
    }

    function applyDictation(kind, text) {
        const field = byId(DICTATION[kind].field);
        if (!field) return;
        const heard = String(text || '').replace(/\s+/g, ' ').trim();
        if (kind === 'note') {
            const existing = field.value.trim();
            field.value = ((existing ? existing + ' ' : '') + heard).slice(0, 2000);
        } else {
            field.value = heard.slice(0, 200);
        }
        field.dispatchEvent(new Event('input', { bubbles: true }));
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
            try { recognition.speech.abort(); } catch (_) {}
            recognition = null;
        }
        resetMics();
    }

    function finishDictation(session, message, tone) {
        if (dictation !== session) return;
        dictation = null;
        setMic(session.kind, 'idle');
        nameStatus(message, tone);
    }

    async function startDictation(kind) {
        cancelDictation();
        // The page may still be saying "Item needs a name"; don't record it.
        try { if (window.speechSynthesis) window.speechSynthesis.cancel(); } catch (_) {}
        const config = DICTATION[kind];
        const session = { kind, chunks: [] };
        dictation = session;
        setMic(kind, 'starting');
        nameStatus('');
        // The start tone plays before the microphone opens, so it is heard, not recorded.
        await wait(playSound('mic-on'));
        if (dictation !== session) return;
        try {
            session.stream = await navigator.mediaDevices.getUserMedia({
                audio: { echoCancellation: true, noiseSuppression: true }
            });
        } catch (error) {
            finishDictation(session, error && error.name === 'NotAllowedError'
                ? 'Microphone access is blocked. Allow it for this site, or type instead.'
                : 'Microphone unavailable. Type instead.', 'bad');
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
            finishDictation(session, 'Recording is not supported here. Type instead.', 'bad');
            return;
        }
        session.startedAt = Date.now();
        session.maxTimer = setTimeout(() => stopRecording(session), config.maxMs);
        watchForPause(session);
        setMic(kind, 'listening');
        nameStatus(config.prompt);
    }

    // Stop by itself once the speaker pauses, so dictating is a single tap.
    function watchForPause(session) {
        const Context = window.AudioContext || window.webkitAudioContext;
        if (!Context) return;
        const config = DICTATION[session.kind];
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
                const button = byId(config.button);
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
                        if (now - quietSince >= config.quietMs) stopRecording(session);
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
        // Give the phone a moment to leave recording mode so the stop tone is audible.
        setTimeout(() => playSound('mic-off'), 120);
        const config = DICTATION[session.kind];
        const type = (session.recorder && session.recorder.mimeType) || recordingType() || 'audio/webm';
        const audio = new Blob(session.chunks, { type });
        if (!audio.size) {
            finishDictation(session, "Didn't catch that. Tap the microphone and try again.", 'bad');
            return;
        }
        setMic(session.kind, 'working');
        nameStatus('');
        session.controller = new AbortController();
        const timeout = setTimeout(() => session.controller.abort(), 30000);
        try {
            const form = new FormData();
            form.append('kind', session.kind);
            form.append('audio', audio, session.kind + fileExtension(type));
            const response = await fetch('/api/warehouse/name-dictation', {
                method: 'POST',
                body: form,
                signal: session.controller.signal
            });
            const result = await response.json().catch(() => ({}));
            if (dictation !== session) return;
            if (!response.ok || !result.success) throw new Error(result.error || 'Dictation failed. Type it or try again.');
            applyDictation(session.kind, result.text);
            finishDictation(session, config.done, 'ok');
        } catch (error) {
            finishDictation(session, error && error.name === 'AbortError'
                ? 'Dictation timed out. Try again or type it.'
                : (error && error.message) || 'Dictation failed. Type it or try again.', 'bad');
        } finally {
            clearTimeout(timeout);
        }
    }

    // Browsers without MediaRecorder can still use their own speech service.
    function startBrowserSpeech(kind) {
        cancelDictation();
        const config = DICTATION[kind];
        const speech = new SpeechRecognition();
        const session = { kind, speech };
        recognition = session;
        speech.lang = kind === 'name' ? 'en-US' : (document.documentElement.lang || navigator.language || 'en-US');
        speech.interimResults = false;
        speech.onresult = event => {
            if (recognition !== session) return;
            applyDictation(kind, event.results[0][0].transcript);
            nameStatus(config.done, 'ok');
        };
        speech.onerror = () => {
            if (recognition === session) nameStatus('Dictation unavailable. Type instead.', 'bad');
        };
        speech.onend = () => {
            if (recognition !== session) return;
            recognition = null;
            setMic(kind, 'idle');
            playSound('mic-off');
        };
        playSound('mic-on');
        setMic(kind, 'listening');
        nameStatus(config.prompt);
        try {
            speech.start();
        } catch (_) {
            recognition = null;
            setMic(kind, 'idle');
            nameStatus('Dictation unavailable. Type instead.', 'bad');
        }
    }

    function toggleDictation(kind) {
        if (recognition) {
            try { recognition.speech.stop(); } catch (_) {}
            return;
        }
        if (dictation) {
            if (dictation.kind === kind) {
                if (dictation.recorder && !dictation.stopping) stopRecording(dictation);
                return;
            }
            // The other field is still being written down; let it finish.
            if (dictation.stopping) return;
            cancelDictation();
        }
        if (canRecord()) startDictation(kind);
        else if (SpeechRecognition) startBrowserSpeech(kind);
    }

    // The name wraps onto more lines instead of scrolling sideways, so all of it shows.
    function fitNameField() {
        const field = byId('missingTitleInput');
        if (!field || field.tagName !== 'TEXTAREA' || !field.offsetParent) return;
        field.style.height = 'auto';
        field.style.height = (field.scrollHeight + field.offsetHeight - field.clientHeight) + 'px';
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
        if (!photos) requestAnimationFrame(fitNameField);
    }

    const currentPhotos = () => (strip ? strip.photos : []);

    function renderPhotoControls() {
        const count = currentPhotos().length;
        const current = byId('missingTitlePhotoPreview');
        if (current) {
            current.hidden = !flow.existingImage || count > 0;
            if (flow.existingImage && current.getAttribute('src') !== flow.existingImage) current.src = flow.existingImage;
        }
        const take = byId('missingTitlePhotoBtn');
        if (take) {
            take.disabled = !!flow.busy;
            take.innerHTML = `<i class="fas fa-camera"></i> ${count ? 'Add another photo' : 'Take photo'}`;
        }
        const done = byId('warehousePhotoDoneBtn');
        if (done) {
            done.disabled = !count || !!flow.busy;
            done.textContent = count > 1 ? `Save ${count} photos` : 'Save photo';
        }
        const skip = byId('warehousePhotoSkipBtn');
        if (skip) {
            skip.disabled = !!flow.busy;
            skip.textContent = flow.existingImage ? 'Keep current' : 'Skip';
        }
    }

    function renderPhotos() {
        if (strip) strip.render();
        renderPhotoControls();
    }

    function photoHint() {
        const hint = strip ? strip.hint() : '';
        if (hint) return hint;
        if (!currentPhotos().length && flow.existingImage) return 'Current thumbnail shown. A new photo replaces it.';
        return '';
    }

    async function addPhotos(input) {
        const files = Array.from(input.files || []);
        input.value = '';
        if (!files.length || !strip) return;
        photoStatus('Preparing photo\u2026');
        const result = await strip.add(files);
        if (result.stale) return;
        renderPhotoControls();
        if (result.failed) photoStatus('A photo could not be read. Try again.', 'bad');
        else photoStatus(photoHint());
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
        const photos = currentPhotos();
        if (flow.busy || !photos.length || !capture()) return;
        const generation = flow.generation;
        const job = { upc: flow.upc, title: flow.title, photos };
        flow.busy = true;
        renderPhotos();
        photoStatus(photos.length > 1 ? `Saving ${photos.length} photos\u2026` : 'Saving photo\u2026');
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
                const file = capture().photoFile(photo, `receiving-${index + 1}`);
                form.append('photos[]', file, file.name);
            });
            await send('/api/items_prep/diagnostic/' + encodeURIComponent(job.upc) + '/photos',
                { method: 'POST', body: form }, 'Photos were not saved. Try again or skip.');
            if (generation !== flow.generation) return;
            flow.busy = false;
            playSound('confirm');
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
            thumbnail: { source: '', url: '' },
            busy: false
        });
        if (strip) strip.set([]);
        nameStatus('');
        photoStatus('');
        const field = byId('missingTitleInput');
        if (field) field.style.height = '';
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
            playSound('confirm');
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
        const canDictate = canRecord() || !!SpeechRecognition;
        Object.keys(DICTATION).forEach(kind => {
            const button = byId(DICTATION[kind].button);
            if (!button || !canDictate) return;
            button.hidden = false;
            setMic(kind, 'idle');
            button.addEventListener('click', () => toggleDictation(kind));
        });
        const nameField = byId('missingTitleInput');
        if (nameField) nameField.addEventListener('input', fitNameField);
        window.addEventListener('resize', fitNameField);
        const modal = byId('missingTitleModal');
        // Any tap inside the prompt unlocks audio, so later tones are allowed to play.
        if (modal) modal.addEventListener('pointerdown', () => { if (capture()) capture().unlockAudio(); }, true);
        if (capture()) {
            strip = capture().createPhotoStrip(byId('warehousePhotoStrip'), {
                thumbnail: true,
                isBusy: () => !!flow.busy,
                onChange: () => {
                    renderPhotoControls();
                    photoStatus(photoHint());
                }
            });
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

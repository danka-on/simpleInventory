/* Prep + : capture one item, hit Next, capture the next one.
 *
 * The bench is the slow part, not the network, so nothing here waits on a server.
 * Next hands the finished item to a background queue and clears the form
 * immediately; the queue uploads one item at a time to /api/prep-plus/item and each
 * chip reports sent, failed or retrying. A failed item keeps its photos and audio in
 * memory so Retry is a real retry, not a re-shoot.
 *
 * Voice goes to the same two endpoints the receiving screens use:
 * /api/warehouse/name-dictation writes the title, and
 * /api/warehouse/voice-notes/transcribe returns Lithuanian plus English for the
 * condition note. The recording itself is uploaded with the item either way, so the
 * original is always there to play back when a transcript reads oddly.
 */
(() => {
    const capture = () => window.MediaCapture;
    const byId = id => document.getElementById(id);
    const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
    const playSound = name => (capture() ? capture().playSound(name) : 0);
    const escapeHtml = value => String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[ch]));

    const NO_SPEECH_STOP_MS = 8000;
    const MICS = {
        title: { button: 'micTitle', maxMs: 15000, quietMs: 1300, prompt: 'Say the brand, item, size and colour.' },
        condition: { button: 'micCondition', maxMs: 90000, quietMs: 2200, prompt: 'Say what is wrong with it, or how it looks.' }
    };

    // A barcode gun types far faster than fingers, which is how the page tells a scan
    // from someone filling the field in by hand and only spends a store lookup on a scan.
    const SCAN_GAP_MS = 35;
    const SCAN_SETTLE_MS = 140;
    const SCAN_MIN_KEYS = 6;
    const SCANNABLE = /^[0-9]{8,14}$/;

    const state = {
        photos: null,          // photo strip (data URLs)
        conditionAudio: null,  // { blob, name } kept for upload
        queue: [],
        sending: false,
        counter: 0,
        lookedUp: '',          // the barcode a lookup was already spent on
        lookingUp: false
    };
    const scan = { last: 0, fast: 0, timer: null };
    let mic = null;

    // ---- small ui helpers ------------------------------------------------

    function status(message, tone) {
        const el = byId('formStatus');
        el.textContent = message || '';
        el.dataset.tone = tone || '';
    }

    function micStatus(message, tone) {
        const el = byId('micStatus');
        el.textContent = message || '';
        el.dataset.tone = tone || '';
    }

    function lookupStatus(message, tone) {
        const el = byId('lookupStatus');
        el.textContent = message || '';
        el.dataset.tone = tone || '';
    }

    function setMicState(kind, mode) {
        const button = byId(MICS[kind].button);
        if (!button) return;
        button.dataset.mode = mode;
        button.classList.toggle('is-live', mode === 'listening');
        button.disabled = mode === 'starting' || mode === 'working';
        const label = button.querySelector('.mic-label');
        if (label) {
            label.textContent = mode === 'listening' ? 'Stop' : mode === 'working' ? 'Writing…'
                : mode === 'starting' ? 'Starting…' : (kind === 'title' ? 'Say the name' : 'Say the condition');
        }
    }

    function resetMics() {
        Object.keys(MICS).forEach(kind => setMicState(kind, 'idle'));
    }

    // ---- recording -------------------------------------------------------

    const canRecord = () => !!(window.MediaRecorder && navigator.mediaDevices && navigator.mediaDevices.getUserMedia);

    function recordingType() {
        const supported = type => {
            try { return MediaRecorder.isTypeSupported(type); } catch (_) { return false; }
        };
        return ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'].find(supported) || '';
    }

    function fileExtension(type) {
        if (/mp4|m4a|aac/i.test(type)) return '.mp4';
        if (/ogg/i.test(type)) return '.ogg';
        return '.webm';
    }

    function release(session) {
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

    function cancelMic() {
        const session = mic;
        mic = null;
        if (session) {
            session.stopping = true;
            if (session.controller) session.controller.abort();
            try {
                if (session.recorder && session.recorder.state !== 'inactive') session.recorder.stop();
            } catch (_) {}
            release(session);
        }
        resetMics();
    }

    function finishMic(session, message, tone) {
        if (mic !== session) return;
        mic = null;
        setMicState(session.kind, 'idle');
        micStatus(message, tone);
    }

    // Stop by itself once the speaker pauses, so dictating is a single tap.
    function watchForPause(session) {
        const Context = window.AudioContext || window.webkitAudioContext;
        if (!Context) return;
        const config = MICS[session.kind];
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
        handleRecording(session);
    }

    async function startMic(kind) {
        if (mic && mic.kind === kind) {
            stopRecording(mic);
            return;
        }
        cancelMic();
        if (!canRecord()) {
            micStatus('Recording is not supported here. Type it instead.', 'bad');
            return;
        }
        const config = MICS[kind];
        const session = { kind, chunks: [] };
        mic = session;
        setMicState(kind, 'starting');
        micStatus('');
        // The start tone plays before the microphone opens, so it is heard, not recorded.
        await wait(playSound('mic-on'));
        if (mic !== session) return;
        try {
            session.stream = await navigator.mediaDevices.getUserMedia({
                audio: { echoCancellation: true, noiseSuppression: true }
            });
        } catch (error) {
            finishMic(session, error && error.name === 'NotAllowedError'
                ? 'Microphone access is blocked. Allow it for this site, or type instead.'
                : 'Microphone unavailable. Type instead.', 'bad');
            return;
        }
        if (mic !== session) {
            release(session);
            return;
        }
        try {
            const type = recordingType();
            session.recorder = new MediaRecorder(session.stream, type ? { mimeType: type } : undefined);
            session.recorder.ondataavailable = event => {
                if (event.data && event.data.size) session.chunks.push(event.data);
            };
            session.recorder.onstop = () => handleRecording(session);
            session.recorder.start();
        } catch (_) {
            release(session);
            finishMic(session, 'Recording is not supported here. Type instead.', 'bad');
            return;
        }
        session.startedAt = Date.now();
        session.maxTimer = setTimeout(() => stopRecording(session), config.maxMs);
        watchForPause(session);
        setMicState(kind, 'listening');
        micStatus(config.prompt);
    }

    async function handleRecording(session) {
        if (session.handled) return;
        session.handled = true;
        release(session);
        if (mic !== session) return;
        setTimeout(() => playSound('mic-off'), 120);
        const type = (session.recorder && session.recorder.mimeType) || recordingType() || 'audio/webm';
        const blob = new Blob(session.chunks, { type });
        if (!blob.size) {
            finishMic(session, "Didn't catch that. Tap the microphone and try again.", 'bad');
            return;
        }
        setMicState(session.kind, 'working');
        micStatus('');
        session.controller = new AbortController();
        const timeout = setTimeout(() => session.controller.abort(), 60000);
        try {
            if (session.kind === 'title') await writeTitle(session, blob, type);
            else await writeCondition(session, blob, type);
        } catch (error) {
            finishMic(session, error && error.name === 'AbortError'
                ? 'That took too long. Try again or type it.'
                : (error && error.message) || 'Could not write that down. Type it instead.', 'bad');
        } finally {
            clearTimeout(timeout);
        }
    }

    async function writeTitle(session, blob, type) {
        const form = new FormData();
        form.append('kind', 'name');
        form.append('audio', blob, 'name' + fileExtension(type));
        const response = await fetch('/api/warehouse/name-dictation', {
            method: 'POST', body: form, signal: session.controller.signal
        });
        const result = await response.json().catch(() => ({}));
        if (mic !== session) return;
        if (!response.ok || !result.success) throw new Error(result.error || 'Dictation failed. Type it or try again.');
        const field = byId('itemTitle');
        field.value = String(result.text || '').trim();
        field.dispatchEvent(new Event('input', { bubbles: true }));
        finishMic(session, 'Check the name against the item, then carry on.', 'ok');
    }

    async function writeCondition(session, blob, type) {
        // The recording is kept whatever the transcript says: a clip is evidence, a
        // transcript is a convenience, and warehouse audio is not always clean.
        const name = 'condition' + fileExtension(type);
        state.conditionAudio = { blob, name };
        renderAudio();
        const form = new FormData();
        form.append('audio', blob, name);
        let text = '';
        try {
            const response = await fetch('/api/warehouse/voice-notes/transcribe', {
                method: 'POST', body: form, signal: session.controller.signal
            });
            const result = await response.json().catch(() => ({}));
            if (mic !== session) return;
            if (!response.ok || !result.success) throw new Error(result.error || 'Could not transcribe the note.');
            text = capture() ? capture().formatVoiceNote(result) : String(result.english || result.lithuanian || '');
        } catch (error) {
            finishMic(session, (error && error.message) || 'Could not transcribe the note. The recording is still attached.', 'bad');
            return;
        }
        const field = byId('itemNote');
        field.value = field.value.trim() ? field.value.trim() + '\n' + text : text;
        field.dispatchEvent(new Event('input', { bubbles: true }));
        finishMic(session, 'Note written down. The recording is attached too.', 'ok');
    }

    function renderAudio() {
        const row = byId('audioRow');
        if (!state.conditionAudio) {
            row.hidden = true;
            byId('audioPlayer').removeAttribute('src');
            return;
        }
        row.hidden = false;
        byId('audioPlayer').src = URL.createObjectURL(state.conditionAudio.blob);
    }

    // ---- barcode ---------------------------------------------------------

    function barcodeValue() {
        return String(byId('itemBarcode').value || '').trim();
    }

    // ---- scanning --------------------------------------------------------

    function markScanKey() {
        const now = Date.now();
        scan.fast = now - scan.last < SCAN_GAP_MS ? scan.fast + 1 : 0;
        scan.last = now;
        clearTimeout(scan.timer);
        scan.timer = setTimeout(() => endScan(false), SCAN_SETTLE_MS);
    }

    function endScan(terminated) {
        clearTimeout(scan.timer);
        const scanned = terminated || scan.fast >= SCAN_MIN_KEYS;
        scan.fast = 0;
        if (!scanned || !SCANNABLE.test(barcodeValue())) return;
        lookupStores({ auto: true });
    }

    // A scan has to land in the barcode box even when nothing was focused, or when the
    // last thing touched was a button: the person picks up an item and pulls the trigger.
    function onPageKey(event) {
        if (event.ctrlKey || event.metaKey || event.altKey) return;
        const field = byId('itemBarcode');
        const active = document.activeElement;
        const inField = active === field;
        if (event.key === 'Enter') {
            // A gun ends its scan with Enter; that must not submit or move on by itself.
            if (!inField) return;
            event.preventDefault();
            endScan(true);
            return;
        }
        if (event.key.length !== 1 || !/[0-9A-Za-z-]/.test(event.key)) return;
        const typingElsewhere = active && !inField
            && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable);
        if (typingElsewhere) return;  // they are writing a name or a note, not scanning
        if (!inField) {
            event.preventDefault();
            field.focus();
            field.value += event.key;
            field.dispatchEvent(new Event('input', { bubbles: true }));
        }
        markScanKey();
    }

    async function generateBarcode() {
        const button = byId('generateBarcode');
        button.disabled = true;
        status('Reserving a new barcode…');
        try {
            const used = state.queue.filter(job => job.status !== 'failed').map(job => job.barcode);
            const response = await fetch('/api/items-prep/generate-barcode', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ exclude_barcodes: used })
            });
            const result = await response.json();
            if (!response.ok || !result.success) throw new Error(result.error || 'Could not generate a barcode.');
            byId('itemBarcode').value = result.barcode;
            byId('itemBarcode').dispatchEvent(new Event('input', { bubbles: true }));
            status('Barcode ' + result.barcode + ' reserved. Print it and stick it on.', 'ok');
        } catch (error) {
            status(error.message || 'Could not generate a barcode.', 'bad');
        } finally {
            button.disabled = false;
        }
    }

    async function printBarcode() {
        const barcode = barcodeValue();
        if (!barcode) {
            status('Scan, type or generate a barcode before printing.', 'bad');
            return;
        }
        const title = String(byId('itemTitle').value || '').trim() || 'Prep + item';
        const button = byId('printBarcode');
        button.disabled = true;
        status('Sending the label to the printer…');
        try {
            const response = await fetch('/api/printer/print-barcode', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ upc: barcode, item_description: title, quantity: 1 })
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok || !result.success) throw new Error(result.error || 'The printer did not answer.');
            status('Label printed.', 'ok');
        } catch (error) {
            // A queued label still gets printed later from the print queue page.
            try {
                await fetch('/api/print-queue', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title, barcode })
                });
                status((error.message || 'The printer did not answer.') + ' Added to the print queue instead.', 'bad');
            } catch (_) {
                status(error.message || 'The printer did not answer.', 'bad');
            }
        } finally {
            button.disabled = false;
        }
    }

    function useName(title, label) {
        const field = byId('itemTitle');
        field.value = String(title || '').slice(0, 200);
        field.dispatchEvent(new Event('input', { bubbles: true }));
        lookupStatus('Name from ' + (label || 'the listing') + '. Check it against the item.', 'ok');
    }

    // Our own listings and the Macy manifest are things we already decided about, so a
    // hit there fills the name straight away. Outside catalogs are guesses about someone
    // else's barcode, so they are only ever offered.
    async function identify(barcode) {
        const response = await fetch('/api/prep-plus/identify?upc=' + encodeURIComponent(barcode));
        const result = await response.json();
        if (!response.ok || !result.success) throw new Error(result.error || 'Could not check this barcode.');
        return result;
    }

    async function askCatalogs(barcode, results) {
        results.innerHTML = '<div class="lookup-note">Not ours — asking Amazon, eBay and UPCitemdb…</div>';
        const response = await fetch('/api/items-prep/unmatched/lookup?upc=' + encodeURIComponent(barcode));
        const result = await response.json();
        if (!response.ok || !result.success) throw new Error(result.error || 'The lookup is unavailable.');
        if (barcodeValue() !== barcode) return;  // scanned again while this was in flight
        const candidates = result.candidates || [];
        if (!candidates.length) {
            const tried = (result.tried || []).map(row => `${row.source}: ${row.result}`).join(' · ');
            results.innerHTML = `<div class="lookup-note">${escapeHtml(result.message || 'Nothing we hold and no catalog knows this barcode.')}`
                + (tried ? `<br><span class="muted">${escapeHtml(tried)}</span>` : '') + '</div>';
            lookupStatus('Nobody knows this one — say or type the name.');
            return;
        }
        results.innerHTML = candidates.map((candidate, index) => `
            <button type="button" class="lookup-hit" data-hit="${index}">
                ${candidate.image_url ? `<img src="${escapeHtml(candidate.image_url)}" alt="">` : '<span class="lookup-noimg">no photo</span>'}
                <span class="lookup-text">
                    <strong>${escapeHtml(candidate.title || '')}</strong>
                    <span class="muted">${escapeHtml(candidate.source || '')}</span>
                </span>
            </button>`).join('');
        results.querySelectorAll('[data-hit]').forEach(button => {
            button.addEventListener('click', () => {
                const candidate = candidates[Number(button.dataset.hit)];
                useName(candidate.title, candidate.source);
            });
        });
        lookupStatus(`${candidates.length} catalog match${candidates.length > 1 ? 'es' : ''} — tap the right one, or say the name yourself.`);
    }

    async function lookupStores(options = {}) {
        const barcode = barcodeValue();
        if (!barcode) {
            if (!options.auto) lookupStatus('Scan or type the item barcode first — a lookup needs it.', 'bad');
            return;
        }
        // A scan that repeats, or a second scan of the same code, must not spend another
        // lookup: UPCitemdb's free allowance is about a hundred a day for the whole site.
        if (options.auto && (state.lookingUp || barcode === state.lookedUp)) return;
        state.lookedUp = barcode;
        state.lookingUp = true;
        const button = byId('lookupStores');
        const results = byId('lookupResults');
        button.disabled = true;
        lookupStatus('');
        results.innerHTML = '<div class="lookup-note">Checking what we already know…</div>';
        try {
            const ours = await identify(barcode);
            if (barcodeValue() !== barcode) return;
            if (ours.found) {
                results.innerHTML = '';
                // A scan must never overwrite a name the person already said or typed.
                const typed = String(byId('itemTitle').value || '').trim();
                if (typed && options.auto) lookupStatus(`We hold this as "${ours.title}" (${ours.label}). Your name is kept.`);
                else useName(ours.title, ours.label);
                return;
            }
            await askCatalogs(barcode, results);
        } catch (error) {
            results.innerHTML = `<div class="lookup-note bad">${escapeHtml(error.message || 'The lookup is unavailable.')}</div>`;
        } finally {
            state.lookingUp = false;
            button.disabled = false;
        }
    }

    // ---- the item form ---------------------------------------------------

    function condition() {
        const checked = document.querySelector('input[name="condition"]:checked');
        return checked ? checked.value : 'good';
    }

    function applyCondition() {
        const damaged = condition() === 'damaged';
        byId('defectRow').hidden = !damaged;
        byId('itemCard').dataset.condition = damaged ? 'damaged' : 'good';
    }

    function selectedDefects() {
        return Array.from(document.querySelectorAll('#defectRow input:checked')).map(input => input.value);
    }

    function updateReady() {
        const ready = !!barcodeValue() && !!String(byId('itemTitle').value || '').trim();
        byId('nextItem').disabled = !ready;
        // The hint's words live in the template so i18n.js can translate them.
        byId('readyHint').hidden = ready;
    }

    function resetForm() {
        byId('itemTitle').value = '';
        byId('itemNote').value = '';
        byId('itemBarcode').value = '';
        state.photos.set([]);
        state.conditionAudio = null;
        renderAudio();
        document.querySelectorAll('#defectRow input:checked').forEach(input => { input.checked = false; });
        const good = document.querySelector('input[name="condition"][value="good"]');
        if (good) good.checked = true;
        applyCondition();
        byId('lookupResults').innerHTML = '';
        lookupStatus('');
        micStatus('');
        state.lookedUp = '';
        scan.fast = 0;
        updateReady();
        byId('itemBarcode').focus();
    }

    function dataUrlToBlob(dataUrl) {
        if (capture() && capture().photoBlob) return capture().photoBlob(dataUrl);
        const [head, body] = String(dataUrl).split(',');
        const bytes = atob(body || '');
        const buffer = new Uint8Array(bytes.length);
        for (let i = 0; i < bytes.length; i++) buffer[i] = bytes.charCodeAt(i);
        return new Blob([buffer], { type: (head.match(/:(.*?);/) || [, 'image/jpeg'])[1] });
    }

    function newToken() {
        if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
        return 'xxxxxxxxxxxx4xxxyxxxxxxxxxxxxxxx'.replace(/[xy]/g, ch => {
            const random = Math.random() * 16 | 0;
            return (ch === 'x' ? random : (random & 0x3 | 0x8)).toString(16);
        });
    }

    function queueItem() {
        const barcode = barcodeValue();
        const title = String(byId('itemTitle').value || '').trim();
        if (!barcode || !title) {
            status('A name and a barcode are needed before this item can go on.', 'bad');
            return;
        }
        if (mic) cancelMic();
        const job = {
            id: ++state.counter,
            token: newToken(),
            barcode,
            title,
            condition: condition(),
            defects: selectedDefects(),
            note: String(byId('itemNote').value || '').trim(),
            lot: String(byId('itemLot').value || '').trim(),
            photos: state.photos.photos,
            photoCount: state.photos.count,
            audio: state.conditionAudio,
            status: 'waiting',
            message: 'Waiting to upload'
        };
        state.queue.unshift(job);
        renderQueue();
        playSound('confirm');
        status(`${title} queued as ${barcode}. Next item.`, 'ok');
        resetForm();
        drain();
    }

    function jobForm(job) {
        const form = new FormData();
        form.append('request_id', job.token);
        form.append('barcode', job.barcode);
        form.append('title', job.title);
        form.append('condition', job.condition);
        form.append('note', job.note);
        form.append('lot_number', job.lot);
        job.defects.forEach(defect => form.append('defect[]', defect));
        job.photos.forEach((photo, index) => form.append('photos[]', dataUrlToBlob(photo), `photo_${index + 1}.jpg`));
        if (job.audio) form.append('audio', job.audio.blob, job.audio.name);
        return form;
    }

    async function drain() {
        if (state.sending) return;
        const job = [...state.queue].reverse().find(entry => entry.status === 'waiting');
        if (!job) {
            state.sending = false;
            return;
        }
        state.sending = true;
        job.status = 'sending';
        job.message = 'Uploading…';
        renderQueue();
        try {
            const response = await fetch('/api/prep-plus/item', { method: 'POST', body: jobForm(job) });
            const result = await response.json().catch(() => ({}));
            if (!response.ok || !result.success) throw new Error(result.error || 'The server refused this item.');
            job.status = 'sent';
            job.savedUpc = result.upc;
            job.message = result.upc === job.barcode ? 'On Items to List' : `On Items to List as ${result.upc}`;
            // The photos are on the server now; keeping the data URLs would grow the tab.
            job.photos = [];
            job.audio = null;
        } catch (error) {
            job.status = 'failed';
            job.message = error.message || 'Upload failed.';
        } finally {
            state.sending = false;
            renderQueue();
            drain();
        }
    }

    function renderQueue() {
        const list = byId('queueList');
        const sent = state.queue.filter(job => job.status === 'sent').length;
        const failed = state.queue.filter(job => job.status === 'failed').length;
        const pending = state.queue.length - sent - failed;
        // Nothing has been added yet only before the first item, and the template's
        // own wording for that is the one i18n.js translated.
        if (state.queue.length) {
            byId('queueCount').textContent =
                `${sent} uploaded · ${pending} waiting${failed ? ` · ${failed} failed` : ''}`;
        }
        byId('queueEmpty').hidden = state.queue.length > 0;
        list.innerHTML = state.queue.map(job => `
            <li class="queue-row" data-status="${job.status}">
                <span class="queue-dot"></span>
                <span class="queue-text">
                    <strong>${escapeHtml(job.title)}</strong>
                    <span class="muted">${escapeHtml(job.savedUpc || job.barcode)} · ${job.condition === 'damaged' ? 'Damaged' : 'Good'}${job.photoCount ? ` · ${job.photoCount} photo${job.photoCount > 1 ? 's' : ''}` : ''}</span>
                    <span class="queue-message">${escapeHtml(job.message)}</span>
                </span>
                ${job.status === 'failed' ? `<button type="button" class="btn btn-gray" data-retry="${job.id}">Retry</button>` : ''}
                ${job.status === 'sent' ? `<a class="queue-link" href="/items-to-list?q=${encodeURIComponent(job.barcode)}">Open</a>` : ''}
            </li>`).join('');
        list.querySelectorAll('[data-retry]').forEach(button => {
            button.addEventListener('click', () => {
                const job = state.queue.find(entry => entry.id === Number(button.dataset.retry));
                if (!job) return;
                job.status = 'waiting';
                job.message = 'Waiting to upload';
                renderQueue();
                drain();
            });
        });
    }

    // ---- wiring ----------------------------------------------------------

    function start() {
        state.photos = capture().createPhotoStrip(byId('photoStrip'), {
            onChange: () => {
                const count = state.photos.count;
                byId('photoCount').textContent = count ? `${count} photo${count > 1 ? 's' : ''} — the first one is the thumbnail.` : '';
                byId('photoHint').textContent = state.photos.hint();
            }
        });

        byId('photoInput').addEventListener('change', async event => {
            const files = Array.from(event.target.files || []);
            event.target.value = '';
            if (!files.length) return;
            status('Preparing photos…');
            const result = await state.photos.add(files);
            status(result.failed ? 'A photo could not be read. Take it again.' : '', result.failed ? 'bad' : '');
        });
        byId('takePhoto').addEventListener('click', () => byId('photoInput').click());

        Object.keys(MICS).forEach(kind => {
            const button = byId(MICS[kind].button);
            if (button) button.addEventListener('click', () => startMic(kind));
        });

        byId('generateBarcode').addEventListener('click', generateBarcode);
        byId('printBarcode').addEventListener('click', printBarcode);
        byId('lookupStores').addEventListener('click', lookupStores);
        byId('nextItem').addEventListener('click', queueItem);
        byId('itemTitle').addEventListener('input', updateReady);
        byId('itemBarcode').addEventListener('input', () => {
            updateReady();
            // A different code means the last answer is about a different item.
            if (barcodeValue() !== state.lookedUp) {
                byId('lookupResults').innerHTML = '';
                lookupStatus('');
            }
        });
        // Capture phase, on the document: a scan should land in the barcode box whatever
        // happens to be focused, including nothing at all on a freshly opened page.
        document.addEventListener('keydown', onPageKey, true);
        document.querySelectorAll('input[name="condition"]').forEach(input => {
            input.addEventListener('change', applyCondition);
        });
        byId('clearAudio').addEventListener('click', () => {
            state.conditionAudio = null;
            renderAudio();
        });
        document.addEventListener('pointerdown', () => capture().unlockAudio(), { once: true });
        window.addEventListener('beforeunload', event => {
            if (state.queue.some(job => job.status !== 'sent')) {
                event.preventDefault();
                event.returnValue = '';
            }
        });

        applyCondition();
        renderQueue();
        updateReady();
        byId('itemBarcode').focus();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();

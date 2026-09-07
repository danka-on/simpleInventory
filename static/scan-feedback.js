/* Receiving alerts: inspect the original scan before UPC normalization. */
(() => {
    'use strict';
    let context;
    let speechQueue = Promise.resolve();
    const thumbnailAlerts = new Set();
    function inspect(raw) {
        const code = String(raw || '').trim();
        if (!code) return [];
        const match = /^(\d+)(?:-([1-9]\d*))?$/.exec(code);
        if (!match) return ['Barcode contains unexpected characters or an invalid suffix.'];
        const base = match[1];
        const warnings = [];
        const zeroes = (/^0+/.exec(base) || [''])[0].length;
        if (zeroes > 1) warnings.push(`Barcode starts with ${zeroes} zeroes. Check the label.`);
        else if (zeroes === 1) warnings.push('Barcode starts with zero. Check the label.');
        if (![8, 12, 13].includes(base.length)) {
            warnings.push(`Barcode has ${base.length} digits. Check the length. This screen expects 8, 12, or 13 digits before a warehouse suffix.`);
        }
        return warnings;
    }
    function show(message) {
        const status = document.getElementById('scanFeedbackStatus');
        if (status) status.textContent = message;
    }
    async function unlock() {
        try {
            const Audio = window.AudioContext || window.webkitAudioContext;
            if (!Audio) return;
            context = window.audioCtx || context || new Audio();
            window.audioCtx = context;
            if (context.state === 'suspended') await Promise.race([context.resume(), new Promise(resolve => setTimeout(resolve, 300))]);
        } catch (_) { /* Visible feedback remains available. */ }
    }
    async function tones(kind) {
        await unlock();
        if (!context || context.state !== 'running') return;
        const notes = kind === 'info' ? [932, 698, 1046] : [440, 220, 440, 220];
        notes.forEach((frequency, index) => {
            const start = context.currentTime + 0.02 + index * 0.14;
            const osc = context.createOscillator();
            const gain = context.createGain();
            osc.type = kind === 'info' ? 'triangle' : 'sine';
            osc.frequency.setValueAtTime(frequency, start);
            gain.gain.setValueAtTime(0.0001, start);
            gain.gain.exponentialRampToValueAtTime(0.2, start + 0.015);
            gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.12);
            osc.connect(gain);
            gain.connect(context.destination);
            osc.start(start);
            osc.stop(start + 0.13);
            osc.onended = () => { osc.disconnect(); gain.disconnect(); };
        });
        await new Promise(resolve => setTimeout(resolve, notes.length * 140 + 30));
    }
    function speak(message) {
        if (!window.speechSynthesis || !window.SpeechSynthesisUtterance) return Promise.resolve();
        const run = () => new Promise(resolve => {
            const utterance = new window.SpeechSynthesisUtterance(message);
            utterance.lang = document.documentElement.lang || navigator.language || 'en-US';
            utterance.volume = 1;
            utterance.rate = 1.05;
            let timer;
            const finish = () => { clearTimeout(timer); resolve(); };
            utterance.onend = finish;
            utterance.onerror = finish;
            // A browser denying speech must never leave the scan queue stuck.
            timer = setTimeout(() => { window.speechSynthesis.cancel(); finish(); }, 6500);
            try {
                window.speechSynthesis.resume();
                window.speechSynthesis.speak(utterance);
            } catch (_) { finish(); }
        });
        speechQueue = speechQueue.then(run, run);
        return speechQueue;
    }
    window.ScanFeedback = {
        inspect,
        async check(raw) {
            const warnings = inspect(raw);
            if (!warnings.length) { show(''); return; }
            show(warnings.join(' '));
            await tones('warning');
            if (navigator.vibrate) navigator.vibrate([150, 70, 150]);
            // Finish the warning before a single scan navigates to the next page.
            await speak(warnings.map(w => w.split(' This screen')[0]).join(' '));
        },
        async needsInfo(missingTitle = true, barcode = '') {
            if (!missingTitle && barcode) {
                if (thumbnailAlerts.has(barcode)) return;
                thumbnailAlerts.add(barcode);
            }
            const message = missingTitle ? 'Item needs a name. Check the label.' : 'Item needs a thumbnail.';
            show(message);
            await tones('info');
            if (navigator.vibrate) navigator.vibrate([90, 60, 90, 60, 130]);
            await speak(message);
        }
    };
    document.addEventListener('DOMContentLoaded', () => {
        const button = document.getElementById('scanVoiceOnBtn');
        if (!button) return;
        button.addEventListener('click', async () => {
            // Explicit interaction unlocks sound on browsers that require it.
            // Voice stays enabled; this button also provides a repeatable test.
            await unlock();
            await tones('info');
            if (!window.speechSynthesis || !window.SpeechSynthesisUtterance) {
                show('Voice is unavailable in this browser. Beep alerts are enabled.');
                return;
            }
            show('Voice alerts on. If silent, check device volume and browser sound permissions.');
            await speak('Voice alerts are on. Ready to scan.');
            const input = document.getElementById('barcodeTextInput') || document.getElementById('barcodeInput');
            if (input) input.focus();
        });
        document.addEventListener('pointerdown', unlock, { once: true });
        document.addEventListener('keydown', unlock, { once: true });
    });
})();

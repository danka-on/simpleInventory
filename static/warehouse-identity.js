/* Shared, durable naming controls for both receiving screens. */
(() => {
    let recognition = null;
    const status = () => document.getElementById('warehouseIdentityStatus');
    window.warehouseIdentity = {
        reset() {
            if (recognition) recognition.abort();
            recognition = null;
            if (status()) status().textContent = '';
        },
        async save(upc, title) {
            if (recognition) recognition.abort();
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 10000);
            try {
                status().textContent = 'Saving name…';
                const response = await fetch('/api/custom-item/identity', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ upc, title }),
                    signal: controller.signal
                });
                const result = await response.json();
                if (!response.ok || !result.success) throw new Error(result.error || 'Name was not saved. Try again.');
                status().textContent = '';
                return result;
            } catch (error) {
                if (error.name === 'AbortError') throw new Error('Saving timed out. Your name is still here; try again.');
                throw error;
            } finally {
                clearTimeout(timeout);
            }
        }
    };
    document.addEventListener('DOMContentLoaded', () => {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        const button = document.getElementById('warehouseDictateBtn');
        if (!SpeechRecognition || !button) return;
        button.hidden = false;
        button.addEventListener('click', () => {
            window.warehouseIdentity.reset();
            const speech = new SpeechRecognition();
            recognition = speech;
            speech.lang = document.documentElement.lang || navigator.language || 'en-US';
            speech.interimResults = false;
            speech.onresult = event => {
                if (recognition !== speech) return;
                document.getElementById('missingTitleInput').value = event.results[0][0].transcript.trim().slice(0, 200);
                status().textContent = 'Check the words against the label, then press Complete.';
            };
            speech.onerror = () => {
                if (recognition === speech) status().textContent = 'Dictation unavailable. Type the name or use your keyboard microphone.';
            };
            status().textContent = 'Listening… Say the brand, item and size/color.';
            try { speech.start(); } catch (_) { status().textContent = 'Dictation unavailable. Use your keyboard microphone or type.'; }
        });
    });
})();

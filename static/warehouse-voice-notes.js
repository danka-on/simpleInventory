/* Opening a note only reads saved text. Only the button starts paid analysis. */
window.WarehouseVoiceNotes = (() => {
    const escape = value => String(value || '').replace(/[&<>"']/g, ch => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[ch]));
    const endpoint = id => `/api/warehouse/voice-notes/${id}/analysis`;

    function render(voice) {
        const id = Number(voice.id);
        if (!Number.isSafeInteger(id) || id <= 0) return '';
        return `<div class="warehouse-voice-analysis" data-voice-id="${id}" style="margin-top:8px;overflow-wrap:anywhere;">
            <button type="button" data-voice-analyze disabled>Loading saved text…</button>
            <div data-voice-status role="status" aria-live="polite" style="margin-top:6px;font-size:.85rem;"></div>
            <div data-voice-text></div>
        </div>`;
    }

    function display(card, analysis) {
        const button = card.querySelector('[data-voice-analyze]');
        const complete = analysis.status === 'complete';
        const running = analysis.status === 'processing';
        button.disabled = complete || running;
        button.textContent = complete ? 'Analyzed + translated' : (running ? 'Analyzing…' :
            (analysis.lithuanian ? 'Retry translation' : 'Analyze + translate'));
        card.querySelector('[data-voice-text]').innerHTML =
            (analysis.lithuanian ? `<div style="margin-top:12px;"><strong>Lietuvių (original)</strong><div lang="lt" style="white-space:pre-wrap;line-height:1.5;">${escape(analysis.lithuanian)}</div></div>` : '') +
            (analysis.english ? `<div style="margin-top:12px;"><strong>English</strong><div lang="en" style="white-space:pre-wrap;line-height:1.5;">${escape(analysis.english)}</div></div>` : '');
        card.querySelector('[data-voice-status]').textContent = analysis.error ||
            (running ? 'Transcribing Lithuanian and translating to English…' :
                (complete ? 'AI transcript and translation. Play the recording to check unclear details.' : ''));
        return running;
    }

    async function load(card) {
        if (!card.isConnected) return;
        try {
            const response = await fetch(endpoint(card.dataset.voiceId), {cache: 'no-store'});
            const data = await response.json();
            if (!response.ok || !data.success) throw new Error(data.error || 'Unable to load saved text.');
            if (display(card, data.analysis)) setTimeout(() => load(card), 3000);
        } catch (error) {
            const button = card.querySelector('[data-voice-analyze]');
            button.disabled = false;
            button.textContent = 'Analyze + translate';
            card.querySelector('[data-voice-status]').textContent = error.message || 'Unable to load saved text.';
        }
    }

    function hydrate(root) {
        root.querySelectorAll('[data-voice-id]').forEach(card => {
            if (card.dataset.voiceBound) return;
            card.dataset.voiceBound = '1';
            card.querySelector('[data-voice-analyze]').addEventListener('click', async () => {
                const button = card.querySelector('[data-voice-analyze]');
                if (button.disabled) return;
                button.disabled = true;
                button.textContent = 'Analyzing…';
                card.querySelector('[data-voice-status]').textContent = 'Transcribing Lithuanian and translating to English…';
                try {
                    const response = await fetch(endpoint(card.dataset.voiceId), {
                        method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'
                    });
                    const data = await response.json();
                    if (!response.ok || !data.success) throw new Error(data.error || 'Analysis failed. Please retry.');
                    display(card, data.analysis);
                } catch (error) {
                    // Recover persisted Lithuanian and a concurrent/server-side result after a lost response.
                    await load(card);
                    card.querySelector('[data-voice-status]').textContent = error.message || 'Analysis failed. Please retry.';
                }
            });
            load(card);
        });
    }
    return {render, hydrate};
})();

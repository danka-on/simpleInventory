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
            <button type="button" data-voice-reanalyze hidden>Analyze again</button>
            <div data-voice-status role="status" aria-live="polite" style="margin-top:6px;font-size:.85rem;"></div>
            <div data-voice-text></div>
        </div>`;
    }

    function display(card, analysis) {
        const button = card.querySelector('[data-voice-analyze]');
        const complete = analysis.status === 'complete';
        const running = analysis.status === 'processing';
        button.disabled = complete || running;
        const again = card.querySelector('[data-voice-reanalyze]');
        again.hidden = !complete;
        again.disabled = running;
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
            async function analyze(reanalyze = false) {
                const button = card.querySelector('[data-voice-analyze]');
                const again = card.querySelector('[data-voice-reanalyze]');
                if (reanalyze ? again.disabled : button.disabled) return;
                button.disabled = true;
                again.disabled = true;
                button.textContent = 'Analyzing…';
                card.querySelector('[data-voice-status]').textContent = 'Transcribing Lithuanian and translating to English…';
                try {
                    const response = await fetch(endpoint(card.dataset.voiceId), {
                        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({reanalyze})
                    });
                    const data = await response.json();
                    if (!response.ok || !data.success) throw new Error(data.error || 'Analysis failed. Please retry.');
                    display(card, data.analysis);
                } catch (error) {
                    // Recover persisted Lithuanian and a concurrent/server-side result after a lost response.
                    await load(card);
                    card.querySelector('[data-voice-status]').textContent = error.message || 'Analysis failed. Please retry.';
                }
            }
            card.querySelector('[data-voice-analyze]').addEventListener('click', () => analyze());
            card.querySelector('[data-voice-reanalyze]').addEventListener('click', () => analyze(true));
            load(card);
        });
        hydrateWritten(root);
    }

    function renderWritten(kind, id, original) {
        if (!String(original || '').trim() || !['warehouse','prep-note','prep-reason','prep-status-note'].includes(kind)
            || !Number.isSafeInteger(Number(id)) || Number(id) <= 0) return '';
        return `<div data-written-id="${Number(id)}" data-written-kind="${kind}" data-written-original="${escape(original)}"
            style="margin-top:8px;white-space:normal;overflow-wrap:anywhere;">
            <button type="button" data-written-translate disabled>Loading saved translation…</button>
            <div data-written-status role="status" aria-live="polite" style="margin-top:6px;font-size:.85rem;"></div>
            <div data-written-result></div></div>`;
    }

    function hydrateWritten(root) {
        root.querySelectorAll('[data-written-id]').forEach(card => {
            if (card.dataset.writtenBound) return;
            card.dataset.writtenBound = '1';
            const button = card.querySelector('[data-written-translate]');
            const status = card.querySelector('[data-written-status]');
            const url = `/api/warehouse/written-notes/${card.dataset.writtenKind}/${card.dataset.writtenId}/translation`;
            const normalize = value => String(value || '').replace(/\r\n/g, '\n').trim();
            function display(result) {
                if (normalize(result.original) !== normalize(card.dataset.writtenOriginal)) {
                    button.disabled = true;
                    button.textContent = 'Reopen note';
                    status.textContent = 'This note changed. Reopen the item to see the current text.';
                    card.querySelector('[data-written-result]').textContent = '';
                    return false;
                }
                const running = result.status === 'processing', done = result.status === 'complete';
                button.disabled = running || done;
                button.textContent = running ? 'Translating…' : done ? 'Translated' : 'Translate to English';
                status.textContent = result.error || (running ? 'Translating the note to English…' : '');
                card.querySelector('[data-written-result]').innerHTML = result.english ?
                    `<div style="margin-top:10px;"><strong>English</strong><div lang="en" style="white-space:pre-wrap;line-height:1.5;">${escape(result.english)}</div></div>` : '';
                return running;
            }
            async function read() {
                if (!card.isConnected) return;
                try {
                    const response = await fetch(url, {cache:'no-store'});
                    const data = await response.json();
                    if (!response.ok || !data.success) throw new Error(data.error || 'Unable to load translation.');
                    if (display(data.translation)) setTimeout(read, 3000);
                } catch (error) {
                    button.disabled = false;
                    button.textContent = 'Translate to English';
                    status.textContent = error.message;
                }
            }
            button.addEventListener('click', async () => {
                if (button.disabled) return;
                button.disabled = true;
                button.textContent = 'Translating…';
                status.textContent = 'Translating the note to English…';
                try {
                    const response = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
                        body:JSON.stringify({original:card.dataset.writtenOriginal})});
                    const data = await response.json();
                    if (!response.ok || !data.success) throw new Error(data.error || 'Translation failed. Please retry.');
                    display(data.translation);
                } catch (error) {
                    await read();
                    status.textContent = error.message;
                }
            });
            read();
        });
    }
    return {render, renderWritten, hydrate};
})();

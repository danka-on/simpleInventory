/* Saved FBA exceptions. Checks are read-only; transferring requires an explicit click. */
(() => {
  'use strict';
  const root = document.getElementById('attentionList');
  if (!root) return;
  const labels = {approval: 'Amazon approval required', catalog: 'Brand / catalog conflict', listing: 'Listing needs correction', activation: 'Waiting for FBA activation', ready: 'Ready to retry'};
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  let items = [], drafts = [], busy = false, loaded = false;
  const message = text => { document.getElementById('attentionStatus').textContent = text; };
  const visible = () => document.getElementById('panel-attention').classList.contains('active') && !document.hidden;
  async function api(url, body) {
    const response = await fetch(url, body === undefined ? {headers: {Accept: 'application/json'}} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.error || 'Could not load Needs attention.');
    return data;
  }
  function render() {
    const filter = document.getElementById('attentionFilter').value;
    const query = document.getElementById('attentionSearch').value.trim().toLowerCase();
    const open = items.filter(r => r.status !== 'assigned');
    document.getElementById('attentionCount').textContent = open.length;
    document.getElementById('attentionTotals').textContent = `${open.length} item types · ${open.reduce((n, r) => n + Number(r.item.quantity || 0), 0)} units saved · ${open.filter(r => r.status === 'ready_to_retry').length} ready to retry`;
    const rows = items.filter(r => (filter === 'assigned' ? r.status === 'assigned' : r.status !== 'assigned') && (['all', 'assigned'].includes(filter) || r.category === filter) && (!query || [r.item.barcode, r.item.title, r.item.seller_sku, r.checked_sku, r.current_reason].join(' ').toLowerCase().includes(query)));
    root.innerHTML = rows.length ? rows.map(r => `<article class="review-card">
      <div class="review-top"><div><div class="review-title">${esc(r.item.title || r.item.barcode)}</div><div class="review-meta">${esc(r.item.barcode)} · SKU ${esc(r.checked_sku || r.item.seller_sku || 'not saved')} · <strong>${esc(r.item.quantity)} units</strong> · source batch #${r.source_session_id}</div></div><span class="reason">${esc(r.status === 'assigned' ? 'Added to shipment' : labels[r.category])}</span></div>
      <p style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(r.current_reason)}</p>
      ${r.original_reason !== r.current_reason ? `<details><summary>Original reason</summary><p style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(r.original_reason)}</p></details>` : ''}
      <p class="muted">${r.checked_at ? 'Last check: ' + esc(new Date(r.checked_at).toLocaleString()) : 'Not yet rechecked'}${r.source_active && r.status !== 'assigned' ? ' · Still in the source batch: set it aside there before moving it.' : ''}</p>
      ${r.check_error ? `<p class="danger">${esc(r.check_error)} The original reason is retained.</p>` : ''}
      <div class="review-actions">${r.status === 'assigned' ? `<button class="btn btn-blue btn-sm" data-open="${r.assigned_session_id}">Open shipment #${r.assigned_session_id}</button>` : `<button class="btn btn-ghost btn-sm" data-check="${r.id}" ${busy ? 'disabled' : ''}>Recheck</button><a class="btn btn-blue btn-sm" href="${esc(r.fix_url)}" target="_blank" rel="noopener">Fix listing ↗</a>${r.approval_required || r.category === 'approval' ? `<a class="btn btn-ghost btn-sm" href="${esc(r.approval_url)}" target="_blank" rel="noopener">Open Amazon approval ↗</a>` : ''}${r.status === 'ready_to_retry' ? `<button class="btn btn-green btn-sm" data-add="${r.id}" ${busy || r.source_active ? 'disabled' : ''}>Add ${esc(r.item.quantity)} units to next shipment</button>` : ''}`}</div>
    </article>`).join('') : '<div class="empty">No items match this view. Failed and set-aside items are saved here automatically.</div>';
    document.getElementById('attentionRecheckAll').disabled = busy || !open.length;
    document.getElementById('attentionRefresh').disabled = busy;
  }
  async function load() {
    const data = await api('/api/fba-prep/attention');
    items = data.items; drafts = data.drafts; loaded = true;
    const select = document.getElementById('attentionDestination'), previous = select.value;
    select.innerHTML = '<option value="">Create a new retry batch</option>' + drafts.map(d => `<option value="${d.id}">${esc(d.name)} (#${d.id})</option>`).join('');
    if (drafts.some(d => String(d.id) === previous)) select.value = previous;
    render();
  }
  async function recheck(ids, automatic = false) {
    if (busy) return;
    busy = true; render();
    let count = 0;
    try {
      for (const id of ids) {
        if (!visible()) break;
        message(`${automatic ? 'Checking pending items' : 'Rechecking Amazon'}… ${count + 1} of ${ids.length}`);
        const data = await api(`/api/fba-prep/attention/${id}/recheck`, {});
        items = items.map(r => r.id === id ? data.item : r);
        count++; render();
        if (count < ids.length) await new Promise(resolve => setTimeout(resolve, 1100));
      }
      message(`Checked ${count} item types. ${items.filter(r => r.status === 'ready_to_retry').length} ready to retry. Counts are preserved; nothing was added to a shipment.`);
    } catch (error) { message(error.message); }
    finally { busy = false; render(); }
  }
  root.addEventListener('click', async event => {
    const button = event.target.closest('button');
    if (!button || busy) return;
    if (button.dataset.open) { window.dispatchEvent(new CustomEvent('fba-attention-open-session', {detail: Number(button.dataset.open)})); return; }
    if (button.dataset.check) { await recheck([Number(button.dataset.check)]); return; }
    if (!button.dataset.add) return;
    const id = Number(button.dataset.add), row = items.find(r => r.id === id);
    const destination = document.getElementById('attentionDestination');
    if (!confirm(`Move the saved ${row.item.quantity} units of ${row.item.title || row.item.barcode} into ${destination.selectedOptions[0].textContent}?\n\nUse these set-aside units without scanning them again. Amazon will still validate the new plan.`)) return;
    busy = true; render(); message('Rechecking Amazon and saving the retry batch…');
    try {
      const data = await api(`/api/fba-prep/attention/${id}/add`, {session_id: Number(destination.value) || null});
      await load();
      destination.value = String(data.session_id);
      message(`Saved ${row.item.quantity} units in shipment #${data.session_id}. Select “Added to shipment” to open it. Keep adding repaired items to this draft; do not rescan the saved units.`);
    } catch (error) { message(error.message); await load().catch(() => {}); }
    finally { busy = false; render(); }
  });
  document.getElementById('attentionSearch').addEventListener('input', render);
  document.getElementById('attentionSearch').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); render(); } });
  document.getElementById('attentionFilter').addEventListener('change', render);
  document.getElementById('attentionRefresh').onclick = () => load().catch(e => message(e.message));
  document.getElementById('attentionRecheckAll').onclick = () => recheck(items.filter(r => r.status !== 'assigned').map(r => r.id));
  window.fbaAttention = {open: () => { if (!busy) load().catch(e => message(e.message)); }};
  setInterval(() => {
    if (!visible() || busy || !loaded) return;
    const pending = items.filter(r => r.status !== 'assigned' && r.category === 'activation' && (!r.checked_at || Date.now() - Date.parse(r.checked_at) > 60000));
    if (pending.length) recheck(pending.slice(0, 3).map(r => r.id), true);
  }, 60000);
})();

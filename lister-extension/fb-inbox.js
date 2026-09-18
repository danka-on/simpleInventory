// Facebook Marketplace inbox reader. Runs on facebook.com/marketplace pages, but only reads while
// the Marketplace inbox is showing, so personal Messenger chats are never looked at. It never
// clicks, types or sends anything: it reads the chat list the page already shows and hands the
// raw rows to the service worker, which posts them to Sweet Shelves (the server does the
// parsing, so a Facebook layout change is fixed on the Pi without a new extension release).
(() => {
  if (window.__ssFbInboxReader) return;
  window.__ssFbInboxReader = true;

  const THREAD_RE = /\/(?:messages|marketplace)\/t\/(\d{5,})/;
  const MAX_ROWS = 60;
  const HEARTBEAT_MS = 5 * 60 * 1000;  // resend an unchanged inbox this often so the server knows the reader is alive
  let lastFingerprint = '';
  let lastSentAt = 0;
  let timer = 0;

  const onInbox = () => location.pathname.startsWith('/marketplace/inbox');
  const clean = text => String(text || '').replace(/\s+/g, ' ').trim();

  function linesOf(element) {
    return String(element.innerText || '').split('\n').map(clean).filter(Boolean).slice(0, 10).map(line => line.slice(0, 300));
  }

  // Which inbox list is open: Selling or Buying.
  function role() {
    const picked = document.querySelectorAll('[role="tab"][aria-selected="true"], [aria-current="page"], [aria-selected="true"]');
    for (const element of picked) {
      const text = clean(element.innerText).toLowerCase();
      if (text === 'selling' || text.startsWith('selling ')) return 'selling';
      if (text === 'buying' || text.startsWith('buying ')) return 'buying';
    }
    const query = new URLSearchParams(location.search);
    const hint = (query.get('tab') || query.get('folder') || location.pathname).toLowerCase();
    if (hint.includes('sell')) return 'selling';
    if (hint.includes('buy')) return 'buying';
    return '';
  }

  // The inbox prints an unread chat's snippet in bold; report the bold text pieces and let the server decide.
  function boldPieces(row) {
    const pieces = [];
    for (const element of row.querySelectorAll('span, div')) {
      if (pieces.length >= 6) break;
      if (element.childElementCount) continue;
      const text = clean(element.textContent);
      if (!text || text.length < 2) continue;
      if (Number(getComputedStyle(element).fontWeight) >= 600) pieces.push(text.slice(0, 300));
    }
    return pieces;
  }

  function collect() {
    const byId = new Map();
    for (const link of document.querySelectorAll('a[href]')) {
      const href = link.getAttribute('href') || '';
      const match = href.match(THREAD_RE);
      if (!match) continue;
      let row = link;
      // Some layouts put the whole row inside the link, others only the avatar.
      if (linesOf(link).length < 2) {
        const up = link.closest('[role="row"], [role="listitem"], [role="gridcell"]');
        if (up) row = up;
      }
      const lines = linesOf(row);
      const previous = byId.get(match[1]);
      if (previous && previous.lines.join(' ').length >= lines.join(' ').length) continue;
      const image = row.querySelector('img[src^="https://"]');
      byId.set(match[1], {
        threadId: match[1],
        href: new URL(href, location.href).href,
        lines,
        label: clean(link.getAttribute('aria-label')).slice(0, 300),
        bold: boldPieces(row),
        img: image ? image.src : '',
      });
      if (byId.size >= MAX_ROWS) break;
    }
    return [...byId.values()];
  }

  // When nothing matches, say what the page did have, so the reader can be fixed from the Pi.
  function probe() {
    const rows = document.querySelectorAll('[role="row"], [role="listitem"]');
    return {
      threadLinks: document.querySelectorAll('a[href*="/t/"]').length,
      rowElements: rows.length,
      sample: [...rows].slice(0, 3).map(linesOf),
      title: document.title.slice(0, 120),
    };
  }

  function report() {
    timer = 0;
    if (!onInbox() || document.visibilityState === 'prerender') return;
    const rows = collect();
    const fingerprint = JSON.stringify(rows.map(row => [row.threadId, row.lines, row.bold]));
    const now = Date.now();
    if (fingerprint === lastFingerprint && now - lastSentAt < HEARTBEAT_MS) return;
    lastFingerprint = fingerprint;
    lastSentAt = now;
    const payload = { rows, role: role(), url: location.href.slice(0, 300), capturedAt: new Date().toISOString() };
    if (!rows.length) payload.probe = probe();
    try {
      chrome.runtime.sendMessage({ type: 'fb-inbox-capture', payload }).catch(() => {});
    } catch {
      // The extension was reloaded or updated; this copy of the script is orphaned until the tab reloads.
    }
  }

  function schedule(delay = 2500) {
    if (!timer) timer = setTimeout(report, delay);
  }

  new MutationObserver(() => { if (onInbox()) schedule(); }).observe(document.documentElement, { childList: true, subtree: true, characterData: true });
  setInterval(() => schedule(0), 60 * 1000);
  schedule(4000);
})();

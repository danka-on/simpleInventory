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

  // Facebook keeps its Messenger dropdown, chat pop-ups and the contacts column on every page,
  // including the Marketplace inbox. Those list ALL chats, so only the page's own content is read.
  const OUTSIDE = '[role="dialog"], [role="complementary"], [role="banner"], [role="navigation"], ' +
    '[aria-label="Chats"], [aria-label="Messenger"], [aria-label="Contacts"], [data-pagelet*="Chat"], [data-pagelet*="Messenger"]';
  const CANDIDATES = 'a[href], [role="button"], [role="link"], [role="row"], [role="listitem"], [role="gridcell"]';
  const AGE_RE = /(^|\s·\s)(just now|now|yesterday|\d{1,2}\s?(m|min|h|hr|d|w|wk|mo|y|yr)|(mon|tue|wed|thu|fri|sat|sun)[a-z]*|(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]* \d{1,2})$/i;

  function inboxArea() {
    return document.querySelector('[role="main"]');
  }

  // A chat row: a clickable block with a picture and 2-8 lines, one of which ends in an age ("2h", "Mon").
  function looksLikeChat(element, lines) {
    return lines.length >= 2 && lines.length <= 8 && element.querySelector('img, svg image') &&
      lines.some(line => AGE_RE.test(line) || line.includes(' · '));
  }

  function collect() {
    const area = inboxArea();
    if (!area) return [];
    const found = [];
    for (const element of area.querySelectorAll(CANDIDATES)) {
      if (element.closest(OUTSIDE)) continue;
      const lines = linesOf(element);
      if (!looksLikeChat(element, lines)) continue;
      // Keep the innermost block that still is a whole row (drops the list around the rows).
      if ([...element.querySelectorAll(CANDIDATES)].some(inner => looksLikeChat(inner, linesOf(inner)))) continue;
      const link = element.matches('a[href]') ? element : element.querySelector('a[href]') || element.closest('a[href]');
      const href = link ? link.getAttribute('href') || '' : '';
      const match = href.match(THREAD_RE);
      const image = element.querySelector('img[src^="https://"]');
      found.push({
        threadId: match ? match[1] : '',
        href: href ? new URL(href, location.href).href : '',
        lines,
        label: clean(element.getAttribute('aria-label')).slice(0, 300),
        bold: boldPieces(element),
        img: image ? image.src : '',
        area: 'marketplace-inbox',
      });
      if (found.length >= MAX_ROWS) break;
    }
    return found;
  }

  // When nothing matches, describe the page's content area (not the Messenger parts), so the
  // reader can be fixed from the Pi.
  function probe() {
    const area = inboxArea();
    const blocks = area ? [...area.querySelectorAll(CANDIDATES)].filter(el => !el.closest(OUTSIDE)) : [];
    return {
      hasMain: Boolean(area),
      blocks: blocks.length,
      sample: blocks.map(el => ({ tag: el.tagName.toLowerCase(), role: el.getAttribute('role') || '',
        href: (el.getAttribute('href') || '').replace(/\d{5,}/g, '#').slice(0, 80), lines: linesOf(el).slice(0, 4),
        img: Boolean(el.querySelector('img')) })).filter(one => one.lines.length >= 2).slice(0, 8),
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

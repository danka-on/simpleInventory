// Runs only on Items to List: when "+" changes the Listing Agent queue there, the side panel reloads
// its list right away. Only a "the queue changed" nudge crosses this bridge, never item data.
// "+" with the panel closed asks to open the panel instead ('open-panel'); the answer goes back
// to the page as 'panel-opened' so it knows not to add the item everywhere.
// After the Lister updates or reloads, this copy stays in an already-open tab but can no longer
// reach the extension: sendMessage throws at once ("Extension context invalidated"), before any
// .catch. The orphan then unhooks itself; the page's open-panel wait simply times out.
function bridgeSend(message) {
  try {
    if (!chrome.runtime?.id) throw new Error('Extension context invalidated.');
    return chrome.runtime.sendMessage(message);
  } catch (err) {
    window.removeEventListener('message', onPageMessage);
    return Promise.reject(err);
  }
}

function onPageMessage(event) {
  if (event.source !== window || event.origin !== location.origin ||
      event.data?.source !== 'sweetshelves-items-to-list') return;
  if (event.data.type === 'queue-changed') {
    // Nobody listening (the panel is closed) is fine: it loads the queue when it opens.
    bridgeSend({ type: 'ss-lister-queue-changed' }).catch(() => {});
  } else if (event.data.type === 'open-panel') {
    // Sent straight from the click, so Chrome still counts it as a user gesture.
    bridgeSend({ type: 'ss-lister-open-panel' })
      .then(reply => {
        if (reply?.ok) window.postMessage({ source: 'sweetshelves-lister', type: 'panel-opened', id: event.data.id }, location.origin);
      })
      .catch(() => {});
  }
}

window.addEventListener('message', onPageMessage);

// The side panel says which store list it shows (on open, close and every store switch): the page's
// Listing Agent column redraws at once instead of waiting for its next poll of the server.
try {
  chrome.runtime.onMessage.addListener(message => {
    if (message?.type !== 'ss-lister-panel-state') return;
    window.postMessage({ source: 'sweetshelves-lister', type: 'panel-state',
      active: !!message.open && message.follow !== false, platform: message.platform || '' }, location.origin);
  });
} catch (err) { /* orphaned copy after an update */ }

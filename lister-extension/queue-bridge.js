// Runs only on Items to List: when "+" changes the Listing Agent queue there, the side panel reloads
// its list right away. Only a "the queue changed" nudge crosses this bridge, never item data.
// "+" with the panel closed asks to open the panel instead ('open-panel'); the answer goes back
// to the page as 'panel-opened' so it knows not to add the item everywhere.
window.addEventListener('message', event => {
  if (event.source !== window || event.origin !== location.origin ||
      event.data?.source !== 'sweetshelves-items-to-list') return;
  if (event.data.type === 'queue-changed') {
    // Nobody listening (the panel is closed) is fine: it loads the queue when it opens.
    chrome.runtime.sendMessage({ type: 'ss-lister-queue-changed' }).catch(() => {});
  } else if (event.data.type === 'open-panel') {
    // Sent straight from the click, so Chrome still counts it as a user gesture.
    chrome.runtime.sendMessage({ type: 'ss-lister-open-panel' })
      .then(reply => {
        if (reply?.ok) window.postMessage({ source: 'sweetshelves-lister', type: 'panel-opened', id: event.data.id }, location.origin);
      })
      .catch(() => {});
  }
});

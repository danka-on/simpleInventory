// Runs only on Items to List: when "+" changes the Listing Agent queue there, the side panel reloads
// its list right away. Only a "the queue changed" nudge crosses this bridge, never item data.
window.addEventListener('message', event => {
  if (event.source !== window || event.origin !== location.origin ||
      event.data?.source !== 'sweetshelves-items-to-list' || event.data.type !== 'queue-changed') return;
  // Nobody listening (the panel is closed) is fine: it loads the queue when it opens.
  chrome.runtime.sendMessage({ type: 'ss-lister-queue-changed' }).catch(() => {});
});

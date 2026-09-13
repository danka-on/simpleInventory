// Runs only on the private Sweet Shelves update page so it can ask the extension to reload
// after a folder update. No listing data or credentials cross this bridge.
window.addEventListener('message', async event => {
  if (event.source !== window || event.origin !== location.origin ||
      event.data?.source !== 'sweetshelves-lister-updater' ||
      !['status', 'reload'].includes(event.data.type) || typeof event.data.requestId !== 'string') return;
  const { type, requestId } = event.data;
  try {
    const result = await chrome.runtime.sendMessage({ type: 'website-update-' + type });
    window.postMessage({ source: 'sweetshelves-lister-extension', requestId, result }, location.origin);
  } catch {
    window.postMessage({ source: 'sweetshelves-lister-extension', requestId, result: null }, location.origin);
  }
});

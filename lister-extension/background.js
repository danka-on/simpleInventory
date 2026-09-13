// Sweet Shelves Lister service worker: opens the side panel from the toolbar button and
// answers the private update page's status/reload bridge. Everything else runs in the panel.

const UPDATE_ORIGINS = ['https://pi.nexuscentralhq.org', 'https://debby.taila97a84.ts.net'];

function isUpdatePage(value) {
  try {
    const url = new URL(value ?? '');
    return UPDATE_ORIGINS.includes(url.origin) && url.pathname.startsWith('/lister/');
  } catch {
    return false;
  }
}

chrome.runtime.onInstalled.addListener(() => {
  void chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
});
void chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const type = message?.type;
  if (type === 'website-update-status' || type === 'website-update-reload') {
    if (!isUpdatePage(sender.url) || sender.frameId !== 0) {
      sendResponse({ ok: false });
      return false;
    }
    sendResponse({ ok: true, busy: false, version: chrome.runtime.getManifest().version });
    if (type === 'website-update-reload') setTimeout(() => chrome.runtime.reload(), 350);
    return false;
  }
  return false;
});

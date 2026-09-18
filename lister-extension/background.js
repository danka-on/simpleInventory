// Sweet Shelves Lister service worker: opens the side panel from the toolbar button, answers the
// private update page's status/reload bridge, and runs the Facebook Marketplace inbox reader
// (keeps a pinned inbox tab open and relays what fb-inbox.js reads to Sweet Shelves).
// Everything else runs in the panel.

const UPDATE_ORIGINS = ['https://pi.nexuscentralhq.org'];
const DEFAULT_SERVER = 'https://pi.nexuscentralhq.org';

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

// -- Facebook Marketplace inbox reader ---------------------------------------------------------
// Only the Marketplace inbox is read (never personal Messenger chats), and nothing is ever sent
// on Facebook. The pinned tab is reloaded every 10 minutes while nobody is looking at it. Closing
// it pauses the reader until Chrome restarts; landing on Facebook's login page turns the reader
// off in this Chrome until the Marketplace inbox is opened here again.

const FB_KEY = 'ssFbReader';
const FB_INBOX_URL = 'https://www.facebook.com/marketplace/inbox/';
const FB_ALARM = 'ss-fb-reader';
const FB_RELOAD_MS = 10 * 60 * 1000;

function isFbMarketplace(value) {
  try {
    const url = new URL(value ?? '');
    return url.origin === 'https://www.facebook.com' && url.pathname.startsWith('/marketplace/');
  } catch {
    return false;
  }
}

async function fbState() {
  return (await chrome.storage.local.get(FB_KEY))[FB_KEY] || { enabled: true };
}

async function fbSave(patch) {
  const next = { ...(await fbState()), ...patch };
  await chrome.storage.local.set({ [FB_KEY]: next });
  return next;
}

async function fbKeep() {
  const state = await fbState();
  if (!state.enabled || state.pausedByUser) return;
  const tabs = await chrome.tabs.query({ url: 'https://www.facebook.com/marketplace/inbox*' });
  if (tabs.length) {
    const tab = tabs.find(one => one.id === state.tabId) || tabs.find(one => one.pinned) || tabs[0];
    if (tab.id !== state.tabId) await fbSave({ tabId: tab.id });
    // Only the pinned reader tab is refreshed, and never while it is the tab on screen.
    if (tab.pinned && !tab.active && Date.now() - (state.lastReloadAt || 0) > FB_RELOAD_MS) {
      await chrome.tabs.reload(tab.id);
      await fbSave({ lastReloadAt: Date.now() });
    }
    return;
  }
  const windows = await chrome.windows.getAll({ windowTypes: ['normal'] });
  if (!windows.length) return;
  const tab = await chrome.tabs.create({ url: FB_INBOX_URL, pinned: true, active: false, index: 0 });
  await fbSave({ tabId: tab.id, lastReloadAt: Date.now() });
}

function fbKeepSoon() {
  fbKeep().catch(() => {});
}

async function fbPost(payload) {
  const settings = (await chrome.storage.local.get('ssListerSettings')).ssListerSettings || {};
  const base = String(settings.server || DEFAULT_SERVER).replace(/\/+$/, '');
  const response = await fetch(base + '/api/fb-marketplace/capture', {
    method: 'POST', credentials: 'include', cache: 'no-store', redirect: 'manual',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-Sweet-Shelves-Lister': '1' },
    body: JSON.stringify({ ...payload, version: chrome.runtime.getManifest().version }),
  });
  // Reading the inbox (even once, by hand) is what turns the reader on in this Chrome.
  await fbSave({ enabled: true, lastCaptureAt: Date.now(), lastStatus: response.status });
  return { ok: response.ok, status: response.status };
}

chrome.alarms.create(FB_ALARM, { periodInMinutes: 5 });
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === FB_ALARM) fbKeepSoon(); });
chrome.runtime.onStartup.addListener(() => { fbSave({ pausedByUser: false }).then(fbKeepSoon, fbKeepSoon); });
chrome.runtime.onInstalled.addListener(() => fbKeepSoon());

chrome.tabs.onRemoved.addListener(async (tabId, info) => {
  const state = await fbState();
  if (tabId !== state.tabId) return;
  // Closing the reader tab by hand pauses it until the next Chrome start; closing the window does not.
  await fbSave({ tabId: null, pausedByUser: !info.isWindowClosing });
});

chrome.tabs.onUpdated.addListener(async (tabId, change) => {
  if (!change.url) return;
  const state = await fbState();
  if (tabId !== state.tabId) return;
  if (/^https:\/\/www\.facebook\.com\/(login|checkpoint)/.test(change.url)) {
    // Not signed in to Facebook in this Chrome: stop opening the tab here.
    await fbSave({ enabled: false, tabId: null, disabledReason: 'signed-out' });
    chrome.tabs.remove(tabId).catch(() => {});
  }
});

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
  if (type === 'fb-inbox-capture') {
    if (!isFbMarketplace(sender.url) || sender.frameId !== 0 || !message.payload) {
      sendResponse({ ok: false });
      return false;
    }
    fbPost(message.payload).then(sendResponse, error => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  }
  return false;
});

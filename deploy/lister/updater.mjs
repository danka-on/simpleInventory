// Browser-side updater for the Sweet Shelves Lister update page. Writes a verified release into
// the user's unpacked-extension folder with the File System Access API, then asks the extension
// to reload through the update bridge.
const APP_NAME = 'Sweet Shelves Lister';
const REQUIRED = ['manifest.json', 'background.js', 'sidepanel.html'];

export async function sha256(bytes) {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), b => b.toString(16).padStart(2, '0')).join('');
}
export function validateRelease(r) {
  if (r?.schemaVersion !== 1 || !/^[a-f0-9]{20}$/.test(r.release) ||
      r.archive !== `releases/${r.release}.zip` || r.payload !== `releases/${r.release}.json` ||
      !/^[a-f0-9]{64}$/.test(r.sha256) || !/^[a-f0-9]{64}$/.test(r.payloadSha256) ||
      !r.files || typeof r.files !== 'object') throw new Error('Invalid release. Refresh this page.');
  const names = Object.keys(r.files), seen = new Set();
  if (names.length > 2000) throw new Error('Release has too many files.');
  for (const name of names) {
    if (!/^[a-zA-Z0-9_-]+(?:[./-][a-zA-Z0-9_-]+)*$/.test(name) ||
        /(^|\/)(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(\.|\/|$)/i.test(name) ||
        !/^[a-f0-9]{64}$/.test(r.files[name]) || seen.has(name.toLowerCase())) throw new Error('Unsafe release file name or checksum.');
    seen.add(name.toLowerCase());
  }
  for (const name of names) {
    const parts = name.toLowerCase().split('/'); parts.pop();
    while (parts.length) {
      if (seen.has(parts.join('/'))) throw new Error('Conflicting release paths.');
      parts.pop();
    }
  }
  for (const name of REQUIRED) if (!names.includes(name)) throw new Error(`Missing ${name}.`);
  return r;
}
export async function verifyPayload(release, bytes) {
  validateRelease(release);
  if (bytes.byteLength > 64 * 1024 * 1024 || await sha256(bytes) !== release.payloadSha256) throw new Error('Download verification failed. Your folder has not changed.');
  const encoded = JSON.parse(new TextDecoder().decode(bytes));
  if (Object.keys(encoded).length !== Object.keys(release.files).length) throw new Error('Release file count mismatch.');
  const files = new Map();
  for (const [name, hash] of Object.entries(release.files)) {
    if (typeof encoded[name] !== 'string') throw new Error(`Missing ${name}.`);
    const data = Uint8Array.from(atob(encoded[name]), char => char.charCodeAt(0));
    if (await sha256(data) !== hash) throw new Error(`File verification failed: ${name}`);
    files.set(name, data);
  }
  const manifest = JSON.parse(new TextDecoder().decode(files.get('manifest.json')));
  if (manifest.name !== APP_NAME || manifest.manifest_version !== 3) throw new Error(`This is not a ${APP_NAME} extension.`);
  return files;
}
async function snapshot(dir, prefix = '', files = new Map(), budget = { bytes: 0 }) {
  for await (const [name, handle] of dir.entries()) {
    const path = prefix + name;
    if (handle.kind === 'directory') await snapshot(handle, path + '/', files, budget);
    else {
      const file = await handle.getFile(); budget.bytes += file.size;
      if (budget.bytes > 64 * 1024 * 1024 || files.size >= 2000) throw new Error('Choose only the extension folder.');
      files.set(path, new Uint8Array(await file.arrayBuffer()));
    }
  }
  return files;
}
async function parent(root, path, create) {
  const parts = path.split('/'), name = parts.pop(); let dir = root;
  for (const part of parts) dir = await dir.getDirectoryHandle(part, { create });
  return [dir, name];
}
async function write(root, path, bytes) {
  const [dir, name] = await parent(root, path, true);
  const stream = await (await dir.getFileHandle(name, { create: true })).createWritable();
  try { await stream.write(bytes); await stream.close(); }
  catch (error) { await stream.abort().catch(() => {}); throw error; }
}
async function remove(root, path) {
  try { const [dir, name] = await parent(root, path, false); await dir.removeEntry(name); }
  catch (error) { if (error.name !== 'NotFoundError') throw error; }
}
export async function installFiles(root, files, progress = () => {}) {
  let hasEntries = false;
  for await (const _entry of root.entries()) { hasEntries = true; break; }
  if (hasEntries) {
    try {
      const manifest = JSON.parse(await (await (await root.getFileHandle('manifest.json')).getFile()).text());
      if (manifest.name !== APP_NAME || manifest.manifest_version !== 3) throw new Error();
    } catch { throw new Error(`Choose your existing ${APP_NAME} folder, or a new empty folder.`); }
  }
  const before = await snapshot(root);
  let current = before.size === files.size;
  for (const [name, bytes] of files) if (!before.has(name) || await sha256(before.get(name)) !== await sha256(bytes)) current = false;
  if (current) return { current: true, firstInstall: false };
  const names = [...files.keys()].filter(name => name !== 'manifest.json').concat('manifest.json');
  try {
    for (const [index, name] of names.entries()) {
      progress(`Updating file ${index + 1} of ${names.length}…`);
      await write(root, name, files.get(name));
    }
    for (const name of before.keys()) if (!files.has(name)) await remove(root, name);
    const after = await snapshot(root);
    if (after.size !== files.size) throw new Error('Folder changed during update.');
    for (const [name, bytes] of files) if (!after.has(name) || await sha256(after.get(name)) !== await sha256(bytes)) throw new Error(`Saved file verification failed: ${name}`);
  } catch (error) {
    try {
      for (const [name, bytes] of before) await write(root, name, bytes);
      for (const name of files.keys()) if (!before.has(name)) await remove(root, name);
    } catch { throw new Error('Update interrupted; restore could not finish. Keep the extension closed and run the update again.'); }
    throw new Error(`Update failed; previous files restored. ${error.message}`);
  }
  return { current: false, firstInstall: !hasEntries };
}

function savedFolder(value) {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open('sweetshelves-lister-updates', 1);
    open.onupgradeneeded = () => open.result.createObjectStore('settings');
    open.onerror = () => reject(open.error);
    open.onsuccess = () => {
      const db = open.result, tx = db.transaction('settings', value ? 'readwrite' : 'readonly');
      const request = value ? tx.objectStore('settings').put(value, 'folder') : tx.objectStore('settings').get('folder');
      tx.oncomplete = () => { resolve(request.result); db.close(); };
      tx.onerror = () => { reject(tx.error); db.close(); };
    };
  });
}
function extensionMessage(type) {
  return new Promise(resolve => {
    const requestId = crypto.randomUUID();
    const send = () => window.postMessage({ source: 'sweetshelves-lister-updater', type, requestId }, location.origin);
    const retry = type === 'status' ? setInterval(send, 250) : null;
    const timer = setTimeout(() => { clearInterval(retry); window.removeEventListener('message', receive); resolve(null); }, 2000);
    function receive(event) {
      if (event.source !== window || event.origin !== location.origin || event.data?.source !== 'sweetshelves-lister-extension' || event.data.requestId !== requestId) return;
      clearTimeout(timer); clearInterval(retry); window.removeEventListener('message', receive); resolve(event.data.result);
    }
    window.addEventListener('message', receive);
    send();
  });
}
export function startPage() {
  const status = document.getElementById('status'), label = document.getElementById('release');
  const button = document.getElementById('install'), change = document.getElementById('folder');
  let latest, folder, busy = false;
  function showFolder() { document.getElementById('folder-name').textContent = folder ? `Updates go to: ${folder.name}` : 'Choose your extension folder once. We’ll remember it on this computer.'; }
  savedFolder().then(value => { folder = value; showFolder(); }).catch(() => {});
  async function check() {
    const response = await fetch(`latest.json?check=${Date.now()}`, { cache: 'no-store', credentials: 'same-origin' });
    if (!response.ok || response.redirected) throw new Error('Refresh this page and sign in again.');
    latest = validateRelease(await response.json());
    label.textContent = `Version ${latest.version} · Build ${latest.release}`;
    const link = document.getElementById('download'); link.href = latest.archive; link.hidden = false;
    button.disabled = !('showDirectoryPicker' in window);
    return latest;
  }
  async function choose() {
    folder = await window.showDirectoryPicker({ id: 'sweetshelves-lister-extension', mode: 'readwrite' });
    showFolder();
  }
  change.addEventListener('click', async () => { try { await choose(); } catch (error) { if (error.name !== 'AbortError') status.textContent = error.message; } });
  button.addEventListener('click', async () => {
    if (busy) return;
    busy = true; button.disabled = true; change.disabled = true;
    try {
      if (!folder) await choose();
      else if (await folder.requestPermission({ mode: 'readwrite' }) !== 'granted') throw new Error('Allow access to your extension folder to update it.');
      const update = async () => {
        status.textContent = 'Checking and downloading the latest extension…';
        const release = await check(); button.disabled = true;
        const response = await fetch(release.payload, { cache: 'no-store', credentials: 'same-origin' });
        if (!response.ok || response.redirected) throw new Error('Sign-in expired. Refresh this page.');
        const files = await verifyPayload(release, await response.arrayBuffer());
        const result = await installFiles(folder, files, text => { status.textContent = text; });
        await savedFolder(folder).catch(() => {});
        if (result.firstInstall) { status.textContent = 'Installed! Open chrome://extensions (or edge://extensions), enable Developer mode, choose Load unpacked, and select this folder.'; return; }
        const reloaded = await extensionMessage('reload');
        status.textContent = reloaded?.ok ? `Updated. Reloading ${APP_NAME}…` :
          `Files are current. For this first update, click Reload on ${APP_NAME} at chrome://extensions (or edge://extensions). Future updates can reload from this button.`;
        if (reloaded?.ok) {
          sessionStorage.setItem('sweetshelves-lister-just-updated', '1');
          setTimeout(() => location.reload(), 1200);
        }
      };
      if (navigator.locks) await navigator.locks.request('sweetshelves-lister-update', { ifAvailable: true }, lock => {
        if (!lock) throw new Error(`Another tab is updating ${APP_NAME}. Wait for it to finish.`);
        return update();
      }); else await update();
    } catch (error) { status.textContent = error.name === 'AbortError' ? 'Cancelled. No update applied.' : error.message; }
    finally { busy = false; button.disabled = !latest || !('showDirectoryPicker' in window); change.disabled = false; }
  });
  window.addEventListener('beforeunload', event => { if (busy) { event.preventDefault(); event.returnValue = ''; } });
  if (!('showDirectoryPicker' in window)) { change.disabled = true; status.textContent = 'Open this page in Chrome or Edge on Windows or Mac to update a folder. ZIP downloads also work below.'; }
  check().catch(() => { label.textContent = 'Unable to check for updates. Refresh this page and sign in again.'; });
  if (sessionStorage.getItem('sweetshelves-lister-just-updated')) {
    sessionStorage.removeItem('sweetshelves-lister-just-updated');
    window.addEventListener('load', async () => {
      const extension = await extensionMessage('status');
      status.textContent = extension?.ok ? `Updated and reloaded (extension ${extension.version || ''}). Refresh your eBay and Seller Central tabs.` : `Files updated. If ${APP_NAME} is not active, click Reload on your browser’s extensions page.`;
    }, { once: true });
  }
}

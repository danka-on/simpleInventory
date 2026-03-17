(function (global) {
    'use strict';

    const BARCODE_KEY = 'barcode';
    const ENTRIES_KEY = 'barcode_entries';

    function splitActualCode(rawValue) {
        const raw = String(rawValue || '').trim();
        if (!raw) return { code: '', suffix: 0 };
        const match = raw.match(/^(.*?)-(\d+)$/);
        if (!match) return { code: raw, suffix: 0 };
        const code = String(match[1] || '').trim();
        const suffix = parseInt(match[2], 10);
        if (!code || !Number.isFinite(suffix) || suffix < 1) {
            return { code: raw, suffix: 0 };
        }
        return { code, suffix };
    }

    function normalizeEntry(rawEntry) {
        if (rawEntry == null) return null;

        const source = (typeof rawEntry === 'object') ? rawEntry : { code: rawEntry };
        const split = splitActualCode(source.code);
        const code = split.code;
        if (!code) return null;

        let suffix = source.suffix;
        if (suffix == null) suffix = split.suffix;
        suffix = parseInt(suffix, 10);
        if (!Number.isFinite(suffix) || suffix < 0) suffix = 0;

        let quantity = parseInt(source.quantity, 10);
        if (!Number.isFinite(quantity) || quantity < 1) quantity = 1;
        if (suffix > 0) quantity = 1;

        return {
            code,
            suffix,
            quantity
        };
    }

    function nextAvailableSuffix(entries, code, minSuffix, excludeIndex) {
        const normalized = normalizeEntries(entries);
        const targetCode = String(code || '').trim();
        let suffix = Math.max(1, parseInt(minSuffix, 10) || 1);
        const used = new Set();
        normalized.forEach((entry, index) => {
            if (index === excludeIndex) return;
            if (entry.code !== targetCode || entry.suffix <= 0) return;
            used.add(entry.suffix);
        });
        while (used.has(suffix)) suffix += 1;
        return suffix;
    }

    function previousAvailableSuffix(entries, code, currentSuffix, excludeIndex) {
        const normalized = normalizeEntries(entries);
        const targetCode = String(code || '').trim();
        const used = new Set();
        normalized.forEach((entry, index) => {
            if (index === excludeIndex) return;
            if (entry.code !== targetCode || entry.suffix <= 0) return;
            used.add(entry.suffix);
        });
        let suffix = Math.max(0, parseInt(currentSuffix, 10) || 0) - 1;
        while (suffix > 0 && used.has(suffix)) suffix -= 1;
        return Math.max(0, suffix);
    }

    function normalizeEntries(rawEntries) {
        const list = Array.isArray(rawEntries) ? rawEntries : [];
        const out = [];

        list.forEach(rawEntry => {
            const entry = normalizeEntry(rawEntry);
            if (!entry) return;

            if (entry.suffix <= 0) {
                const existingBase = out.find(item => item.code === entry.code && item.suffix === 0);
                if (existingBase) {
                    existingBase.quantity += entry.quantity;
                } else {
                    out.push(entry);
                }
                return;
            }

            const uniqueSuffix = nextAvailableSuffix(out, entry.code, entry.suffix, -1);
            out.push({
                code: entry.code,
                suffix: uniqueSuffix,
                quantity: 1
            });
        });

        return out;
    }

    function actualCode(entry) {
        const normalized = normalizeEntry(entry);
        if (!normalized) return '';
        return normalized.suffix > 0
            ? `${normalized.code}-${normalized.suffix}`
            : normalized.code;
    }

    function displayCode(entry, formatter) {
        const normalized = normalizeEntry(entry);
        if (!normalized) return '';
        const format = (typeof formatter === 'function') ? formatter : (value => value);
        const base = format(normalized.code);
        return normalized.suffix > 0 ? `${base}-${normalized.suffix}` : base;
    }

    function serializeEntries(entries) {
        const normalized = normalizeEntries(entries);
        const expanded = [];
        normalized.forEach(entry => {
            const code = actualCode(entry);
            const quantity = entry.suffix > 0 ? 1 : Math.max(1, parseInt(entry.quantity, 10) || 1);
            for (let i = 0; i < quantity; i += 1) {
                expanded.push(code);
            }
        });
        return expanded.join(',');
    }

    function parseEntriesFromString(rawValue) {
        const raw = String(rawValue || '').trim();
        if (!raw) return [];
        const entries = [];
        raw.split(',').forEach(token => {
            const split = splitActualCode(token);
            if (!split.code) return;
            if (split.suffix > 0) {
                entries.push({ code: split.code, suffix: split.suffix, quantity: 1 });
                return;
            }
            const existingBase = entries.find(entry => entry.code === split.code && entry.suffix === 0);
            if (existingBase) {
                existingBase.quantity += 1;
            } else {
                entries.push({ code: split.code, suffix: 0, quantity: 1 });
            }
        });
        return normalizeEntries(entries);
    }

    function loadEntries(storage) {
        const store = storage || global.sessionStorage;
        if (!store) return [];

        const jsonRaw = store.getItem(ENTRIES_KEY);
        if (jsonRaw) {
            try {
                const parsed = JSON.parse(jsonRaw);
                return normalizeEntries(parsed);
            } catch (err) {
                // Fall back to the flat barcode string.
            }
        }

        return parseEntriesFromString(store.getItem(BARCODE_KEY));
    }

    function saveEntries(entries, storage) {
        const store = storage || global.sessionStorage;
        const normalized = normalizeEntries(entries);
        if (!store) return normalized;

        if (!normalized.length) {
            store.removeItem(BARCODE_KEY);
            store.removeItem(ENTRIES_KEY);
            return normalized;
        }

        store.setItem(ENTRIES_KEY, JSON.stringify(normalized));
        store.setItem(BARCODE_KEY, serializeEntries(normalized));
        return normalized;
    }

    function setSingleScanSuffix(code, suffix) {
        const normalizedCode = String(code || '').trim();
        if (!normalizedCode) return [];
        return normalizeEntries([{ code: normalizedCode, suffix: Math.max(0, parseInt(suffix, 10) || 0), quantity: 1 }]);
    }

    function addBaseScan(entries, code) {
        const normalized = normalizeEntries(entries);
        const rawCode = String(code || '').trim();
        if (!rawCode) return normalized;
        const existingBase = normalized.find(entry => entry.code === rawCode && entry.suffix === 0);
        if (existingBase) {
            existingBase.quantity += 1;
        } else {
            normalized.push({ code: rawCode, suffix: 0, quantity: 1 });
        }
        return normalizeEntries(normalized);
    }

    function incrementSuffix(entries, index) {
        const normalized = normalizeEntries(entries);
        if (index < 0 || index >= normalized.length) return normalized;

        const item = normalized[index];
        if (item.suffix > 0) {
            item.suffix = nextAvailableSuffix(normalized, item.code, item.suffix + 1, index);
            return normalizeEntries(normalized);
        }

        if (item.quantity > 1) {
            item.quantity -= 1;
            normalized.push({
                code: item.code,
                suffix: nextAvailableSuffix(normalized, item.code, 1, -1),
                quantity: 1
            });
            return normalizeEntries(normalized);
        }

        item.suffix = nextAvailableSuffix(normalized, item.code, 1, index);
        item.quantity = 1;
        return normalizeEntries(normalized);
    }

    function decrementSuffix(entries, index) {
        const normalized = normalizeEntries(entries);
        if (index < 0 || index >= normalized.length) return normalized;

        const item = normalized[index];
        if (item.suffix <= 0) return normalized;

        const prevSuffix = previousAvailableSuffix(normalized, item.code, item.suffix, index);
        if (prevSuffix > 0) {
            item.suffix = prevSuffix;
            return normalizeEntries(normalized);
        }

        const baseEntry = normalized.find((entry, entryIndex) => entryIndex !== index && entry.code === item.code && entry.suffix === 0);
        if (baseEntry) {
            baseEntry.quantity += 1;
            normalized.splice(index, 1);
            return normalizeEntries(normalized);
        }

        item.suffix = 0;
        item.quantity = 1;
        return normalizeEntries(normalized);
    }

    function removeOne(entries, index) {
        const normalized = normalizeEntries(entries);
        if (index < 0 || index >= normalized.length) return normalized;

        const item = normalized[index];
        if (item.suffix === 0 && item.quantity > 1) {
            item.quantity -= 1;
        } else {
            normalized.splice(index, 1);
        }
        return normalizeEntries(normalized);
    }

    function totalCount(entries) {
        const normalized = normalizeEntries(entries);
        return normalized.reduce((sum, entry) => sum + (entry.suffix > 0 ? 1 : entry.quantity), 0);
    }

    global.BarcodeEntryState = {
        BARCODE_KEY,
        ENTRIES_KEY,
        splitActualCode,
        normalizeEntry,
        normalizeEntries,
        actualCode,
        displayCode,
        serializeEntries,
        parseEntriesFromString,
        loadEntries,
        saveEntries,
        setSingleScanSuffix,
        addBaseScan,
        incrementSuffix,
        decrementSuffix,
        removeOne,
        nextAvailableSuffix,
        totalCount
    };
})(window);

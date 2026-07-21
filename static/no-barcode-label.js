(function () {
    function byId(id) {
        return document.getElementById(id);
    }

    function esc(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, function (ch) {
            return {
                '&': '&amp;',
                '<': '&lt;',
                '>': '&gt;',
                '"': '&quot;',
                "'": '&#39;'
            }[ch];
        });
    }

    function mmValue(value, fallback) {
        var parsed = parseFloat(value);
        return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
    }

    function getBarcode(modal) {
        return String(modal.barcodeInput && modal.barcodeInput.value || '').trim();
    }

    function focusBarcodeInput(modal, selectAll) {
        if (!modal || !modal.barcodeInput) return;

        var attemptFocus = function () {
            try {
                modal.barcodeInput.focus({ preventScroll: true });
            } catch (_) {
                try {
                    modal.barcodeInput.focus();
                } catch (__) {}
            }
            if (typeof modal.barcodeInput.click === 'function') {
                try { modal.barcodeInput.click(); } catch (_) {}
            }
            if (selectAll && typeof modal.barcodeInput.select === 'function') {
                try { modal.barcodeInput.select(); } catch (_) {}
            }
            if (!selectAll && typeof modal.barcodeInput.setSelectionRange === 'function') {
                var end = String(modal.barcodeInput.value || '').length;
                try { modal.barcodeInput.setSelectionRange(end, end); } catch (_) {}
            }
        };

        attemptFocus();
        window.requestAnimationFrame(attemptFocus);
        window.setTimeout(attemptFocus, 50);
        window.setTimeout(attemptFocus, 180);
    }

    function setStatus(modal, message, tone) {
        if (!modal.status) return;
        modal.status.textContent = message || '';
        modal.status.className = 'no-barcode-status';
        if (tone) modal.status.classList.add(tone);
    }

    function pageBarcodeIsValid(barcode) {
        if (typeof window.isValidBarcode === 'function') {
            return window.isValidBarcode(barcode);
        }
        return /^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$/.test(barcode);
    }

    function validateBarcode(modal) {
        var barcode = getBarcode(modal);
        if (!barcode) {
            setStatus(modal, 'Enter or generate a barcode first.', 'bad');
            focusBarcodeInput(modal, false);
            return '';
        }
        if (!pageBarcodeIsValid(barcode)) {
            setStatus(modal, 'That barcode format is not valid for this flow.', 'bad');
            focusBarcodeInput(modal, true);
            return '';
        }
        return barcode;
    }

    function buttonBusy(button, busy, text) {
        if (!button) return;
        if (busy) {
            button.dataset.originalText = button.textContent;
            button.disabled = true;
            button.textContent = text;
        } else {
            button.disabled = false;
            if (button.dataset.originalText) {
                button.textContent = button.dataset.originalText;
                delete button.dataset.originalText;
            }
        }
    }

    function printBarcodeBrowser(barcodeData, config) {
        var widthMm = mmValue(config && config.label_width, 62);
        var heightMm = mmValue(config && config.label_height, 29);
        var showName = !(config && (config.label_show_name === false || String(config.label_show_name) === '0'));
        var showBarcode = !(config && (config.label_show_barcode === false || String(config.label_show_barcode) === '0'));
        var showBarcodeText = !(config && (config.label_show_barcode_text === false || String(config.label_show_barcode_text) === '0'));
        var pxPerMm = 96 / 25.4;
        var designTitleSize = Math.max(6, parseInt(config && config.label_title_font_size, 10) || Math.max(8, Math.min(12, Math.round(heightMm * 0.32))));
        var designBarcodeFontSize = Math.max(6, parseInt(config && config.label_barcode_font_size, 10) || Math.max(9, Math.min(12, Math.round(heightMm * 0.34))));
        var designBarcodeHeight = Math.max(20, parseInt(config && config.label_barcode_height, 10) || Math.max(26, Math.round(heightMm * 1.2)));
        var designBarcodeWidth = Math.max(0.6, parseFloat(config && config.label_barcode_scale) || (widthMm >= 62 ? 1.8 : 1.6));
        var titlePx = Math.max(12, Math.round(designTitleSize * pxPerMm * 0.9));
        var fontPx = Math.max(10, Math.round(designBarcodeFontSize * pxPerMm * 0.92));
        var barcodeHeight = Math.max(24, Math.round(designBarcodeHeight * pxPerMm * 0.95));
        var barcodeWidth = Math.max(1.2, designBarcodeWidth * 1.35);
        var titleLines = Math.max(1, parseInt(config && config.label_title_lines, 10) || 2);
        var titleMaxHeight = Math.ceil(titlePx * 1.08 * titleLines);
        var printWindow = window.open('', '', 'toolbar=0,location=0,menubar=0,scrollbars=0,status=0,titlebar=0');

        if (!printWindow) {
            throw new Error('Allow popups so the barcode label can print.');
        }

        var barcode = String(barcodeData.upc || '');
        var title = String(barcodeData.description || '');
        var html = [
            '<!DOCTYPE html>',
            '<html>',
            '<head>',
            '  <title></title>',
            '  <style>',
            '    @page { size: ' + widthMm + 'mm ' + heightMm + 'mm; margin: 0; }',
            '    html, body { width: ' + widthMm + 'mm; height: ' + heightMm + 'mm; margin: 0 !important; padding: 0 !important; background: white !important; overflow: hidden !important; }',
            '    body { font-family: Arial, sans-serif; display: block; }',
            '    .barcode-label { width: ' + widthMm + 'mm; height: ' + heightMm + 'mm; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 1.5mm 2mm; gap: 1.5mm; box-sizing: border-box; overflow: hidden; margin: 0; }',
            '    .barcode-title { font-size: ' + titlePx + 'px; font-weight: bold; text-align: center; color: #000; width: 100%; line-height: 1.08; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: ' + titleLines + '; overflow: hidden; word-break: break-word; max-height: ' + titleMaxHeight + 'px; }',
            '    .barcode-plain-text { font-size: ' + fontPx + 'px; font-weight: bold; text-align: center; color: #000; width: 100%; line-height: 1.08; word-break: break-all; }',
            '    #barcode { display: block; width: calc(' + widthMm + 'mm - 8mm); max-height: calc(' + heightMm + 'mm - 10mm); }',
            '    @media print { body { -webkit-print-color-adjust: exact; print-color-adjust: exact; } header, footer { display: none !important; } }',
            '  </style>',
            '  <script src="https://cdn.jsdelivr.net/npm/jsbarcode@3.11.5/dist/JsBarcode.all.min.js"><\/script>',
            '</head>',
            '<body>',
            '  <div class="barcode-label">',
            showName && title ? '    <div class="barcode-title">' + esc(title) + '</div>' : '',
            showBarcode ? '    <svg id="barcode"></svg>' : '',
            !showBarcode && showBarcodeText ? '    <div class="barcode-plain-text">' + esc(barcode) + '</div>' : '',
            '  </div>',
            '  <script>',
            showBarcode
                ? '    JsBarcode("#barcode", ' + JSON.stringify(barcode) + ', { format: "CODE128", width: ' + barcodeWidth + ', height: ' + barcodeHeight + ', displayValue: ' + (showBarcodeText ? 'true' : 'false') + ', fontSize: ' + fontPx + ', margin: 0 });'
                : '',
            '    setTimeout(function(){ window.print(); }, 350);',
            '    window.onafterprint = function(){ window.close(); };',
            '  <\/script>',
            '</body>',
            '</html>'
        ].join('\n');

        printWindow.document.open();
        printWindow.document.write(html);
        printWindow.document.close();
    }

    async function printBarcode(modal, options) {
        options = options || {};
        var barcode = options.barcode || validateBarcode(modal);
        if (!barcode) return;

        var description = String(modal.descriptionInput && modal.descriptionInput.value || '').trim() || 'No barcode item';
        var busyButton = options.busyButton || modal.printBtn;
        buttonBusy(busyButton, true, options.busyText || 'Printing...');
        setStatus(modal, 'Sending label to printer...', '');

        try {
            var configResponse = await fetch('/api/printer/config');
            var configData = await configResponse.json().catch(function () { return {}; });
            var config = configData.config || {};

            if (String(config.print_method || '').toLowerCase() === 'browser') {
                printBarcodeBrowser({ upc: barcode, description: description }, config);
                setStatus(modal, 'Print dialog opened.', 'ok');
                return true;
            }

            var response = await fetch('/api/printer/print-barcode', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ upc: barcode, item_description: description, quantity: 1 })
            });
            var data = await response.json().catch(function () {
                return { success: false, error: 'Printer response invalid' };
            });
            if (!data || !data.success) {
                throw new Error((data && data.error) || 'Print failed');
            }
            setStatus(modal, 'Label sent to printer.', 'ok');
            return true;
        } catch (error) {
            setStatus(modal, (error && error.message) || 'Print failed.', 'bad');
            return false;
        } finally {
            buttonBusy(busyButton, false);
        }
    }

    async function printExistingLabel(options) {
        options = options || {};
        var barcode = String(options.barcode || '').trim();
        if (!barcode) {
            throw new Error('Barcode is required');
        }
        var description = String(options.description || '').trim() || 'Warehouse item';

        var configResponse = await fetch('/api/printer/config');
        var configData = await configResponse.json().catch(function () { return {}; });
        var config = configData.config || {};

        if (String(config.print_method || '').toLowerCase() === 'browser') {
            printBarcodeBrowser({ upc: barcode, description: description }, config);
            return true;
        }

        var response = await fetch('/api/printer/print-barcode', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ upc: barcode, item_description: description, quantity: 1 })
        });
        var data = await response.json().catch(function () {
            return { success: false, error: 'Printer response invalid' };
        });
        if (!response.ok || !data || !data.success) {
            throw new Error((data && data.error) || 'Print failed');
        }
        return true;
    }

    function reservedBarcodeList(modal) {
        var reserved = [];
        if (modal && modal.generatedBarcodes && typeof modal.generatedBarcodes.forEach === 'function') {
            modal.generatedBarcodes.forEach(function (barcode) {
                if (barcode) reserved.push(barcode);
            });
        }
        if (modal && typeof modal.getReservedBarcodes === 'function') {
            try {
                var pageReserved = modal.getReservedBarcodes() || [];
                pageReserved.forEach(function (barcode) {
                    barcode = String(barcode || '').trim();
                    if (barcode && reserved.indexOf(barcode) === -1) reserved.push(barcode);
                });
            } catch (_) {}
        }
        return reserved;
    }

    async function generateBarcode(modal) {
        buttonBusy(modal.generateBtn, true, 'Generating...');
        setStatus(modal, 'Generating unique barcode...', '');
        try {
            var response = await fetch('/api/items-prep/generate-barcode', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ exclude_barcodes: reservedBarcodeList(modal) })
            });
            var data = await response.json().catch(function () {
                return { success: false, error: 'Generator response invalid' };
            });
            if (!data || !data.success || !data.barcode) {
                throw new Error((data && data.error) || 'Could not generate barcode');
            }
            modal.barcodeInput.value = String(data.barcode).trim();
            modal.generatedBarcodes.add(modal.barcodeInput.value);
            setStatus(modal, 'Unique barcode ready.', 'ok');
            focusBarcodeInput(modal, true);
        } catch (error) {
            setStatus(modal, (error && error.message) || 'Could not generate barcode.', 'bad');
        } finally {
            buttonBusy(modal.generateBtn, false);
        }
    }

    async function persistCustomIdentity(modal, barcode) {
        var description = String(modal.descriptionInput && modal.descriptionInput.value || '').trim() || 'No barcode item';
        var response = await fetch('/api/custom-item/identity', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ upc: barcode, item_description: description })
        });
        var data = await response.json().catch(function () {
            return { success: false, error: 'Custom item response invalid' };
        });
        if (!response.ok || !data || !data.success) {
            throw new Error((data && data.error) || 'Could not save custom item identity');
        }
        return data;
    }

    function insertBarcodeText(modal, text) {
        if (!modal || !modal.barcodeInput) return;
        var raw = String(text || '').replace(/\D+/g, '');
        if (!raw) return;
        var input = modal.barcodeInput;
        var value = String(input.value || '');
        var start = typeof input.selectionStart === 'number' ? input.selectionStart : value.length;
        var end = typeof input.selectionEnd === 'number' ? input.selectionEnd : value.length;
        var next = value.slice(0, start) + raw + value.slice(end);
        input.value = next.slice(0, 64);
        var caret = Math.min(start + raw.length, input.value.length);
        try { input.setSelectionRange(caret, caret); } catch (_) {}
        input.dispatchEvent(new Event('input', { bubbles: true }));
        focusBarcodeInput(modal, false);
    }

    function backspaceBarcodeText(modal) {
        if (!modal || !modal.barcodeInput) return;
        var input = modal.barcodeInput;
        var value = String(input.value || '');
        var start = typeof input.selectionStart === 'number' ? input.selectionStart : value.length;
        var end = typeof input.selectionEnd === 'number' ? input.selectionEnd : value.length;
        if (!value) {
            focusBarcodeInput(modal, false);
            return;
        }
        var next = value;
        var caret = start;
        if (start !== end) {
            next = value.slice(0, start) + value.slice(end);
        } else if (start > 0) {
            next = value.slice(0, start - 1) + value.slice(end);
            caret = start - 1;
        }
        input.value = next;
        try { input.setSelectionRange(caret, caret); } catch (_) {}
        input.dispatchEvent(new Event('input', { bubbles: true }));
        focusBarcodeInput(modal, false);
    }

    function clearBarcodeText(modal) {
        if (!modal || !modal.barcodeInput) return;
        modal.barcodeInput.value = '';
        try { modal.barcodeInput.setSelectionRange(0, 0); } catch (_) {}
        modal.barcodeInput.dispatchEvent(new Event('input', { bubbles: true }));
        focusBarcodeInput(modal, false);
    }

    function closeModal(modal, focusTarget, options) {
        modal.root.style.display = 'none';
        setStatus(modal, '', '');
        if (options && typeof options.onClose === 'function') {
            options.onClose(modal);
        }
        if (focusTarget && typeof focusTarget.focus === 'function') {
            setTimeout(function () { focusTarget.focus(); }, 50);
        }
    }

    function openModal(modal, options) {
        if (options && typeof options.onOpen === 'function') {
            options.onOpen(modal);
        }
        modal.root.style.display = 'flex';
        setStatus(modal, '', '');
        focusBarcodeInput(modal, true);
    }

    window.NoBarcodeLabelFlow = {
        printExistingLabel: printExistingLabel,
        init: function (options) {
            options = options || {};
            var modal = {
                root: byId(options.modalId || 'noBarcodeModal'),
                openBtn: byId(options.openButtonId || 'noBarcodeBtn'),
                closeBtn: byId(options.closeButtonId || 'noBarcodeCloseBtn'),
                barcodeInput: byId(options.barcodeInputId || 'noBarcodeInput'),
                descriptionInput: byId(options.descriptionInputId || 'noBarcodeDescriptionInput'),
                generateBtn: byId(options.generateButtonId || 'noBarcodeGenerateBtn'),
                printBtn: byId(options.printButtonId || 'noBarcodePrintBtn'),
                useBtn: byId(options.useButtonId || 'noBarcodeUseBtn'),
                keypad: byId(options.keypadId || 'noBarcodeKeypad'),
                status: byId(options.statusId || 'noBarcodeStatus'),
                generatedBarcodes: new Set(),
                getReservedBarcodes: typeof options.getReservedBarcodes === 'function' ? options.getReservedBarcodes : null
            };
            var focusTarget = typeof options.focusTarget === 'function' ? options.focusTarget() : options.focusTarget;

            if (!modal.root || !modal.openBtn || !modal.barcodeInput) return;

            modal.openBtn.addEventListener('click', function () {
                openModal(modal, options);
            });
            if (modal.closeBtn) {
                modal.closeBtn.addEventListener('click', function () {
                    closeModal(modal, focusTarget, options);
                });
            }
            modal.root.addEventListener('click', function (event) {
                if (event.target === modal.root) closeModal(modal, focusTarget, options);
            });
            modal.root.addEventListener('pointerdown', function (event) {
                if (event.target && event.target.closest && event.target.closest('.no-barcode-keypad')) {
                    event.preventDefault();
                }
            });
            modal.barcodeInput.addEventListener('focus', function () {
                if (typeof modal.barcodeInput.select === 'function') {
                    window.setTimeout(function () {
                        modal.barcodeInput.select();
                    }, 0);
                }
            });
            modal.barcodeInput.addEventListener('keydown', function (event) {
                if (event.key === 'Enter') {
                    event.preventDefault();
                    if (modal.useBtn) modal.useBtn.click();
                }
            });
            if (modal.keypad) {
                modal.keypad.addEventListener('click', function (event) {
                    var button = event.target && event.target.closest ? event.target.closest('button') : null;
                    if (!button) return;
                    var key = button.getAttribute('data-no-barcode-key');
                    var action = button.getAttribute('data-no-barcode-action');
                    if (key != null) {
                        insertBarcodeText(modal, key);
                    } else if (action === 'backspace') {
                        backspaceBarcodeText(modal);
                    } else if (action === 'clear') {
                        clearBarcodeText(modal);
                    }
                });
            }
            if (modal.generateBtn) {
                modal.generateBtn.addEventListener('click', function () {
                    generateBarcode(modal);
                });
            }
            if (modal.printBtn) {
                modal.printBtn.addEventListener('click', function () {
                    printBarcode(modal);
                });
            }
            if (modal.useBtn) {
                modal.useBtn.addEventListener('click', async function () {
                    var barcode = validateBarcode(modal);
                    if (!barcode) return;
                    try {
                        await persistCustomIdentity(modal, barcode);
                    } catch (error) {
                        setStatus(modal, (error && error.message) || 'Could not save custom item.', 'bad');
                        focusBarcodeInput(modal, true);
                        return;
                    }
                    var printed = await printBarcode(modal, {
                        barcode: barcode,
                        busyButton: modal.useBtn,
                        busyText: 'Printing...'
                    });
                    if (!printed) {
                        focusBarcodeInput(modal, true);
                        return;
                    }
                    modal.generatedBarcodes.add(barcode);
                    if (typeof options.onUseBarcode === 'function') {
                        options.onUseBarcode(barcode);
                    }
                    closeModal(modal, null, options);
                });
            }
        }
    };
}());

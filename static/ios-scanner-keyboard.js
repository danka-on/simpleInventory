(function () {
  'use strict';

  if (window.IOSScannerKeyboard) return;

  var ua = navigator.userAgent || '';
  var isIOS = /iPhone|iPad|iPod/.test(ua)
    || (/Macintosh/.test(ua) && ('ontouchend' in document || navigator.maxTouchPoints > 1));
  if (!isIOS) return;

  var STYLE_ID = 'ss-ios-scanner-keyboard-style';
  var ROOT_ID = 'ss-ios-scanner-keyboard';
  var activeField = null;
  var root = null;
  var fieldLabel = null;
  var letterPanel = null;
  var numberPanel = null;
  var shiftKey = null;
  var shifted = true;
  var focusTimer = null;
  var lastPointerField = null;
  var lastPointerAt = 0;
  var viewport = window.visualViewport || null;
  var baselineHeight = Math.max(
    window.innerHeight || 0,
    document.documentElement ? document.documentElement.clientHeight : 0,
    viewport ? viewport.height : 0
  );

  function installStyles() {
    if (document.getElementById(STYLE_ID)) return;
    var style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = [
      '#ss-ios-scanner-keyboard{position:fixed;left:0;right:0;bottom:0;z-index:2147483646;display:flex;justify-content:center;pointer-events:none;transform:translateY(110%);transition:transform .18s ease-out;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",sans-serif;-webkit-user-select:none;user-select:none;-webkit-tap-highlight-color:transparent;}',
      '#ss-ios-scanner-keyboard.ss-ios-kb-open{pointer-events:auto;transform:translateY(0);}',
      '.ss-ios-kb-deck{width:100%;max-width:540px;padding:0 4px calc(7px + env(safe-area-inset-bottom,0px));box-sizing:border-box;background:linear-gradient(180deg,#d8dbe0 0%,#c8cbd1 100%);box-shadow:0 -1px 0 rgba(0,0,0,.18),0 -10px 28px rgba(0,0,0,.2);touch-action:manipulation;}',
      '.ss-ios-kb-accessory{height:34px;margin:0 -4px 7px;padding:0 8px 0 12px;display:flex;align-items:center;justify-content:space-between;gap:8px;background:rgba(246,247,249,.96);border-bottom:1px solid rgba(60,60,67,.18);color:#5b5d63;font-size:12px;font-weight:600;}',
      '.ss-ios-kb-field-label{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}',
      '.ss-ios-kb-key.ss-ios-kb-hide{width:38px;height:30px;flex:0 0 38px;margin:0;padding:0;border:0;border-radius:7px;background:transparent;color:#007aff;font-size:20px;line-height:1;box-shadow:none;}',
      '.ss-ios-kb-panel{display:flex;flex-direction:column;gap:7px;}',
      '.ss-ios-kb-panel[hidden]{display:none!important;}',
      '.ss-ios-kb-row{display:flex;align-items:stretch;gap:6px;width:100%;}',
      '.ss-ios-kb-row.ss-ios-kb-home{padding:0 5%;box-sizing:border-box;}',
      '.ss-ios-kb-key{flex:1 1 0;min-width:0;height:43px;margin:0;padding:0;display:flex;align-items:center;justify-content:center;border:0;border-radius:5px;background:#fff;color:#080808;font:400 17px/1 -apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",sans-serif;white-space:nowrap;box-shadow:0 1px 0 rgba(0,0,0,.42);-webkit-appearance:none;appearance:none;transition:background-color 45ms linear,transform 45ms linear;}',
      '.ss-ios-kb-key:active{transform:translateY(1px);background:#b8bcc3;box-shadow:none;}',
      '.ss-ios-kb-key.ss-ios-kb-action{background:#aeb3bc;color:#111;font-size:14px;font-weight:600;}',
      '.ss-ios-kb-key.ss-ios-kb-action.ss-ios-kb-active{background:#fff;}',
      '.ss-ios-kb-key.ss-ios-kb-side{flex:1.35 1 0;font-size:20px;}',
      '.ss-ios-kb-key.ss-ios-kb-mode,.ss-ios-kb-key.ss-ios-kb-done{flex:1.65 1 0;font-size:13px;}',
      '.ss-ios-kb-key.ss-ios-kb-space{flex:5.6 1 0;font-size:14px;}',
      '.ss-ios-kb-key.ss-ios-kb-done{background:#0a84ff;color:#fff;}',
      'html.ss-ios-keyboard-open{scroll-padding-bottom:265px;}',
      '@media(max-width:430px){.ss-ios-kb-deck{padding-left:3px;padding-right:3px}.ss-ios-kb-panel{gap:6px}.ss-ios-kb-row{gap:4px}.ss-ios-kb-key{height:42px;font-size:16px}.ss-ios-kb-key.ss-ios-kb-side{font-size:19px}.ss-ios-kb-key.ss-ios-kb-mode,.ss-ios-kb-key.ss-ios-kb-done{font-size:12px}}',
      '@media(max-height:540px) and (orientation:landscape){.ss-ios-kb-accessory{height:28px;margin-bottom:4px}.ss-ios-kb-panel{gap:4px}.ss-ios-kb-row{gap:4px}.ss-ios-kb-key{height:34px}.ss-ios-kb-deck{padding-bottom:calc(4px + env(safe-area-inset-bottom,0px))}}'
    ].join('');
    document.head.appendChild(style);
  }

  function makeKey(label, options) {
    options = options || {};
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'ss-ios-kb-key' + (options.className ? ' ' + options.className : '');
    button.textContent = label;
    if (options.char != null) button.setAttribute('data-ios-kb-char', options.char);
    if (options.action) button.setAttribute('data-ios-kb-action', options.action);
    if (options.letter) button.setAttribute('data-ios-kb-letter', '1');
    if (options.label) button.setAttribute('aria-label', options.label);
    return button;
  }

  function makeRow(keys, className) {
    var row = document.createElement('div');
    row.className = 'ss-ios-kb-row' + (className ? ' ' + className : '');
    keys.forEach(function (key) { row.appendChild(key); });
    return row;
  }

  function letterKeys(text) {
    return text.split('').map(function (letter) {
      return makeKey(letter.toUpperCase(), { char: letter, letter: true });
    });
  }

  function buildKeyboard() {
    if (root) return root;
    installStyles();

    root = document.createElement('div');
    root.id = ROOT_ID;
    root.setAttribute('aria-hidden', 'true');
    root.setAttribute('role', 'group');
    root.setAttribute('aria-label', 'On-screen keyboard');

    var deck = document.createElement('div');
    deck.className = 'ss-ios-kb-deck';

    var accessory = document.createElement('div');
    accessory.className = 'ss-ios-kb-accessory';
    fieldLabel = document.createElement('div');
    fieldLabel.className = 'ss-ios-kb-field-label';
    fieldLabel.textContent = 'Keyboard';
    var hideButton = makeKey('⌄', {
      action: 'hide',
      className: 'ss-ios-kb-hide',
      label: 'Hide keyboard'
    });
    accessory.appendChild(fieldLabel);
    accessory.appendChild(hideButton);
    deck.appendChild(accessory);

    letterPanel = document.createElement('div');
    letterPanel.className = 'ss-ios-kb-panel';
    letterPanel.setAttribute('data-ios-kb-layout', 'letters');
    letterPanel.appendChild(makeRow(letterKeys('qwertyuiop')));
    letterPanel.appendChild(makeRow(letterKeys('asdfghjkl'), 'ss-ios-kb-home'));

    shiftKey = makeKey('⇧', {
      action: 'shift',
      className: 'ss-ios-kb-action ss-ios-kb-side ss-ios-kb-active',
      label: 'Shift'
    });
    var lowerRow = [shiftKey].concat(letterKeys('zxcvbnm'));
    lowerRow.push(makeKey('⌫', {
      action: 'backspace',
      className: 'ss-ios-kb-action ss-ios-kb-side',
      label: 'Delete'
    }));
    letterPanel.appendChild(makeRow(lowerRow));

    var quickLeft = makeKey("'", { char: "'" });
    quickLeft.setAttribute('data-ios-kb-quick', 'left');
    var quickRight = makeKey('-', { char: '-' });
    quickRight.setAttribute('data-ios-kb-quick', 'right');
    letterPanel.appendChild(makeRow([
      makeKey('123', { action: 'numbers', className: 'ss-ios-kb-action ss-ios-kb-mode' }),
      quickLeft,
      makeKey('space', { char: ' ', className: 'ss-ios-kb-space' }),
      quickRight,
      makeKey('Done', { action: 'done', className: 'ss-ios-kb-action ss-ios-kb-done' })
    ]));
    deck.appendChild(letterPanel);

    numberPanel = document.createElement('div');
    numberPanel.className = 'ss-ios-kb-panel';
    numberPanel.setAttribute('data-ios-kb-layout', 'numbers');
    numberPanel.hidden = true;
    numberPanel.appendChild(makeRow('1234567890'.split('').map(function (value) {
      return makeKey(value, { char: value });
    })));
    numberPanel.appendChild(makeRow([
      ['-', '-'], ['/', '/'], [':', ':'], [';', ';'], ['(', '('], [')', ')'], ['$', '$'], ['&', '&'], ['@', '@'], ['"', '"']
    ].map(function (entry) { return makeKey(entry[0], { char: entry[1] }); })));
    numberPanel.appendChild(makeRow([
      makeKey('ABC', { action: 'letters', className: 'ss-ios-kb-action ss-ios-kb-side' }),
      makeKey('.', { char: '.' }),
      makeKey(',', { char: ',' }),
      makeKey('?', { char: '?' }),
      makeKey('!', { char: '!' }),
      makeKey("'", { char: "'" }),
      makeKey('#', { char: '#' }),
      makeKey('%', { char: '%' }),
      makeKey('⌫', { action: 'backspace', className: 'ss-ios-kb-action ss-ios-kb-side', label: 'Delete' })
    ]));
    numberPanel.appendChild(makeRow([
      makeKey('ABC', { action: 'letters', className: 'ss-ios-kb-action ss-ios-kb-mode' }),
      makeKey('Clear', { action: 'clear', className: 'ss-ios-kb-action ss-ios-kb-mode' }),
      makeKey('space', { char: ' ', className: 'ss-ios-kb-space' }),
      makeKey('Done', { action: 'done', className: 'ss-ios-kb-action ss-ios-kb-done' })
    ]));
    deck.appendChild(numberPanel);
    root.appendChild(deck);

    root.addEventListener('pointerdown', function (event) {
      if (event.target.closest('button')) event.preventDefault();
    });
    root.addEventListener('click', handleKeyboardClick);
    document.body.appendChild(root);
    return root;
  }

  function explicitMode(field) {
    return String(field && field.getAttribute && field.getAttribute('data-scanner-keyboard') || '').toLowerCase();
  }

  function isSupportedField(field) {
    if (!field || field.disabled || field.readOnly) return false;
    var mode = explicitMode(field);
    if (mode === 'off' || mode === 'false' || mode === 'none') return false;
    if (field.isContentEditable) return true;
    if (field.tagName === 'TEXTAREA') return true;
    if (field.tagName !== 'INPUT') return false;
    var type = String(field.type || 'text').toLowerCase();
    if (['text', 'search', 'email', 'url', 'tel', 'number', 'password'].indexOf(type) === -1) return false;
    if (mode === 'on' || mode === 'true' || mode === 'auto') return true;
    if (type === 'search') return true;

    var metadata = [
      field.id,
      field.name,
      field.className,
      field.placeholder,
      field.getAttribute('aria-label')
    ].join(' ').toLowerCase();
    if (/search|keyword|type a name|enter a name|description|title|\bname\b|note|comment|address|email/.test(metadata)) return true;
    if (/scanner|scan\b|barcode|\bupc\b|qr\s*(code)?/.test(metadata)) return false;
    return true;
  }

  function fieldName(field) {
    if (!field) return 'Keyboard';
    var id = field.id;
    if (id) {
      try {
        var label = document.querySelector('label[for="' + CSS.escape(id) + '"]');
        if (label && label.textContent.trim()) return label.textContent.trim();
      } catch (_) {}
    }
    return String(field.getAttribute('aria-label') || field.placeholder
      || field.getAttribute('data-placeholder') || field.name || 'Keyboard').trim();
  }

  function setLayout(layout) {
    buildKeyboard();
    var numbers = layout === 'numbers';
    letterPanel.hidden = numbers;
    numberPanel.hidden = !numbers;
  }

  function prefersNumberLayout(field) {
    if (!field) return false;
    var type = String(field.type || '').toLowerCase();
    var inputMode = String(field.inputMode || field.getAttribute('inputmode') || '').toLowerCase();
    return type === 'number' || type === 'tel' || /numeric|decimal|tel/.test(inputMode);
  }

  function setShift(active) {
    shifted = !!active;
    if (!root) return;
    root.querySelectorAll('[data-ios-kb-letter]').forEach(function (key) {
      var value = String(key.getAttribute('data-ios-kb-char') || '');
      key.textContent = shifted ? value.toUpperCase() : value.toLowerCase();
    });
    if (shiftKey) shiftKey.classList.toggle('ss-ios-kb-active', shifted);
  }

  function syncShift() {
    if (!activeField) return;
    var value = String(activeField.isContentEditable ? activeField.textContent : (activeField.value || ''));
    var before = value;
    if (activeField.isContentEditable) {
      try {
        var selection = window.getSelection();
        if (selection && selection.rangeCount && activeField.contains(selection.anchorNode)) {
          var beforeRange = selection.getRangeAt(0).cloneRange();
          beforeRange.selectNodeContents(activeField);
          beforeRange.setEnd(selection.anchorNode, selection.anchorOffset);
          before = beforeRange.toString();
        }
      } catch (_) {}
    } else {
      var caret = typeof activeField.selectionStart === 'number' ? activeField.selectionStart : value.length;
      before = value.slice(0, caret);
    }
    var capitalize = !before || /[\s.!?(\-\/&]$/.test(before);
    var autoCapitalize = String(activeField.autocapitalize || activeField.getAttribute('autocapitalize') || '').toLowerCase();
    setShift(autoCapitalize === 'off' ? false : capitalize);
  }

  function updateQuickKeys(field) {
    if (!root || !field) return;
    var left = root.querySelector('[data-ios-kb-quick="left"]');
    var right = root.querySelector('[data-ios-kb-quick="right"]');
    if (!left || !right) return;
    var type = String(field.type || '').toLowerCase();
    if (type === 'email') {
      left.textContent = '@'; left.setAttribute('data-ios-kb-char', '@');
      right.textContent = '.'; right.setAttribute('data-ios-kb-char', '.');
    } else if (type === 'url') {
      left.textContent = '/'; left.setAttribute('data-ios-kb-char', '/');
      right.textContent = '.'; right.setAttribute('data-ios-kb-char', '.');
    } else {
      left.textContent = "'"; left.setAttribute('data-ios-kb-char', "'");
      right.textContent = '-'; right.setAttribute('data-ios-kb-char', '-');
    }
  }

  function currentViewportHeight() {
    return viewport ? viewport.height : (window.innerHeight || 0);
  }

  function nativeKeyboardVisible() {
    var current = currentViewportHeight();
    var reference = Math.max(baselineHeight, document.documentElement ? document.documentElement.clientHeight : 0);
    return reference > 0 && current > 0 && current < reference - 110;
  }

  function showKeyboard(field, force) {
    if (!isSupportedField(field) && !force) return false;
    if (!field || field.disabled || field.readOnly) return false;
    if (!force && nativeKeyboardVisible()) return false;
    buildKeyboard();
    activeField = field;
    fieldLabel.textContent = fieldName(field);
    updateQuickKeys(field);
    setLayout(prefersNumberLayout(field) ? 'numbers' : 'letters');
    syncShift();
    root.classList.add('ss-ios-kb-open');
    root.setAttribute('aria-hidden', 'false');
    document.documentElement.classList.add('ss-ios-keyboard-open');
    window.setTimeout(function () {
      if (!activeField || !root.classList.contains('ss-ios-kb-open')) return;
      try { activeField.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'smooth' }); } catch (_) {}
    }, 70);
    return true;
  }

  function hideKeyboard(options) {
    options = options || {};
    if (focusTimer) {
      clearTimeout(focusTimer);
      focusTimer = null;
    }
    if (root) {
      root.classList.remove('ss-ios-kb-open');
      root.setAttribute('aria-hidden', 'true');
    }
    document.documentElement.classList.remove('ss-ios-keyboard-open');
    var field = activeField;
    activeField = null;
    if (options.blur && field && typeof field.blur === 'function') field.blur();
  }

  function inputEvent(type, inputType, data, cancelable) {
    try {
      return new InputEvent(type, {
        bubbles: true,
        cancelable: !!cancelable,
        inputType: inputType,
        data: data
      });
    } catch (_) {
      return new Event(type, { bubbles: true, cancelable: !!cancelable });
    }
  }

  function replaceSelection(text, inputType) {
    var field = activeField;
    if (!field || field.disabled || field.readOnly) return;
    var insert = String(text == null ? '' : text);
    if (field.isContentEditable) {
      if (!field.dispatchEvent(inputEvent('beforeinput', inputType || 'insertText', insert, true))) return;
      try {
        var selection = window.getSelection();
        var range = selection && selection.rangeCount ? selection.getRangeAt(0) : null;
        if (!range || !field.contains(range.commonAncestorContainer)) {
          range = document.createRange();
          range.selectNodeContents(field);
          range.collapse(false);
        }
        range.deleteContents();
        var node = document.createTextNode(insert);
        range.insertNode(node);
        range.setStartAfter(node);
        range.collapse(true);
        selection.removeAllRanges();
        selection.addRange(range);
      } catch (_) {
        field.textContent += insert;
      }
      field.dispatchEvent(inputEvent('input', inputType || 'insertText', insert, false));
      syncShift();
      return;
    }
    var value = String(field.value || '');
    var start = typeof field.selectionStart === 'number' ? field.selectionStart : value.length;
    var end = typeof field.selectionEnd === 'number' ? field.selectionEnd : value.length;
    var maxLength = parseInt(field.maxLength, 10);
    if (Number.isFinite(maxLength) && maxLength >= 0) {
      insert = insert.slice(0, Math.max(0, maxLength - (value.length - (end - start))));
    }
    if (!field.dispatchEvent(inputEvent('beforeinput', inputType || 'insertText', insert, true))) return;
    field.value = value.slice(0, start) + insert + value.slice(end);
    var caret = start + insert.length;
    try { field.setSelectionRange(caret, caret); } catch (_) {}
    field.dispatchEvent(inputEvent('input', inputType || 'insertText', insert, false));
    syncShift();
  }

  function backspace() {
    var field = activeField;
    if (!field || field.disabled || field.readOnly) return;
    if (field.isContentEditable) {
      if (!field.dispatchEvent(inputEvent('beforeinput', 'deleteContentBackward', null, true))) return;
      try {
        var selection = window.getSelection();
        if (selection && selection.rangeCount && activeField.contains(selection.anchorNode)) {
          if (selection.isCollapsed && typeof selection.modify === 'function') {
            selection.modify('extend', 'backward', 'character');
          }
          selection.deleteFromDocument();
        }
      } catch (_) {}
      field.dispatchEvent(inputEvent('input', 'deleteContentBackward', null, false));
      syncShift();
      return;
    }
    var value = String(field.value || '');
    var start = typeof field.selectionStart === 'number' ? field.selectionStart : value.length;
    var end = typeof field.selectionEnd === 'number' ? field.selectionEnd : value.length;
    if (start === end && start > 0) start -= 1;
    if (start === end) return;
    if (!field.dispatchEvent(inputEvent('beforeinput', 'deleteContentBackward', null, true))) return;
    field.value = value.slice(0, start) + value.slice(end);
    try { field.setSelectionRange(start, start); } catch (_) {}
    field.dispatchEvent(inputEvent('input', 'deleteContentBackward', null, false));
    syncShift();
  }

  function clearField() {
    var field = activeField;
    if (!field || field.disabled || field.readOnly) return;
    if (!field.dispatchEvent(inputEvent('beforeinput', 'deleteByCut', null, true))) return;
    if (field.isContentEditable) {
      field.textContent = '';
      try {
        var range = document.createRange();
        range.selectNodeContents(field);
        range.collapse(true);
        var selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
      } catch (_) {}
    } else {
      field.value = '';
      try { field.setSelectionRange(0, 0); } catch (_) {}
    }
    field.dispatchEvent(inputEvent('input', 'deleteByCut', null, false));
    setShift(true);
  }

  function pressDone() {
    var field = activeField;
    if (!field) return;
    var allowed = true;
    try {
      allowed = field.dispatchEvent(new KeyboardEvent('keydown', {
        key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true
      }));
      if (allowed) {
        allowed = field.dispatchEvent(new KeyboardEvent('keypress', {
          key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true
        }));
      }
      field.dispatchEvent(new KeyboardEvent('keyup', {
        key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true
      }));
    } catch (_) {}
    try { field.dispatchEvent(new Event('change', { bubbles: true })); } catch (_) {}
    if (!allowed && field.getClientRects && field.getClientRects().length > 0) return;
    hideKeyboard({ blur: allowed });
  }

  function handleKeyboardClick(event) {
    var button = event.target.closest('[data-ios-kb-char],[data-ios-kb-action]');
    if (!button) return;
    event.preventDefault();
    event.stopPropagation();
    var action = button.getAttribute('data-ios-kb-action');
    if (action === 'hide') { hideKeyboard({ blur: false }); return; }
    if (action === 'shift') { setShift(!shifted); return; }
    if (action === 'numbers' || action === 'letters') { setLayout(action); return; }
    if (action === 'backspace') { backspace(); return; }
    if (action === 'clear') { clearField(); return; }
    if (action === 'done') { pressDone(); return; }
    var value = button.getAttribute('data-ios-kb-char');
    if (value != null) {
      if (/^[a-z]$/i.test(value)) value = shifted ? value.toUpperCase() : value.toLowerCase();
      replaceSelection(value, 'insertText');
    }
  }

  function queueForField(field) {
    if (focusTimer) clearTimeout(focusTimer);
    if (!isSupportedField(field)) {
      hideKeyboard();
      return;
    }
    activeField = field;
    if (root && root.classList.contains('ss-ios-kb-open')) {
      showKeyboard(field, true);
      return;
    }
    var handheld = false;
    try { handheld = localStorage.getItem('scannerMode') === 'handheld'; } catch (_) {}
    focusTimer = window.setTimeout(function () {
      focusTimer = null;
      if (document.activeElement !== field || !isSupportedField(field)) return;
      if (!nativeKeyboardVisible()) showKeyboard(field, false);
    }, handheld ? 180 : 420);
  }

  function init() {
    installStyles();
    document.addEventListener('focusin', function (event) {
      if (root && root.contains(event.target)) return;
      if (!isSupportedField(event.target)) {
        hideKeyboard();
        return;
      }
      var mode = explicitMode(event.target);
      var recentPointer = event.target === lastPointerField && (Date.now() - lastPointerAt) < 1000;
      var alreadyOpen = root && root.classList.contains('ss-ios-kb-open');
      if (mode === 'on' || mode === 'true' || alreadyOpen || recentPointer) queueForField(event.target);
    }, true);
    document.addEventListener('pointerdown', function (event) {
      if (isSupportedField(event.target)) {
        lastPointerField = event.target;
        lastPointerAt = Date.now();
        if (!root || !root.classList.contains('ss-ios-kb-open')) queueForField(event.target);
        return;
      }
      if (!root || !root.classList.contains('ss-ios-kb-open') || root.contains(event.target)) return;
      window.setTimeout(function () { hideKeyboard(); }, 0);
    }, true);
    document.addEventListener('selectionchange', function () {
      if (activeField && document.activeElement === activeField) syncShift();
    });
    window.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && root && root.classList.contains('ss-ios-kb-open')) hideKeyboard();
    });
    var trackViewport = function () {
      if (!isSupportedField(document.activeElement) && (!root || !root.classList.contains('ss-ios-kb-open'))) {
        baselineHeight = Math.max(baselineHeight, currentViewportHeight(), window.innerHeight || 0);
      }
    };
    window.addEventListener('resize', trackViewport);
    if (viewport) viewport.addEventListener('resize', trackViewport);
  }

  window.IOSScannerKeyboard = {
    show: function (field) { return showKeyboard(field || document.activeElement, true); },
    hide: hideKeyboard,
    isEligible: isSupportedField
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();

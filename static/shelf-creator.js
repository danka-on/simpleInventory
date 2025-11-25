/**
 * Shelf Creator - Main JavaScript
 * Handles all shelf management functionality
 */

// ============================================================================
// GLOBAL STATE
// ============================================================================
const state = {
    groups: [],
    shelves: [],
    currentGroupId: null, // null = showing groups list
    selectedShelves: new Set(),
    selectMode: false,
    currentShelfCode: '',
    currentImage: null,
    cameraStream: null,
    isMobile: /iPhone|iPad|iPod|Android/i.test(navigator.userAgent),
    isEditing: false,
    canvas: null,
    ctx: null,
    isDrawing: false,
    startX: 0,
    startY: 0,
    rectX: 0,
    rectY: 0,
    rectW: 0,
    rectH: 0
};

// ============================================================================
// STEP 1: LIST VIEW FUNCTIONALITY
// ============================================================================

/**
 * Initialize the application
 */
function init() {
    console.log('Initializing Shelf Creator...');
    console.log('Device type:', state.isMobile ? 'Mobile' : 'Desktop');
    
    loadData();
    setupEventListeners();
    setupCanvas();
}

/**
 * Load groups and shelves
 */
function loadData() {
    Promise.all([
        fetch('/api/groups').then(r => r.json()),
        fetch('/api/list_shelves').then(r => r.json())
    ]).then(([groupsData, shelvesData]) => {
        if (groupsData.success) state.groups = groupsData.groups;
        if (shelvesData.success) state.shelves = shelvesData.shelves;
        
        renderMain();
    }).catch(err => {
        console.error('Error loading data:', err);
        showError('Failed to load data');
    });
}

/**
 * Setup all event listeners
 */
function setupEventListeners() {
    // List view
    document.getElementById('add-btn').addEventListener('click', handleAddClick);
    // Note: back-btn onclick is set dynamically in renderMain()
    document.getElementById('select-mode-btn').addEventListener('click', toggleSelectMode);
    document.getElementById('print-qr-btn').addEventListener('click', printQRCodes);
    document.getElementById('move-group-btn').addEventListener('click', showMoveModal);
    document.getElementById('select-all-btn').addEventListener('click', selectAllShelves);
    document.getElementById('delete-selected-btn').addEventListener('click', deleteSelectedShelves);
    
    // Add/Edit view
    document.getElementById('cancel-btn').addEventListener('click', cancelAdd);
    document.getElementById('save-btn').addEventListener('click', saveShelf);
    document.getElementById('start-camera-btn').addEventListener('click', startCapture);
    
    // Camera controls
    document.getElementById('capture-btn').addEventListener('click', capturePhoto);
    document.getElementById('retake-btn').addEventListener('click', retakePhoto);
    const replaceImgBtn = document.getElementById('replace-image-btn');
    if (replaceImgBtn) replaceImgBtn.addEventListener('click', replaceImage);
    document.getElementById('continue-btn').addEventListener('click', continueToEditor);
    
    // Modal
    document.getElementById('modal-close-btn').addEventListener('click', closeModal);
    document.getElementById('edit-shelf-btn').addEventListener('click', editShelf);
    document.getElementById('delete-shelf-btn').addEventListener('click', deleteShelf);
    const clearBtn = document.getElementById('clear-inventory-btn');
    if (clearBtn) clearBtn.addEventListener('click', clearInventory);
    const viewBtn = document.getElementById('view-items-btn');
    if (viewBtn) viewBtn.addEventListener('click', () => {
        const code = state.currentShelfCode;
        if (!code) return;
        window.location.href = `/searchrack?q=${encodeURIComponent(code)}`;
    });

    // Group Modals
    document.getElementById('cancel-group-btn').addEventListener('click', () => {
        document.getElementById('create-group-modal').classList.remove('active');
    });
    document.getElementById('save-group-btn').addEventListener('click', createGroup);
    
    document.getElementById('cancel-move-btn').addEventListener('click', () => {
        document.getElementById('move-group-modal').classList.remove('active');
    });
    document.getElementById('confirm-move-btn').addEventListener('click', moveShelves);
    
    // Code validation
    setupCodeValidation();
    // Enable save when code changes
    const codeInput = document.getElementById('shelf-code');
    if (codeInput) codeInput.addEventListener('input', updateSaveButton);

    // Apply sort button (if present)
    const applyBtn = document.getElementById('apply-sort-btn');
    if (applyBtn) applyBtn.addEventListener('click', (e) => {
        e.preventDefault();
        renderShelves(); // Re-render with sort
    });
}

/**
 * Main render function
 */
function renderMain() {
    const container = document.getElementById('shelf-list');
    const title = document.getElementById('view-title');
    const backBtn = document.getElementById('back-btn');
    const backText = document.getElementById('back-btn-text');
    const sortControls = document.getElementById('sort-controls');
    const selectBtn = document.getElementById('select-mode-btn');
    
    if (state.currentGroupId === null) {
        // Show Groups
        title.textContent = 'Groups';
        backText.textContent = 'Exit';
        backBtn.onclick = () => window.location.href = '/tools';
        sortControls.style.display = 'none';
        selectBtn.style.display = 'none';
        renderGroups();
    } else {
        // Show Shelves in Group
        const group = state.groups.find(g => g.id === state.currentGroupId);
        title.textContent = group ? group.name : 'Unknown Group';
        backText.textContent = 'Groups';
        backBtn.onclick = (e) => {
            e.preventDefault();
            e.stopPropagation();
            window.location.href = '/shelfmanager';
        };
        sortControls.style.display = 'flex';
        selectBtn.style.display = 'inline-block';
        renderShelves();
    }
}

/**
 * Render Groups List
 */
function renderGroups() {
    const container = document.getElementById('shelf-list');
    
    // Sort groups: non-default first, then default (id=1) last
    const sortedGroups = state.groups.slice().sort((a, b) => {
        if (a.id === 1) return 1;  // Default group goes last
        if (b.id === 1) return -1; // Default group goes last
        return a.id - b.id;
    });
    
    container.innerHTML = sortedGroups.map(g => {
        // Count shelves in this group
        const shelvesInGroup = state.shelves.filter(s => s.group_id === g.id);
        const shelfCount = shelvesInGroup.length;
        
        // Hide default group if empty
        const isDefaultGroup = g.id === 1;
        if (isDefaultGroup && shelfCount === 0) {
            return ''; // Don't render empty default group
        }
        
        // Sum up all items from all shelves in this group
        const totalItems = shelvesInGroup.reduce((sum, shelf) => sum + (shelf.count || 0), 0);
        
        return `
        <div class="shelf-item group-card" onclick="handleGroupClick(${g.id})">
            <div class="group-icon">
                <i class="fas ${isDefaultGroup ? 'fa-box-open' : 'fa-folder'}"></i>
            </div>
            <div class="shelf-code">${g.name}</div>
            <div class="group-count">${shelfCount} shelves • ${totalItems} items</div>
            ${!isDefaultGroup ? `<button class="delete-group-btn" onclick="deleteGroup(event, ${g.id})"><i class="fas fa-trash"></i></button>` : ''}
        </div>
        `;
    }).join('');
}

/**
 * Render Shelves (filtered by current group)
 */
function renderShelves() {
    const container = document.getElementById('shelf-list');
    const sortSel = document.getElementById('shelf-sort');
    const sortBy = sortSel ? sortSel.value : 'name';
    
    // Filter shelves by group
    let shelves = state.shelves.filter(s => s.group_id === state.currentGroupId);
    
    // Sort shelves
    shelves.sort((a, b) => {
        if (sortBy === 'name') return a.code.localeCompare(b.code);
        if (sortBy === 'items') return (b.count || 0) - (a.count || 0);
        if (sortBy === 'created') return (b.created_at || 0) - (a.created_at || 0);
        return 0;
    });
    
    if (shelves.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <i class="fas fa-box-open"></i>
                <p>No shelves in this group. Click + to add one!</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = shelves.map(shelf => {
        const isSelected = state.selectedShelves.has(shelf.code);
        const classes = ['shelf-item'];
        if (isSelected) classes.push('selected');
        if (state.selectMode) classes.push('selecting');
        const countVal = (typeof shelf.count !== 'undefined' ? shelf.count : 0);
        
        return `
       <div class="${classes.join(' ')}" 
           data-code="${shelf.code}"
           onclick="handleShelfClick(event,'${shelf.code}')">
            <span class="shelf-count" data-code="${shelf.code}" title="View items" onclick="onCountClick(event,'${shelf.code}')">${countVal}</span>
            <img src="${shelf.url}" alt="${shelf.code}" loading="lazy">
            <div class="shelf-code">${shelf.code}</div>
        </div>
    `}).join('');
    
    updatePrintButton();
}

/**
 * Handle Group Click
 */
function handleGroupClick(groupId) {
    state.currentGroupId = groupId;
    state.selectedShelves.clear();
    state.selectMode = false;
    renderMain();
}

/**
 * Handle Back Click
 */
function handleBackClick() {
    if (state.currentGroupId !== null) {
        state.currentGroupId = null;
        renderMain();
    } else {
        window.location.href = '/tools';
    }
}

/**
 * Handle Add Button Click
 */
function handleAddClick() {
    if (state.currentGroupId === null) {
        // In Groups view -> Create Group
        document.getElementById('create-group-modal').classList.add('active');
        document.getElementById('new-group-name').value = '';
        document.getElementById('new-group-name').focus();
    } else {
        // In Shelf view -> Add Shelf
        showAddView();
    }
}

/**
 * Create a new group
 */
function createGroup() {
    const name = document.getElementById('new-group-name').value.trim();
    if (!name) return;
    
    fetch('/api/create_group', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name})
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            document.getElementById('create-group-modal').classList.remove('active');
            loadData(); // Reload everything
        } else {
            showError(data.error);
        }
    });
}

/**
 * Delete a group
 */
function deleteGroup(e, groupId) {
    e.stopPropagation();
    if (!confirm('Delete this group? Shelves inside will be moved to "Ungrouped".')) return;
    
    fetch(`/api/delete_group/${groupId}`, {method: 'POST'})
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            loadData();
        } else {
            showError(data.error);
        }
    });
}

/**
 * Show Move Modal
 */
function showMoveModal() {
    if (state.selectedShelves.size === 0) return;
    
    const select = document.getElementById('move-group-select');
    select.innerHTML = state.groups.map(g => 
        `<option value="${g.id}" ${g.id === state.currentGroupId ? 'disabled' : ''}>${g.name}</option>`
    ).join('');
    
    document.getElementById('move-count').textContent = state.selectedShelves.size;
    document.getElementById('move-group-modal').classList.add('active');
}

/**
 * Move Shelves
 */
function moveShelves() {
    const groupId = parseInt(document.getElementById('move-group-select').value);
    const shelves = Array.from(state.selectedShelves);
    
    fetch('/api/move_shelves', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            shelf_codes: shelves,
            group_id: groupId
        })
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            document.getElementById('move-group-modal').classList.remove('active');
            state.selectedShelves.clear();
            state.selectMode = false;
            loadData(); // Reload to reflect changes
        } else {
            showError(data.error);
        }
    });
}

/**
 * Click handler for shelf count badge: prevent parent click and navigate to searchrack
 */
function onCountClick(e, code) {
    if (e) {
        if (e.stopImmediatePropagation) e.stopImmediatePropagation();
        if (e.stopPropagation) e.stopPropagation();
        if (e.preventDefault) e.preventDefault();
    }
    if (!code) return false;
    window.location.href = `/searchrack?q=${encodeURIComponent(code)}`;
    return false;
}

/**
 * Handle shelf item click (enlarge or select based on mode)
 */
function handleShelfClick(event, code) {
    if (event && event.target && event.target.closest && event.target.closest('.shelf-count')) {
        // Click originated from the badge; don't propagate to card
        return;
    }
    if (state.selectMode) {
        console.log('Selecting shelf:', code);
        toggleShelfSelection(code);
        console.log('Selected shelves:', Array.from(state.selectedShelves));
    } else {
        enlargeShelf(code);
    }
}

/**
 * Toggle select mode on/off
 */
function toggleSelectMode() {
    state.selectMode = !state.selectMode;
    const btn = document.getElementById('select-mode-btn');
    const selectAllBtn = document.getElementById('select-all-btn');
    const deleteBtn = document.getElementById('delete-selected-btn');
    
    if (state.selectMode) {
        btn.innerHTML = '<i class="fas fa-times"></i> Cancel';
        btn.classList.add('btn-warning');
        btn.classList.remove('btn-secondary');
        selectAllBtn.style.display = 'inline-block';
    } else {
        btn.innerHTML = '<i class="fas fa-check-square"></i> Select';
        btn.classList.remove('btn-warning');
        btn.classList.add('btn-secondary');
        // Clear selections when exiting select mode
        state.selectedShelves.clear();
        selectAllBtn.style.display = 'none';
        deleteBtn.style.display = 'none';
        updatePrintButton();
    }
    
    renderShelves();
}

/**
 * Toggle shelf selection
 */
function toggleShelfSelection(code) {
    if (state.selectedShelves.has(code)) {
        state.selectedShelves.delete(code);
    } else {
        state.selectedShelves.add(code);
    }
    
    // Re-render to update visual state
    renderShelves();
    updatePrintButton();
}

/**
 * Update print button visibility and count
 */
function updatePrintButton() {
    const printBtn = document.getElementById('print-qr-btn');
    const moveBtn = document.getElementById('move-group-btn');
    const deleteBtn = document.getElementById('delete-selected-btn');
    const count = state.selectedShelves.size;
    
    if (count > 0) {
        printBtn.style.display = 'inline-block';
        moveBtn.style.display = 'inline-block';
        deleteBtn.style.display = 'inline-block';
        document.getElementById('selected-count').textContent = count;
        document.getElementById('delete-count').textContent = count;
    } else {
        printBtn.style.display = 'none';
        moveBtn.style.display = 'none';
        deleteBtn.style.display = 'none';
    }
}

/**
 * Select all shelves in current group
 */
function selectAllShelves() {
    const shelves = state.shelves.filter(s => s.group_id === state.currentGroupId);
    shelves.forEach(s => state.selectedShelves.add(s.code));
    renderShelves();
    updatePrintButton();
}

/**
 * Delete selected shelves
 */
function deleteSelectedShelves() {
    if (state.selectedShelves.size === 0) return;
    
    const count = state.selectedShelves.size;
    if (!confirm(`Delete ${count} shelf(es)? This will remove the shelf images and database entries.`)) {
        return;
    }
    
    const shelves = Array.from(state.selectedShelves);
    let completed = 0;
    
    shelves.forEach(code => {
        fetch('/api/delete_shelf', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ code: code })
        })
        .then(r => r.json())
        .then(data => {
            completed++;
            if (completed === shelves.length) {
                state.selectedShelves.clear();
                state.selectMode = false;
                loadData();
            }
        })
        .catch(err => {
            console.error('Delete failed:', err);
            completed++;
            if (completed === shelves.length) {
                state.selectedShelves.clear();
                state.selectMode = false;
                loadData();
            }
        });
    });
}

/**
 * Enlarge shelf image in modal
 */
function enlargeShelf(code) {
    console.log('Enlarging shelf:', code);
    state.currentShelfCode = code;
    const shelf = state.shelves.find(s => s.code === code);
    
    if (!shelf) {
        console.error('Shelf not found:', code);
        return;
    }
    
    document.getElementById('modal-image').src = shelf.url;
    document.getElementById('image-modal').classList.add('active');
}

/**
 * Close modal
 */
function closeModal() {
    document.getElementById('image-modal').classList.remove('active');
}

/**
 * Begin editing the currently-viewed shelf: load its image into the editor and prefill code
 */
function editShelf() {
    const code = state.currentShelfCode;
    if (!code) return;

    const shelf = state.shelves.find(s => s.code === code);
    if (!shelf) {
        showError('Shelf not found');
        return;
    }

    // Prepare editor
    state.isEditing = true;
    document.getElementById('form-title').textContent = `Edit Shelf: ${code}`;
    document.getElementById('shelf-code').value = code;
    
    // Mark code as valid immediately since we are editing
    const input = document.getElementById('shelf-code');
    input.classList.add('valid');
    input.classList.remove('invalid');
    document.getElementById('code-validation').textContent = '';

    // Load image into canvas
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => {
        const canvas = document.getElementById('editor-canvas');
        const ctx = canvas.getContext('2d');
        canvas.width = img.width;
        canvas.height = img.height;
        ctx.clearRect(0,0,canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0);
        // store current image and reset any rectangle
        state.canvas = canvas;
        state.ctx = ctx;
        state.currentImage = img;
        state.rectX = 0; state.rectY = 0; state.rectW = 0; state.rectH = 0;
        
        // Show editor, hide other sections
        document.getElementById('camera-section').style.display = 'none';
        document.getElementById('upload-section').style.display = 'none';
        const editorContainer = document.getElementById('editor-container');
        editorContainer.classList.add('active');
        editorContainer.style.display = 'block';
        
        // Show replace image button
        const replaceBtn = document.getElementById('replace-image-btn');
        if (replaceBtn) replaceBtn.style.display = 'inline-block';
        
        updateSaveButton();
    };
    img.onerror = () => showError('Failed to load shelf image into editor');
    img.src = shelf.url + '?_=' + Date.now(); // cache bust

    // Show add/edit view
    closeModal();
    document.getElementById('list-view').classList.remove('active');
    document.getElementById('add-view').classList.add('active');
}

/**
 * Replace the current image (go back to capture/upload)
 */
function replaceImage() {
    console.log('Replacing image...');
    
    // Hide editor
    const editorContainer = document.getElementById('editor-container');
    editorContainer.classList.remove('active');
    editorContainer.style.display = 'none';
    
    // Hide replace button
    document.getElementById('replace-image-btn').style.display = 'none';
    
    // Show appropriate input method
    const forceCamera = true; // Consistent with showAddView
    
    if (state.isMobile || forceCamera) {
        document.getElementById('camera-section').style.display = 'block';
        document.getElementById('upload-section').style.display = 'none';
        document.getElementById('start-camera-btn').style.display = 'block';
        // Start camera immediately
        startCapture();
    } else {
        document.getElementById('camera-section').style.display = 'none';
        document.getElementById('upload-section').style.display = 'block';
        document.getElementById('start-camera-btn').style.display = 'none';
    }
}

/**
 * Clear all inventory entries that reference the current shelf code in rack.db
 */
function clearInventory() {
    const code = state.currentShelfCode;
    if (!code) return;
    if (!confirm(`Remove shelf code '${code}' from all items in the rack database? This will clear the location for all items referencing this shelf.`)) return;

    fetch(`/api/clear_shelf_inventory/${encodeURIComponent(code)}`, { method: 'POST' })
        .then(async r => {
            const txt = await r.text();
            try {
                const data = JSON.parse(txt);
                if (data.success) {
                    showSuccess(`Cleared ${data.updated} items from inventory for shelf ${code}`);
                    closeModal();
                } else {
                    showError(data.error || 'Failed to clear inventory');
                }
            } catch (e) {
                // Not JSON - show the raw response (likely HTML error page)
                console.error('Clear inventory non-JSON response', r.status, txt.substring(0,1000));
                showError('Error clearing inventory: server returned unexpected response (see console)');
            }
        })
        .catch(err => {
            console.error('Clear inventory fetch error:', err);
            showError('Error clearing inventory: ' + err.message);
        });
}

// ============================================================================
// STEP 2: ADD SHELF FORM
// ============================================================================

/**
 * Show add shelf view
 */
function showAddView() {
    console.log('Showing add view');
    document.getElementById('list-view').classList.remove('active');
    document.getElementById('add-view').classList.add('active');
    document.getElementById('form-title').textContent = 'Add New Shelf';
    state.isEditing = false;
    resetForm();
    
    // Show appropriate input method
    // Force camera mode for testing if requested, otherwise detect mobile
    const forceCamera = true; // Set to true to test camera on desktop
    
    if (state.isMobile || forceCamera) {
        document.getElementById('camera-section').style.display = 'block';
        document.getElementById('upload-section').style.display = 'none';
        document.getElementById('start-camera-btn').style.display = 'block';
    } else {
        document.getElementById('camera-section').style.display = 'none';
        document.getElementById('upload-section').style.display = 'block';
        document.getElementById('start-camera-btn').style.display = 'none';
        setupUploadArea();
    }
    
    // Focus on shelf code input
    setTimeout(() => {
        document.getElementById('shelf-code').focus();
    }, 100);
}

/**
 * Cancel add/edit
 */
function cancelAdd() {
    console.log('Canceling add/edit');
    stopCamera();
    document.getElementById('add-view').classList.remove('active');
    document.getElementById('list-view').classList.add('active');
    resetForm();
}

/**
 * Reset form to initial state
 */
function resetForm() {
    document.getElementById('shelf-code').value = '';
    document.getElementById('code-validation').textContent = '';
    document.getElementById('shelf-code').classList.remove('valid', 'invalid');
    document.getElementById('save-btn').disabled = true;
    
    const replaceBtn = document.getElementById('replace-image-btn');
    if (replaceBtn) replaceBtn.style.display = 'none';
    
    const editorContainer = document.getElementById('editor-container');
    editorContainer.classList.remove('active');
    editorContainer.style.display = 'none';
    
    state.currentImage = null;
    state.rectX = state.rectY = state.rectW = state.rectH = 0;
    state.isEditing = false;
}

/**
 * Setup real-time code validation
 */
function setupCodeValidation() {
    const input = document.getElementById('shelf-code');
    let timeout;
    
    input.addEventListener('input', () => {
        clearTimeout(timeout);
        const code = input.value.trim();
        
        if (!code) {
            document.getElementById('code-validation').textContent = '';
            input.classList.remove('valid', 'invalid');
            updateSaveButton();
            return;
        }
        
        timeout = setTimeout(() => {
            validateCode(code);
        }, 500);
    });
}

/**
 * Validate shelf code against API
 */
function validateCode(code) {
    // If editing and code hasn't changed, it's valid
    if (state.isEditing && code === state.currentShelfCode) {
        const input = document.getElementById('shelf-code');
        const msgEl = document.getElementById('code-validation');
        input.classList.remove('invalid');
        input.classList.add('valid');
        msgEl.className = 'validation-message success';
        msgEl.innerHTML = '<i class="fas fa-check-circle"></i> Code valid (current)';
        updateSaveButton();
        return;
    }

    fetch('/api/check_shelf_code', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({code})
    })
    .then(r => r.json())
    .then(data => {
        const input = document.getElementById('shelf-code');
        const msgEl = document.getElementById('code-validation');
        
        if (data.exists) {
            input.classList.remove('valid');
            input.classList.add('invalid');
            msgEl.className = 'validation-message error';
            msgEl.innerHTML = `<i class="fas fa-times-circle"></i> ${data.reason}`;
        } else {
            input.classList.remove('invalid');
            input.classList.add('valid');
            msgEl.className = 'validation-message success';
            msgEl.innerHTML = '<i class="fas fa-check-circle"></i> Code available';
        }
        
        updateSaveButton();
    })
    .catch(err => {
        console.error('Validation error:', err);
        updateSaveButton();
    });
}

/**
 * Update save button state based on current conditions
 */
function updateSaveButton() {
    const codeInput = document.getElementById('shelf-code');
    const code = codeInput.value.trim();
    const codeValid = codeInput.classList.contains('valid');
    const hasImage = state.currentImage !== null;
    
    // Allow save if code is present and image is present
    // We relax the 'valid' check slightly to allow saving if the user insists, 
    // but ideally it should be valid.
    const canSave = code && hasImage; 
    
    const saveBtn = document.getElementById('save-btn');
    saveBtn.disabled = !canSave;
    
    console.log('Save button update:', {
        code: code,
        codeValid: codeValid,
        hasImage: hasImage,
        canSave: canSave
    });
}

/**
 * Setup upload area for desktop
 */
function setupUploadArea() {
    const uploadArea = document.getElementById('upload-area');
    const fileInput = document.getElementById('file-input');
    
    uploadArea.onclick = () => fileInput.click();
    fileInput.onchange = handleFileSelect;
}

/**
 * Handle file selection (desktop upload)
 */
function handleFileSelect(event) {
    const file = event.target.files[0];
    if (!file) return;
    
    console.log('File selected:', file.name, file.type);
    
    if (!file.type.match('image/png')) {
        showError('Only PNG images are accepted');
        return;
    }
    
    const reader = new FileReader();
    reader.onload = (e) => {
        loadImageToEditor(e.target.result);
    };
    reader.readAsDataURL(file);
}

// ============================================================================
// STEP 3: CAMERA FUNCTIONALITY
// ============================================================================

/**
 * Start camera capture (mobile)
 */
function startCapture() {
    console.log('Starting camera...');
    
    // Check if code is entered and valid first
    const codeInput = document.getElementById('shelf-code');
    const code = codeInput.value.trim();
    
    if (!code) {
        showError('Please enter a shelf code first');
        codeInput.focus();
        return;
    }
    
    if (!codeInput.classList.contains('valid')) {
        showError('Please wait for code validation or enter a different code');
        return;
    }
    
    const video = document.getElementById('camera-video');
    const cameraContainer = document.getElementById('camera-container');
    const captureBtn = document.getElementById('capture-btn');
    const startCameraBtn = document.getElementById('start-camera-btn');
    
    navigator.mediaDevices.getUserMedia({
        // Use 'user' (front) camera for desktop testing, 'environment' (back) for mobile
        video: { facingMode: state.isMobile ? "environment" : "user" },
        audio: false
    })
    .then(stream => {
        console.log('Camera stream started');
        state.cameraStream = stream;
        video.srcObject = stream;
        cameraContainer.classList.add('active');
        captureBtn.style.display = 'block';
        startCameraBtn.style.display = 'none';
    })
    .catch(err => {
        console.error('Camera error:', err);
        showError('Camera access denied: ' + err.message);
    });
}

/**
 * Capture photo from video stream
 */
function capturePhoto() {
    console.log('Capturing photo...');
    const video = document.getElementById('camera-video');
    const preview = document.getElementById('camera-preview');
    const ctx = preview.getContext('2d');
    
    // Set canvas size to match video
    preview.width = video.videoWidth;
    preview.height = video.videoHeight;
    
    // Draw current video frame to canvas
    ctx.drawImage(video, 0, 0);
    
    // Hide video, show preview
    video.style.display = 'none';
    preview.classList.add('active');
    document.getElementById('capture-btn').style.display = 'none';
    document.getElementById('camera-controls').style.display = 'flex';
    
    // Stop camera stream
    stopCamera();
}

/**
 * Retake photo
 */
function retakePhoto() {
    console.log('Retaking photo...');
    const video = document.getElementById('camera-video');
    const preview = document.getElementById('camera-preview');
    
    // Hide preview, show video
    video.style.display = 'block';
    preview.classList.remove('active');
    document.getElementById('capture-btn').style.display = 'block';
    document.getElementById('camera-controls').style.display = 'none';
    
    // Restart camera
    startCapture();
}

/**
 * Continue to rectangle editor with captured photo
 */
function continueToEditor() {
    console.log('Continuing to editor with photo...');
    const preview = document.getElementById('camera-preview');
    
    try {
        // Use toDataURL directly - it's synchronous and simpler
        const dataUrl = preview.toDataURL('image/png');
        console.log('Photo converted to data URL (length: ' + dataUrl.length + '), loading to editor...');
        
        // Hide camera UI explicitly
        document.getElementById('camera-container').classList.remove('active');
        document.getElementById('camera-controls').style.display = 'none';
        document.getElementById('camera-section').style.display = 'none'; // Hide the whole section
        
        // Stop camera to free resources
        stopCamera();
        
        // Load to editor
        // Small timeout to allow UI to update
        setTimeout(() => {
            loadImageToEditor(dataUrl);
        }, 100);
        
    } catch (e) {
        console.error('Error converting canvas to image:', e);
        showError('Failed to process photo: ' + e.message);
    }
}

/**
 * Stop camera stream and release resources
 */
function stopCamera() {
    if (state.cameraStream) {
        console.log('Stopping camera stream');
        state.cameraStream.getTracks().forEach(track => track.stop());
        state.cameraStream = null;
    }
}

// ============================================================================
// STEP 4: RECTANGLE EDITOR (Placeholder - will implement next)
// ============================================================================

/**
 * Setup canvas for rectangle drawing
 */
function setupCanvas() {
    state.canvas = document.getElementById('editor-canvas');
    state.ctx = state.canvas.getContext('2d');
    
    // Mouse events
    state.canvas.addEventListener('mousedown', startDrawing);
    state.canvas.addEventListener('mousemove', draw);
    state.canvas.addEventListener('mouseup', stopDrawing);
    
    // Touch events
    state.canvas.addEventListener('touchstart', handleTouchStart);
    state.canvas.addEventListener('touchmove', handleTouchMove);
    state.canvas.addEventListener('touchend', stopDrawing);
}

/**
 * Load image to canvas editor
 */
function loadImageToEditor(dataUrl) {
    console.log('Loading image to editor...');
    const img = new Image();
    
    img.onload = () => {
        console.log('Image loaded successfully, dimensions:', img.width, 'x', img.height);
        state.currentImage = img;
        
        // Ensure canvas context exists
        if (!state.canvas) {
            console.log('Canvas state missing, re-initializing...');
            setupCanvas();
        }
        
        // Set canvas size to fit the image
        const maxWidth = 600;
        const scale = Math.min(1, maxWidth / img.width);
        const canvasWidth = img.width * scale;
        const canvasHeight = img.height * scale;
        
        state.canvas.width = canvasWidth;
        state.canvas.height = canvasHeight;
        
        console.log('Canvas sized to:', canvasWidth, 'x', canvasHeight);
        
        // Draw image on canvas
        state.ctx.drawImage(img, 0, 0, state.canvas.width, state.canvas.height);
        
        console.log('Image drawn on canvas');
        
        // Show the editor container
        const editorContainer = document.getElementById('editor-container');
        editorContainer.style.display = 'block'; // Force display block
        editorContainer.classList.add('active');
        
        // Show replace image button if present
        const replaceBtn = document.getElementById('replace-image-btn');
        if (replaceBtn) replaceBtn.style.display = 'inline-block';
        
        // Ensure canvas is visible
        state.canvas.style.display = 'block';
        
        console.log('Editor container activated. You should now see the image and be able to draw rectangles.');
        
        // Update save button - image is now loaded
        updateSaveButton();
    };
    
    img.onerror = (e) => {
        console.error('Failed to load image:', e);
        showError('Failed to load image into editor');
    };
    
    img.src = dataUrl;
}

function startDrawing(e) {
    state.isDrawing = true;
    const rect = state.canvas.getBoundingClientRect();
    state.startX = e.clientX - rect.left;
    state.startY = e.clientY - rect.top;
}

function draw(e) {
    if (!state.isDrawing || !state.currentImage) return;
    e.preventDefault();
    
    const rect = state.canvas.getBoundingClientRect();
    const currentX = e.clientX - rect.left;
    const currentY = e.clientY - rect.top;
    
    // Redraw image
    state.ctx.drawImage(state.currentImage, 0, 0, state.canvas.width, state.canvas.height);
    
    // Draw rectangle
    state.rectW = currentX - state.startX;
    state.rectH = currentY - state.startY;
    
    state.ctx.strokeStyle = '#00ff00';
    state.ctx.lineWidth = 3;
    state.ctx.strokeRect(state.startX, state.startY, state.rectW, state.rectH);
}

function stopDrawing() {
    if (state.isDrawing) {
        state.isDrawing = false;
        state.rectX = state.startX;
        state.rectY = state.startY;
        console.log('Drawing stopped. Rectangle:', {
            x: state.rectX,
            y: state.rectY,
            w: state.rectW,
            h: state.rectH
        });
    }
}

function handleTouchStart(e) {
    e.preventDefault();
    console.log('Touch start');
    const touch = e.touches[0];
    const rect = state.canvas.getBoundingClientRect();
    state.isDrawing = true;
    state.startX = touch.clientX - rect.left;
    state.startY = touch.clientY - rect.top;
    console.log('Touch start at:', state.startX, state.startY);
}

function handleTouchMove(e) {
    if (!state.isDrawing || !state.currentImage) return;
    e.preventDefault();
    
    const touch = e.touches[0];
    const rect = state.canvas.getBoundingClientRect();
    const currentX = touch.clientX - rect.left;
    const currentY = touch.clientY - rect.top;
    
    // Redraw image
    state.ctx.drawImage(state.currentImage, 0, 0, state.canvas.width, state.canvas.height);
    
    // Draw rectangle
    state.rectW = currentX - state.startX;
    state.rectH = currentY - state.startY;
    
    state.ctx.strokeStyle = '#00ff00';
    state.ctx.lineWidth = 3;
    state.ctx.strokeRect(state.startX, state.startY, state.rectW, state.rectH);
}

// ============================================================================
// STEP 5: SAVE FUNCTIONALITY
// ============================================================================

/**
 * Save shelf to server
 */
function saveShelf() {
    console.log('=== SAVE SHELF CLICKED ===');
    
    const code = document.getElementById('shelf-code').value.trim();
    const hasImage = state.currentImage !== null;
    
    console.log('Save shelf check:', {
        code: code,
        hasImage: hasImage,
        isEditing: state.isEditing
    });
    
    if (!code || !hasImage) {
        const msg = !code ? 'Please provide a shelf code' : 'Please provide an image';
        console.error('Save blocked:', msg);
        showError(msg);
        return;
    }
    
    console.log('Preparing to save shelf:', code);
    
    // Finalize canvas with rectangle if one was drawn
    if (state.rectW && state.rectH) {
        console.log('Drawing final rectangle on canvas');
        state.ctx.strokeStyle = '#00ff00';
        state.ctx.lineWidth = 3;
        state.ctx.strokeRect(state.rectX, state.rectY, state.rectW, state.rectH);
    }
    
    console.log('Converting canvas to blob...');
    
    const saveBtn = document.getElementById('save-btn');
    const originalBtnText = saveBtn.innerHTML;
    saveBtn.disabled = true;
    saveBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';
    
    // Convert canvas to blob
    state.canvas.toBlob(blob => {
        if (!blob) {
            console.error('Failed to create blob from canvas');
            showError('Failed to process image');
            saveBtn.disabled = false;
            saveBtn.innerHTML = originalBtnText;
            return;
        }
        
        console.log('Blob created, size:', blob.size);
        const formData = new FormData();
        formData.append('image', blob, `${code}.png`);

        if (state.isEditing) {
            const oldCode = state.currentShelfCode;
            formData.append('old_code', oldCode);
            if (oldCode !== code) formData.append('new_code', code);

            console.log('Updating shelf via /api/update_shelf');
            fetch('/api/update_shelf', {
                method: 'POST',
                body: formData
            })
            .then(r => r.json())
            .then(data => {
                console.log('Update response:', data);
                if (data.success) {
                    showSuccess('Shelf updated successfully!');
                    state.isEditing = false;
                    state.currentShelfCode = '';
                    cancelAdd();
                    loadData();
                } else {
                    showError(data.error || 'Failed to update shelf');
                    saveBtn.disabled = false;
                    saveBtn.innerHTML = originalBtnText;
                }
            })
            .catch(err => {
                console.error('Update error:', err);
                showError('Error updating shelf: ' + err.message);
                saveBtn.disabled = false;
                saveBtn.innerHTML = originalBtnText;
            });
        } else {
            formData.append('code', code);
            // Add group_id if we are inside a group
            if (state.currentGroupId) {
                formData.append('group_id', state.currentGroupId);
                console.log('Adding to group:', state.currentGroupId);
            }
            
            console.log('Creating new shelf via /api/upload_shelf');
            fetch('/api/upload_shelf', {
                method: 'POST',
                body: formData
            })
            .then(r => {
                console.log('Upload response status:', r.status);
                return r.json();
            })
            .then(data => {
                console.log('Upload response data:', data);
                if (data.success) {
                    showSuccess('Shelf saved successfully!');
                    cancelAdd();
                    loadData();
                } else {
                    showError(data.error || 'Failed to save shelf');
                    saveBtn.disabled = false;
                    saveBtn.innerHTML = originalBtnText;
                }
            })
            .catch(err => {
                console.error('Save error:', err);
                showError('Error saving shelf: ' + err.message);
                saveBtn.disabled = false;
                saveBtn.innerHTML = originalBtnText;
            });
        }
    }, 'image/png');
}

// ============================================================================
// STEP 6: EDIT/DELETE FUNCTIONALITY
// ============================================================================

function deleteShelf() {
    const code = state.currentShelfCode;
    if (!code) return;
    
    if (!confirm(`Delete shelf ${code}? This cannot be undone.`)) return;
    
    console.log('Deleting shelf:', code);
    
    fetch('/api/delete_shelf', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ code: code })
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            showSuccess('Shelf deleted');
            closeModal();
            loadData();
        } else {
            showError(data.error || 'Failed to delete shelf');
        }
    })
    .catch(err => {
        console.error('Delete error:', err);
        showError('Error: ' + err.message);
    });
}

// ============================================================================
// QR CODE PRINTING
// ============================================================================

/**
 * Print QR codes for selected shelves
 */
function printQRCodes() {
    if (state.selectedShelves.size === 0) {
        showError('No shelves selected');
        return;
    }
    
    console.log('Generating QR codes for:', Array.from(state.selectedShelves));
    
    // Create print window
    const printWindow = window.open('', '_blank');
    
    // Build HTML for print page
    const html = `
        <!DOCTYPE html>
        <html>
        <head>
            <title>Shelf QR Codes</title>
            <style>
                @page {
                    size: letter;
                    margin: 0.5in;
                }
                
                * {
                    margin: 0;
                    padding: 0;
                    box-sizing: border-box;
                }
                
                body {
                    font-family: Arial, sans-serif;
                    background: white;
                }
                
                .qr-grid {
                    display: grid;
                    grid-template-columns: repeat(4, 2in);
                    gap: 0.25in;
                    padding: 0;
                }
                
                .qr-item {
                    width: 2in;
                    height: 2.5in;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                    page-break-inside: avoid;
                    border: 1px dashed #ccc;
                    padding: 0.1in;
                }
                
                .qr-code {
                    width: 2in;
                    height: 2in;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                }
                
                .qr-code canvas {
                    max-width: 100%;
                    max-height: 100%;
                }
                
                .qr-label {
                    margin-top: 0.1in;
                    font-size: 14pt;
                    font-weight: bold;
                    text-align: center;
                    color: #000;
                }
                
                @media print {
                    .qr-item {
                        border: none;
                    }
                }
            </style>
            <script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
        </head>
        <body>
            <div class="qr-grid" id="qr-grid"></div>
            <script>
                const codes = ${JSON.stringify(Array.from(state.selectedShelves))};
                const grid = document.getElementById('qr-grid');
                
                codes.forEach(code => {
                    // Create item container
                    const item = document.createElement('div');
                    item.className = 'qr-item';
                    
                    // Create QR code container
                    const qrDiv = document.createElement('div');
                    qrDiv.className = 'qr-code';
                    qrDiv.id = 'qr-' + code;
                    
                    // Create label
                    const label = document.createElement('div');
                    label.className = 'qr-label';
                    label.textContent = code;
                    
                    item.appendChild(qrDiv);
                    item.appendChild(label);
                    grid.appendChild(item);
                    
                    // Generate QR code
                    new QRCode(qrDiv, {
                        text: code,
                        width: 192,  // 2 inches at 96 DPI
                        height: 192,
                        colorDark: '#000000',
                        colorLight: '#ffffff',
                        correctLevel: QRCode.CorrectLevel.H
                    });
                });
                
                // Auto-print after QR codes are generated
                setTimeout(() => {
                    window.print();
                }, 500);
            </script>
        </body>
        </html>
    `;
    
    printWindow.document.write(html);
    printWindow.document.close();
}

// ============================================================================
// UTILITY FUNCTIONS
// ============================================================================

function showError(msg) {
    alert('❌ ' + msg);
}

function showSuccess(msg) {
    alert('✅ ' + msg);
}

// ============================================================================
// INITIALIZE ON DOM READY
// ============================================================================

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}

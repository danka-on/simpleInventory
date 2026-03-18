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
    isDragging: false,
    isResizing: false,
    dragHandle: null, // 'move', 'nw', 'ne', 'sw', 'se'
    dragStartX: 0,
    dragStartY: 0,
    startX: 0,
    startY: 0,
    rectX: 0,
    rectY: 0,
    rectW: 0,
    rectH: 0,
    rectRotation: 0,
    handleSize: 40, // Size of corner handles for touch
    imageLoadToken: 0,
    pendingContinueTimer: null,
    cameraStarting: false,
    isSaving: false,
    saveRequestToken: 0,
    saveAbortController: null,
    saveTimeoutId: null,
    defaultSaveBtnHtml: ''
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

    const saveBtn = document.getElementById('save-btn');
    if (saveBtn) {
        state.defaultSaveBtnHtml = saveBtn.innerHTML;
    }
    
    loadData();
    setupEventListeners();
    setupCanvas();
}

function invalidatePendingImageWork() {
    state.imageLoadToken += 1;
    if (state.pendingContinueTimer) {
        clearTimeout(state.pendingContinueTimer);
        state.pendingContinueTimer = null;
    }
}

function invalidateSaveFlow() {
    state.saveRequestToken += 1;
    if (state.saveTimeoutId) {
        clearTimeout(state.saveTimeoutId);
        state.saveTimeoutId = null;
    }
    if (state.saveAbortController) {
        try {
            state.saveAbortController.abort();
        } catch (err) {
            console.warn('Abort save failed:', err);
        }
        state.saveAbortController = null;
    }
    state.isSaving = false;
}

function resetCameraPreviewUI() {
    const video = document.getElementById('camera-video');
    const preview = document.getElementById('camera-preview');
    const cameraContainer = document.getElementById('camera-container');
    const cameraControls = document.getElementById('camera-controls');
    const captureBtn = document.getElementById('capture-btn');

    if (video) {
        video.style.display = 'block';
        video.srcObject = null;
    }
    if (preview) {
        const pctx = preview.getContext('2d');
        if (pctx) pctx.clearRect(0, 0, preview.width, preview.height);
        preview.classList.remove('active');
        preview.width = 0;
        preview.height = 0;
    }
    if (cameraContainer) cameraContainer.classList.remove('active');
    if (cameraControls) cameraControls.style.display = 'none';
    if (captureBtn) captureBtn.style.display = 'none';
}

function resetSaveButtonUI() {
    const saveBtn = document.getElementById('save-btn');
    if (!saveBtn) return;

    const defaultText = state.defaultSaveBtnHtml || '<i class="fas fa-save"></i> Save Shelf';
    saveBtn.innerHTML = defaultText;
    saveBtn.disabled = true;
}

function getShelfImageUrl(shelf, extraVersion = '') {
    if (!shelf || !shelf.url) return '';
    const baseUrl = String(shelf.url);
    const params = new URLSearchParams();
    const cacheVersion = String((shelf.cacheVersion ?? shelf.lastModified ?? '') || '').trim();
    if (cacheVersion) params.set('_v', cacheVersion);
    const extra = String(extraVersion || '').trim();
    if (extra) params.set('_t', extra);
    const query = params.toString();
    if (!query) return baseUrl;
    return `${baseUrl}${baseUrl.includes('?') ? '&' : '?'}${query}`;
}

async function parseJsonResponse(response) {
    const text = await response.text();
    if (!text) return {};
    try {
        return JSON.parse(text);
    } catch (err) {
        throw new Error(`Server returned non-JSON response (${response.status})`);
    }
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
    
    const deleteSelectedBtn = document.getElementById('delete-selected-btn');
    deleteSelectedBtn.addEventListener('click', deleteSelectedShelves);
    deleteSelectedBtn.addEventListener('touchend', (e) => {
        e.preventDefault();
        deleteSelectedShelves();
    });
    
    // Add/Edit view
    document.getElementById('cancel-btn').addEventListener('click', cancelAdd);
    document.getElementById('save-btn').addEventListener('click', saveShelf);
    
    // Redraw button
    const redrawBtn = document.getElementById('redraw-rect-btn');
    if (redrawBtn) redrawBtn.addEventListener('click', resetRect);

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
    
    const duplicateShelfBtn = document.getElementById('duplicate-shelf-btn');
    if (duplicateShelfBtn) {
        duplicateShelfBtn.addEventListener('click', duplicateShelf);
        duplicateShelfBtn.addEventListener('touchend', (e) => {
            e.preventDefault();
            duplicateShelf();
        });
    }
    
    const deleteShelfBtn = document.getElementById('delete-shelf-btn');
    deleteShelfBtn.addEventListener('click', deleteShelf);
    // Add touch listener for better mobile response
    deleteShelfBtn.addEventListener('touchend', (e) => {
        e.preventDefault();
        deleteShelf();
    });

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
        if (sortBy === 'created') {
            // created_at is string "YYYY-MM-DD HH:MM:SS", lastModified is seconds
            const getTimestamp = (item) => {
                if (item.created_at) {
                    // Replace space with T for better cross-browser parsing
                    return new Date(item.created_at.replace(' ', 'T')).getTime();
                }
                return (item.lastModified || 0) * 1000;
            };
            return getTimestamp(b) - getTimestamp(a);
        }
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
            <img src="${getShelfImageUrl(shelf)}" alt="${shelf.code}" loading="lazy">
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
    
    document.getElementById('modal-image').src = getShelfImageUrl(shelf);
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

    invalidatePendingImageWork();
    stopCamera();
    resetCameraPreviewUI();

    // Prepare editor
    state.isEditing = true;
    document.getElementById('form-title').textContent = `Edit Shelf: ${code}`;
    document.getElementById('shelf-code').value = code;
    const originalOnlyCheckbox = document.getElementById('original-only');
    if (originalOnlyCheckbox) originalOnlyCheckbox.checked = false;
    state.currentImage = null;
    state.rectX = 0;
    state.rectY = 0;
    state.rectW = 0;
    state.rectH = 0;
    state.rectRotation = 0;
    updateSaveButton();
    
    // Mark code as valid immediately since we are editing
    const input = document.getElementById('shelf-code');
    input.classList.add('valid');
    input.classList.remove('invalid');
    document.getElementById('code-validation').textContent = '';

    // Load image into canvas
    const imageToken = state.imageLoadToken;
    const img = new Image();
    img.crossOrigin = 'anonymous';
    
    img.onload = () => {
        if (imageToken !== state.imageLoadToken) {
            console.log('Ignoring stale edit image load for:', code);
            return;
        }

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
        state.rectRotation = 0;
        
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
    
    img.onerror = () => {
        if (imageToken !== state.imageLoadToken) return;
        showError('Failed to load shelf image into editor');
    };
    
    // Load standard image (with baked-in rectangle)
    console.log('Loading standard image for editing');
    img.src = getShelfImageUrl(shelf, Date.now());

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

    invalidatePendingImageWork();
    stopCamera();
    resetCameraPreviewUI();
    state.currentImage = null;
    state.rectX = 0;
    state.rectY = 0;
    state.rectW = 0;
    state.rectH = 0;
    state.rectRotation = 0;
    updateSaveButton();
    
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
    if (state.isSaving) {
        console.warn('Cancel requested while save in progress, aborting save');
        invalidateSaveFlow();
        resetSaveButtonUI();
    }

    stopCamera();
    document.getElementById('add-view').classList.remove('active');
    document.getElementById('list-view').classList.add('active');
    resetForm();
}

/**
 * Reset form to initial state
 */
function resetForm() {
    invalidatePendingImageWork();
    invalidateSaveFlow();
    document.getElementById('shelf-code').value = '';
    const originalOnlyCheckbox = document.getElementById('original-only');
    if (originalOnlyCheckbox) originalOnlyCheckbox.checked = false;
    document.getElementById('code-validation').textContent = '';
    document.getElementById('shelf-code').classList.remove('valid', 'invalid');
    resetSaveButtonUI();
    
    const replaceBtn = document.getElementById('replace-image-btn');
    if (replaceBtn) replaceBtn.style.display = 'none';
    
    const editorContainer = document.getElementById('editor-container');
    editorContainer.classList.remove('active');
    editorContainer.style.display = 'none';
    
    resetCameraPreviewUI();
    state.currentImage = null;
    state.rectX = state.rectY = state.rectW = state.rectH = 0;
    state.rectRotation = 0;
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
    saveBtn.disabled = state.isSaving || !canSave;
    
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

    if (state.cameraStarting) {
        console.log('Camera start already in progress');
        return;
    }
    
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

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        showError('Camera not supported on this device/browser');
        return;
    }

    stopCamera();
    resetCameraPreviewUI();
    state.cameraStarting = true;
    
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
        
        // Scroll to camera view
        setTimeout(() => {
            cameraContainer.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }, 100);
    })
    .catch(err => {
        console.error('Camera error:', err);
        showError('Camera access denied: ' + err.message);
    })
    .finally(() => {
        state.cameraStarting = false;
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
    const pctx = preview.getContext('2d');
    if (pctx) pctx.clearRect(0, 0, preview.width, preview.height);
    preview.width = 0;
    preview.height = 0;
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

    if (!preview.width || !preview.height) {
        showError('Please capture a photo first');
        return;
    }
    
    try {
        // Use toDataURL directly - it's synchronous and simpler
        const dataUrl = preview.toDataURL('image/png');
        console.log('Photo converted to data URL (length: ' + dataUrl.length + '), loading to editor...');

        const imageToken = ++state.imageLoadToken;
        
        // Hide camera UI explicitly
        document.getElementById('camera-container').classList.remove('active');
        document.getElementById('camera-controls').style.display = 'none';
        document.getElementById('camera-section').style.display = 'none'; // Hide the whole section
        
        // Stop camera to free resources
        stopCamera();
        
        // Load to editor
        // Small timeout to allow UI to update
        if (state.pendingContinueTimer) {
            clearTimeout(state.pendingContinueTimer);
        }
        state.pendingContinueTimer = setTimeout(() => {
            state.pendingContinueTimer = null;
            loadImageToEditor(dataUrl, imageToken);
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
    state.cameraStarting = false;
    if (state.cameraStream) {
        console.log('Stopping camera stream');
        state.cameraStream.getTracks().forEach(track => track.stop());
        state.cameraStream = null;
    }

    const video = document.getElementById('camera-video');
    if (video) video.srcObject = null;
}

// ============================================================================
// STEP 4: RECTANGLE EDITOR
// ============================================================================

/**
 * Reset the rectangle to allow drawing a new one
 */
function buildOriginalImageCandidates(code) {
    const raw = String(code || '').trim();
    const compact = raw.replace(/\s+/g, '');
    const lower = compact.toLowerCase();
    const upper = compact.toUpperCase();
    const candidates = [];
    const add = (src) => {
        if (src && !candidates.includes(src)) candidates.push(src);
    };

    add(`/shelf-original/${encodeURIComponent(lower)}.png`);
    add(`/shelf-original/${encodeURIComponent(upper)}.png`);
    add(`/shelf-original/${encodeURIComponent(raw)}.png`);
    add(`/shelf-base/${encodeURIComponent(lower)}.png`);
    add(`/shelf-base/${encodeURIComponent(upper)}.png`);
    add(`/shelf-base/${encodeURIComponent(raw)}.png`);

    return candidates;
}

function resetRect() {
    console.log('Resetting rectangle');
    state.rectX = 0;
    state.rectY = 0;
    state.rectW = 0;
    state.rectH = 0;
    state.rectRotation = 0;

    // If we are editing, try to load the original clean image
    if (state.isEditing && state.currentShelfCode) {
        const imageToken = state.imageLoadToken;
        const originalUrls = buildOriginalImageCandidates(state.currentShelfCode);
        const tryNext = (index) => {
            if (index >= originalUrls.length) {
                if (imageToken !== state.imageLoadToken) return;
                console.log('No original image found, sticking with current image');
                redrawCanvas();
                return;
            }

            const img = new Image();
            img.crossOrigin = 'anonymous';
            img.onload = () => {
                if (imageToken !== state.imageLoadToken) return;
                console.log('Loaded original clean image for redraw');
                state.currentImage = img;
                redrawCanvas();
            };
            img.onerror = () => {
                if (imageToken !== state.imageLoadToken) return;
                tryNext(index + 1);
            };
            img.src = originalUrls[index] + '?_=' + Date.now();
        };

        tryNext(0);
    } else {
        redrawCanvas();
    }
}

/**
 * Setup canvas for rectangle drawing with move/resize
 */
function setupCanvas() {
    state.canvas = document.getElementById('editor-canvas');
    state.ctx = state.canvas.getContext('2d');
    
    // Mouse events
    state.canvas.addEventListener('mousedown', handleMouseDown);
    state.canvas.addEventListener('mousemove', handleMouseMove);
    state.canvas.addEventListener('mouseup', handleMouseUp);
    state.canvas.addEventListener('mouseout', handleMouseUp);
    
    // Touch events
    state.canvas.addEventListener('touchstart', handleTouchStart, { passive: false });
    state.canvas.addEventListener('touchmove', handleTouchMove, { passive: false });
    state.canvas.addEventListener('touchend', handleTouchEnd, { passive: false });
}

/**
 * Check if point is inside rectangle
 */
function isInsideRect(x, y) {
    const rx = state.rectX;
    const ry = state.rectY;
    const rw = state.rectW;
    const rh = state.rectH;
    
    // Center
    const cx = rx + rw / 2;
    const cy = ry + rh / 2;
    
    // Rotate point around center by -rotation
    const dx = x - cx;
    const dy = y - cy;
    const cos = Math.cos(-state.rectRotation);
    const sin = Math.sin(-state.rectRotation);
    const lx = cx + dx * cos - dy * sin;
    const ly = cy + dx * sin + dy * cos;
    
    // Normalize negative dimensions
    const minX = rw >= 0 ? rx : rx + rw;
    const maxX = rw >= 0 ? rx + rw : rx;
    const minY = rh >= 0 ? ry : ry + rh;
    const maxY = rh >= 0 ? ry + rh : ry;
    
    return lx >= minX && lx <= maxX && ly >= minY && ly <= maxY;
}

/**
 * Get which handle (if any) is being touched
 */
function getHandle(x, y) {
    const h = state.handleSize;
    const rx = state.rectX;
    const ry = state.rectY;
    const rw = state.rectW;
    const rh = state.rectH;
    
    // Center
    const cx = rx + rw / 2;
    const cy = ry + rh / 2;
    
    // Rotate point around center by -rotation
    const dx = x - cx;
    const dy = y - cy;
    const cos = Math.cos(-state.rectRotation);
    const sin = Math.sin(-state.rectRotation);
    const lx = cx + dx * cos - dy * sin;
    const ly = cy + dx * sin + dy * cos;
    
    // Normalize rectangle coordinates to handle negative dimensions
    const minX = rw >= 0 ? rx : rx + rw;
    const maxX = rw >= 0 ? rx + rw : rx;
    const minY = rh >= 0 ? ry : ry + rh;
    const maxY = rh >= 0 ? ry + rh : ry;
    
    // Check rotation handle (top center)
    if (Math.abs(rw) > 10 && Math.abs(rh) > 10) {
         if (Math.abs(lx - cx) < h && Math.abs(ly - (minY - 30)) < h) return 'rotate';
    }
    
    // Check corners first (for resizing)
    if (Math.abs(lx - minX) < h && Math.abs(ly - minY) < h) return 'nw';
    if (Math.abs(lx - maxX) < h && Math.abs(ly - minY) < h) return 'ne';
    if (Math.abs(lx - minX) < h && Math.abs(ly - maxY) < h) return 'sw';
    if (Math.abs(lx - maxX) < h && Math.abs(ly - maxY) < h) return 'se';
    
    // Check if inside rect (for moving) - only if rect is reasonably sized
    if (Math.abs(rw) > h * 2 && Math.abs(rh) > h * 2) {
        // We already calculated rotated point lx, ly
        if (lx >= minX && lx <= maxX && ly >= minY && ly <= maxY) return 'move';
    }
    
    return null;
}

/**
 * Redraw canvas with image and rectangle
 */
function redrawCanvas(hideHandles = false) {
    if (!state.currentImage) return;
    
    // Clear and redraw image
    state.ctx.clearRect(0, 0, state.canvas.width, state.canvas.height);
    state.ctx.drawImage(state.currentImage, 0, 0, state.canvas.width, state.canvas.height);
    
    // Draw rectangle if it exists
    if (state.rectW !== 0 || state.rectH !== 0) {
        const rx = state.rectX;
        const ry = state.rectY;
        const rw = state.rectW;
        const rh = state.rectH;
        const cx = rx + rw / 2;
        const cy = ry + rh / 2;
        
        state.ctx.save();
        state.ctx.translate(cx, cy);
        state.ctx.rotate(state.rectRotation);
        state.ctx.translate(-cx, -cy);
        
        // Draw rectangle
        state.ctx.strokeStyle = '#00ff00';
        state.ctx.lineWidth = 3;
        state.ctx.strokeRect(rx, ry, rw, rh);
        
        // Draw corner handles (only when not actively drawing)
        if (!hideHandles && !state.isDrawing && Math.abs(rw) > 10 && Math.abs(rh) > 10) {
            const h = 12; // Visual handle size
            state.ctx.fillStyle = '#00ff00';
            
            // Normalize coordinates for handles
            const minX = rw >= 0 ? rx : rx + rw;
            const maxX = rw >= 0 ? rx + rw : rx;
            const minY = rh >= 0 ? ry : ry + rh;
            const maxY = rh >= 0 ? ry + rh : ry;
            
            // Top-left
            state.ctx.fillRect(minX - h/2, minY - h/2, h, h);
            // Top-right
            state.ctx.fillRect(maxX - h/2, minY - h/2, h, h);
            // Bottom-left
            state.ctx.fillRect(minX - h/2, maxY - h/2, h, h);
            // Bottom-right
            state.ctx.fillRect(maxX - h/2, maxY - h/2, h, h);

            // Rotation handle
            state.ctx.beginPath();
            state.ctx.moveTo(cx, minY);
            state.ctx.lineTo(cx, minY - 30);
            state.ctx.strokeStyle = '#00ff00';
            state.ctx.stroke();
            
            state.ctx.beginPath();
            state.ctx.arc(cx, minY - 30, 6, 0, Math.PI * 2);
            state.ctx.fill();
        }
        state.ctx.restore();
    }
}

/**
 * Start action (draw/drag/resize)
 */
function startAction(x, y) {
    console.log('Action start at:', x, y);
    
    // Only check for existing rectangle handles if we have a valid rectangle
    const hasValidRect = state.rectW !== 0 && state.rectH !== 0 && 
                         Math.abs(state.rectW) > 5 && Math.abs(state.rectH) > 5;
    
    if (hasValidRect) {
        const handle = getHandle(x, y);
        if (handle) {
            state.dragHandle = handle;
            state.isDragging = handle === 'move';
            state.isResizing = handle !== 'move';
            state.dragStartX = x;
            state.dragStartY = y;
            return;
        }
        // If we have a valid rect, prevent drawing a new one unless explicitly reset
        return;
    }
    
    // Start drawing new rectangle
    state.isDrawing = true;
    state.startX = x;
    state.startY = y;
    state.rectX = x;
    state.rectY = y;
    state.rectW = 0;
    state.rectH = 0;
    state.rectRotation = 0;
}

/**
 * Move action
 */
function moveAction(x, y) {
    if (state.isDrawing) {
        state.rectW = x - state.startX;
        state.rectH = y - state.startY;
        redrawCanvas();
    } else if (state.isDragging) {
        const dx = x - state.dragStartX;
        const dy = y - state.dragStartY;
        state.rectX += dx;
        state.rectY += dy;
        state.dragStartX = x;
        state.dragStartY = y;
        redrawCanvas();
    } else if (state.isResizing) {
        if (state.dragHandle === 'rotate') {
            const cx = state.rectX + state.rectW / 2;
            const cy = state.rectY + state.rectH / 2;
            const angle = Math.atan2(y - cy, x - cx);
            state.rectRotation = angle + Math.PI / 2;
            redrawCanvas();
            return;
        }

        const dx = x - state.dragStartX;
        const dy = y - state.dragStartY;
        
        // Rotate delta into local space
        const cos = Math.cos(-state.rectRotation);
        const sin = Math.sin(-state.rectRotation);
        const ldx = dx * cos - dy * sin;
        const ldy = dx * sin + dy * cos;
        
        switch (state.dragHandle) {
            case 'nw': state.rectX += ldx; state.rectY += ldy; state.rectW -= ldx; state.rectH -= ldy; break;
            case 'ne': state.rectY += ldy; state.rectW += ldx; state.rectH -= ldy; break;
            case 'sw': state.rectX += ldx; state.rectW -= ldx; state.rectH += ldy; break;
            case 'se': state.rectW += ldx; state.rectH += ldy; break;
        }
        
        state.dragStartX = x;
        state.dragStartY = y;
        redrawCanvas();
    } else {
        // Cursor updates (only relevant for mouse usually)
        const handle = getHandle(x, y);
        if (handle === 'move') state.canvas.style.cursor = 'move';
        else if (handle === 'rotate') state.canvas.style.cursor = 'alias';
        else if (handle) state.canvas.style.cursor = (handle === 'nw' || handle === 'se') ? 'nwse-resize' : 'nesw-resize';
        else state.canvas.style.cursor = 'crosshair';
    }
}

/**
 * End action
 */
function endAction() {
    if (state.isDrawing) {
        state.isDrawing = false;
        // Normalize negative width/height
        if (state.rectW < 0) { state.rectX += state.rectW; state.rectW = Math.abs(state.rectW); }
        if (state.rectH < 0) { state.rectY += state.rectH; state.rectH = Math.abs(state.rectH); }
        redrawCanvas();
    }
    state.isDragging = false;
    state.isResizing = false;
    state.dragHandle = null;
}

// ============================================================================
// EVENT HANDLERS
// ============================================================================

/**
 * Get canvas coordinates from screen coordinates, accounting for CSS scaling
 */
function getCanvasCoordinates(clientX, clientY) {
    const rect = state.canvas.getBoundingClientRect();
    const scaleX = state.canvas.width / rect.width;
    const scaleY = state.canvas.height / rect.height;
    
    return {
        x: (clientX - rect.left) * scaleX,
        y: (clientY - rect.top) * scaleY
    };
}

function handleMouseDown(e) {
    const coords = getCanvasCoordinates(e.clientX, e.clientY);
    startAction(coords.x, coords.y);
}

function handleMouseMove(e) {
    const coords = getCanvasCoordinates(e.clientX, e.clientY);
    moveAction(coords.x, coords.y);
}

function handleMouseUp(e) {
    endAction();
}

function handleTouchStart(e) {
    e.preventDefault();
    if (e.touches.length > 1) return; // Ignore multi-touch
    const touch = e.touches[0];
    const coords = getCanvasCoordinates(touch.clientX, touch.clientY);
    startAction(coords.x, coords.y);
}

function handleTouchMove(e) {
    e.preventDefault();
    if (e.touches.length > 1) return;
    const touch = e.touches[0];
    const coords = getCanvasCoordinates(touch.clientX, touch.clientY);
    moveAction(coords.x, coords.y);
}

function handleTouchEnd(e) {
    e.preventDefault();
    endAction();
}

/**
 * Load image to canvas editor
 */
function loadImageToEditor(dataUrl, imageToken = state.imageLoadToken) {
    console.log('Loading image to editor...');
    const img = new Image();
    
    img.onload = () => {
        if (imageToken !== state.imageLoadToken) {
            console.log('Ignoring stale image load');
            return;
        }

        console.log('Image loaded successfully, dimensions:', img.width, 'x', img.height);
        state.currentImage = img;
        state.rectX = 0;
        state.rectY = 0;
        state.rectW = 0;
        state.rectH = 0;
        state.rectRotation = 0;
        
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
        
        // Scroll to editor
        setTimeout(() => {
            editorContainer.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }, 100);
        
        // Show replace image button if present
        const replaceBtn = document.getElementById('replace-image-btn');
        if (replaceBtn) replaceBtn.style.display = 'inline-block';
        
        // Ensure canvas is visible and has crosshair cursor
        state.canvas.style.display = 'block';
        state.canvas.style.cursor = 'crosshair';
        
        console.log('Editor container activated. You should now see the image and be able to draw/move rectangles.');
        
        // Update save button - image is now loaded
        updateSaveButton();
    };
    
    img.onerror = (e) => {
        if (imageToken !== state.imageLoadToken) return;
        console.error('Failed to load image:', e);
        showError('Failed to load image into editor');
    };
    
    img.src = dataUrl;
}



// ============================================================================
// STEP 5: SAVE FUNCTIONALITY
// ============================================================================

/**
 * Save shelf to server
 */
function saveShelf() {
    console.log('=== SAVE SHELF CLICKED ===');

    if (state.isSaving) {
        console.log('Save ignored: save already in progress');
        return;
    }
    
    const code = document.getElementById('shelf-code').value.trim();
    const hasImage = state.currentImage !== null;
    const originalOnly = !!(document.getElementById('original-only') && document.getElementById('original-only').checked);
    
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

    if (!state.canvas || !state.ctx) {
        showError('Image editor is not ready. Please retake the image.');
        return;
    }
    
    console.log('Preparing to save shelf:', code);
    
    // Finalize canvas with rectangle if one was drawn
    try {
        if (state.rectW && state.rectH) {
            console.log('Drawing final rectangle on canvas');
            redrawCanvas(true); // Use redraw to ensure handles are not included in final image
        }
    } catch (e) {
        console.error('Error redrawing canvas:', e);
    }
    
    console.log('Converting canvas to blob...');
    
    const saveBtn = document.getElementById('save-btn');
    const saveToken = ++state.saveRequestToken;
    state.isSaving = true;
    saveBtn.disabled = true;
    saveBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';

    const finishSave = (restoreButton = true) => {
        if (saveToken !== state.saveRequestToken) return;

        if (state.saveTimeoutId) {
            clearTimeout(state.saveTimeoutId);
            state.saveTimeoutId = null;
        }
        state.saveAbortController = null;
        state.isSaving = false;

        if (restoreButton) {
            const defaultText = state.defaultSaveBtnHtml || '<i class="fas fa-save"></i> Save Shelf';
            saveBtn.innerHTML = defaultText;
            updateSaveButton();
        }
    };

    const controller = new AbortController();
    state.saveAbortController = controller;

    // Safety timeout in case toBlob or fetch hangs indefinitely
    state.saveTimeoutId = setTimeout(() => {
        if (saveToken !== state.saveRequestToken) return;
        console.error('Save operation timed out');
        try {
            controller.abort();
        } catch (abortErr) {
            console.warn('Failed to abort timed out save request:', abortErr);
        }
        showError('Save operation timed out. Please try again.');
        finishSave(true);
    }, 90000); // 90 seconds for slower mobile/Pi uploads

    // Helper to get a clean shelf image blob matching the editor canvas size.
    // This keeps uploads small instead of sending a full camera-resolution PNG.
    const getOriginalBlob = () => {
        return new Promise(resolve => {
            if (!state.currentImage) return resolve(null);
            try {
                const tempCanvas = document.createElement('canvas');
                const targetWidth = Math.max(1, Math.round(state.canvas?.width || state.currentImage.width || 0));
                const targetHeight = Math.max(1, Math.round(state.canvas?.height || state.currentImage.height || 0));
                tempCanvas.width = targetWidth;
                tempCanvas.height = targetHeight;
                const ctx = tempCanvas.getContext('2d');
                ctx.drawImage(state.currentImage, 0, 0, targetWidth, targetHeight);
                tempCanvas.toBlob(blob => resolve(blob), 'image/png');
            } catch (e) {
                console.error('Error creating original blob:', e);
                resolve(null);
            }
        });
    };

    const applySaveSuccess = (message) => {
        finishSave(false);
        showSuccess(message);
        state.isEditing = false;
        state.currentShelfCode = '';
        cancelAdd();
        loadData();
    };

    // Build a cropped blob if the user drew a rectangle; otherwise use full canvas.
    const getCroppedBlob = () => new Promise(resolve => {
        try {
            const hasRect = state.rectW !== 0 && state.rectH !== 0 && state.currentImage;
            if (!hasRect) {
                state.canvas.toBlob(b => resolve(b), 'image/png');
                return;
            }
            const rw = Math.abs(state.rectW);
            const rh = Math.abs(state.rectH);
            const rx = state.rectW >= 0 ? state.rectX : state.rectX + state.rectW;
            const ry = state.rectH >= 0 ? state.rectY : state.rectY + state.rectH;
            const cx = rx + rw / 2;
            const cy = ry + rh / 2;
            // Scale canvas display coords → source image coords
            const scaleX = (state.currentImage.naturalWidth || state.currentImage.width) / state.canvas.width;
            const scaleY = (state.currentImage.naturalHeight || state.currentImage.height) / state.canvas.height;
            const cropW = Math.max(1, Math.round(rw * scaleX));
            const cropH = Math.max(1, Math.round(rh * scaleY));
            const imgCX = cx * scaleX;
            const imgCY = cy * scaleY;
            const imgW = state.currentImage.naturalWidth || state.currentImage.width;
            const imgH = state.currentImage.naturalHeight || state.currentImage.height;
            const tempCanvas = document.createElement('canvas');
            tempCanvas.width = cropW;
            tempCanvas.height = cropH;
            const tCtx = tempCanvas.getContext('2d');
            tCtx.save();
            tCtx.translate(cropW / 2, cropH / 2);
            tCtx.rotate(-state.rectRotation);
            tCtx.drawImage(state.currentImage, -imgCX, -imgCY, imgW, imgH);
            tCtx.restore();
            tempCanvas.toBlob(b => resolve(b || null), 'image/png');
        } catch (e) {
            console.error('Crop error, falling back to full canvas:', e);
            state.canvas.toBlob(b => resolve(b), 'image/png');
        }
    });

    try {
        // Convert canvas to blob (cropped to rect if one was drawn)
        getCroppedBlob().then(async blob => {
            try {
                if (saveToken !== state.saveRequestToken) return;

                if (!blob) {
                    console.error('Failed to create blob from canvas');
                    showError('Failed to process image');
                    finishSave(true);
                    return;
                }
                
                console.log('Blob created, size:', blob.size);
                const formData = new FormData();
                const origBlob = await getOriginalBlob();
                const uploadBlob = (originalOnly && origBlob) ? origBlob : blob;
                formData.append('image', uploadBlob, `${code}.png`);
                
                // Also save original image (clean)
                if (origBlob) {
                    console.log('Original blob created, size:', origBlob.size);
                    formData.append('original_image', origBlob, `${code}.png`);
                }
                if (originalOnly) {
                    formData.append('original_only', '1');
                }

                let endpoint = '/api/upload_shelf';
                let successMessage = originalOnly ? 'Original saved successfully!' : 'Shelf saved successfully!';

                if (state.isEditing) {
                    const oldCode = state.currentShelfCode;
                    formData.append('old_code', oldCode);
                    if (oldCode !== code) formData.append('new_code', code);
                    endpoint = '/api/update_shelf';
                    successMessage = originalOnly ? 'Original updated successfully!' : 'Shelf updated successfully!';
                    console.log('Updating shelf via /api/update_shelf');
                } else {
                    formData.append('code', code);
                    if (state.currentGroupId) {
                        formData.append('group_id', state.currentGroupId);
                        console.log('Adding to group:', state.currentGroupId);
                    }
                    console.log('Creating new shelf via /api/upload_shelf');
                }

                const response = await fetch(endpoint, {
                    method: 'POST',
                    body: formData,
                    signal: controller.signal
                });
                const data = await parseJsonResponse(response);
                if (saveToken !== state.saveRequestToken) return;

                console.log('Save response:', data);
                if (data.success) {
                    applySaveSuccess(successMessage);
                } else {
                    showError(data.error || 'Failed to save shelf');
                    finishSave(true);
                }
            } catch (err) {
                if (saveToken !== state.saveRequestToken) return;
                if (err.name === 'AbortError') {
                    console.warn('Save request aborted');
                    finishSave(true);
                    return;
                }
                console.error('Save error:', err);
                showError('Error saving shelf: ' + err.message);
                finishSave(true);
            }
        });
    } catch (e) {
        console.error('Error in save process:', e);
        showError('Error preparing save: ' + e.message);
        finishSave(true);
    }
}

// ============================================================================
// STEP 6: EDIT/DELETE FUNCTIONALITY
// ============================================================================

function deleteShelf() {
    const code = state.currentShelfCode;
    if (!code) {
        showError('No shelf selected');
        return;
    }
    
    if (!confirm(`Delete shelf ${code}? This cannot be undone.`)) return;
    
    console.log('Deleting shelf:', code);
    
    // Show loading state
    const btn = document.getElementById('delete-shelf-btn');
    const originalText = btn.innerHTML;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Deleting...';
    btn.disabled = true;
    
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
    })
    .finally(() => {
        // Restore button state
        btn.innerHTML = originalText;
        btn.disabled = false;
    });
}

/**
 * Duplicate the current shelf
 */
function duplicateShelf() {
    const code = state.currentShelfCode;
    if (!code) {
        showError('No shelf selected');
        return;
    }
    
    if (!confirm(`Duplicate shelf ${code}?`)) return;
    
    console.log('Duplicating shelf:', code);
    
    // Show loading state
    const btn = document.getElementById('duplicate-shelf-btn');
    const originalText = btn.innerHTML;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Copying...';
    btn.disabled = true;
    
    fetch('/api/duplicate_shelf', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ code: code })
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            showSuccess(`Duplicated as ${data.new_code}`);
            closeModal();
            loadData();
        } else {
            showError(data.error || 'Failed to duplicate shelf');
        }
    })
    .catch(err => {
        console.error('Duplicate error:', err);
        showError('Error: ' + err.message);
    })
    .finally(() => {
        // Restore button state
        btn.innerHTML = originalText;
        btn.disabled = false;
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
    
    showSuccess('Preparing print job...');
    
    // Load QRCode library if needed
    const loadLib = () => {
        return new Promise((resolve) => {
            if (window.QRCode) return resolve();
            const script = document.createElement('script');
            script.src = 'https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js';
            script.onload = resolve;
            document.head.appendChild(script);
        });
    };
    
    loadLib().then(() => {
        // Create print area
        let printArea = document.getElementById('print-area');
        if (!printArea) {
            printArea = document.createElement('div');
            printArea.id = 'print-area';
            document.body.appendChild(printArea);
        }
        printArea.innerHTML = ''; // Clear previous
        
        // Add styles
        let style = document.getElementById('print-styles');
        if (!style) {
            style = document.createElement('style');
            style.id = 'print-styles';
            style.textContent = `
                @media print {
                    body > *:not(#print-area) {
                        display: none !important;
                    }
                    #print-area {
                        display: block !important;
                        position: absolute;
                        top: 0;
                        left: 0;
                        width: 100%;
                        background: white;
                        z-index: 9999;
                    }
                    @page {
                        size: letter;
                        margin: 0.5in;
                    }
                }
                @media screen {
                    #print-area {
                        display: none;
                    }
                }
                .qr-grid {
                    display: block;
                    padding: 0;
                    font-size: 0; /* Eliminates whitespace between inline-block items */
                }
                .qr-item {
                    display: inline-block;
                    vertical-align: top;
                    width: 1.75in;
                    margin: 0.05in;
                    page-break-inside: avoid;
                    break-inside: avoid; /* Standard property for preventing breaks */
                    border: 1px dashed #ccc;
                    padding: 0.125in;
                    box-sizing: border-box;
                    text-align: center;
                }
                .qr-code {
                    width: 1.5in !important;
                    height: 1.5in !important;
                    display: inline-block;
                }
                .qr-code img, .qr-code canvas {
                    width: 1.5in !important;
                    height: 1.5in !important;
                    max-width: none !important;
                    max-height: none !important;
                }
                .qr-label {
                    margin-top: 0.1in;
                    font-size: 12pt;
                    font-weight: bold;
                    text-align: center;
                    color: #000;
                    word-break: break-all;
                    font-family: sans-serif;
                }
            `;
            document.head.appendChild(style);
        }
        
        // Build content
        const grid = document.createElement('div');
        grid.className = 'qr-grid';
        printArea.appendChild(grid);
        
        const codes = Array.from(state.selectedShelves);
        
        codes.forEach(code => {
            const item = document.createElement('div');
            item.className = 'qr-item';
            
            const qrDiv = document.createElement('div');
            qrDiv.className = 'qr-code';
            
            const label = document.createElement('div');
            label.className = 'qr-label';
            label.textContent = code;
            
            item.appendChild(qrDiv);
            item.appendChild(label);
            grid.appendChild(item);
            
            new QRCode(qrDiv, {
                text: code,
                width: 192,
                height: 192,
                colorDark: '#000000',
                colorLight: '#ffffff',
                correctLevel: QRCode.CorrectLevel.H
            });
        });
        
        // Wait for images to render
        setTimeout(() => {
            window.print();
        }, 1000);
    });
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

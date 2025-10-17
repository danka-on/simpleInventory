/**
 * Shelf Creator - Main JavaScript
 * Handles all shelf management functionality
 */

// ============================================================================
// GLOBAL STATE
// ============================================================================
const state = {
    shelves: [],
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
    
    loadShelves();
    setupEventListeners();
    setupCanvas();
}

/**
 * Setup all event listeners
 */
function setupEventListeners() {
    // List view
    document.getElementById('add-btn').addEventListener('click', showAddView);
    
    // Add/Edit view
    document.getElementById('cancel-btn').addEventListener('click', cancelAdd);
    document.getElementById('save-btn').addEventListener('click', saveShelf);
    document.getElementById('start-camera-btn').addEventListener('click', startCapture);
    
    // Camera controls
    document.getElementById('capture-btn').addEventListener('click', capturePhoto);
    document.getElementById('retake-btn').addEventListener('click', retakePhoto);
    document.getElementById('continue-btn').addEventListener('click', continueToEditor);
    
    // Modal
    document.getElementById('modal-close-btn').addEventListener('click', closeModal);
    document.getElementById('edit-shelf-btn').addEventListener('click', editShelf);
    document.getElementById('delete-shelf-btn').addEventListener('click', deleteShelf);
    
    // Code validation
    setupCodeValidation();
}

/**
 * Load shelves from API
 */
function loadShelves() {
    console.log('Loading shelves...');
    
    fetch('/api/list_shelves')
        .then(r => r.json())
        .then(data => {
            console.log('Shelves loaded:', data);
            if (data.success) {
                state.shelves = data.shelves;
                renderShelves();
            } else {
                showError('Failed to load shelves');
            }
        })
        .catch(err => {
            console.error('Error loading shelves:', err);
            showError('Error loading shelves: ' + err.message);
        });
}

/**
 * Render shelf grid
 */
function renderShelves() {
    const container = document.getElementById('shelf-list');
    
    if (state.shelves.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <i class="fas fa-box-open"></i>
                <p>No shelves yet. Click + to add your first shelf!</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = state.shelves.map(shelf => `
        <div class="shelf-item" onclick="enlargeShelf('${shelf.code}')">
            <img src="${shelf.url}" alt="${shelf.code}" loading="lazy">
            <div class="shelf-code">${shelf.code}</div>
        </div>
    `).join('');
    
    console.log(`Rendered ${state.shelves.length} shelves`);
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
    if (state.isMobile) {
        document.getElementById('camera-section').style.display = 'block';
        document.getElementById('upload-section').style.display = 'none';
        document.getElementById('start-camera-btn').style.display = 'block';
    } else {
        document.getElementById('camera-section').style.display = 'none';
        document.getElementById('upload-section').style.display = 'block';
        document.getElementById('start-camera-btn').style.display = 'none';
        setupUploadArea();
    }
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
    document.getElementById('editor-container').classList.remove('active');
    state.currentImage = null;
    state.rectX = state.rectY = state.rectW = state.rectH = 0;
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
            checkSaveReady();
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
        
        checkSaveReady();
    })
    .catch(err => {
        console.error('Validation error:', err);
    });
}

/**
 * Check if save button should be enabled
 */
function checkSaveReady() {
    const code = document.getElementById('shelf-code').value.trim();
    const codeValid = document.getElementById('shelf-code').classList.contains('valid');
    const hasImage = state.currentImage !== null;
    
    const ready = code && codeValid && hasImage;
    document.getElementById('save-btn').disabled = !ready;
    
    console.log('Save ready:', {code, codeValid, hasImage, ready});
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
    const video = document.getElementById('camera-video');
    const cameraContainer = document.getElementById('camera-container');
    const captureBtn = document.getElementById('capture-btn');
    const startCameraBtn = document.getElementById('start-camera-btn');
    
    navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment" }, // Front-facing camera
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
    console.log('Continuing to editor...');
    const preview = document.getElementById('camera-preview');
    
    // Convert canvas to blob and load into editor
    preview.toBlob(blob => {
        const reader = new FileReader();
        reader.onload = (e) => {
            loadImageToEditor(e.target.result);
        };
        reader.readAsDataURL(blob);
    }, 'image/png');
    
    // Hide camera UI
    document.getElementById('camera-container').classList.remove('active');
    document.getElementById('camera-controls').style.display = 'none';
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
    console.log('Loading image to editor');
    const img = new Image();
    
    img.onload = () => {
        state.currentImage = img;
        
        // Set canvas size
        const maxWidth = 600;
        const scale = Math.min(1, maxWidth / img.width);
        state.canvas.width = img.width * scale;
        state.canvas.height = img.height * scale;
        
        // Draw image
        state.ctx.drawImage(img, 0, 0, state.canvas.width, state.canvas.height);
        
        // Show editor
        document.getElementById('editor-container').classList.add('active');
        
        checkSaveReady();
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
    }
}

function handleTouchStart(e) {
    e.preventDefault();
    const touch = e.touches[0];
    const rect = state.canvas.getBoundingClientRect();
    state.isDrawing = true;
    state.startX = touch.clientX - rect.left;
    state.startY = touch.clientY - rect.top;
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
    const code = document.getElementById('shelf-code').value.trim();
    if (!code || !state.currentImage) {
        showError('Please provide code and image');
        return;
    }
    
    console.log('Saving shelf:', code);
    
    // Finalize canvas with rectangle
    if (state.rectW && state.rectH) {
        state.ctx.strokeStyle = '#00ff00';
        state.ctx.lineWidth = 3;
        state.ctx.strokeRect(state.rectX, state.rectY, state.rectW, state.rectH);
    }
    
    // Convert canvas to blob
    state.canvas.toBlob(blob => {
        const formData = new FormData();
        formData.append('code', code);
        formData.append('image', blob, `${code}.png`);
        
        fetch('/api/upload_shelf', {
            method: 'POST',
            body: formData
        })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                showSuccess('Shelf saved successfully!');
                cancelAdd();
                loadShelves();
            } else {
                showError(data.error || 'Failed to save shelf');
            }
        })
        .catch(err => {
            console.error('Save error:', err);
            showError('Error saving shelf: ' + err.message);
        });
    }, 'image/png');
}

// ============================================================================
// STEP 6: EDIT/DELETE FUNCTIONALITY (Placeholder)
// ============================================================================

function editShelf() {
    console.log('Edit shelf:', state.currentShelfCode);
    closeModal();
    showError('Edit feature coming in next step!');
}

function deleteShelf() {
    const code = state.currentShelfCode;
    if (!code) return;
    
    if (!confirm(`Delete shelf ${code}? This cannot be undone.`)) return;
    
    console.log('Deleting shelf:', code);
    
    fetch(`/api/delete_shelf/${code}`, {
        method: 'POST'
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            showSuccess('Shelf deleted');
            closeModal();
            loadShelves();
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

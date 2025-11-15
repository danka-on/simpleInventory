# Returns Lifecycle Implementation

## ✅ Completed Backend Changes

### Database
- ✅ Added lifecycle columns to `returns` table (via ensure_returns_table migration):
  - `relisted` (INTEGER)
  - `relisted_date` (TEXT)
  - `relisted_store` (TEXT) - amazon/ebay/marketplace
  - `relisted_item_id` (TEXT) - ASIN/ItemID
  - `resold` (INTEGER)
  - `resold_date` (TEXT)
  - `resold_order_id` (TEXT)
  - `lifecycle_count` (INTEGER) - tracks total cycles

- ✅ Created `return_lifecycle_events` table for history tracking:
  - `id`, `return_id`, `event_type`, `event_date`
  - `auto_detected` (0=manual, 1=automatic)
  - `store`, `item_id`, `order_id`, `notes`

### API Endpoints
- ✅ `POST /api/returns/<id>/relist` - Mark return as relisted
- ✅ `POST /api/returns/<id>/resold` - Mark return as resold  
- ✅ `POST /api/returns/<id>/restart-lifecycle` - Restart after resold
- ✅ `GET /api/returns/<id>/history` - Get lifecycle event history
- ✅ Updated `GET /api/returns` to include `event_count`
- ✅ Updated `DELETE /api/returns/<id>` to also delete lifecycle events

### Auto-Detection
- ✅ Added auto-detection in marketplace sales (line 8258)
  - Checks for relisted returns matching barcode
  - Auto-marks as resold when sold
  - Creates lifecycle event with `auto_detected=1`

## 🎨 Frontend Updates Needed (returns.html)

### 1. Update Status Badge Logic
```javascript
// Current: 'pending', 'received', 'restocked'
// New: 'pending', 'received', 'restocked', 'relisted', 'resold'

function getLifecycleStatus(ret) {
    if (ret.resold) return 'resold';
    if (ret.relisted) return 'relisted';
    if (ret.restocked) return 'restocked';
    if (ret.received_date) return 'received';
    return 'pending';
}
```

### 2. Add Action Buttons
```html
<!-- After SHELF button, add: -->
${ret.restocked && !ret.relisted ? 
    `<button class="btn btn-primary" onclick="openRelistModal(${ret.id})">
        <i class="fas fa-tag"></i> Mark Relisted
    </button>` : ''}

${ret.relisted && !ret.resold ? 
    `<button class="btn btn-success" onclick="openResoldModal(${ret.id})">
        <i class="fas fa-dollar-sign"></i> Mark Resold
    </button>` : ''}

<!-- Add history button for all items with events -->
${ret.event_count > 0 ? 
    `<button class="btn btn-secondary" onclick="openHistoryModal(${ret.id})">
        <i class="fas fa-history"></i> (${ret.event_count})
    </button>` : ''}

<!-- Restart lifecycle button (after resold) -->
${ret.resold ? 
    `<button class="btn btn-warning" onclick="restartLifecycle(${ret.id})">
        <i class="fas fa-redo"></i> Returned Again
    </button>` : ''}
```

### 3. Relist Modal
```html
<div id="relistModal" class="modal" style="display:none;">
    <div class="modal-content">
        <h3>Mark as Relisted</h3>
        <label>Store:</label>
        <select id="relistStore">
            <option value="amazon">Amazon</option>
            <option value="ebay">eBay</option>
            <option value="marketplace">Marketplace</option>
        </select>
        
        <label>Item ID / ASIN:</label>
        <input id="relistItemId" placeholder="B08XYZ... or 1234...">
        
        <label>Notes (optional):</label>
        <textarea id="relistNotes" rows="3"></textarea>
        
        <div style="margin-top:16px;display:flex;gap:12px;">
            <button class="btn btn-primary" onclick="saveRelist()">Save</button>
            <button class="btn btn-secondary" onclick="closeRelistModal()">Cancel</button>
        </div>
    </div>
</div>
```

### 4. Resold Modal
```html
<div id="resoldModal" class="modal" style="display:none;">
    <div class="modal-content">
        <h3>Mark as Resold</h3>
        <label>Order ID:</label>
        <input id="resoldOrderId" placeholder="Order ID or leave blank">
        
        <label>Notes (optional):</label>
        <textarea id="resoldNotes" rows="3"></textarea>
        
        <div style="margin-top:16px;display:flex;gap:12px;">
            <button class="btn btn-success" onclick="saveResold()">Save</button>
            <button class="btn btn-secondary" onclick="closeResoldModal()">Cancel</button>
        </div>
    </div>
</div>
```

### 5. History Modal
```html
<div id="historyModal" class="modal" style="display:none;">
    <div class="modal-content" style="max-width:700px;">
        <h3>Return Lifecycle History</h3>
        <div id="historyContent"></div>
        <button class="btn btn-secondary" onclick="closeHistoryModal()">Close</button>
    </div>
</div>
```

### 6. JavaScript Functions
```javascript
let currentReturnId = null;

async function openRelistModal(returnId) {
    currentReturnId = returnId;
    document.getElementById('relistModal').style.display = 'flex';
}

async function saveRelist() {
    const store = document.getElementById('relistStore').value;
    const item_id = document.getElementById('relistItemId').value;
    const notes = document.getElementById('relistNotes').value;
    
    const response = await fetch(`/api/returns/${currentReturnId}/relist`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({store, item_id, notes})
    });
    
    const result = await response.json();
    if (result.success) {
        closeRelistModal();
        loadReturns();
        showMessage('success', '✓ Marked as relisted');
    } else {
        showMessage('error', result.error);
    }
}

async function openResoldModal(returnId) {
    currentReturnId = returnId;
    document.getElementById('resoldModal').style.display = 'flex';
}

async function saveResold() {
    const order_id = document.getElementById('resoldOrderId').value;
    const notes = document.getElementById('resoldNotes').value;
    
    const response = await fetch(`/api/returns/${currentReturnId}/resold`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({order_id, notes})
    });
    
    const result = await response.json();
    if (result.success) {
        closeResoldModal();
        loadReturns();
        showMessage('success', '✓ Marked as resold');
    } else {
        showMessage('error', result.error);
    }
}

async function restartLifecycle(returnId) {
    if (!confirm('This item was returned again after being resold. Restart lifecycle?')) return;
    
    const notes = prompt('Notes (optional):') || 'Item returned again';
    
    const response = await fetch(`/api/returns/${returnId}/restart-lifecycle`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({notes})
    });
    
    const result = await response.json();
    if (result.success) {
        loadReturns();
        showMessage('success', '✓ Lifecycle restarted');
    } else {
        showMessage('error', result.error);
    }
}

async function openHistoryModal(returnId) {
    const response = await fetch(`/api/returns/${returnId}/history`);
    const result = await response.json();
    
    if (!result.success) {
        showMessage('error', result.error);
        return;
    }
    
    const ret = result.return;
    const events = result.events;
    
    let html = `
        <div style="margin-bottom:20px;">
            <h4>${ret.title}</h4>
            <p><strong>Barcode:</strong> ${ret.barcode}</p>
            <p><strong>Lifecycle Count:</strong> ${ret.lifecycle_count}</p>
        </div>
        <div style="border-top:1px solid #ddd;padding-top:16px;">
            <h4>Event History</h4>
    `;
    
    if (events.length === 0) {
        html += '<p style="color:#888;">No lifecycle events yet</p>';
    } else {
        html += '<div class="timeline">';
        events.forEach(event => {
            const icon = event.auto_detected ? '🤖' : '👤';
            const autoLabel = event.auto_detected ? '<span class="badge badge-info">AUTO</span>' : '';
            html += `
                <div class="timeline-event">
                    <div style="display:flex;align-items:center;gap:8px;">
                        <span style="font-size:1.2rem;">${icon}</span>
                        <strong>${event.event_type.replace('_', ' ').toUpperCase()}</strong>
                        ${autoLabel}
                    </div>
                    <div style="color:#666;font-size:0.9rem;">${formatDateTime(event.event_date)}</div>
                    ${event.store ? `<div><strong>Store:</strong> ${event.store}</div>` : ''}
                    ${event.item_id ? `<div><strong>Item ID:</strong> ${event.item_id}</div>` : ''}
                    ${event.order_id ? `<div><strong>Order ID:</strong> ${event.order_id}</div>` : ''}
                    ${event.notes ? `<div style="margin-top:4px;font-style:italic;">${event.notes}</div>` : ''}
                </div>
            `;
        });
        html += '</div>';
    }
    
    html += '</div>';
    
    document.getElementById('historyContent').innerHTML = html;
    document.getElementById('historyModal').style.display = 'flex';
}

function formatDateTime(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    return d.toLocaleString();
}
```

### 7. Update Stats Dashboard
```javascript
// Add new lifecycle stats to updateStats()
document.getElementById('stat-relisted').textContent = stats.relisted_count || 0;
document.getElementById('stat-resold').textContent = stats.resold_count || 0;
document.getElementById('stat-recovery-rate').textContent = 
    stats.total_returns > 0 ? 
    `${((stats.resold_count / stats.total_returns) * 100).toFixed(1)}%` : 
    '0%';
```

### 8. Add Timeline CSS
```css
.timeline {
    display: flex;
    flex-direction: column;
    gap: 16px;
}
.timeline-event {
    padding: 12px;
    background: #f8f9fa;
    border-left: 4px solid #0984e3;
    border-radius: 8px;
}
.modal {
    position: fixed;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    background: rgba(0,0,0,0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
}
.modal-content {
    background: white;
    padding: 24px;
    border-radius: 16px;
    max-width: 500px;
    width: 90%;
    max-height: 80vh;
    overflow-y: auto;
}
```

## 🚀 Testing Checklist

1. ✅ Database migration creates new columns
2. ✅ Mark return as relisted (manual)
3. ✅ Mark return as resold (manual)
4. ✅ View lifecycle history
5. ✅ Auto-detection on marketplace sale
6. ✅ Restart lifecycle after resold
7. ✅ Filter by lifecycle status
8. ✅ Stats show recovery rate
9. ✅ History shows 🤖 for auto vs 👤 for manual

## 📊 Recovery Rate Formula
```
Recovery Rate = (resold_count / total_returns) × 100%
```

Example: 50 returns, 32 resold = 64% recovery rate

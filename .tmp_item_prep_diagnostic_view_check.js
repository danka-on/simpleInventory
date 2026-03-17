

    const DIAG_VIEW_ITEM_ID = {{ (bol.id if bol and bol.id is not none else none)|tojson }};
    const DIAG_VIEW_SELECTED_LOT = {{ (selected_lot if selected_lot else (bol.lot_number if bol and bol.lot_number else ''))|tojson }};
    const DIAG_VIEW_SELECTED_STATUS = {{ ((selected_status if selected_status else (status.status if status and status.status else '')))|tojson }};

    function diagViewNotesAllowed(){
      const normalized = String(DIAG_VIEW_SELECTED_STATUS || '').trim().toLowerCase();
      return ['good', 'bad', 'return'].includes(normalized);
    }

    function updateDiagViewNoteComposerState(){
      const noteInput = document.getElementById('newNoteInput');
      const addBtn = document.getElementById('addNoteBtn');
      if(!noteInput || !addBtn) return;
      const allowed = diagViewNotesAllowed();
      noteInput.disabled = !allowed;
      addBtn.disabled = !allowed;
      noteInput.placeholder = allowed ? 'Add a note...' : 'Notes require Good, Bad, or Return status';
      addBtn.title = allowed ? 'Add note' : 'Notes require Good, Bad, or Return status';
      addBtn.style.opacity = allowed ? '1' : '0.55';
      addBtn.style.cursor = allowed ? 'pointer' : 'not-allowed';
    }

    // Format Updated time to Eastern in a friendly way (YYYY-MM-DD h:mmam)
    (function(){
      const el = document.getElementById('updatedAt');
      if(!el) return;
      const iso = el.getAttribute('data-iso');
      try{
        function parseISO(s){
          if(!s) return new Date(NaN);
          try{
            let str = String(s).trim();
            if(/\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(str) && !(/[zZ]|[+-]\d{2}:?\d{2}$/.test(str))){ str = str.replace(' ','T'); if(!str.endsWith('Z')) str = str + 'Z'; }
            return new Date(str);
          }catch(e){ return new Date(NaN); }
        }
        function fmtFriendly(iso){
          try{
            const d = parseISO(iso);
            if(isNaN(d.getTime())) return iso;
            const parts = new Intl.DateTimeFormat('en-US', { timeZone:'America/New_York', year:'numeric', month:'2-digit', day:'2-digit', hour:'numeric', minute:'2-digit', hour12:true }).formatToParts(d);
            const vals = {}; parts.forEach(p=> vals[p.type] = p.value);
            const year = vals.year || d.getFullYear();
            const month = vals.month || String(d.getMonth()+1).padStart(2,'0');
            const day = vals.day || String(d.getDate()).padStart(2,'0');
            let hour = vals.hour || String(d.getHours()).padStart(2,'0');
            let minute = vals.minute || String(d.getMinutes()).padStart(2,'0');
            const ampm = (vals.dayPeriod || '').toLowerCase();
            hour = String(hour).replace(/^0/, '');
            return `${year}-${month}-${day} ${hour}:${minute}${ampm}`;
          }catch(e){ return iso; }
        }
        el.textContent = fmtFriendly(iso);
      }catch(e){ /* leave as-is */ }
    })();

    // Back button logic: if return param provided from Items to List, use that; else default to Item Prep with UPC; if from=trash, go Trash Manager
    (function(){
      try{
        const params = new URLSearchParams(window.location.search);
        const ret = params.get('return') || '';
        if(ret){ const a = document.getElementById('backLink'); if(a) a.href = ret; return; }
        if((params.get('from')||'').toLowerCase()==='trash'){
          const a = document.getElementById('backLink');
          if(a) a.href = '/trash-manager';
        }
      }catch(e){}
    })();

    // If this details page was opened from Items-to-List with a return param, ensure the Edit link also preserves that return/from
    (function(){
      try{
        const params = new URLSearchParams(window.location.search);
        const ret = params.get('return');
        const from = params.get('from');
        const lot = params.get('lot');
        const rowStatus = params.get('row_status') || params.get('status') || '';
        const edit = document.getElementById('editDiagLink');
        if(edit){
          // Build a proper href preserving upc and optional from/return/lot
          const base = edit.getAttribute('href') || ('/item-prep/diagnostic?upc={{ upc }}');
          const u = new URL(base, window.location.origin);
          u.searchParams.set('upc', '{{ upc }}');
          if(from) u.searchParams.set('from', from);
          if(ret) u.searchParams.set('return', ret);
          if(lot) u.searchParams.set('lot', lot);
          if(rowStatus) u.searchParams.set('row_status', rowStatus);
          edit.href = u.pathname + u.search;
        }
      }catch(e){}
    })();

    // Click-to-enlarge images + download
    (function(){
      const modal = document.getElementById('imgModal');
      const modalImg = document.getElementById('modalImg');
      const modalDl = document.getElementById('modalDownload');
      const modalSetDisplay = document.getElementById('modalSetDisplay');
      window.__diagActivePhotoId = null;
      window.__diagActivePhotoPath = null;
      
      function openModal(src, photoId, photoPath){ 
        modalImg.src = src; 
        modalDl.href = src; 
        window.__diagActivePhotoId = photoId;
        window.__diagActivePhotoPath = photoPath;
        // Reset button state
        if(modalSetDisplay){
          modalSetDisplay.disabled = false;
          modalSetDisplay.textContent = 'Set as Display Image';
          modalSetDisplay.style.background = '#00b894';
        }
        modal.style.display='flex'; 
      }
      window.openDiagPhotoModal = openModal;
      
      function closeModal(e){ 
        // Don't close if clicking on the set display button or download link
        if(e && (e.target === modalSetDisplay || e.target === modalDl || modalSetDisplay.contains(e.target) || modalDl.contains(e.target))){
          return;
        }
        modal.style.display='none'; 
        modalImg.src=''; 
        window.__diagActivePhotoId = null;
        window.__diagActivePhotoPath = null;
      }
      
      document.querySelectorAll('.thumb').forEach(img=>{
        img.addEventListener('click', (e)=>{
          e.stopPropagation();
          openModal(
            img.getAttribute('data-src'),
            img.getAttribute('data-id'),
            img.getAttribute('data-path')
          );
        });
      });
      
      modal.addEventListener('click', closeModal);
      document.addEventListener('keydown', (e)=>{ if(e.key==='Escape') closeModal(); });
      
      // Set display image from modal
      if(modalSetDisplay){
        modalSetDisplay.addEventListener('click', async (e)=>{
          e.stopPropagation();
          if(!window.__diagActivePhotoId || !window.__diagActivePhotoPath) return;
          try{
            modalSetDisplay.disabled = true;
            modalSetDisplay.textContent = 'Setting...';
            const res = await fetch('/api/items_prep/set_display_image', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
                upc: '{{ upc }}',
                photo_id: window.__diagActivePhotoId,
                image_path: window.__diagActivePhotoPath,
                id: (Number.isFinite(DIAG_VIEW_ITEM_ID) && DIAG_VIEW_ITEM_ID > 0) ? DIAG_VIEW_ITEM_ID : undefined,
                lot_number: String(DIAG_VIEW_SELECTED_LOT || '').trim()
              })
            });
            const j = await res.json();
            if(j.success){
              modalSetDisplay.textContent = '✓ Set as Display!';
              modalSetDisplay.style.background = '#2ecc71';
              // Update hero image if it exists
              const heroImg = document.getElementById('heroImg');
              if(heroImg){
                heroImg.src = j.image_url;
              } else {
                // Reload to show new display image
                setTimeout(()=>{ window.location.reload(); }, 800);
                return;
              }
              // Update display badges - hide all, show current
              document.querySelectorAll('.display-badge').forEach(b=> b.style.display = 'none');
              document.querySelectorAll('.thumb-wrap').forEach(wrap=>{
                const imgPath = wrap.getAttribute('data-path');
                if(imgPath === window.__diagActivePhotoPath){
                  const badge = wrap.querySelector('.display-badge');
                  if(badge) badge.style.display = 'block';
                }
              });
            } else {
              alert('Failed to set display image: ' + (j.error || 'Unknown'));
              modalSetDisplay.disabled = false;
              modalSetDisplay.textContent = 'Set as Display Image';
            }
          }catch(e){
            alert('Error: ' + e);
            modalSetDisplay.disabled = false;
            modalSetDisplay.textContent = 'Set as Display Image';
          }
        });
      }
    })();

    function markDisplayBadges(){
      const currentDisplayUrl = '{{ bol.image_url if bol and bol.image_url else "" }}';
      if(!currentDisplayUrl) return;
      
      // Extract the path from the full URL (e.g., /static/items_prep/xxx.jpg -> items_prep/xxx.jpg)
      const pathMatch = currentDisplayUrl.match(/items_prep\/[^\/]+$/);
      if(!pathMatch) return;
      const currentPath = pathMatch[0];
      
      // Find and mark the matching thumbnail
      document.querySelectorAll('.thumb-wrap').forEach(wrap=>{
        const imgPath = wrap.getAttribute('data-path');
        if(imgPath && imgPath.includes(currentPath)){
          const badge = wrap.querySelector('.display-badge');
          if(badge) badge.style.display = 'block';
        }
      });
    }
    markDisplayBadges();

    // Add display image from camera/file
    (function(){
      const fileInput = document.getElementById('displayImgInput');
      if(!fileInput) return;
      
      const handleClick = (e)=> {
        e.preventDefault();
        e.stopPropagation();
        fileInput.click();
      };
      
      // Use event delegation to handle both initial and dynamically added buttons
      document.addEventListener('click', (e)=>{
        const btnAdd = e.target.closest('.btnAddDisplayImg');
        const placeholder = e.target.closest('#addDisplayImgPlaceholder');
        if(btnAdd || placeholder){
          handleClick(e);
        }
      });
      
      fileInput.addEventListener('change', async ()=>{
        const file = fileInput.files[0];
        if(!file) return;
        try{
          const fd = new FormData();
          fd.append('photo', file);
          if(DIAG_VIEW_SELECTED_STATUS) fd.append('row_status', DIAG_VIEW_SELECTED_STATUS);
          const res = await fetch('/api/items_prep/diagnostic/{{ upc }}/photos', {method:'POST', body: fd});
          const j = await res.json();
          if(!j.success || !j.images || j.images.length === 0){
            alert('Failed to upload image: ' + (j.error || 'Unknown error'));
            return;
          }
          const img = j.images[0];
          // Set as display image
          const setRes = await fetch('/api/items_prep/set_display_image', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
              upc: '{{ upc }}',
              image_path: img.image_path,
              id: (Number.isFinite(DIAG_VIEW_ITEM_ID) && DIAG_VIEW_ITEM_ID > 0) ? DIAG_VIEW_ITEM_ID : undefined,
              lot_number: String(DIAG_VIEW_SELECTED_LOT || '').trim()
            })
          });
          const setJ = await setRes.json();
          if(setJ.success){
            // Reload page to show new display image
            window.location.reload();
          } else {
            alert('Image uploaded but failed to set as display: ' + (setJ.error || 'Unknown'));
          }
        }catch(e){
          alert('Error uploading display image: ' + e);
        }
      });
    })();



    // Add quality badges based on image intrinsic size (LOW/MED/HIGH)
    (function(){
      function labelFor(w, h){
        const maxSide = Math.max(w||0, h||0);
        if(maxSide <= 900) return 'LOW';
        if(maxSide <= 2200) return 'MED';
        return 'HIGH';
      }
      document.querySelectorAll('.thumb').forEach(img=>{
        function apply(){
          const q = labelFor(img.naturalWidth, img.naturalHeight);
          const badge = document.createElement('span');
          badge.className = 'badge-quality';
          badge.textContent = q;
          const wrap = img.parentElement;
          if(wrap && wrap.classList.contains('thumb-wrap')){
            wrap.appendChild(badge);
          }
        }
        if(img.complete && img.naturalWidth){ apply(); }
        else { img.addEventListener('load', apply, { once: true }); }
      });
    })();

    // Delete all photos
    (function(){
      const btn = document.getElementById('btnDeleteAll');
      if(!btn) return;
      btn.addEventListener('click', async ()=>{
        if(!confirm('Delete all diagnostic photos for this item? This cannot be undone.')) return;
        try{
          btn.setAttribute('data-busy', '1');
          btn.disabled = true; btn.textContent = 'Deleting...';
          const params = new URLSearchParams();
          if(DIAG_VIEW_SELECTED_STATUS) params.set('row_status', DIAG_VIEW_SELECTED_STATUS);
          const res = await fetch('/api/items_prep/diagnostic/{{ upc }}/photos' + (params.toString() ? ('?' + params.toString()) : ''), { method:'DELETE' });
          const j = await res.json();
          if(!(j && j.success)){
            btn.disabled = false; btn.textContent = 'Delete All';
            alert('Failed to delete photos: '+(j&&j.error||'unknown'));
            return;
          }
          await refreshScopedPhotos({ silent: true });
        }catch(e){
          btn.disabled = false; btn.textContent = 'Delete All'; alert('Error: '+e);
        } finally {
          btn.removeAttribute('data-busy');
          updatePhotoStatus(document.querySelectorAll('#photosGrid .thumb-wrap').length);
        }
      });
    })();

    // Per-photo delete buttons (red X overlays) with event delegation
    (function(){
      async function deletePhoto(id, btn){
        if(!confirm('Delete this photo? This will move it to trash.')) return false;
        try{
          btn.disabled = true; btn.textContent = '...';
          const res = await fetch('/api/items_prep/photo/'+id, { method: 'DELETE' });
          const j = await res.json();
          if(!(j && j.success)){
            alert('Failed to delete photo: '+(j&&j.error||'unknown'));
            btn.disabled = false; btn.textContent = '✕';
            return false;
          }
          await refreshScopedPhotos({ silent: true });
          return true;
        }catch(e){ alert('Error: '+e); btn.disabled=false; btn.textContent='✕'; return false; }
      }
      document.addEventListener('click', (ev)=>{
        const btn = ev.target.closest('.del-photo');
        if(!btn) return;
        ev.preventDefault(); ev.stopPropagation();
        const id = btn.getAttribute('data-id');
        deletePhoto(id, btn);
      });
    })();

    // Mark as Listed action
    (function(){
      const btn = document.getElementById('btnMarkListed');
      if(!btn) return;
      btn.addEventListener('click', async ()=>{
        if(btn.disabled) return;
        const proceed = confirm('Marking as listed will delete all diagnostic photos for this item. Make sure you have downloaded them first. Continue?');
        if(!proceed) return;
        try{
          btn.disabled = true; btn.textContent = 'Marking...';
          // 1) set list status
          const res = await fetch('/api/bol_items/list_status', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ upc: '{{ upc }}', list_status: 'listed' })});
          const j = await res.json();
          if(!(j && j.success)){
            btn.disabled = false; btn.textContent = 'Mark as Listed'; alert('Failed to mark listed: '+(j&&j.error||'unknown')); return;
          }
          // 2) delete photos
          const params = new URLSearchParams();
          if(DIAG_VIEW_SELECTED_STATUS) params.set('row_status', DIAG_VIEW_SELECTED_STATUS);
          const delRes = await fetch('/api/items_prep/diagnostic/{{ upc }}/photos' + (params.toString() ? ('?' + params.toString()) : ''), { method:'DELETE' });
          const dj = await delRes.json();
          if(!(dj && dj.success)){
            alert('Marked listed, but failed to delete photos: '+(dj&&dj.error||'unknown'));
          }
          // 3) reflect UI
          btn.textContent = 'Listed'; btn.style.background = '#2ecc71'; btn.style.color = '#fff';
          await refreshScopedPhotos({ silent: true });
        }catch(e){ btn.disabled = false; btn.textContent = 'Mark as Listed'; alert('Failed: '+e); }
      });
    })();

    function buildScopedPhotoQuery(){
      const params = new URLSearchParams();
      params.set('exact_scope', '1');
      if(DIAG_VIEW_SELECTED_LOT) params.set('lot', DIAG_VIEW_SELECTED_LOT);
      if(DIAG_VIEW_SELECTED_STATUS) params.set('row_status', DIAG_VIEW_SELECTED_STATUS);
      return params;
    }

    function buildScopedPhotoSrc(rel){
      const raw = String(rel || '').trim();
      if(!raw) return '';
      if(raw.startsWith('http://') || raw.startsWith('https://') || raw.startsWith('/')) return raw;
      return '/static/' + raw;
    }

    function updatePhotoStatus(count){
      const statusEl = document.getElementById('mobileUploadStatusText');
      if(statusEl) statusEl.textContent = `Uploaded: ${count}`;
      const delBtn = document.getElementById('btnDeleteAll');
      if(delBtn && delBtn.getAttribute('data-busy') !== '1'){
        delBtn.textContent = 'Delete All';
        delBtn.disabled = (count === 0);
      }
    }

    function bindThumbInteractions(wrap, src, id, rel){
      const img = wrap.querySelector('img.thumb');
      if(img){
        img.addEventListener('click', ()=>{
          if(typeof window.openDiagPhotoModal === 'function'){
            window.openDiagPhotoModal(src, id, rel);
          } else {
            const modal = document.getElementById('imgModal');
            const modalImg = document.getElementById('modalImg');
            const modalDl = document.getElementById('modalDownload');
            modalImg.src = src;
            modalDl.href = src;
            modal.style.display = 'flex';
          }
        });
      }
    }

    function renderScopedPhotos(images){
      const section = document.getElementById('photosSection');
      if(!section) return;
      let grid = document.getElementById('photosGrid');
      const empty = document.getElementById('photosEmptyState');
      if(empty) empty.remove();

      const rows = Array.isArray(images) ? images : [];
      if(!rows.length){
        if(grid) grid.remove();
        const msg = document.createElement('div');
        msg.className = 'muted photo-empty';
        msg.id = 'photosEmptyState';
        msg.textContent = 'No photos attached.';
        section.appendChild(msg);
        updatePhotoStatus(0);
        return;
      }

      if(!grid){
        grid = document.createElement('div');
        grid.className = 'grid';
        grid.id = 'photosGrid';
        grid.style.marginTop = '12px';
        section.appendChild(grid);
      }
      grid.innerHTML = '';

      rows.forEach(im => {
        const rel = String(im.image_path || '').trim();
        const src = buildScopedPhotoSrc(rel) + (rel.includes('?') ? '' : `?t=${Date.now()}`);
        const wrap = document.createElement('div');
        wrap.className = 'thumb-wrap';
        wrap.setAttribute('data-id', im.id);
        wrap.setAttribute('data-path', rel);
        wrap.style.position = 'relative';
        wrap.innerHTML = `
          <button class="del-photo" data-id="${im.id}" title="Delete photo" style="position:absolute;top:6px;right:6px;background:#e74c3c;color:#fff;border:none;border-radius:50%;width:26px;height:26px;line-height:26px;text-align:center;cursor:pointer;font-weight:700;z-index:2">✕</button>
          <span class="display-badge" style="display:none;position:absolute;bottom:6px;left:6px;background:#00b894;color:#fff;padding:4px 8px;border-radius:6px;font-size:0.75rem;font-weight:600;z-index:2;box-shadow:0 2px 4px rgba(0,0,0,0.3)">DISPLAY</span>
          <img class="thumb" src="${src}" alt="photo" data-src="${src}" data-id="${im.id}" data-path="${rel}">
        `;
        grid.appendChild(wrap);
        bindThumbInteractions(wrap, src, im.id, rel);
      });

      document.querySelectorAll('#photosGrid .thumb-wrap').forEach(w=>{
        const img = w.querySelector('img.thumb');
        if(!img) return;
        const existing = w.querySelector('.badge-quality');
        if(existing) existing.remove();
        function labelFor(wi, hi){ const m=Math.max(wi||0,hi||0); if(m<=900) return 'LOW'; if(m<=2200) return 'MED'; return 'HIGH'; }
        function apply(){
          const badge = document.createElement('span');
          badge.className = 'badge-quality';
          badge.textContent = labelFor(img.naturalWidth, img.naturalHeight);
          w.appendChild(badge);
        }
        if(img.complete && img.naturalWidth){ apply(); }
        else { img.addEventListener('load', apply, { once: true }); }
      });

      markDisplayBadges();
      updatePhotoStatus(rows.length);
    }

    async function refreshScopedPhotos(options = {}){
      const upc = '{{ upc }}'.trim();
      if(!upc) return;
      try{
        const params = buildScopedPhotoQuery();
        const res = await fetch(`/api/items_prep/diagnostic/${encodeURIComponent(upc)}?${params.toString()}`, { cache: 'no-store' });
        const j = await res.json();
        if(!(res.ok && j)){
          if(!options.silent) alert('Failed to refresh photos');
          return;
        }
        renderScopedPhotos(j.images || []);
      }catch(e){
        if(!options.silent) console.error('Failed to refresh scoped photos:', e);
      }
    }

    function renderMobileQr(url){
      const box = document.getElementById('mobileQr');
      if(!box) return;
      box.innerHTML = '';
      if(!url){
        box.innerHTML = '<div class="muted">QR unavailable</div>';
        return;
      }
      if(typeof window.QRCode === 'undefined'){
        box.innerHTML = '<div class="muted">QR unavailable</div>';
        return;
      }
      try{
        new QRCode(box, {
          text: url,
          width: 148,
          height: 148,
          colorDark: '#0f172a',
          colorLight: '#ffffff',
          correctLevel: QRCode.CorrectLevel.H
        });
      }catch(e){
        box.innerHTML = '<div class="muted">QR error</div>';
      }
    }

    function setupMobileUploadLink(){
      const link = document.getElementById('mobileUploadLink');
      if(!link) return '';
      const upc = '{{ upc }}'.trim();
      const title = {{ (bol.item_description if bol and bol.item_description else '')|tojson }};
      const params = new URLSearchParams();
      params.set('upc', upc);
      if(DIAG_VIEW_SELECTED_STATUS) params.set('row_status', DIAG_VIEW_SELECTED_STATUS);
      if(title) params.set('title', title);
      params.set('return', window.location.pathname + window.location.search);
      const url = `${window.location.origin}/items-to-list/mobile-photos?${params.toString()}`;
      link.href = url;
      renderMobileQr(url);
      return url;
    }

    function setupScopedDownloadLink(){
      const link = document.getElementById('downloadAllPhotosLink');
      const upc = '{{ upc }}'.trim();
      if(!link || !upc) return;
      const params = new URLSearchParams();
      if(DIAG_VIEW_SELECTED_STATUS) params.set('row_status', DIAG_VIEW_SELECTED_STATUS);
      const qs = params.toString();
      link.href = `/api/items_prep/diagnostic/${encodeURIComponent(upc)}/photos.zip${qs ? ('?' + qs) : ''}`;
    }

    // Notes functionality
    function formatNoteTimestamp(iso){
      try{
        function parseISO(s){
          if(!s) return new Date(NaN);
          try{
            let str = String(s).trim();
            if(/\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(str) && !(/[zZ]|[+-]\d{2}:?\d{2}$/.test(str))){
              str = str.replace(' ', 'T');
              if(!str.endsWith('Z')) str = str + 'Z';
            }
            return new Date(str);
          }catch(e){ return new Date(NaN); }
        }
        const d = parseISO(iso);
        if(isNaN(d.getTime())) return iso;
        const parts = new Intl.DateTimeFormat('en-US', {
          timeZone: 'America/New_York',
          year: 'numeric', month: '2-digit', day: '2-digit',
          hour: 'numeric', minute: '2-digit', hour12: true
        }).formatToParts(d);
        const vals = {};
        parts.forEach(p=> vals[p.type] = p.value);
        const year = vals.year || d.getFullYear();
        const month = vals.month || String(d.getMonth()+1).padStart(2,'0');
        const day = vals.day || String(d.getDate()).padStart(2,'0');
        let hour = vals.hour || d.getHours();
        let minute = vals.minute || String(d.getMinutes()).padStart(2,'0');
        const dayPeriod = (vals.dayPeriod || '').toLowerCase();
        hour = String(hour).replace(/^0/, '');
        return `${year}-${month}-${day} ${hour}:${minute}${dayPeriod}`;
      }catch(e){
        return iso;
      }
    }

    async function loadNotes(){
      const upc = '{{ upc }}'.trim();
      if(!upc) return;
      try{
        const params = buildScopedMediaQuery('');
        params.delete('media_type');
        const res = await fetch(`/api/items_prep/notes/${encodeURIComponent(upc)}${params.toString() ? ('?' + params.toString()) : ''}`);
        const j = await res.json();
        if(!j.success) return;
        const container = document.getElementById('notesContainer');
        if(!container) return;
        container.innerHTML = '';
        const notes = j.notes || [];
        if(notes.length === 0){
          container.innerHTML = '<div class="muted">No notes yet.</div>';
          return;
        }
        notes.forEach(n=>{
          const bubble = document.createElement('div');
          bubble.className = 'note-bubble';
          bubble.innerHTML = `
            <div style="flex:1">
              <div class="note-text">${escapeHtml(n.note||'')}</div>
              <div class="note-time">${formatNoteTimestamp(n.created_at)}</div>
            </div>
            <button type="button" class="note-delete" data-id="${n.id}">×</button>
          `;
          bubble.querySelector('.note-delete').addEventListener('click', ()=> deleteNote(n.id));
          container.appendChild(bubble);
        });
      }catch(e){
        console.error('Failed to load notes:', e);
      }
    }

    async function loadVoiceNotes(){
      const upc = '{{ upc }}'.trim();
      if(!upc) return;
      const container = document.getElementById('voiceNotesContainer');
      if(!container) return;
      try{
        const params = buildScopedMediaQuery('audio');
        const res = await fetch(`/api/items_prep/media/${encodeURIComponent(upc)}?${params.toString()}`);
        const j = await res.json();
        if(!(j && j.success)){
          container.innerHTML = '<div class="muted">No voice notes attached.</div>';
          return;
        }
        const items = Array.isArray(j.items) ? j.items : [];
        container.innerHTML = '';
        if(items.length === 0){
          container.innerHTML = '<div class="muted">No voice notes attached.</div>';
          return;
        }
        items.forEach((item, idx) => {
          const src = buildStaticAssetSrc(item && item.file_path);
          const date = formatNoteTimestamp(item && item.created_at);
          const card = document.createElement('div');
          card.className = 'voice-note-card';
          card.innerHTML = `
            <div style="display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap">
              <strong>Voice note ${idx + 1}</strong>
              <span class="muted">${escapeHtml(date)}</span>
            </div>
            <audio controls preload="metadata" src="${escapeHtml(src)}"></audio>
          `;
          container.appendChild(card);
        });
      }catch(e){
        console.error('Failed to load voice notes:', e);
        container.innerHTML = '<div class="muted">Failed to load voice notes.</div>';
      }
    }

    async function loadVideos(){
      const upc = '{{ upc }}'.trim();
      if(!upc) return;
      const container = document.getElementById('videosContainer');
      if(!container) return;
      try{
        const params = buildScopedMediaQuery('video');
        const res = await fetch(`/api/items_prep/media/${encodeURIComponent(upc)}?${params.toString()}`);
        const j = await res.json();
        if(!(j && j.success)){
          container.innerHTML = '<div class="muted">No videos attached.</div>';
          return;
        }
        const items = Array.isArray(j.items) ? j.items : [];
        container.innerHTML = '';
        if(items.length === 0){
          container.innerHTML = '<div class="muted">No videos attached.</div>';
          return;
        }
        const grid = document.createElement('div');
        grid.className = 'videos-grid';
        items.forEach((item, idx) => {
          const src = buildStaticAssetSrc(item && item.file_path);
          const date = formatNoteTimestamp(item && item.created_at);
          const card = document.createElement('div');
          card.className = 'video-card';
          card.innerHTML = `
            <video controls playsinline preload="metadata" src="${escapeHtml(src)}"></video>
            <div class="video-meta">
              <strong>Video ${idx + 1}</strong>
              <span class="muted">${escapeHtml(date)}</span>
            </div>
            <div class="video-meta">
              <a class="link" href="${escapeHtml(src)}" download>Download video</a>
            </div>
          `;
          grid.appendChild(card);
        });
        container.appendChild(grid);
      }catch(e){
        console.error('Failed to load videos:', e);
        container.innerHTML = '<div class="muted">Failed to load videos.</div>';
      }
    }

    async function addNote(){
      if(!diagViewNotesAllowed()){
        alert('Notes can only be added to Good, Bad, or Return rows.');
        return;
      }
      const upc = '{{ upc }}'.trim();
      const input = document.getElementById('newNoteInput');
      const note = (input.value || '').trim();
      if(!note) return;
      try{
        const res = await fetch('/api/items_prep/notes', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ upc, note, row_status: DIAG_VIEW_SELECTED_STATUS })
        });
        const j = await res.json();
        if(j.success){
          input.value = '';
          await loadNotes();
          await loadHistory();
        } else {
          alert('Failed to add note: ' + (j.error || 'unknown'));
        }
      }catch(e){
        alert('Error adding note: ' + e);
      }
    }

    async function deleteNote(noteId){
      if(!confirm('Delete this note?')) return;
      try{
        const res = await fetch(`/api/items_prep/notes/${noteId}`, { method: 'DELETE' });
        const j = await res.json();
        if(j.success){
          await loadNotes();
          await loadHistory();
        } else {
          alert('Failed to delete note: ' + (j.error || 'unknown'));
        }
      }catch(e){
        alert('Error deleting note: ' + e);
      }
    }

    function escapeHtml(text){
      const div = document.createElement('div');
      div.textContent = text;
      return div.innerHTML;
    }

    function buildScopedMediaQuery(mediaType){
      const params = new URLSearchParams();
      if(DIAG_VIEW_SELECTED_LOT) params.set('lot', DIAG_VIEW_SELECTED_LOT);
      if(DIAG_VIEW_SELECTED_STATUS) params.set('row_status', DIAG_VIEW_SELECTED_STATUS);
      if(mediaType) params.set('media_type', mediaType);
      return params;
    }

    function buildStaticAssetSrc(rel){
      const raw = String(rel || '').trim();
      if(!raw) return '';
      if(raw.startsWith('http://') || raw.startsWith('https://') || raw.startsWith('/')) return raw;
      return '/static/' + raw.replace(/^\/+/, '');
    }

    function formatHistoryValue(value){
      if(value === null || value === undefined || value === '') return 'empty';
      if(typeof value === 'object'){
        try{ return JSON.stringify(value); }
        catch(e){ return String(value); }
      }
      return String(value);
    }

    function renderHistoryChange(change){
      const c = (change && typeof change === 'object') ? change : {};
      const field = escapeHtml(String(c.field || 'value'));
      const hasFrom = Object.prototype.hasOwnProperty.call(c, 'from');
      const hasTo = Object.prototype.hasOwnProperty.call(c, 'to');
      const fromText = escapeHtml(formatHistoryValue(c.from));
      const toText = escapeHtml(formatHistoryValue(c.to));
      if(hasFrom && hasTo) return `${field}: ${fromText} -> ${toText}`;
      if(hasTo) return `${field}: set to ${toText}`;
      if(hasFrom) return `${field}: was ${fromText}`;
      return `${field}: updated`;
    }

    async function loadLiveListings(){
      const upc = '{{ upc }}'.trim();
      const summary = document.getElementById('liveListingsSummary');
      const body = document.getElementById('liveListingsBody');
      if(!upc || !summary || !body) return;

      summary.textContent = 'Loading live listings...';
      body.innerHTML = '';
      try{
        const t = Date.now();
        const res = await fetch(`/api/items_prep/diagnostic/${encodeURIComponent(upc)}/live_listings?_t=${t}`, { cache: 'no-store' });
        const j = await res.json();
        if(!(res.ok && j && j.success)){
          summary.textContent = `Failed to load live listings${j && j.error ? ': ' + j.error : ''}`;
          return;
        }

        const ebayCount = Number(j.ebay_count || 0);
        const amazonCount = Number(j.amazon_count || 0);
        const total = Number(j.total_count || 0);
        const rows = Array.isArray(j.listings) ? j.listings : [];
        summary.textContent = `Total: ${total} (eBay ${ebayCount}, Amazon ${amazonCount})`;

        if(!rows.length){
          body.innerHTML = '<div class="muted">No active live listings found.</div>';
          return;
        }

        const frag = document.createDocumentFragment();
        rows.forEach(entry => {
          const platform = String(entry.platform || '').trim().toUpperCase() || 'LIVE';
          const title = String(entry.title || '').trim();
          const url = String(entry.url || '').trim();
          const listingId = String(entry.listing_id || '').trim();

          const row = document.createElement('div');
          row.className = 'live-listing-row';

          const plat = document.createElement('div');
          plat.className = 'live-listing-platform';
          plat.textContent = platform;
          row.appendChild(plat);

          const main = document.createElement('div');
          main.className = 'live-listing-main';

          const titleEl = document.createElement('div');
          if(url){
            const a = document.createElement('a');
            a.href = url;
            a.target = '_blank';
            a.rel = 'noopener noreferrer';
            a.textContent = title || url;
            titleEl.appendChild(a);
          } else {
            titleEl.textContent = title || '(No link available)';
          }
          main.appendChild(titleEl);

          if(listingId){
            const idEl = document.createElement('div');
            idEl.className = 'live-listing-id';
            idEl.textContent = `ID: ${listingId}`;
            main.appendChild(idEl);
          }

          row.appendChild(main);
          frag.appendChild(row);
        });
        body.innerHTML = '';
        body.appendChild(frag);
      }catch(e){
        summary.textContent = `Failed to load live listings: ${String(e)}`;
      }
    }

    async function loadHistory(){
      const upc = '{{ upc }}'.trim();
      const container = document.getElementById('historyContainer');
      if(!upc || !container) return;
      container.innerHTML = '<div class="muted">Loading log...</div>';
      try{
        const params = new URLSearchParams(window.location.search || '');
        const lot = String(DIAG_VIEW_SELECTED_LOT || params.get('lot') || '').trim();
        const q = new URLSearchParams();
        q.set('limit', '250');
        if(lot) q.set('lot', lot);
        if(DIAG_VIEW_SELECTED_STATUS) q.set('row_status', DIAG_VIEW_SELECTED_STATUS);
        const res = await fetch(`/api/items_prep/diagnostic/${encodeURIComponent(upc)}/history?${q.toString()}`);
        const j = await res.json();
        if(!(j && j.success)){
          container.innerHTML = `<div class="muted">Failed to load log: ${escapeHtml((j && j.error) || 'unknown')}</div>`;
          return;
        }
        const items = Array.isArray(j.items) ? j.items : [];
        if(items.length === 0){
          container.innerHTML = '<div class="muted">No logged changes found for this item yet.</div>';
          return;
        }
        container.innerHTML = '';
        items.forEach(ev=>{
          const title = escapeHtml(String(ev && ev.title || 'Update'));
          const timestamp = formatNoteTimestamp(ev && ev.timestamp || '');
          const description = escapeHtml(String(ev && ev.description || ''));
          const category = String(ev && ev.category || '').replace(/_/g, ' ').trim();
          const upcValue = String(ev && ev.upc || '').trim();
          const lotValue = String(ev && ev.lot_number || '').trim();
          const sourceValue = String(ev && ev.source || '').trim();
          const subBits = [];
          if(category) subBits.push(category);
          if(upcValue) subBits.push(`UPC ${upcValue}`);
          if(lotValue) subBits.push(`LOT ${lotValue}`);
          if(sourceValue) subBits.push(`source ${sourceValue}`);
          const changes = Array.isArray(ev && ev.changes) ? ev.changes : [];
          const changesHtml = changes.length
            ? `<div class="history-changes">${changes.map(ch=>`<div class="history-change">${renderHistoryChange(ch)}</div>`).join('')}</div>`
            : '';
          const item = document.createElement('div');
          item.className = 'history-item';
          item.innerHTML = `
            <div class="history-head">
              <div class="history-title">${title}</div>
              <div class="history-time">${escapeHtml(timestamp)}</div>
            </div>
            ${subBits.length ? `<div class="history-sub">${escapeHtml(subBits.join(' | '))}</div>` : ''}
            ${description ? `<div class="history-sub">${description}</div>` : ''}
            ${changesHtml}
          `;
          container.appendChild(item);
        });
      }catch(e){
        container.innerHTML = `<div class="muted">Failed to load log: ${escapeHtml(String(e))}</div>`;
      }
    }

    (function(){
      const addBtn = document.getElementById('addNoteBtn');
      const noteInput = document.getElementById('newNoteInput');
      if(addBtn) addBtn.addEventListener('click', addNote);
      if(noteInput) noteInput.addEventListener('keydown', (e)=>{
        if(e.key === 'Enter'){ e.preventDefault(); addNote(); }
      });
      updateDiagViewNoteComposerState();
    })();

    // Load notes/media on page load
    window.addEventListener('DOMContentLoaded', loadNotes);
    window.addEventListener('DOMContentLoaded', loadVoiceNotes);
    window.addEventListener('DOMContentLoaded', loadVideos);
    window.addEventListener('DOMContentLoaded', loadLiveListings);
    window.addEventListener('DOMContentLoaded', loadHistory);
    window.addEventListener('DOMContentLoaded', ()=>{
      setupMobileUploadLink();
      setupScopedDownloadLink();
      refreshScopedPhotos({ silent: true });
      loadVoiceNotes();
      loadVideos();
      if(window.__diagScopedPhotoPollTimer){
        clearInterval(window.__diagScopedPhotoPollTimer);
      }
      window.__diagScopedPhotoPollTimer = window.setInterval(()=>{
        if(document.visibilityState !== 'hidden'){
          refreshScopedPhotos({ silent: true });
          loadVoiceNotes();
          loadVideos();
        }
      }, 15000);
    });
    document.addEventListener('visibilitychange', ()=>{
      if(document.visibilityState === 'visible'){
        refreshScopedPhotos({ silent: true });
        loadVoiceNotes();
        loadVideos();
      }
    });

    (function(){
      const refreshBtn = document.getElementById('refreshHistoryBtn');
      if(!refreshBtn) return;
      refreshBtn.addEventListener('click', loadHistory);
    })();

    (function(){
      const refreshLiveBtn = document.getElementById('refreshLiveListingsBtn');
      if(!refreshLiveBtn) return;
      refreshLiveBtn.addEventListener('click', loadLiveListings);
    })();

    // Upload photos handling (auto-upload on file selection)
    (function(){
      const input = document.getElementById('uploadPhotosInput');
      const statusEl = document.getElementById('uploadStatus');
      if(!input) return;
      function showStatus(on){ if(!statusEl) return; statusEl.style.display = on ? 'inline' : 'none'; }
      async function doUpload(){
        const files = Array.from(input.files || []);
        if(files.length === 0){ return; }
        try{
          input.disabled = true; showStatus(true);
          const fd = new FormData();
          files.forEach(f=> fd.append('photos[]', f));
          if(DIAG_VIEW_SELECTED_STATUS) fd.append('row_status', DIAG_VIEW_SELECTED_STATUS);
          const res = await fetch('/api/items_prep/diagnostic/{{ upc }}/photos', { method:'POST', body: fd });
          const j = await res.json().catch(()=>({success:false,error:'Invalid response'}));
          if(!(j && j.success)){
            alert('Upload failed: ' + (j && j.error || 'unknown'));
            input.disabled = false; showStatus(false);
            return;
          }
          await refreshScopedPhotos({ silent: true });
        }catch(e){
          alert('Upload error: ' + e);
        } finally {
          input.value = '';
          input.disabled = false; showStatus(false);
        }
      }
      input.addEventListener('change', doUpload);
    })();

    // Location functionality
    async function loadLocation(){
      const upc = '{{ upc }}'.trim();
      if(!upc) return;
      try{
        const lot = String(DIAG_VIEW_SELECTED_LOT || '').trim();
        const query = lot ? (`?lot=${encodeURIComponent(lot)}`) : '';
        const res = await fetch(`/api/items_prep/location/${encodeURIComponent(upc)}${query}`);
        const j = await res.json();
        if(j.success && (j.location || j.pictureposition)){
          const row = document.getElementById('locationRow');
          const text = document.getElementById('locationText');
          if(!row || !text) return;
          if(j.pictureposition){
            text.textContent = 'Picture Position (click to view)';
            text.style.cursor = 'pointer';
            text.onclick = ()=> showLocationImage(j.pictureposition);
          } else if(j.location){
            text.textContent = j.location.toUpperCase();
            text.style.cursor = 'pointer';
            text.onclick = ()=> showLocationImage(null, j.location);
          }
          row.style.display = 'block';
        }
      }catch(e){
        console.error('Failed to load location:', e);
      }
    }

    function showLocationImage(picturePath, shelfCode){
      if(picturePath){
        // Show the picture position image
        const modal = document.getElementById('imgModal');
        const modalImg = document.getElementById('modalImg');
        // Picture positions are in /static/pictureposition/ folder
        const imgPath = (picturePath.startsWith('/') ? picturePath : `/static/pictureposition/${picturePath}`) + `?t=${Date.now()}`;
        modalImg.src = imgPath;
        modal.style.display = 'flex';
      } else if(shelfCode){
        if (window.SSLocationPreview) {
          window.SSLocationPreview.openForLocation(shelfCode, '', `Location: ${String(shelfCode || '').toUpperCase()}`);
          return;
        }
        // Show shelf image from static/shelves/
        const modal = document.getElementById('imgModal');
        const modalImg = document.getElementById('modalImg');
        modalImg.src = `/shelf-image/${encodeURIComponent(shelfCode.toLowerCase())}.png?t=${Date.now()}`;
        modal.style.display = 'flex';
      }
    }

    // Load location on page load
    window.addEventListener('DOMContentLoaded', loadLocation);

    // Position button functionality
    (function(){
      const btn = document.getElementById('setPositionBtn');
      if(!btn) return;
      btn.addEventListener('click', ()=>{
        const upc = '{{ upc }}'.trim();
        const currentUrl = window.location.href;
        window.location.href = `/position?for_diagnostic=${encodeURIComponent(upc)}&return=${encodeURIComponent(currentUrl)}`;
      });
    })();

    // Done button functionality - navigate back
    (function(){
      const doneBtn = document.getElementById('doneBtn');
      if(!doneBtn) return;
      doneBtn.addEventListener('click', ()=>{
        try{
          const params = new URLSearchParams(window.location.search);
          const ret = params.get('return');
          if(ret){ window.location.href = ret; }
          else { window.location.href = '/item-prep?forceFocus=1'; }
        }catch(e){ window.location.href = '/item-prep?forceFocus=1'; }
      });
    })();

    // Quantity editing functionality
    (function(){
      const btnEdit = document.getElementById('btnEditQty');
      const btnSave = document.getElementById('btnSaveQty');
      const btnCancel = document.getElementById('btnCancelQty');
      const qtyDisplay = document.getElementById('qtyDisplay');
      const qtyValue = document.getElementById('qtyValue');
      const editPanel = document.getElementById('qtyEditPanel');
      const qtyInput = document.getElementById('qtyInput');
      const DIAG_UPC = {{ upc|tojson }};
      const DIAG_ITEM_ID = {{ (bol.id if bol and bol.id is not none else none)|tojson }};
      const DIAG_BOL_LOT = {{ (bol.lot_number if bol and bol.lot_number else '')|tojson }};
      const DIAG_SELECTED_LOT = {{ (selected_lot if selected_lot else '')|tojson }};

      if(!btnEdit || !btnSave || !btnCancel || !editPanel || !qtyInput) return;

      // Show edit panel
      btnEdit.addEventListener('click', ()=>{
        btnEdit.style.display = 'none';
        editPanel.style.display = 'flex';
        qtyInput.focus();
        qtyInput.select();
      });

      // Cancel edit
      btnCancel.addEventListener('click', ()=>{
        qtyInput.value = qtyValue.textContent;
        editPanel.style.display = 'none';
        btnEdit.style.display = 'inline-block';
      });

      // Save quantity
      async function saveQuantity(){
        const newQty = parseInt(qtyInput.value, 10);
        if(isNaN(newQty) || newQty < 1){
          alert('Please enter a valid quantity (1 or more)');
          return;
        }
        try{
          btnSave.disabled = true;
          btnSave.textContent = 'Saving...';
          const payload = { upc: DIAG_UPC, quantity: newQty };
          const lotFromUrl = new URLSearchParams(window.location.search || '').get('lot') || '';
          const targetLot = String(DIAG_BOL_LOT || DIAG_SELECTED_LOT || lotFromUrl || '').trim();
          if(targetLot) payload.lot_number = targetLot;
          if(Number.isFinite(DIAG_ITEM_ID) && DIAG_ITEM_ID > 0) payload.id = DIAG_ITEM_ID;
          const res = await fetch('/api/bol_items/quantity', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
          });
          const j = await res.json();
          if(j.success){
            qtyValue.textContent = newQty;
            // Update color based on quantity
            if(newQty > 1){
              qtyDisplay.style.color = '#e74c3c';
            } else {
              qtyDisplay.style.color = '#555';
            }
            editPanel.style.display = 'none';
            btnEdit.style.display = 'inline-block';
            await loadHistory();
          } else {
            alert('Failed to update quantity: ' + (j.error || 'unknown'));
          }
        }catch(e){
          alert('Error updating quantity: ' + e);
        } finally {
          btnSave.disabled = false;
          btnSave.textContent = 'Save';
        }
      }

      btnSave.addEventListener('click', saveQuantity);
      
      // Allow Enter key to save
      qtyInput.addEventListener('keydown', (e)=>{
        if(e.key === 'Enter'){
          e.preventDefault();
          saveQuantity();
        } else if(e.key === 'Escape'){
          btnCancel.click();
        }
      });
    })();
  

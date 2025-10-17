# Flask Development - Quick Verification Guide

## 🛠️ Tools Created

### 1. Python Script: `verify_routes.py`
Full route verification with import testing

**Usage:**
```bash
# Verify all routes
python verify_routes.py

# Check specific route/pattern
python verify_routes.py shelfcreator
python verify_routes.py api/create
```

### 2. PowerShell Helpers: `dev-helpers.ps1`
Convenient PowerShell functions for quick checks

**Load the helpers:**
```powershell
. .\dev-helpers.ps1
```

**Commands:**
```powershell
# Quick check file + routes
Quick-Check shelfcreator        # alias: qc

# Verify routes are registered
Verify-FlaskRoutes api/create   # alias: vfr

# Compare disk vs memory (catches sync issues!)
Compare-DiskVsMemory shelfcreator  # alias: cmp

# Check file status
Check-AppFile
```

---

## 🚨 When to Run Verification

### After Making Changes:
```powershell
# 1. Make your edits
# 2. Immediately verify:
cmp your_new_route

# 3. If mismatch detected, restart Flask server
```

### Before Debugging 404s:
```powershell
# Don't waste time debugging - verify first!
qc the_missing_route

# If it shows "On disk: ✓" but "In Flask: ✗"
# → File is fine, just restart your server
```

### After AI Edits:
```powershell
# AI said it added something? Verify immediately:
cmp the_new_feature

# If mismatch: THE EDIT DIDN'T STICK!
# Don't debug, just re-apply the edit manually
```

---

## 📋 Quick Workflow

### Adding a New Route:

1. **Before:**
   ```powershell
   Check-AppFile  # Note current line count
   ```

2. **Make changes** (via AI or manually)

3. **Verify immediately:**
   ```powershell
   cmp your_new_route
   ```

4. **If mismatch:**
   ```powershell
   # Check what's actually in the file:
   Select-String -Path app.py -Pattern "your_new_route" -Context 2
   
   # If missing, re-apply manually or via terminal
   ```

5. **Restart Flask and verify:**
   ```powershell
   # After restart:
   vfr your_new_route
   ```

---

## 🎯 The Golden Rules

1. **Always verify after edits** - Don't assume they stuck
2. **Check disk vs memory** - Catches the sync issue we had
3. **If 404 after restart** - Run verification FIRST before debugging
4. **Trust but verify** - Even if AI says "success", check with `cmp`

---

## 💡 Pro Tips

### Add to your PowerShell Profile:
```powershell
# Edit your profile
notepad $PROFILE

# Add this line:
. C:\Users\boxatron\Desktop\simpleInventory\dev-helpers.ps1

# Now helpers load automatically in every terminal!
```

### Quick aliases:
- `qc` = Quick-Check
- `vfr` = Verify-FlaskRoutes  
- `cmp` = Compare-DiskVsMemory

### Example workflow:
```powershell
# After AI adds a route:
cmp api/new_endpoint

# See all routes:
vfr

# Quick status:
qc
```

---

## 🐛 Troubleshooting

**Problem:** Route not found (404)
```powershell
cmp your_route
# Check what it shows - guides you to the fix
```

**Problem:** AI says edit succeeded but nothing changed
```powershell
Check-AppFile  # See if line count changed
# If not → edit didn't stick, use manual approach
```

**Problem:** Import errors
```powershell
python verify_routes.py
# Shows the exact import error
```

---

## 📝 Files Created

- `verify_routes.py` - Python verification script
- `dev-helpers.ps1` - PowerShell helper functions  
- `VERIFICATION_GUIDE.md` - This guide

Keep these in your project root for easy access!

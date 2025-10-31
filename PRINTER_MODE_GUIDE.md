# Printer Mode Configuration Guide

Your inventory system now supports **two printer modes**: **Thermal** and **Inkjet**. Choose the correct mode based on your printer type for optimal barcode quality.

---

## 🔥 Thermal Mode

**For:** Label printers, receipt printers, and specialized barcode printers

### Characteristics:
- **1-bit black/white** printing (no grayscale)
- **Lower resolution** (384-576 pixels wide)
- **ESC/POS protocol** (standard for thermal printers)
- **Direct thermal or thermal transfer** printing
- Uses `bitImageColumn` method for crisp barcodes

### Compatible Printers:
- ✅ **Zebra** ZD410, ZD420, ZD620 series
- ✅ **MUNBYN** label printers (M220, M230, etc.)
- ✅ **Phomemo** M110, M220, M221
- ✅ **POLONO** thermal printers
- ✅ **Rollo** label printers
- ✅ **Epson TM-series** (TM-T20, TM-T88, etc.)
- ✅ **Star Micronics** TSP series
- ✅ 58mm/80mm receipt printers

### Use Cases:
- Shipping labels
- Barcode stickers
- Inventory labels
- Receipt printing
- Warehouse operations

### Print Quality:
- **Fast printing** (seconds per label)
- **High contrast** black/white
- **Optimized for scanning**
- **No ink/toner needed**
- Smaller file size

---

## 🖨️ Inkjet Mode

**For:** Home/office all-in-one printers and standard desktop printers

### Characteristics:
- **Full color/RGB** support
- **Higher resolution** (up to 800 pixels wide)
- **Better quality** for complex images
- Uses `high_density` printing for better detail
- Paper feeding instead of cutting

### Compatible Printers:
- ✅ **HP** Envy, OfficeJet, DeskJet series
- ✅ **Canon** PIXMA, MAXIFY series
- ✅ **Epson** EcoTank, Expression Home
- ✅ **Brother** MFC series
- ✅ Most home/office printers with network or USB

### Use Cases:
- Testing barcode designs
- Full-page labels
- Color product labels
- Mixed text/image documents
- Temporary solution before getting thermal printer

### Print Quality:
- **Slower printing** (10-30 seconds per page)
- **Higher detail** with grayscale
- **Color support** (if needed)
- **Requires ink/toner**
- Better for mixed content

---

## Configuration Examples

### Example 1: HP Envy (Your Current Setup)
```
Printer Type: Network Printer (WiFi)
Printer Mode: Inkjet
Network IP: 192.168.1.150
Port: 9100
```

**What to expect:**
- Barcode will print on full page
- Higher quality grayscale
- Use scissors to cut labels
- Good for testing

### Example 2: Zebra ZD420 (Future Setup)
```
Printer Type: Network Printer (WiFi) or Bluetooth
Printer Mode: Thermal
Network IP: 192.168.1.151 (or COM port)
Port: 9100
```

**What to expect:**
- Direct label printing
- Fast, crisp barcodes
- Automatic cutting
- Perfect for production

### Example 3: MUNBYN Bluetooth Label Printer
```
Printer Type: Bluetooth/Serial
Printer Mode: Thermal
Port: COM3 (Windows) or /dev/rfcomm0 (Linux)
```

---

## Choosing the Right Mode

### Use **THERMAL MODE** if:
- ✅ You have a dedicated label/barcode printer
- ✅ You print many labels per day
- ✅ You need fast, automated printing
- ✅ You want crisp, scannable barcodes
- ✅ Cost per label matters (no ink needed)

### Use **INKJET MODE** if:
- ✅ You're using an HP/Canon/Epson home printer
- ✅ You're testing the system before buying thermal printer
- ✅ You need occasional labels only
- ✅ You want to print on regular paper
- ✅ You need color for product identification

---

## Technical Differences

| Feature | Thermal Mode | Inkjet Mode |
|---------|-------------|-------------|
| **Image Format** | 1-bit B&W | RGB Color/Grayscale |
| **Max Width** | 384-576 px | 800+ px |
| **Print Method** | `bitImageColumn` | `high_density` |
| **Paper Feed** | Auto-cut | Manual feed |
| **Resolution** | Optimized for thermal | Higher quality |
| **Speed** | Very fast | Moderate |
| **Dithering** | Yes (for smooth edges) | No (preserves detail) |

---

## Migration Path: HP Envy → Zebra

**Phase 1: Testing (Current)**
1. Configure HP Envy in **Inkjet Mode**
2. Test barcode generation and printing
3. Verify barcodes scan correctly
4. Get comfortable with the system

**Phase 2: Production (Future)**
1. Purchase Zebra/thermal printer
2. Connect to network or Bluetooth
3. Change **Printer Mode** to **Thermal**
4. Enter new printer IP/port
5. Test print - barcodes will be crisper!

**No code changes needed** - just change the setting!

---

## Troubleshooting

### Issue: "Symbols printing instead of barcode" (Thermal)
- ✅ Make sure **Printer Mode = Thermal**
- ✅ Image should be converted to 1-bit B&W
- ✅ Check print method is `bitImageColumn`

### Issue: "Low quality barcode" (Inkjet)
- ✅ Make sure **Printer Mode = Inkjet**
- ✅ Image should be RGB/grayscale
- ✅ Check printer DPI settings (use highest quality)

### Issue: "Barcode too small/large"
**Thermal:**
- Adjust `max_width = 384` (58mm) or `576` (80mm)

**Inkjet:**
- Adjust `max_width = 800` for larger barcodes

### Issue: "Paper not cutting" (Inkjet)
- Expected behavior - inkjet printers don't auto-cut
- System will feed 5 blank lines instead
- Use scissors or perforated label sheets

---

## Recommended Setup for Production

**Best Configuration:**
```
Primary: Zebra ZD420 (Thermal, Network)
├── For: Daily inventory labels
├── Location: Warehouse/packing station
└── Mode: Thermal

Backup: HP Envy (Inkjet, Network)
├── For: Emergency/testing
├── Location: Office
└── Mode: Inkjet
```

**Switch between them** in settings as needed!

---

## Cost Comparison

### Thermal Printing
- Initial: $150-500 (printer)
- Per label: $0.01-0.03
- Speed: 1-2 seconds
- Maintenance: Minimal

### Inkjet Printing
- Initial: $50-300 (if you don't have one)
- Per label: $0.10-0.25 (ink cost)
- Speed: 10-30 seconds
- Maintenance: Ink refills, cleaning

**For high-volume printing, thermal pays for itself in months!**

---

## Next Steps

1. **Test current setup** (HP Envy in Inkjet mode)
2. **Verify barcodes scan** with a scanner app
3. **Evaluate printing volume** (labels per day/week)
4. **Budget for thermal printer** if volume justifies it
5. **Migrate to thermal** when ready (just change settings!)

The system is ready for both - no additional setup needed! 🎉

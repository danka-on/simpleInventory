# Browser Print Mode Guide

## Overview
The inventory system now supports **two printing methods** for barcode labels:

1. **🖨️ Browser Print** (Recommended for most users)
2. **⚡ ESC/POS Direct** (Advanced - for thermal printers)

## Browser Print Mode

### What is it?
Browser Print opens a standard browser print dialog, allowing you to print barcodes using **any printer** connected to your device - including WiFi printers, Bluetooth printers, and standard inkjet/laser printers.

### Benefits
- ✅ **Works with ANY printer** (HP, Canon, Epson, Brother, Zebra, etc.)
- ✅ **No driver configuration needed**
- ✅ **Works from iPad, tablet, phone, or desktop**
- ✅ **Preview before printing**
- ✅ **Choose print settings** (copies, paper size, etc.)
- ✅ **Can print multiple barcodes at once** (future feature)
- ✅ **Save as PDF** if no printer available

### How to Enable
1. Go to **Printer Settings** in the app
2. Select **Print Method**: `Browser Print (Recommended)`
3. Click **Save Configuration**

That's it! No other settings needed.

### How it Works
When you click "Print Barcode" in item creation:
1. A new browser window opens with your barcode label
2. The browser's print dialog appears automatically
3. Select your printer and adjust settings
4. Click "Print" in the dialog
5. The barcode prints to your chosen printer

### Label Format
Browser print creates a **4" × 2"** label with:
- Item description (top)
- Barcode with human-readable code (bottom)

### Compatibility
- **Desktop browsers**: Chrome, Edge, Firefox, Safari
- **Mobile browsers**: Safari (iOS), Chrome (Android)
- **Tablets**: iPad, Android tablets
- **Printers**: Any printer accessible from your device

### Tips
- **iPad/iPhone**: Make sure your printer is added in Settings → Printers
- **Android**: Ensure printer is discoverable or connected via WiFi
- **Desktop**: Use your system's print dialog to select any printer
- **No printer?**: Choose "Save as PDF" in the print dialog

## ESC/POS Direct Mode

### What is it?
ESC/POS Direct sends raw barcode data directly to a thermal label printer using the ESC/POS protocol.

### When to Use
- You have a **dedicated thermal label printer** (Zebra, DYMO, etc.)
- You need **very fast printing** (no dialog, instant print)
- You want **precise thermal printer control**

### Setup Required
1. Configure printer connection (Bluetooth/Serial/USB/Network)
2. Select printer type and mode
3. Test connection
4. Configure network settings if using WiFi

See [IPAD_NETWORK_PRINTER_SETUP.md](IPAD_NETWORK_PRINTER_SETUP.md) for detailed setup.

## Switching Between Modes

You can switch between Browser and ESC/POS modes at any time:

1. Go to **Printer Settings**
2. Change **Print Method** dropdown
3. Save configuration

The app will immediately use the new method for all future prints.

## Troubleshooting

### Browser Print Issues

**Print dialog doesn't appear?**
- Check if popup blocker is enabled (allow popups for this site)
- Try again - the window may have opened behind another window

**Wrong printer selected?**
- Change printer in the print dialog dropdown
- Set your default printer in system settings

**Label too small/large?**
- Adjust print scale in print dialog (try 100%, 90%, or custom)
- Change paper size if your printer supports custom sizes

**iPad can't find printer?**
- Go to Settings → Printers & Scanners
- Make sure printer is on same WiFi network
- Add printer if not listed

### ESC/POS Issues

**Connection errors?**
- Check network connection (ping printer IP)
- Verify printer is powered on
- Test connection in printer settings
- Check firewall settings

**Barcode prints but looks wrong?**
- Verify printer mode (thermal vs inkjet)
- Check printer type selection
- See [PRINTER_MODE_GUIDE.md](PRINTER_MODE_GUIDE.md)

## Migration Path

### Current Setup: HP Envy (Inkjet)
**Recommended**: Use Browser Print
- No configuration needed
- Print from any device on your network
- Works with your existing HP Envy

### Future Setup: Zebra (Thermal)
**Option 1**: Keep Browser Print
- Simple, works with Zebra thermal printers too
- Use browser print dialog

**Option 2**: Switch to ESC/POS Direct
- Faster printing
- No dialogs
- Requires network/USB configuration

## Technical Details

### Browser Print Process
1. User clicks "Print Barcode"
2. App opens new window with HTML/SVG barcode
3. JsBarcode library renders barcode as SVG
4. window.print() triggers browser print dialog
5. User selects printer and prints
6. Window closes after printing

### ESC/POS Process
1. User clicks "Print Barcode"
2. App sends POST to /api/printer/print-barcode
3. Python generates barcode image
4. Image converted to printer-specific format
5. Raw data sent to printer via network/serial
6. Printer immediately prints label

### Code Libraries
- **Browser Print**: JsBarcode (CDN) - https://github.com/lindell/JsBarcode
- **ESC/POS Print**: python-escpos, python-barcode, Pillow

## Comparison Table

| Feature | Browser Print | ESC/POS Direct |
|---------|--------------|----------------|
| Setup time | 0 minutes | 10-30 minutes |
| Printer compatibility | Any printer | ESC/POS thermal only |
| Speed | 3-10 seconds | 1-2 seconds |
| Preview | Yes | No |
| Multi-device | Yes (any device) | Server only |
| Configuration | None | Network/serial settings |
| Print dialog | Yes | No |
| iPad support | Native | Via network |
| Offline | No (needs browser) | Yes (if local) |

## Conclusion

**For most users**: Start with **Browser Print**. It's simple, works everywhere, and requires zero setup.

**For power users**: Once you have a dedicated thermal printer and need speed, switch to **ESC/POS Direct**.

Both methods produce identical barcodes - the difference is in how they get to your printer!

# iPad/Mobile Network Printer Setup Guide

This guide explains how to use barcode printing from your iPad or other mobile devices via **network-enabled thermal printers**.

## Overview

Your Flask app now supports **two printer connection modes**:

1. **Direct Connection** (Bluetooth/Serial/USB) - Printer connects directly to the server running Flask
2. **Network Connection** (WiFi/Ethernet) - Printer on network, accessible from iPad/mobile devices

## For iPad/Mobile Users: Network Printer Setup

### Prerequisites

- A **WiFi-enabled thermal printer** that supports ESC/POS protocol
- The printer and your Flask server must be on the **same WiFi network**
- Your iPad/mobile device must also be on the same network

### Compatible Printers

Most modern thermal/label printers with WiFi support ESC/POS over TCP:

- **MUNBYN** WiFi label printers (M220, M230, etc.)
- **Phomemo** M110/M220 WiFi models
- **POLONO** WiFi thermal printers
- **Rollo** WiFi label printers
- **Epson TM-series** with network interface
- **Star Micronics** WiFi models
- Most 58mm/80mm thermal receipt printers with WiFi

### Step-by-Step Setup

#### 1. Connect Printer to WiFi

**Option A: Using Printer Menu (Most Common)**
```
1. Turn on printer
2. Press Menu/Settings button
3. Navigate to Network Settings → WiFi
4. Select your WiFi network
5. Enter WiFi password
6. Wait for connection confirmation
```

**Option B: Using Printer's Mobile App**
```
1. Download printer manufacturer's app (e.g., "MUNBYN Print", "Phomemo", etc.)
2. Follow app's WiFi setup wizard
3. Connect printer to your home/office network
```

#### 2. Find Printer's IP Address

**Method 1: Print Configuration Page**
```
1. Access printer menu
2. Select "Print Configuration" or "Network Info"
3. Printer will print a page showing:
   - IP Address (e.g., 192.168.1.150)
   - MAC Address
   - Port (usually 9100)
```

**Method 2: Check Your Router**
```
1. Log into your WiFi router admin page
2. Look for "Connected Devices" or "DHCP Clients"
3. Find your printer by name/MAC address
4. Note the IP address assigned
```

**Method 3: Use Printer's App**
```
1. Open printer manufacturer's app
2. Go to printer settings/info
3. View network configuration
4. Note the IP address
```

#### 3. Configure in Flask App

1. **Access Printer Settings:**
   - From your computer: `http://localhost:5000/printer-settings`
   - From iPad: `http://[SERVER-IP]:5000/printer-settings`
     - Replace `[SERVER-IP]` with your Flask server's IP (e.g., `http://192.168.1.100:5000`)

2. **Select Network Printer:**
   - Choose "Network Printer (WiFi/Ethernet - For iPad/Mobile)" from dropdown

3. **Enter Printer Details:**
   - **Printer IP Address:** Enter the IP you found (e.g., `192.168.1.150`)
   - **Port:** Leave as `9100` (default for ESC/POS)
   - **Printer Name:** Optional friendly name

4. **Save Configuration:**
   - Click "Save Configuration"

5. **Test Print:**
   - Click "Test Print"
   - Your printer should print a test label

#### 4. Access from iPad

1. **Open Safari on your iPad**
2. **Navigate to Flask app:**
   ```
   http://[SERVER-IP]:5000
   ```
   Example: `http://192.168.1.100:5000`

3. **Use the app normally:**
   - Scan barcodes
   - Create items
   - Print labels
   - All print jobs will be sent to the network printer

4. **Optional: Add to Home Screen**
   - In Safari, tap the Share button
   - Select "Add to Home Screen"
   - Name it (e.g., "Inventory Manager")
   - Now you can launch like a native app!

## Troubleshooting

### Printer Not Found / Connection Failed

**Check 1: Same Network**
```bash
# On your Flask server, verify you can reach printer:
ping 192.168.1.150

# If ping fails, printer and server are on different networks
```

**Check 2: Firewall**
```bash
# Windows: Allow port 9100 through firewall
# Linux: Check iptables rules
sudo iptables -L
```

**Check 3: Printer Status**
- Verify printer is powered on
- Check WiFi connection LED on printer
- Print network config page to confirm settings

**Check 4: Port Number**
- Most ESC/POS printers use port 9100
- Some might use 9101, 9102, or 8080
- Check printer manual or configuration page

### Test Print Works But App Prints Don't

**Issue:** Configuration saved but app not using network printer

**Solution:**
```python
# Verify printer_manager is using network mode
# Check database:
sqlite3 bol.db
SELECT * FROM printer_config;
# Should show printer_type='network'
```

### iPad Can't Access Flask App

**Issue:** iPad shows "Cannot connect to server"

**Solution 1: Find Server IP**
```bash
# Windows:
ipconfig
# Look for "IPv4 Address" under your WiFi adapter

# Linux/Mac:
ifconfig
# or
ip addr show
```

**Solution 2: Flask Binding**
Make sure Flask is listening on all interfaces:
```python
# In app.py, ensure:
app.run(host="0.0.0.0", port=5000)
```

**Solution 3: Firewall**
```bash
# Windows: Allow port 5000 in Windows Defender Firewall
# Add inbound rule for TCP port 5000

# Linux:
sudo ufw allow 5000/tcp
```

### Print Quality Issues

**Issue:** Barcode prints but is blurry or unreadable

**Solution:**
```python
# Adjust barcode generation settings in printer_manager.py
# Look for generate_barcode_image() method
# Increase DPI or module width
```

## Static IP Recommendation

To avoid IP changes, assign a **static IP** to your printer:

### Router Method (Recommended)
1. Log into your router admin page
2. Find "DHCP Reservation" or "Static IP" settings
3. Reserve an IP for printer's MAC address
4. Example: Always assign `192.168.1.150` to printer

### Printer Method
1. Access printer's network settings
2. Change from DHCP to Static IP
3. Set IP, Subnet Mask, Gateway
4. Example:
   - IP: `192.168.1.150`
   - Subnet: `255.255.255.0`
   - Gateway: `192.168.1.1`

## Security Considerations

**Network Printer Security:**
- Printer is accessible to anyone on your network
- Use a secure/private WiFi network
- Consider setting up a guest network for devices
- Use VPN if accessing remotely

**Flask App Security:**
- Currently no authentication implemented
- Anyone on network can access
- Consider adding login/authentication for production use
- Use HTTPS in production

## Advanced: Multiple Location Setup

If you have multiple workstations/locations:

**Server Location (Main Computer):**
```
1. Configure as Network Printer
2. IP: 192.168.1.150
3. Access from: http://SERVER-IP:5000
```

**iPad/Mobile Warehouse:**
```
1. Connect to same WiFi
2. Open: http://SERVER-IP:5000
3. Print labels to network printer
```

**Raspberry Pi at Packaging Station:**
```
1. Run separate Flask instance
2. Configure direct Bluetooth to local printer
3. Or connect to network printer
```

## Cost-Effective Setup

**Budget Option:**
- WiFi thermal printer: $60-$150
- Raspberry Pi 4 (server): $35-$75
- iPad (optional, use existing): $0-$329
- Total: $95-$554

**Alternative:**
- Direct Bluetooth to iPad using printer's app (outside this system)
- Or use Windows PC as server + network printer

## Testing Checklist

- [ ] Printer connected to WiFi
- [ ] Printer IP address identified
- [ ] Flask server accessible from iPad
- [ ] Printer settings saved in Flask app
- [ ] Test print successful
- [ ] Barcode print from item creation works
- [ ] Print quality acceptable
- [ ] iPad can access app on network
- [ ] Multiple devices can print simultaneously

## Next Steps

Once network printing works:
1. Create barcode labels for inventory
2. Use iPad for mobile scanning/printing
3. Set up backup printer if needed
4. Consider auto-start Flask on server boot
5. Implement user authentication for security

## Support

Common printer manufacturers' support:
- **MUNBYN:** https://www.munbyn.com/pages/support
- **Phomemo:** https://phomemo.com/pages/support
- **Rollo:** https://www.rolloprinter.com/support/

For ESC/POS protocol issues:
- python-escpos docs: https://python-escpos.readthedocs.io/

---

**Need Help?** Check the main `RASPBERRY_PI_PRINTER_SETUP.md` for additional troubleshooting tips.

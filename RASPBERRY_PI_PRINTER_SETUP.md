# Raspberry Pi Bluetooth Printer Setup Guide

This application now supports **cross-platform** Bluetooth barcode printing on both **Windows** and **Raspberry Pi/Linux**.

## Platform Detection

The system automatically detects your platform and adjusts:
- **Windows**: Uses COM ports (e.g., COM3, COM4)
- **Raspberry Pi/Linux**: Uses device paths (e.g., /dev/rfcomm0, /dev/ttyUSB0)

## Raspberry Pi Setup Instructions

### 1. Install System Dependencies

```bash
sudo apt-get update
sudo apt-get install -y python3-pip bluetooth libbluetooth-dev python3-dev
```

### 2. Install Python Packages

```bash
cd /path/to/simpleInventory
pip3 install -r requirements.txt

# Optional: For Bluetooth device scanning
pip3 install pybluez
```

### 3. Pair Your Bluetooth Printer

**Method 1: Using bluetoothctl (Recommended)**
```bash
sudo bluetoothctl
> scan on
# Wait for your printer to appear (note the MAC address)
> pair XX:XX:XX:XX:XX:XX
> connect XX:XX:XX:XX:XX:XX
> trust XX:XX:XX:XX:XX:XX
> exit
```

**Method 2: Using Raspberry Pi Desktop**
- Click Bluetooth icon in taskbar
- Make device discoverable
- Add device
- Select your printer

### 4. Create RFCOMM Device

```bash
# Bind Bluetooth printer to /dev/rfcomm0
sudo rfcomm bind 0 XX:XX:XX:XX:XX:XX

# Give permission to access the device
sudo chmod 666 /dev/rfcomm0

# Make it persistent (add to /etc/rc.local before 'exit 0'):
echo "rfcomm bind 0 XX:XX:XX:XX:XX:XX" | sudo tee -a /etc/rc.local
```

### 5. Configure in Web App

1. Start the Flask app:
   ```bash
   python3 app.py
   ```

2. Navigate to Printer Settings:
   - Go to `http://localhost:5000/printer-settings`
   - Or click Tools → Printer Settings

3. Enter device path:
   - For RFCOMM: `/dev/rfcomm0`
   - For USB: `/dev/ttyUSB0`
   - For Serial: `/dev/ttyAMA0`
   - Or click "Scan for Devices"

4. Save and Test Print

## Supported Connection Methods

### Bluetooth via RFCOMM
```
Device: /dev/rfcomm0
Most common for Bluetooth thermal printers
```

### USB Thermal Printer
```
Device: /dev/ttyUSB0 or /dev/usb/lp0
Automatically detected if plugged in
```

### Serial Port
```
Device: /dev/ttyAMA0 or /dev/ttyS0
For RS232 serial printers
```

### USB by Vendor/Product ID
```
Device: 0x0416:0x5011
Format: vendor_id:product_id in hex
```

## Troubleshooting

### Permission Denied Error
```bash
# Give your user permission to access serial devices
sudo usermod -a -G dialout $USER
sudo usermod -a -G bluetooth $USER

# Or set permissions on specific device
sudo chmod 666 /dev/rfcomm0
```

### Bluetooth Not Connecting
```bash
# Restart Bluetooth service
sudo systemctl restart bluetooth

# Check Bluetooth status
sudo systemctl status bluetooth

# Re-pair the device
sudo bluetoothctl
> remove XX:XX:XX:XX:XX:XX
> scan on
> pair XX:XX:XX:XX:XX:XX
```

### RFCOMM Device Not Persisting After Reboot
```bash
# Add to /etc/rc.local (before 'exit 0')
sudo nano /etc/rc.local

# Add these lines:
rfcomm bind 0 XX:XX:XX:XX:XX:XX
chmod 666 /dev/rfcomm0

# Make rc.local executable
sudo chmod +x /etc/rc.local
```

### Finding USB Printer IDs
```bash
# List USB devices
lsusb

# Find printer (look for vendor:product ID)
# Example output: Bus 001 Device 004: ID 0416:5011 Winbond Electronics Corp

# Use format: 0x0416:0x5011 in the web interface
```

## Auto-Start on Raspberry Pi Boot

Create a systemd service:

```bash
sudo nano /etc/systemd/system/inventory-app.service
```

Add:
```ini
[Unit]
Description=Simple Inventory Flask App
After=network.target bluetooth.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/simpleInventory
ExecStartPre=/bin/sleep 10
ExecStartPre=/usr/bin/rfcomm bind 0 XX:XX:XX:XX:XX:XX
ExecStartPre=/bin/chmod 666 /dev/rfcomm0
ExecStart=/usr/bin/python3 /home/pi/simpleInventory/app.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable inventory-app
sudo systemctl start inventory-app
```

## Testing

Test barcode generation without printer:
```bash
python3 test_printer_setup.py
```

This will create a test barcode image at:
`static/barcodes/test_012345678905.png`

## Supported Printers

Any ESC/POS compatible thermal printer with Bluetooth, USB, or Serial connection:
- MUNBYN label printers
- Phomemo M110/M220
- POLONO thermal printers  
- Rollo label printers
- Epson TM series
- Star Micronics
- Most 58mm/80mm thermal receipt printers

## Platform Differences

| Feature | Windows | Raspberry Pi/Linux |
|---------|---------|-------------------|
| Connection | COM ports | Device paths |
| Bluetooth | Virtual COM port | RFCOMM binding |
| USB | Auto COM port | /dev/ttyUSB0 |
| Scanning | COM port list | Bluetooth + device scan |
| Setup | Device Manager | bluetoothctl |

The web interface automatically adjusts instructions based on your platform!

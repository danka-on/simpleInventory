# 🥧 Raspberry Pi Transfer Guide

## Complete guide to transferring your Sweet Shelves inventory system to a Raspberry Pi

---

## 📋 Prerequisites

### Hardware Required
- **Raspberry Pi 4** (recommended: 4GB or 8GB RAM model)
- **MicroSD Card** (32GB or larger, Class 10 or better)
- **Power Supply** (official Raspberry Pi USB-C power supply)
- **Bluetooth Thermal Printer** (for barcode printing)
- **Network Connection** (Ethernet or WiFi)
- Optional: Case with fan for better cooling

### Software Required
- **Raspberry Pi OS** (64-bit Lite or Desktop version)
- SSH client (PuTTY for Windows, built-in Terminal for Mac/Linux)

---

## 🔧 Part 1: Raspberry Pi Setup

### Step 1: Install Raspberry Pi OS

1. **Download Raspberry Pi Imager**
   - Get it from: https://www.raspberrypi.com/software/
   - Install on your computer

2. **Flash the SD Card**
   ```
   - Insert microSD card into your computer
   - Open Raspberry Pi Imager
   - Choose OS: "Raspberry Pi OS (64-bit)"
   - Choose Storage: Your SD card
   - Click Settings (gear icon):
     ✓ Enable SSH
     ✓ Set username: pi
     ✓ Set password: [your-secure-password]
     ✓ Configure WiFi (optional)
     ✓ Set hostname: sweetshelvespi
   - Click WRITE
   ```

3. **First Boot**
   ```
   - Insert SD card into Raspberry Pi
   - Connect power, ethernet (if not using WiFi)
   - Wait 2-3 minutes for first boot
   - Find Pi's IP address (check your router or use: ping sweetshelvespi.local)
   ```

### Step 2: Connect via SSH

**From Windows:**
```powershell
ssh pi@sweetshelvespi.local
# or
ssh pi@192.168.1.XXX
```

**Enter the password you set during imaging**

### Step 3: Update System

```bash
# Update package lists
sudo apt update

# Upgrade all packages
sudo apt upgrade -y

# Install essential tools
sudo apt install -y git vim curl wget
```

---

## 🐍 Part 2: Python Environment Setup

### Step 1: Install Python 3.11+

```bash
# Check Python version
python3 --version

# If Python < 3.11, install latest:
sudo apt install -y python3 python3-pip python3-venv python3-dev

# Install system dependencies for Python packages
sudo apt install -y build-essential libssl-dev libffi-dev \
    libjpeg-dev zlib1g-dev libxml2-dev libxslt1-dev \
    libbluetooth-dev libcups2-dev
```

### Step 2: Create Application Directory

```bash
# Create app directory
sudo mkdir -p /opt/sweetshelves
sudo chown pi:pi /opt/sweetshelves
cd /opt/sweetshelves
```

---

## 📦 Part 3: Transfer Application Files

### Option A: Using Git (Recommended)

```bash
cd /opt/sweetshelves

# If you have a GitHub repository:
git clone https://github.com/YOUR-USERNAME/simpleInventory.git .

# If not, initialize repo on Windows first:
# On your Windows machine in the project folder:
# git init
# git add .
# git commit -m "Initial commit"
# git remote add origin https://github.com/YOUR-USERNAME/simpleInventory.git
# git push -u origin main
```

### Option B: Using SCP/SFTP

**From your Windows machine (PowerShell):**

```powershell
# Navigate to your project directory
cd C:\Users\boxatron\Desktop\simpleInventory

# Transfer entire directory to Pi
scp -r * pi@sweetshelvespi.local:/opt/sweetshelves/

# Or use WinSCP GUI tool (easier):
# Download from: https://winscp.net/
# Connect to: sweetshelvespi.local
# Drag and drop files from Windows to /opt/sweetshelves/
```

### Step 3: Create Python Virtual Environment

```bash
cd /opt/sweetshelves

# Create virtual environment
python3 -m venv .venv

# Activate it
source .venv/bin/activate

# Upgrade pip
pip install --upgrade pip
```

### Step 4: Install Python Dependencies

```bash
# Install all required packages
pip install -r requirements.txt

# If any package fails, install system dependencies:
sudo apt install -y python3-pandas python3-numpy python3-lxml
```

---

## 🔐 Part 4: Configuration Files

### Step 1: Transfer Credentials

**Copy from Windows to Pi:**

```powershell
# From Windows PowerShell:
scp .env pi@sweetshelvespi.local:/opt/sweetshelves/
scp credentials.json pi@sweetshelvespi.local:/opt/sweetshelves/
scp tokens.json pi@sweetshelvespi.local:/opt/sweetshelves/
scp amazon_credentials.json pi@sweetshelvespi.local:/opt/sweetshelves/
```

### Step 2: Verify Permissions

```bash
# On the Pi:
cd /opt/sweetshelves
chmod 600 .env credentials.json tokens.json amazon_credentials.json
```

---

## 💾 Part 5: Database Transfer

### Step 1: Transfer Database Files

**From Windows:**

```powershell
# Transfer all .db files
scp *.db pi@sweetshelvespi.local:/opt/sweetshelves/
```

**On the Pi:**

```bash
cd /opt/sweetshelves

# Verify databases
ls -lh *.db

# Expected files:
# - searchRack.db
# - sold.db
# - amazonStore.db
# - ebayStore.db
# - bol.db
# - rawbol.db
# - deleted.db
# - removed.db
# - sync_settings.db
```

### Step 2: Test Database Integrity

```bash
# Check each database
sqlite3 searchRack.db "PRAGMA integrity_check;"
sqlite3 sold.db "PRAGMA integrity_check;"
sqlite3 amazonStore.db "PRAGMA integrity_check;"
sqlite3 ebayStore.db "PRAGMA integrity_check;"

# Should all return: ok
```

---

## 🖨️ Part 6: Bluetooth Printer Setup

### Step 1: Install Bluetooth Tools

```bash
sudo apt install -y bluetooth bluez python3-bluez cups

# Enable Bluetooth
sudo systemctl enable bluetooth
sudo systemctl start bluetooth
```

### Step 2: Pair Printer

```bash
# Start Bluetooth control
bluetoothctl

# In bluetoothctl:
power on
agent on
scan on

# Wait for your printer to appear (e.g., "POS-58")
# Note the MAC address (XX:XX:XX:XX:XX:XX)

pair XX:XX:XX:XX:XX:XX
trust XX:XX:XX:XX:XX:XX
connect XX:XX:XX:XX:XX:XX
exit
```

### Step 3: Configure Printer in Application

```bash
# Edit printer settings in your .env file
nano .env

# Add or update:
PRINTER_ADDRESS=XX:XX:XX:XX:XX:XX
PRINTER_MODE=bluetooth
```

---

## 🚀 Part 7: Running the Application

### Option A: Manual Start (for testing)

```bash
cd /opt/sweetshelves
source .venv/bin/activate
python app.py
```

**Test in browser:**
- From same network: http://sweetshelvespi.local:8080
- Or: http://192.168.1.XXX:8080

### Option B: Production with Gunicorn

```bash
# Create gunicorn config
nano gunicorn_config.py
```

**Add this content:**

```python
bind = "0.0.0.0:8080"
workers = 2
worker_class = "sync"
timeout = 120
keepalive = 5
errorlog = "/opt/sweetshelves/logs/gunicorn-error.log"
accesslog = "/opt/sweetshelves/logs/gunicorn-access.log"
loglevel = "info"
```

**Create logs directory:**

```bash
mkdir -p /opt/sweetshelves/logs
```

**Run with Gunicorn:**

```bash
gunicorn -c gunicorn_config.py app:app
```

---

## ⚙️ Part 8: Autostart with Systemd

### Step 1: Create Service File

```bash
sudo nano /etc/systemd/system/sweetshelves.service
```

**Add this content:**

```ini
[Unit]
Description=Sweet Shelves Inventory System
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/opt/sweetshelves
Environment="PATH=/opt/sweetshelves/.venv/bin"
ExecStart=/opt/sweetshelves/.venv/bin/gunicorn -c gunicorn_config.py app:app
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### Step 2: Enable and Start Service

```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable service to start on boot
sudo systemctl enable sweetshelves.service

# Start service now
sudo systemctl start sweetshelves.service

# Check status
sudo systemctl status sweetshelves.service
```

### Step 3: Service Management Commands

```bash
# Stop the service
sudo systemctl stop sweetshelves.service

# Restart the service
sudo systemctl restart sweetshelves.service

# View logs
sudo journalctl -u sweetshelves.service -f

# View last 100 lines
sudo journalctl -u sweetshelves.service -n 100
```

---

## 🌐 Part 9: Network Access & Cloudflare Tunnel

### Option A: Local Network Only

**Access from any device on your network:**
- http://sweetshelvespi.local:8080
- Or: http://192.168.1.XXX:8080

### Option B: Cloudflare Tunnel (for remote access)

```bash
# Install cloudflared
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb
sudo dpkg -i cloudflared-linux-arm64.deb

# Login to Cloudflare
cloudflared tunnel login

# Create tunnel
cloudflared tunnel create sweetshelves

# Route DNS
cloudflared tunnel route dns sweetshelves sweetshelves.yourdomain.com

# Create config
mkdir -p ~/.cloudflared
nano ~/.cloudflared/config.yml
```

**Add this content:**

```yaml
tunnel: YOUR-TUNNEL-ID
credentials-file: /home/pi/.cloudflared/YOUR-TUNNEL-ID.json

ingress:
  - hostname: sweetshelves.yourdomain.com
    service: http://localhost:8080
  - service: http_status:404
```

**Create systemd service for tunnel:**

```bash
sudo nano /etc/systemd/system/cloudflared.service
```

```ini
[Unit]
Description=Cloudflare Tunnel
After=network.target

[Service]
Type=simple
User=pi
ExecStart=/usr/local/bin/cloudflared tunnel run sweetshelves
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Enable tunnel:**

```bash
sudo systemctl enable cloudflared.service
sudo systemctl start cloudflared.service
```

---

## 🔒 Part 10: Security & Maintenance

### Firewall Setup

```bash
# Install UFW
sudo apt install -y ufw

# Allow SSH
sudo ufw allow ssh

# Allow app port (only on local network)
sudo ufw allow from 192.168.1.0/24 to any port 8080

# Enable firewall
sudo ufw enable
```

### Automatic Updates

```bash
# Install unattended-upgrades
sudo apt install -y unattended-upgrades

# Configure automatic security updates
sudo dpkg-reconfigure -plow unattended-upgrades
```

### Backup Script

```bash
nano /opt/sweetshelves/backup.sh
```

**Add this content:**

```bash
#!/bin/bash
BACKUP_DIR="/opt/sweetshelves/backups"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR

# Backup databases
cp /opt/sweetshelves/*.db $BACKUP_DIR/backup_${DATE}/

# Keep only last 7 days
find $BACKUP_DIR -type d -mtime +7 -exec rm -rf {} +

echo "Backup completed: $DATE"
```

**Make executable and schedule:**

```bash
chmod +x /opt/sweetshelves/backup.sh

# Add to crontab (runs daily at 2 AM)
crontab -e

# Add this line:
0 2 * * * /opt/sweetshelves/backup.sh
```

---

## 📊 Part 11: Monitoring & Logs

### View Application Logs

```bash
# Real-time logs
sudo journalctl -u sweetshelves.service -f

# Today's logs
sudo journalctl -u sweetshelves.service --since today

# Last 50 errors
sudo journalctl -u sweetshelves.service -p err -n 50
```

### Check System Resources

```bash
# CPU and memory usage
htop

# Disk space
df -h

# Database sizes
du -h /opt/sweetshelves/*.db
```

### Temperature Monitoring

```bash
# Check CPU temperature
vcgencmd measure_temp

# Install monitoring tool
sudo apt install -y rpi-monitor

# Access at: http://sweetshelvespi.local:8888
```

---

## 🔧 Part 12: Troubleshooting

### Application Won't Start

```bash
# Check service status
sudo systemctl status sweetshelves.service

# View detailed errors
sudo journalctl -u sweetshelves.service -n 100

# Test manually
cd /opt/sweetshelves
source .venv/bin/activate
python app.py

# Check port availability
sudo lsof -i :8080
```

### Database Locked Errors

```bash
# Check for stale locks
lsof /opt/sweetshelves/*.db

# If locked, restart service
sudo systemctl restart sweetshelves.service
```

### Printer Connection Issues

```bash
# Check Bluetooth status
sudo systemctl status bluetooth

# Re-pair printer
bluetoothctl
# Follow pairing steps from Part 6

# Test printer connection
python3 -c "from printer_manager import printer_manager; print(printer_manager.test_connection())"
```

### Network Access Issues

```bash
# Check if service is listening
sudo netstat -tulpn | grep 8080

# Test local access
curl http://localhost:8080

# Check firewall
sudo ufw status
```

---

## ✅ Part 13: Verification Checklist

After completing the transfer, verify everything works:

- [ ] System boots and service starts automatically
- [ ] Web interface accessible on network
- [ ] Database queries work (check /searchrack page)
- [ ] eBay sync works
- [ ] Amazon sync works
- [ ] Bluetooth printer connects and prints
- [ ] BOL extraction works
- [ ] Image uploads work
- [ ] Barcode scanning works (if using iPad)
- [ ] Backups run automatically

---

## 📱 Part 14: iPad Configuration

### Update iPad URLs

In iPad Settings or Safari:
- Change all bookmarks from `localhost:8080` to `sweetshelvespi.local:8080`
- Or use the Pi's IP address: `192.168.1.XXX:8080`

### Test iPad Barcode Scanner

1. Open: http://sweetshelvespi.local:8080
2. Navigate to search page
3. Test barcode scanning
4. Verify data loads correctly

---

## 🚨 Emergency Recovery

### If Pi becomes inaccessible:

1. **Remove SD card**
2. **Insert into computer**
3. **Access boot partition**
4. **Enable SSH**: Create empty file named `ssh` in boot partition
5. **Reinsert SD card into Pi**
6. **Reconnect via SSH**

### Complete System Restore:

```bash
# Stop service
sudo systemctl stop sweetshelves.service

# Restore from Windows backup
# Transfer databases from Windows again

# Restart service
sudo systemctl start sweetshelves.service
```

---

## 📞 Support Resources

### Useful Commands Reference

```bash
# Service management
sudo systemctl start sweetshelves.service
sudo systemctl stop sweetshelves.service
sudo systemctl restart sweetshelves.service
sudo systemctl status sweetshelves.service

# View logs
sudo journalctl -u sweetshelves.service -f

# Update application
cd /opt/sweetshelves
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart sweetshelves.service

# Check system info
cat /proc/cpuinfo
free -h
df -h
vcgencmd measure_temp
```

---

## 🎉 You're Done!

Your Sweet Shelves inventory system is now running on a Raspberry Pi!

**Next Steps:**
1. Keep your Windows installation as backup
2. Test thoroughly on Pi for a few days
3. Once confident, transition fully to Pi
4. Set up regular backups
5. Monitor system performance

**Benefits:**
- ✅ Lower power consumption
- ✅ Runs 24/7 reliably
- ✅ Dedicated hardware
- ✅ Can be accessed from anywhere
- ✅ No need to keep laptop running

---

**Last Updated:** November 5, 2025
**Version:** 1.0

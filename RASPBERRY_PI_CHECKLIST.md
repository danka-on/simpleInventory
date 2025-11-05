# 📝 Raspberry Pi Transfer Checklist

## Pre-Transfer (on Windows)

- [ ] Update requirements.txt (already done ✓)
- [ ] Backup all databases to external drive
- [ ] Document any custom settings in .env
- [ ] Note down printer MAC address
- [ ] Test that app works on Windows one last time
- [ ] Optional: Initialize Git repository
- [ ] Copy amazon_credentials.json
- [ ] Copy credentials.json (eBay)
- [ ] Copy tokens.json

## Hardware Setup

- [ ] Flash Raspberry Pi OS to SD card using Imager
- [ ] Enable SSH during imaging
- [ ] Set hostname: sweetshelvespi
- [ ] Set WiFi credentials (if using WiFi)
- [ ] Boot Pi and find IP address
- [ ] Connect via SSH successfully

## System Configuration

- [ ] Run: `sudo apt update && sudo apt upgrade -y`
- [ ] Install Python 3.11+
- [ ] Install system dependencies
- [ ] Create /opt/sweetshelves directory
- [ ] Set proper ownership: `sudo chown pi:pi /opt/sweetshelves`

## File Transfer

- [ ] Transfer all Python files
- [ ] Transfer templates/ folder
- [ ] Transfer static/ folder
- [ ] Transfer all .db files
- [ ] Transfer .env file
- [ ] Transfer credentials.json
- [ ] Transfer tokens.json
- [ ] Transfer amazon_credentials.json
- [ ] Verify all files present: `ls -la /opt/sweetshelves/`

## Python Environment

- [ ] Create venv: `python3 -m venv .venv`
- [ ] Activate venv: `source .venv/bin/activate`
- [ ] Upgrade pip: `pip install --upgrade pip`
- [ ] Install requirements: `pip install -r requirements.txt`
- [ ] Fix any failed package installations

## Database Setup

- [ ] Verify database integrity checks pass
- [ ] Check file permissions: `chmod 644 *.db`
- [ ] Test database access via sqlite3
- [ ] Run: `sqlite3 searchRack.db "SELECT COUNT(*) FROM INVENTORY;"`

## Printer Setup (if using Bluetooth)

- [ ] Install Bluetooth packages
- [ ] Enable Bluetooth service
- [ ] Pair printer using bluetoothctl
- [ ] Note printer MAC address
- [ ] Update PRINTER_ADDRESS in .env
- [ ] Test printer connection

## Application Testing

- [ ] Test manual start: `python app.py`
- [ ] Access from browser: http://sweetshelvespi.local:8080
- [ ] Test main page loads
- [ ] Test /searchrack page
- [ ] Test database queries work
- [ ] Test image uploads
- [ ] Stop manual test (Ctrl+C)

## Production Setup

- [ ] Create gunicorn_config.py
- [ ] Create logs directory
- [ ] Test with Gunicorn: `gunicorn -c gunicorn_config.py app:app`
- [ ] Verify it works in browser
- [ ] Stop Gunicorn (Ctrl+C)

## Systemd Service

- [ ] Create /etc/systemd/system/sweetshelves.service
- [ ] Run: `sudo systemctl daemon-reload`
- [ ] Enable service: `sudo systemctl enable sweetshelves.service`
- [ ] Start service: `sudo systemctl start sweetshelves.service`
- [ ] Check status: `sudo systemctl status sweetshelves.service`
- [ ] Verify no errors in logs
- [ ] Test auto-restart: reboot Pi and check service starts

## Network & Access

- [ ] Test access via hostname: http://sweetshelvespi.local:8080
- [ ] Test access via IP: http://192.168.1.XXX:8080
- [ ] Test from other devices on network
- [ ] Test from iPad
- [ ] Optional: Set up Cloudflare Tunnel for remote access

## Security

- [ ] Install and configure UFW firewall
- [ ] Allow SSH: `sudo ufw allow ssh`
- [ ] Allow app port on local network only
- [ ] Enable firewall: `sudo ufw enable`
- [ ] Set up automatic security updates

## Backups

- [ ] Create backup.sh script
- [ ] Make executable: `chmod +x backup.sh`
- [ ] Test backup script runs: `./backup.sh`
- [ ] Add to crontab: `crontab -e`
- [ ] Schedule daily backups at 2 AM
- [ ] Verify backup directory created

## Integration Testing

- [ ] Test eBay sync functionality
- [ ] Test Amazon sync functionality  
- [ ] Test BOL extraction with sample file
- [ ] Test barcode printing
- [ ] Test search functionality
- [ ] Test adding items to searchRack
- [ ] Test sold items sync
- [ ] Test image uploads for items
- [ ] Test all tool pages work
- [ ] Test sync manager

## iPad Configuration

- [ ] Update iPad bookmarks to use Pi hostname
- [ ] Test barcode scanner on iPad
- [ ] Test add items from iPad
- [ ] Test search from iPad
- [ ] Save iPad home screen shortcut

## Performance Testing

- [ ] Check CPU temperature: `vcgencmd measure_temp`
- [ ] Monitor with htop during use
- [ ] Check disk space: `df -h`
- [ ] Verify database sizes reasonable
- [ ] Test multiple simultaneous users
- [ ] Leave running for 24 hours and monitor

## Monitoring Setup

- [ ] Set up log monitoring commands
- [ ] Install htop for resource monitoring
- [ ] Optional: Install rpi-monitor
- [ ] Document how to check service logs
- [ ] Document restart commands

## Documentation

- [ ] Document Pi's IP address: ________________
- [ ] Document printer MAC: ________________
- [ ] Document any custom settings
- [ ] Save systemd service file copy
- [ ] Save gunicorn config copy
- [ ] Note any issues encountered and fixes

## Final Verification

- [ ] Reboot Pi: `sudo reboot`
- [ ] Wait 2 minutes for boot
- [ ] Verify service auto-starts
- [ ] Test web interface works immediately
- [ ] Test printer connects automatically
- [ ] Leave running for 24 hours without issues
- [ ] Run through one complete workday using Pi

## Cleanup

- [ ] Keep Windows installation running for 1 week as backup
- [ ] Transfer any new data from Windows to Pi during transition
- [ ] Once confident, archive Windows installation
- [ ] Update all documentation with Pi-specific info
- [ ] Celebrate! 🎉

---

## Quick Commands Reference

```bash
# Service management
sudo systemctl status sweetshelves.service
sudo systemctl restart sweetshelves.service

# View logs
sudo journalctl -u sweetshelves.service -f

# Check temperature
vcgencmd measure_temp

# Check disk space
df -h

# Monitor resources
htop

# Backup databases manually
cp /opt/sweetshelves/*.db /opt/sweetshelves/backups/manual_$(date +%Y%m%d)/
```

---

## Troubleshooting Quick Fixes

**Service won't start:**
```bash
sudo journalctl -u sweetshelves.service -n 100
cd /opt/sweetshelves
source .venv/bin/activate
python app.py  # Test manually
```

**Can't access web interface:**
```bash
sudo netstat -tulpn | grep 8080
ping sweetshelvespi.local
sudo systemctl restart sweetshelves.service
```

**Printer won't connect:**
```bash
sudo systemctl restart bluetooth
bluetoothctl  # Re-pair if needed
```

---

**Transfer Started:** _______________
**Transfer Completed:** _______________
**Total Time:** _______________

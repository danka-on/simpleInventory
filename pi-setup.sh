#!/bin/bash
# Quick setup script for Raspberry Pi
# Run with: bash pi-setup.sh

set -e  # Exit on error

echo "🥧 Sweet Shelves Raspberry Pi Setup Script"
echo "=========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running on Raspberry Pi
if [ ! -f /proc/device-tree/model ] || ! grep -q "Raspberry Pi" /proc/device-tree/model 2>/dev/null; then
    echo -e "${YELLOW}Warning: This doesn't appear to be a Raspberry Pi${NC}"
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Update system
echo -e "${GREEN}Step 1: Updating system packages...${NC}"
sudo apt update
sudo apt upgrade -y

# Install dependencies
echo -e "${GREEN}Step 2: Installing system dependencies...${NC}"
sudo apt install -y \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    build-essential \
    libssl-dev \
    libffi-dev \
    libjpeg-dev \
    zlib1g-dev \
    libxml2-dev \
    libxslt1-dev \
    libbluetooth-dev \
    libcups2-dev \
    git \
    vim \
    curl \
    wget \
    sqlite3 \
    bluetooth \
    bluez

# Install cloudflared
echo -e "${GREEN}Step 2b: Installing cloudflared...${NC}"
if ! command -v cloudflared &> /dev/null; then
    sudo mkdir -p --mode=0755 /usr/share/keyrings
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
    echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' | sudo tee /etc/apt/sources.list.d/cloudflared.list
    sudo apt-get update
    sudo apt-get install -y cloudflared
else
    echo "cloudflared is already installed"
fi

# Create application directory
echo -e "${GREEN}Step 3: Setting up application directory...${NC}"
sudo mkdir -p /opt/sweetshelves
sudo chown $USER:$USER /opt/sweetshelves

# Check if files already exist
if [ -f "/opt/sweetshelves/app.py" ]; then
    echo -e "${YELLOW}Application files already exist in /opt/sweetshelves${NC}"
    echo "Skipping file transfer step"
else
    echo -e "${YELLOW}Application files not found${NC}"
    echo "Please transfer your files to /opt/sweetshelves before continuing"
    read -p "Press Enter when files are transferred..."
fi

# Create Python virtual environment
echo -e "${GREEN}Step 4: Creating Python virtual environment...${NC}"
cd /opt/sweetshelves
python3 -m venv .venv
source .venv/bin/activate

# Upgrade pip
echo -e "${GREEN}Step 5: Upgrading pip...${NC}"
pip install --upgrade pip

# Install Python packages
echo -e "${GREEN}Step 6: Installing Python packages...${NC}"
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
else
    echo -e "${RED}Error: requirements.txt not found${NC}"
    exit 1
fi

# Create logs directory
echo -e "${GREEN}Step 7: Creating logs directory...${NC}"
mkdir -p logs

# Create gunicorn config if not exists
if [ ! -f "gunicorn_config.py" ]; then
    echo -e "${GREEN}Step 8: Creating gunicorn config...${NC}"
    cat > gunicorn_config.py << 'EOF'
bind = "0.0.0.0:8080"
workers = 2
worker_class = "sync"
timeout = 120
keepalive = 5
errorlog = "/opt/sweetshelves/logs/gunicorn-error.log"
accesslog = "/opt/sweetshelves/logs/gunicorn-access.log"
loglevel = "info"
EOF
fi

# Create systemd service
echo -e "${GREEN}Step 9: Creating systemd service...${NC}"
sudo tee /etc/systemd/system/sweetshelves.service > /dev/null << EOF
[Unit]
Description=Sweet Shelves Inventory System
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=/opt/sweetshelves
Environment="PATH=/opt/sweetshelves/.venv/bin"
ExecStart=/opt/sweetshelves/.venv/bin/gunicorn -c gunicorn_config.py app:app
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd
sudo systemctl daemon-reload

# Enable and start service
echo -e "${GREEN}Step 10: Enabling and starting service...${NC}"
sudo systemctl enable sweetshelves.service
sudo systemctl start sweetshelves.service

# Wait a moment for service to start
sleep 3

# Check status
echo ""
echo -e "${GREEN}Service Status:${NC}"
sudo systemctl status sweetshelves.service --no-pager

# Get IP address
IP=$(hostname -I | awk '{print $1}')

echo ""
echo -e "${GREEN}=========================================="
echo "✅ Setup Complete!"
echo "==========================================${NC}"
echo ""
echo "Your application should now be running at:"
echo "  http://$(hostname).local:8080"
echo "  http://$IP:8080"
echo ""
echo "Useful commands:"
echo "  sudo systemctl status sweetshelves.service   # Check status"
echo "  sudo systemctl restart sweetshelves.service  # Restart app"
echo "  sudo journalctl -u sweetshelves.service -f   # View logs"
echo ""
echo "Next steps:"
echo "  1. Transfer your database files (*.db) to /opt/sweetshelves/"
echo "  2. Transfer your credentials (.env, credentials.json, etc.)"
echo "  3. Set up Bluetooth printer pairing"
echo "  4. Test the web interface"
echo ""

"""
Printer Manager for Bluetooth Barcode Label Printing
Supports ESC/POS thermal printers via Bluetooth
Cross-platform: Windows (COM ports) and Raspberry Pi/Linux (RFCOMM/USB)
"""

import io
import sqlite3
import platform
import os
from PIL import Image
import barcode
from barcode.writer import ImageWriter

# Define base directory for cross-platform compatibility
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# For Bluetooth printer communication
try:
    from escpos.printer import Serial, Dummy, Network, Usb
    from escpos.exceptions import Error as ESCPOSError
    ESCPOS_AVAILABLE = True
except ImportError:
    ESCPOS_AVAILABLE = False
    print("Warning: python-escpos not installed. Install with: pip install python-escpos")


class PrinterManager:
    """Manages Bluetooth printer connections and printing operations"""
    
    def __init__(self):
        self.printer = None
        self.printer_address = None
        self.printer_type = None
        self.printer_mode = 'thermal'  # 'thermal' or 'inkjet'
        self.print_method = 'escpos'  # 'escpos' or 'browser'
        self.platform = platform.system()  # 'Windows', 'Linux', 'Darwin' (macOS)
        self.network_ip = None
        self.network_port = 9100  # Default port for ESC/POS network printers
        self.load_printer_config()
    
    def is_raspberry_pi(self):
        """Detect if running on Raspberry Pi"""
        if self.platform != 'Linux':
            return False
        try:
            with open('/proc/device-tree/model', 'r') as f:
                return 'raspberry pi' in f.read().lower()
        except:
            return False
    
    def detect_platform_type(self):
        """Detect what type of connection to use based on platform"""
        if self.platform == 'Windows':
            return 'windows_serial'
        elif self.is_raspberry_pi():
            return 'raspberry_pi'
        elif self.platform == 'Linux':
            return 'linux'
        else:
            return 'unknown'
    
    def load_printer_config(self):
        """Load saved printer configuration from database"""
        try:
            conn = sqlite3.connect(os.path.join(BASE_DIR, 'bol.db'))
            cur = conn.cursor()
            
            # Create printer_config table if it doesn't exist
            cur.execute('''
                CREATE TABLE IF NOT EXISTS printer_config (
                    id INTEGER PRIMARY KEY,
                    printer_type TEXT,
                    printer_mode TEXT DEFAULT 'thermal',
                    print_method TEXT DEFAULT 'escpos',
                    bluetooth_address TEXT,
                    printer_name TEXT,
                    network_ip TEXT,
                    network_port INTEGER DEFAULT 9100,
                    label_width INTEGER DEFAULT 50,
                    label_height INTEGER DEFAULT 30,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Migrate existing table to add new columns if needed
            try:
                cur.execute("PRAGMA table_info(printer_config)")
                columns = [col[1] for col in cur.fetchall()]
                
                if 'network_ip' not in columns:
                    print("Migrating printer_config table: adding network_ip column")
                    cur.execute("ALTER TABLE printer_config ADD COLUMN network_ip TEXT")
                
                if 'network_port' not in columns:
                    print("Migrating printer_config table: adding network_port column")
                    cur.execute("ALTER TABLE printer_config ADD COLUMN network_port INTEGER DEFAULT 9100")
                
                if 'printer_mode' not in columns:
                    print("Migrating printer_config table: adding printer_mode column")
                    cur.execute("ALTER TABLE printer_config ADD COLUMN printer_mode TEXT DEFAULT 'thermal'")
                
                if 'print_method' not in columns:
                    print("Migrating printer_config table: adding print_method column")
                    cur.execute("ALTER TABLE printer_config ADD COLUMN print_method TEXT DEFAULT 'escpos'")
                
                conn.commit()
            except Exception as migrate_error:
                print(f"Migration note: {migrate_error}")
            
            # Get current config
            cur.execute('SELECT printer_type, printer_mode, print_method, bluetooth_address, network_ip, network_port FROM printer_config WHERE id = 1')
            row = cur.fetchone()
            if row:
                self.printer_type = row[0]
                self.printer_mode = row[1] if row[1] else 'thermal'
                self.print_method = row[2] if row[2] else 'escpos'
                self.printer_address = row[3]
                self.network_ip = row[4]
                self.network_port = row[5] if row[5] else 9100
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Error loading printer config: {e}")
    
    def save_printer_config(self, printer_type, bluetooth_address, printer_name=None, network_ip=None, network_port=9100, printer_mode='thermal', print_method='escpos'):
        """Save printer configuration to database"""
        try:
            conn = sqlite3.connect(os.path.join(BASE_DIR, 'bol.db'))
            cur = conn.cursor()
            
            cur.execute('''
                INSERT OR REPLACE INTO printer_config 
                (id, printer_type, printer_mode, print_method, bluetooth_address, printer_name, network_ip, network_port, updated_at)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (printer_type, printer_mode, print_method, bluetooth_address, printer_name, network_ip, network_port))
            
            conn.commit()
            conn.close()
            
            self.printer_type = printer_type
            self.printer_mode = printer_mode
            self.print_method = print_method
            self.printer_address = bluetooth_address
            self.network_ip = network_ip
            self.network_port = network_port
            return True
        except Exception as e:
            print(f"Error saving printer config: {e}")
            return False
    
    def connect_printer(self, address=None, ip=None, port=None):
        """Connect to printer (Bluetooth, Serial, USB, or Network)"""
        if not ESCPOS_AVAILABLE:
            raise Exception("python-escpos not installed. Run: pip install python-escpos")
        
        try:
            # Network printer connection (for iPad/mobile access)
            if self.printer_type == 'network' or ip:
                network_ip = ip or self.network_ip
                network_port = port or self.network_port or 9100
                
                if not network_ip:
                    raise Exception("No network IP address configured")
                
                print(f"Connecting to network printer at {network_ip}:{network_port}")
                self.printer = Network(network_ip, network_port)
                print(f"Connected to network printer at {network_ip}:{network_port}")
                return True
            
            # Bluetooth/Serial/USB connection (direct to server)
            device_address = address or self.printer_address
            if not device_address:
                raise Exception("No printer port/address configured")
            
            platform_type = self.detect_platform_type()
            
            if platform_type == 'windows_serial':
                # Windows: Use COM port (e.g., COM3)
                self.printer = Serial(
                    devfile=device_address,
                    baudrate=9600,
                    bytesize=8,
                    parity='N',
                    stopbits=1,
                    timeout=1.0,
                    dsrdtr=True
                )
                print(f"Connected to Windows printer on {device_address}")
                
            elif platform_type in ['raspberry_pi', 'linux']:
                # Raspberry Pi/Linux: Try multiple methods
                
                # Method 1: Check if it's a USB printer path
                if device_address.startswith('/dev/usb/'):
                    self.printer = Usb(0x0416, 0x5011)  # Common USB printer IDs
                    print(f"Connected to USB printer")
                
                # Method 2: RFCOMM device (e.g., /dev/rfcomm0)
                elif device_address.startswith('/dev/rfcomm'):
                    self.printer = Serial(
                        devfile=device_address,
                        baudrate=9600,
                        bytesize=8,
                        parity='N',
                        stopbits=1,
                        timeout=1.0
                    )
                    print(f"Connected to Bluetooth printer via {device_address}")
                
                # Method 3: Serial port (e.g., /dev/ttyUSB0, /dev/ttyAMA0)
                elif device_address.startswith('/dev/tty'):
                    self.printer = Serial(
                        devfile=device_address,
                        baudrate=9600,
                        bytesize=8,
                        parity='N',
                        stopbits=1,
                        timeout=1.0
                    )
                    print(f"Connected to serial printer on {device_address}")
                
                # Method 4: Try as USB vendor/product ID (format: "0x1234:0x5678")
                elif ':' in device_address and 'x' in device_address.lower():
                    parts = device_address.split(':')
                    vendor_id = int(parts[0], 16)
                    product_id = int(parts[1], 16)
                    self.printer = Usb(vendor_id, product_id)
                    print(f"Connected to USB printer {device_address}")
                
                else:
                    # Default to serial
                    self.printer = Serial(
                        devfile=device_address,
                        baudrate=9600,
                        bytesize=8,
                        parity='N',
                        stopbits=1,
                        timeout=1.0
                    )
                    print(f"Connected to printer on {device_address}")
            
            else:
                raise Exception(f"Unsupported platform: {self.platform}")
            
            return True
        except Exception as e:
            print(f"Error connecting to printer: {e}")
            raise
    
    def disconnect_printer(self):
        """Disconnect from printer"""
        if self.printer:
            try:
                self.printer.close()
            except:
                pass
            self.printer = None
    
    def ensure_connection(self):
        """Ensure printer is connected, reconnect if needed"""
        if not self.printer:
            print("No printer connection, establishing new connection...")
            self.connect_printer()
            return
        
        # Test if connection is still alive
        try:
            # Try a simple command to check connection
            if self.printer_type == 'network':
                # For network printers, test the connection
                import socket
                test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                test_socket.settimeout(2)
                result = test_socket.connect_ex((self.network_ip, self.network_port))
                test_socket.close()
                
                if result != 0:
                    print("Network printer connection lost, reconnecting...")
                    self.disconnect_printer()
                    self.connect_printer()
        except Exception as e:
            print(f"Connection check failed: {e}, reconnecting...")
            self.disconnect_printer()
            self.connect_printer()
    
    def generate_barcode_image(self, upc, item_description=None, width=400, height=200):
        """Generate barcode image from UPC"""
        try:
            # Remove any non-numeric characters
            clean_upc = ''.join(filter(str.isdigit, str(upc)))
            
            # Determine barcode type based on length
            if len(clean_upc) == 12:
                barcode_class = barcode.get_barcode_class('upc')
            elif len(clean_upc) == 13:
                barcode_class = barcode.get_barcode_class('ean13')
            elif len(clean_upc) == 8:
                barcode_class = barcode.get_barcode_class('ean8')
            else:
                # Default to Code128 for other lengths
                barcode_class = barcode.get_barcode_class('code128')
            
            # Create barcode with custom writer options
            writer = ImageWriter()
            writer.set_options({
                'module_width': 0.3,
                'module_height': 10,
                'quiet_zone': 2,
                'font_size': 10,
                'text_distance': 3,
                'write_text': True
            })
            
            # Generate barcode
            barcode_instance = barcode_class(clean_upc, writer=writer)
            
            # Save to BytesIO buffer
            buffer = io.BytesIO()
            barcode_instance.write(buffer)
            buffer.seek(0)
            
            # Open image and optionally add description
            img = Image.open(buffer)
            
            if item_description:
                # Add item description text above barcode
                from PIL import ImageDraw, ImageFont
                
                # Create new image with space for text
                new_height = img.height + 80
                new_img = Image.new('RGB', (img.width, new_height), 'white')
                
                # Add description text
                draw = ImageDraw.Draw(new_img)
                try:
                    font = ImageFont.truetype("arial.ttf", 14)
                except:
                    try:
                        font = ImageFont.truetype("Arial.ttf", 14)
                    except:
                        font = ImageFont.load_default()
                
                # Truncate description if too long (wrap text)
                max_chars = 40
                if len(item_description) > max_chars:
                    # Split into two lines
                    line1 = item_description[:max_chars].strip()
                    line2 = item_description[max_chars:max_chars*2].strip()
                    if len(item_description) > max_chars*2:
                        line2 += '...'
                    
                    # Draw two lines
                    bbox1 = draw.textbbox((0, 0), line1, font=font)
                    text_width1 = bbox1[2] - bbox1[0]
                    text_x1 = (new_img.width - text_width1) // 2
                    draw.text((text_x1, 10), line1, fill='black', font=font)
                    
                    bbox2 = draw.textbbox((0, 0), line2, font=font)
                    text_width2 = bbox2[2] - bbox2[0]
                    text_x2 = (new_img.width - text_width2) // 2
                    draw.text((text_x2, 35), line2, fill='black', font=font)
                else:
                    # Single line
                    bbox = draw.textbbox((0, 0), item_description, font=font)
                    text_width = bbox[2] - bbox[0]
                    text_x = (new_img.width - text_width) // 2
                    draw.text((text_x, 20), item_description, fill='black', font=font)
                
                # Paste barcode below text
                new_img.paste(img, (0, 80))
                img = new_img
            
            return img
        except Exception as e:
            print(f"Error generating barcode: {e}")
            raise
    
    def print_barcode(self, upc, item_description=None, quantity=1):
        """Print barcode label to printer (network, Bluetooth, or USB)"""
        if not ESCPOS_AVAILABLE:
            raise Exception("python-escpos not installed")
        
        try:
            # Generate barcode image
            img = self.generate_barcode_image(upc, item_description)
            
            # Ensure printer connection is alive
            self.ensure_connection()
            
            # Different handling for thermal vs inkjet printers
            if self.printer_mode == 'thermal':
                # Thermal printer: Convert to 1-bit black/white for ESC/POS
                print("Printing in THERMAL mode...")
                
                # 1. Convert to grayscale first
                img = img.convert('L')
                
                # 2. Resize if too wide (most thermal printers are 384-576 pixels wide)
                max_width = 384  # Standard for 58mm printers (use 576 for 80mm)
                if img.width > max_width:
                    ratio = max_width / img.width
                    new_height = int(img.height * ratio)
                    img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
                
                # 3. Convert to pure black and white (1-bit) with dithering
                img = img.convert('1')
                
                # Print the label with retry logic
                for i in range(quantity):
                    retry_count = 0
                    max_retries = 2
                    
                    while retry_count <= max_retries:
                        try:
                            self.printer.set(align='center')
                            self.printer.image(img, impl='bitImageColumn')  # Better method for barcodes
                            self.printer.text('\n')
                            self.printer.cut()
                            break  # Success, exit retry loop
                        except Exception as print_error:
                            retry_count += 1
                            print(f"Print attempt {retry_count} failed: {print_error}")
                            if retry_count <= max_retries:
                                print("Reconnecting and retrying...")
                                self.disconnect_printer()
                                import time
                                time.sleep(1)
                                self.ensure_connection()
                            else:
                                raise  # Give up after max retries
            
            else:  # inkjet mode
                # Inkjet printer: Use high-quality color/grayscale image
                print("Printing in INKJET mode...")
                
                # Keep higher resolution for inkjet printers
                # Inkjet can handle larger images (usually 300 DPI)
                max_width = 800  # Higher resolution for inkjet
                if img.width > max_width:
                    ratio = max_width / img.width
                    new_height = int(img.height * ratio)
                    img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
                
                # Keep as RGB/grayscale for better quality on inkjet
                img = img.convert('RGB')
                
                # Print the label with retry logic
                for i in range(quantity):
                    retry_count = 0
                    max_retries = 2
                    
                    while retry_count <= max_retries:
                        try:
                            self.printer.set(align='center')
                            # Use high-density image printing for inkjet
                            self.printer.image(img, high_density_vertical=True, high_density_horizontal=True)
                            self.printer.text('\n\n')
                            # Try to use cut if available, otherwise just feed paper
                            try:
                                self.printer.cut()
                            except:
                                self.printer.text('\n' * 5)  # Feed paper if cut not supported
                            break  # Success, exit retry loop
                        except Exception as print_error:
                            retry_count += 1
                            print(f"Print attempt {retry_count} failed: {print_error}")
                            if retry_count <= max_retries:
                                print("Reconnecting and retrying...")
                                self.disconnect_printer()
                                import time
                                time.sleep(1)
                                self.ensure_connection()
                            else:
                                raise  # Give up after max retries
            
            return True
        except Exception as e:
            print(f"Error printing barcode: {e}")
            # Clean up connection on error
            self.disconnect_printer()
            raise
    
    def test_print(self):
        """Print a test label"""
        retry_count = 0
        max_retries = 2
        
        while retry_count <= max_retries:
            try:
                # Ensure printer connection is alive
                self.ensure_connection()
                
                self.printer.set(align='center', font='a', bold=True, width=2, height=2)
                self.printer.text("TEST PRINT\n")
                self.printer.set(align='center', font='a', bold=False, width=1, height=1)
                self.printer.text("=" * 32 + "\n")
                self.printer.text("Printer Connected!\n")
                
                if self.printer_type == 'network':
                    self.printer.text(f"Network: {self.network_ip}:{self.network_port}\n")
                else:
                    self.printer.text(f"Port: {self.printer_address}\n")
                
                self.printer.text(f"Mode: {self.printer_mode.upper()}\n")
                self.printer.text("=" * 32 + "\n")
                
                # Print a test barcode
                self.printer.text("\nTest Barcode:\n")
                test_img = self.generate_barcode_image("123456789012", "Test Item")
                
                # Format based on printer mode
                if self.printer_mode == 'thermal':
                    test_img = test_img.convert('L')
                    max_width = 384
                    if test_img.width > max_width:
                        ratio = max_width / test_img.width
                        new_height = int(test_img.height * ratio)
                        test_img = test_img.resize((max_width, new_height), Image.Resampling.LANCZOS)
                    test_img = test_img.convert('1')
                    self.printer.set(align='center')
                    self.printer.image(test_img, impl='bitImageColumn')
                else:  # inkjet
                    test_img = test_img.convert('RGB')
                    max_width = 800
                    if test_img.width > max_width:
                        ratio = max_width / test_img.width
                        new_height = int(test_img.height * ratio)
                        test_img = test_img.resize((max_width, new_height), Image.Resampling.LANCZOS)
                    self.printer.set(align='center')
                    self.printer.image(test_img, high_density_vertical=True, high_density_horizontal=True)
                
                self.printer.text("\n")
                
                # Try to cut, or feed paper if not supported
                try:
                    self.printer.cut()
                except:
                    self.printer.text('\n' * 3)
                
                return True  # Success!
                
            except Exception as e:
                retry_count += 1
                print(f"Test print attempt {retry_count} failed: {e}")
                if retry_count <= max_retries:
                    print("Reconnecting and retrying...")
                    self.disconnect_printer()
                    import time
                    time.sleep(1)
                else:
                    print(f"Test print failed after {max_retries + 1} attempts")
                    self.disconnect_printer()
                    raise
    
    def get_available_bluetooth_devices(self):
        """Scan for available Bluetooth devices (cross-platform)"""
        platform_type = self.detect_platform_type()
        
        if platform_type == 'windows_serial':
            # On Windows, list COM ports
            devices = []
            try:
                import serial.tools.list_ports
                ports = serial.tools.list_ports.comports()
                for port in ports:
                    devices.append({
                        "address": port.device,
                        "name": f"{port.description} ({port.device})"
                    })
            except ImportError:
                # Fallback: suggest common COM ports
                for i in range(1, 21):
                    devices.append({
                        "address": f"COM{i}",
                        "name": f"COM{i}"
                    })
            return devices
            
        elif platform_type in ['raspberry_pi', 'linux']:
            # On Linux/Raspberry Pi, list serial devices and try Bluetooth scan
            devices = []
            
            # List serial/RFCOMM devices
            serial_devices = [
                '/dev/rfcomm0', '/dev/rfcomm1',
                '/dev/ttyUSB0', '/dev/ttyUSB1',
                '/dev/ttyAMA0', '/dev/ttyS0',
                '/dev/usb/lp0', '/dev/usb/lp1'
            ]
            for dev in serial_devices:
                if os.path.exists(dev):
                    devices.append({
                        "address": dev,
                        "name": f"Serial Device: {dev}"
                    })
            
            # Try Bluetooth scanning if pybluez available
            try:
                import bluetooth
                print("Scanning for Bluetooth devices...")
                nearby_devices = bluetooth.discover_devices(lookup_names=True, duration=8)
                for addr, name in nearby_devices:
                    devices.append({
                        "address": addr,
                        "name": f"{name} (BT: {addr})",
                        "bluetooth_mac": addr
                    })
            except ImportError:
                print("PyBluez not installed. Install with: pip install pybluez")
            except Exception as e:
                print(f"Bluetooth scan error: {e}")
            
            # Try listing USB printers
            try:
                from escpos.printer import Usb
                # Common thermal printer vendor IDs
                common_vendors = [
                    (0x0416, 0x5011, "Winbond Printer"),
                    (0x04b8, 0x0202, "Epson TM-T20"),
                    (0x154f, 0x154f, "Generic Thermal"),
                ]
                for vid, pid, name in common_vendors:
                    try:
                        test = Usb(vid, pid, timeout=1)
                        test.close()
                        devices.append({
                            "address": f"0x{vid:04x}:0x{pid:04x}",
                            "name": f"USB: {name}"
                        })
                    except:
                        pass
            except Exception as e:
                print(f"USB scan error: {e}")
            
            return devices
        
        else:
            raise Exception("Unsupported platform for device scanning")
    
    def generate_barcode_file(self, upc, item_description=None, filepath=None):
        """Generate barcode and save to file (for testing without printer)"""
        try:
            img = self.generate_barcode_image(upc, item_description)
            
            if not filepath:
                filepath = f"static/barcodes/{upc}.png"
            
            # Ensure directory exists
            import os
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            
            img.save(filepath)
            return filepath
        except Exception as e:
            print(f"Error saving barcode file: {e}")
            raise


# Global printer manager instance
printer_manager = PrinterManager()

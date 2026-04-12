"""
Printer Manager for Bluetooth Barcode Label Printing
Supports ESC/POS thermal printers via Bluetooth
Cross-platform: Windows (COM ports) and Raspberry Pi/Linux (RFCOMM/USB)
"""

import io
import sqlite3
import platform
import os
import socket
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

# Brother QL native raster protocol (QL-820NWBc, QL-800, etc.)
try:
    from brother_ql.conversion import convert as _bql_convert
    from brother_ql.backends.helpers import send as _bql_send
    from brother_ql.raster import BrotherQLRaster
    BROTHER_QL_AVAILABLE = True
    # brother_ql uses Image.ANTIALIAS which was removed in Pillow 10 — patch for compatibility
    if not hasattr(Image, 'ANTIALIAS'):
        Image.ANTIALIAS = Image.LANCZOS
except ImportError:
    BROTHER_QL_AVAILABLE = False
    print("Info: brother_ql not installed. Install with: pip install brother_ql")


class PrinterManager:
    """Manages Bluetooth printer connections and printing operations"""
    
    def __init__(self):
        self.printer = None
        self.printer_address = None
        self.printer_type = None
        self.printer_name = ''
        self.printer_model = ''
        self.printer_mode = 'thermal'  # 'thermal' or 'inkjet'
        self.print_method = 'escpos'  # 'escpos' or 'browser'
        self.platform = platform.system()  # 'Windows', 'Linux', 'Darwin' (macOS)
        self.network_ip = None
        self.network_port = 9100  # Default port for ESC/POS network printers
        self.label_width = 50
        self.label_height = 30
        self.label_show_name = True
        self.label_show_barcode = True
        self.label_title_font_size = 10
        self.label_barcode_font_size = 10
        self.label_barcode_height = 34
        self.label_barcode_scale = 1.8
        self.label_title_lines = 2
        self.brother_ql_label = ''  # e.g. '62red', '62', '29x90' — empty = auto-detect from width
        self.label_show_barcode_text = True  # print human-readable digits below barcode bars
        self.printer_mac = ''  # MAC address for Wake-on-LAN (e.g. 'AA:BB:CC:DD:EE:FF')
        self.load_printer_config()
    
    def is_raspberry_pi(self):
        """Detect if running on Raspberry Pi"""
        if self.platform != 'Linux':
            return False
        try:
            with open('/proc/device-tree/model', 'r') as f:
                return 'raspberry pi' in f.read().lower()
        except Exception:
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
        conn = None
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
                    printer_model TEXT,
                    network_ip TEXT,
                    network_port INTEGER DEFAULT 9100,
                    label_width INTEGER DEFAULT 50,
                    label_height INTEGER DEFAULT 30,
                    label_show_name INTEGER DEFAULT 1,
                    label_show_barcode INTEGER DEFAULT 1,
                    label_title_font_size INTEGER DEFAULT 10,
                    label_barcode_font_size INTEGER DEFAULT 10,
                    label_barcode_height INTEGER DEFAULT 34,
                    label_barcode_scale REAL DEFAULT 1.8,
                    label_title_lines INTEGER DEFAULT 2,
                    brother_ql_label TEXT DEFAULT '',
                    label_show_barcode_text INTEGER DEFAULT 1,
                    printer_mac TEXT DEFAULT '',
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

                if 'printer_model' not in columns:
                    print("Migrating printer_config table: adding printer_model column")
                    cur.execute("ALTER TABLE printer_config ADD COLUMN printer_model TEXT")

                if 'label_width' not in columns:
                    print("Migrating printer_config table: adding label_width column")
                    cur.execute("ALTER TABLE printer_config ADD COLUMN label_width INTEGER DEFAULT 50")

                if 'label_height' not in columns:
                    print("Migrating printer_config table: adding label_height column")
                    cur.execute("ALTER TABLE printer_config ADD COLUMN label_height INTEGER DEFAULT 30")

                if 'label_show_name' not in columns:
                    print("Migrating printer_config table: adding label_show_name column")
                    cur.execute("ALTER TABLE printer_config ADD COLUMN label_show_name INTEGER DEFAULT 1")

                if 'label_show_barcode' not in columns:
                    print("Migrating printer_config table: adding label_show_barcode column")
                    cur.execute("ALTER TABLE printer_config ADD COLUMN label_show_barcode INTEGER DEFAULT 1")

                if 'label_title_font_size' not in columns:
                    print("Migrating printer_config table: adding label_title_font_size column")
                    cur.execute("ALTER TABLE printer_config ADD COLUMN label_title_font_size INTEGER DEFAULT 10")

                if 'label_barcode_font_size' not in columns:
                    print("Migrating printer_config table: adding label_barcode_font_size column")
                    cur.execute("ALTER TABLE printer_config ADD COLUMN label_barcode_font_size INTEGER DEFAULT 10")

                if 'label_barcode_height' not in columns:
                    print("Migrating printer_config table: adding label_barcode_height column")
                    cur.execute("ALTER TABLE printer_config ADD COLUMN label_barcode_height INTEGER DEFAULT 34")

                if 'label_barcode_scale' not in columns:
                    print("Migrating printer_config table: adding label_barcode_scale column")
                    cur.execute("ALTER TABLE printer_config ADD COLUMN label_barcode_scale REAL DEFAULT 1.8")

                if 'label_title_lines' not in columns:
                    print("Migrating printer_config table: adding label_title_lines column")
                    cur.execute("ALTER TABLE printer_config ADD COLUMN label_title_lines INTEGER DEFAULT 2")

                if 'brother_ql_label' not in columns:
                    print("Migrating printer_config table: adding brother_ql_label column")
                    cur.execute("ALTER TABLE printer_config ADD COLUMN brother_ql_label TEXT DEFAULT ''")

                if 'label_show_barcode_text' not in columns:
                    print("Migrating printer_config table: adding label_show_barcode_text column")
                    cur.execute("ALTER TABLE printer_config ADD COLUMN label_show_barcode_text INTEGER DEFAULT 1")

                if 'printer_mac' not in columns:
                    print("Migrating printer_config table: adding printer_mac column")
                    cur.execute("ALTER TABLE printer_config ADD COLUMN printer_mac TEXT DEFAULT ''")

                conn.commit()
            except Exception as migrate_error:
                print(f"Migration note: {migrate_error}")

            # Get current config
            cur.execute('''
                SELECT
                    printer_type,
                    printer_mode,
                    print_method,
                    bluetooth_address,
                    printer_name,
                    printer_model,
                    network_ip,
                    network_port,
                    label_width,
                    label_height,
                    label_show_name,
                    label_show_barcode,
                    label_title_font_size,
                    label_barcode_font_size,
                    label_barcode_height,
                    label_barcode_scale,
                    label_title_lines,
                    brother_ql_label,
                    label_show_barcode_text,
                    printer_mac
                FROM printer_config
                WHERE id = 1
            ''')
            row = cur.fetchone()
            if row:
                self.printer_type = row[0]
                self.printer_mode = row[1] if row[1] else 'thermal'
                self.print_method = row[2] if row[2] else 'escpos'
                self.printer_address = row[3]
                self.printer_name = row[4] if row[4] else ''
                self.printer_model = row[5] if row[5] else ''
                self.network_ip = row[6]
                self.network_port = row[7] if row[7] else 9100
                self.label_width = self._coerce_dimension(row[8], 50)
                self.label_height = self._coerce_dimension(row[9], 30)
                self.label_show_name = bool(row[10]) if row[10] is not None else True
                self.label_show_barcode = bool(row[11]) if row[11] is not None else True
                self.label_title_font_size = self._coerce_dimension(row[12], 10)
                self.label_barcode_font_size = self._coerce_dimension(row[13], 10)
                self.label_barcode_height = self._coerce_dimension(row[14], 34)
                self.label_barcode_scale = self._coerce_float(row[15], 1.8, minimum=0.5, maximum=4.0)
                self.label_title_lines = self._coerce_dimension(row[16], 2, minimum=1, maximum=4)
                self.brother_ql_label = str(row[17]).strip() if row[17] else ''
                self.label_show_barcode_text = bool(row[18]) if row[18] is not None else True
                self.printer_mac = str(row[19]).strip() if row[19] else ''

            conn.commit()
        except Exception as e:
            print(f"Error loading printer config: {e}")
        finally:
            if conn:
                conn.close()
    
    def _coerce_dimension(self, value, default, minimum=1, maximum=None):
        try:
            value_i = int(value)
            if value_i < int(minimum):
                raise ValueError('dimension must be positive')
            if maximum is not None and value_i > int(maximum):
                value_i = int(maximum)
            return value_i
        except Exception:
            return int(default)

    def _coerce_float(self, value, default, minimum=None, maximum=None):
        try:
            value_f = float(value)
            if minimum is not None and value_f < float(minimum):
                value_f = float(minimum)
            if maximum is not None and value_f > float(maximum):
                value_f = float(maximum)
            return value_f
        except Exception:
            return float(default)

    def save_printer_config(self, printer_type, bluetooth_address, printer_name=None, network_ip=None, network_port=9100, printer_mode='thermal', print_method='escpos', printer_model=None, label_width=50, label_height=30, label_show_name=True, label_show_barcode=True, label_title_font_size=10, label_barcode_font_size=10, label_barcode_height=34, label_barcode_scale=1.8, label_title_lines=2, brother_ql_label='', label_show_barcode_text=True, printer_mac=''):
        """Save printer configuration to database"""
        conn = None
        try:
            conn = sqlite3.connect(os.path.join(BASE_DIR, 'bol.db'))
            cur = conn.cursor()

            network_port = self._coerce_dimension(network_port, 9100)
            label_width = self._coerce_dimension(label_width, 50)
            label_height = self._coerce_dimension(label_height, 30)
            printer_name = str(printer_name or '').strip()
            printer_model = str(printer_model or '').strip()
            bluetooth_address = str(bluetooth_address or '').strip()
            network_ip = str(network_ip or '').strip()
            label_show_name = 1 if str(label_show_name).strip().lower() not in ('0', 'false', 'no', '') else 0
            label_show_barcode = 1 if str(label_show_barcode).strip().lower() not in ('0', 'false', 'no', '') else 0
            label_title_font_size = self._coerce_dimension(label_title_font_size, 10, minimum=6, maximum=48)
            label_barcode_font_size = self._coerce_dimension(label_barcode_font_size, 10, minimum=6, maximum=36)
            label_barcode_height = self._coerce_dimension(label_barcode_height, 34, minimum=20, maximum=140)
            label_barcode_scale = self._coerce_float(label_barcode_scale, 1.8, minimum=0.6, maximum=4.0)
            label_title_lines = self._coerce_dimension(label_title_lines, 2, minimum=1, maximum=4)
            brother_ql_label = str(brother_ql_label or '').strip()
            label_show_barcode_text = 1 if str(label_show_barcode_text).strip().lower() not in ('0', 'false', 'no', '') else 0
            printer_mac = str(printer_mac or '').strip()

            cur.execute('''
                INSERT OR REPLACE INTO printer_config
                (
                    id,
                    printer_type,
                    printer_mode,
                    print_method,
                    bluetooth_address,
                    printer_name,
                    printer_model,
                    network_ip,
                    network_port,
                    label_width,
                    label_height,
                    label_show_name,
                    label_show_barcode,
                    label_title_font_size,
                    label_barcode_font_size,
                    label_barcode_height,
                    label_barcode_scale,
                    label_title_lines,
                    brother_ql_label,
                    label_show_barcode_text,
                    printer_mac,
                    updated_at
                )
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (
                printer_type,
                printer_mode,
                print_method,
                bluetooth_address,
                printer_name,
                printer_model,
                network_ip,
                network_port,
                label_width,
                label_height,
                label_show_name,
                label_show_barcode,
                label_title_font_size,
                label_barcode_font_size,
                label_barcode_height,
                label_barcode_scale,
                label_title_lines,
                brother_ql_label,
                label_show_barcode_text,
                printer_mac
            ))

            conn.commit()
            
            self.printer_type = printer_type
            self.printer_mode = printer_mode
            self.print_method = print_method
            self.printer_address = bluetooth_address
            self.printer_name = printer_name
            self.printer_model = printer_model
            self.network_ip = network_ip
            self.network_port = network_port
            self.label_width = label_width
            self.label_height = label_height
            self.label_show_name = bool(label_show_name)
            self.label_show_barcode = bool(label_show_barcode)
            self.label_title_font_size = label_title_font_size
            self.label_barcode_font_size = label_barcode_font_size
            self.label_barcode_height = label_barcode_height
            self.label_barcode_scale = label_barcode_scale
            self.label_title_lines = label_title_lines
            self.brother_ql_label = brother_ql_label
            self.label_show_barcode_text = bool(label_show_barcode_text)
            self.printer_mac = printer_mac
            return True
        except Exception as e:
            print(f"Error saving printer config: {e}")
            return False
        finally:
            if conn:
                conn.close()

    def get_config_snapshot(self):
        self.load_printer_config()
        return {
            'printer_type': self.printer_type or '',
            'printer_mode': self.printer_mode or 'thermal',
            'print_method': self.print_method or 'escpos',
            'bluetooth_address': self.printer_address or '',
            'printer_name': self.printer_name or '',
            'printer_model': self.printer_model or '',
            'network_ip': self.network_ip or '',
            'network_port': self.network_port or 9100,
            'label_width': self.label_width or 50,
            'label_height': self.label_height or 30,
            'label_show_name': bool(self.label_show_name),
            'label_show_barcode': bool(self.label_show_barcode),
            'label_title_font_size': self.label_title_font_size or 10,
            'label_barcode_font_size': self.label_barcode_font_size or 10,
            'label_barcode_height': self.label_barcode_height or 34,
            'label_barcode_scale': self.label_barcode_scale or 1.8,
            'label_title_lines': self.label_title_lines or 2,
            'brother_ql_label': self.brother_ql_label or '',
            'label_show_barcode_text': bool(self.label_show_barcode_text),
            'printer_mac': self.printer_mac or ''
        }

    def wake_printer(self):
        """Attempt to wake the printer.

        1. Wake-on-LAN magic packet (if printer_mac is set) — wakes from fully off/deep sleep.
        2. TCP knock on port 9100 — wakes from standby when NIC is still active.
        3. HTTP ping on port 80 — Brother web UI nudge.

        Returns a dict: {'ok': bool, 'host': str, 'mac': str, 'results': dict}
        """
        import logging
        log = logging.getLogger(__name__)

        host = str(self.network_ip or '').strip()
        mac = str(self.printer_mac or '').strip()

        if not host and not mac:
            return {'ok': False, 'error': 'no_config', 'message': 'No network IP or MAC address configured'}

        results = {}

        # ── 1. Wake-on-LAN magic packet ───────────────────────────────────────
        if mac:
            try:
                mac_clean = mac.replace(':', '').replace('-', '').replace('.', '').upper()
                if len(mac_clean) != 12:
                    raise ValueError(f'Invalid MAC address length: {mac!r}')
                mac_bytes = bytes.fromhex(mac_clean)
                magic = b'\xff' * 6 + mac_bytes * 16  # 102-byte magic packet

                # Send to both global broadcast and subnet broadcast
                bcast_targets = ['255.255.255.255']
                if host:
                    parts = host.split('.')
                    if len(parts) == 4:
                        bcast_targets.append(f'{parts[0]}.{parts[1]}.{parts[2]}.255')

                sent_any = False
                for bcast in bcast_targets:
                    try:
                        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                            s.sendto(magic, (bcast, 9))
                        log.warning(f"[wake_printer] WoL magic packet → {bcast} (MAC {mac})")
                        sent_any = True
                    except Exception as e:
                        log.warning(f"[wake_printer] WoL failed for {bcast}: {e}")
                results['wol'] = 'sent' if sent_any else 'failed'
            except Exception as e:
                results['wol'] = f'error: {e}'
                log.warning(f"[wake_printer] WoL error: {e}")

        # ── 2. TCP knock on port 9100 ─────────────────────────────────────────
        if host:
            try:
                with socket.create_connection((host, 9100), timeout=3.0):
                    pass
                results['port_9100'] = 'connected'
            except OSError as e:
                results['port_9100'] = f'failed: {e}'
            log.warning(f"[wake_printer] port 9100 on {host}: {results['port_9100']}")

            # ── 3. HTTP ping on port 80 ───────────────────────────────────────
            try:
                import urllib.request
                req = urllib.request.Request(f'http://{host}/', headers={'User-Agent': 'SweetShelves/1.0'})
                with urllib.request.urlopen(req, timeout=3) as resp:
                    results['port_80'] = f'http {resp.status}'
            except Exception as e:
                results['port_80'] = f'failed: {e}'
            log.warning(f"[wake_printer] port 80 on {host}: {results['port_80']}")

        ok = results.get('wol') == 'sent' or results.get('port_9100') == 'connected'
        return {'ok': ok, 'host': host, 'mac': mac, 'results': results}

    def _tcp_connectable(self, host, port, timeout=2.0):
        host = str(host or '').strip()
        if not host:
            return False
        try:
            port = int(port)
        except Exception:
            return False
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except Exception:
            return False

    def _port_exists_locally(self, port_name):
        target = str(port_name or '').strip()
        if not target:
            return False

        platform_type = self.detect_platform_type()
        if platform_type == 'windows_serial':
            try:
                import serial.tools.list_ports
                for port in serial.tools.list_ports.comports():
                    if str(getattr(port, 'device', '') or '').strip().lower() == target.lower():
                        return True
            except Exception:
                return target.upper().startswith('COM')
            return False

        if target.startswith('/dev/'):
            return os.path.exists(target)
        return False

    def _find_reachable_network_port(self, host, candidate_ports):
        """Return the first reachable TCP port from a candidate list."""
        host = str(host or '').strip()
        if not host:
            return None

        seen = set()
        for raw_port in candidate_ports:
            try:
                port = int(raw_port)
            except (TypeError, ValueError):
                continue

            if port < 1 or port > 65535 or port in seen:
                continue

            seen.add(port)
            if self._tcp_connectable(host, port):
                return port

        return None

    def get_connection_status(self):
        """Return a lightweight status payload for UI connection indicators."""
        config = self.get_config_snapshot()
        printer_type = str(config.get('printer_type') or '').strip().lower()
        print_method = str(config.get('print_method') or 'escpos').strip().lower()
        printer_mode = str(config.get('printer_mode') or 'thermal').strip().lower()
        printer_name = str(config.get('printer_name') or '').strip()
        printer_model = str(config.get('printer_model') or '').strip()
        display_name = printer_name or printer_model or 'Printer'
        address = str(config.get('bluetooth_address') or '').strip()
        network_ip = str(config.get('network_ip') or '').strip()
        network_port = self._coerce_dimension(config.get('network_port'), 9100)

        status = {
            'configured': False,
            'connected': False,
            'can_print': False,
            'printer_type': printer_type,
            'printer_mode': printer_mode or 'thermal',
            'print_method': print_method or 'escpos',
            'printer_name': printer_name,
            'printer_model': printer_model,
            'display_name': display_name,
            'message': 'Printer not configured',
            'detail': '',
            'connection_target': '',
            'label_width': self._coerce_dimension(config.get('label_width'), 50),
            'label_height': self._coerce_dimension(config.get('label_height'), 30)
        }

        configured = bool(network_ip) if printer_type == 'network' else bool(address)
        status['configured'] = configured
        if printer_type == 'network':
            status['connection_target'] = f'{network_ip}:{network_port}' if network_ip else ''
        else:
            status['connection_target'] = address

        if configured and printer_mode != 'thermal':
            status['connected'] = False
            status['can_print'] = False
            status['message'] = 'Thermal setup required'
            status['detail'] = (
                f'Saved profile "{display_name}" is in {printer_mode} mode. '
                'Item Prep is waiting for a thermal printer profile such as the Brother QL-820NWBc.'
            )
            return status

        if not configured:
            if print_method == 'browser':
                status['message'] = 'Set up printer connection'
                status['detail'] = 'Save the thermal printer IP or device path to enable live status and auto-print.'
            else:
                status['detail'] = 'Save a thermal printer address before testing direct printing.'
            return status

        reachable = False
        last_error = ''
        reachable_detail = ''

        try:
            if printer_type == 'network':
                if print_method == 'browser':
                    reachable_port = self._find_reachable_network_port(
                        network_ip,
                        [network_port, 631, 9100, 80, 443]
                    )
                    if reachable_port:
                        reachable = True
                        status['connection_target'] = f'{network_ip}:{reachable_port}'
                        if reachable_port == network_port:
                            reachable_detail = (
                                f'{display_name} responded on {network_ip}:{reachable_port} '
                                'for browser-driven label printing.'
                            )
                        else:
                            reachable_detail = (
                                f'{display_name} responded on {network_ip}:{reachable_port}. '
                                'Browser mode can use that network connection even if the saved port is different.'
                            )
                    else:
                        last_error = (
                            f'No response from {network_ip} on common printer ports '
                            f'({network_port}, 631, 9100, 80, 443)'
                        )
                elif self._tcp_connectable(network_ip, network_port):
                    reachable = True
                    status['connection_target'] = f'{network_ip}:{network_port}'
                else:
                    last_error = f'No response from {network_ip}:{network_port}'
            elif print_method == 'browser':
                reachable = self._port_exists_locally(address)
                if not reachable:
                    last_error = f'{address} not found on this machine'
            else:
                if self.printer:
                    self.ensure_connection()
                    reachable = True
                else:
                    self.connect_printer()
                    reachable = True
        except Exception as e:
            last_error = str(e)
        finally:
            if print_method == 'escpos' and self.printer:
                self.disconnect_printer()

        status['connected'] = bool(reachable)
        if print_method == 'browser':
            status['can_print'] = bool(reachable)
            status['message'] = 'Connected' if reachable else 'Not connected'
            status['detail'] = (
                reachable_detail or f'{display_name} is reachable for browser-driven label printing.'
                if reachable else
                (last_error or 'The saved printer target is not reachable yet.')
            )
        else:
            status['can_print'] = bool(reachable)
            status['message'] = 'Connected' if reachable else 'Not connected'
            status['detail'] = (
                f'{display_name} is ready for direct {printer_mode} printing.'
                if reachable else
                (last_error or 'Direct printer connection failed.')
            )

        return status
    
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
                
                # Method 3: Serial port (e.g., /dev/ttyUSB0, /dev/ttyACM0)
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
            except Exception:
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

    def _coerce_bool(self, value, default=True):
        if isinstance(value, bool):
            return value
        if value is None:
            return bool(default)
        return str(value).strip().lower() not in ('0', 'false', 'no', 'off', '')

    def get_label_layout(self, config_override=None):
        config = self.get_config_snapshot()
        if isinstance(config_override, dict):
            config.update(config_override)

        return {
            'label_width': self._coerce_dimension(config.get('label_width'), 65, minimum=20, maximum=120),
            'label_height': self._coerce_dimension(config.get('label_height'), 30, minimum=5, maximum=300),
            'label_show_name': self._coerce_bool(config.get('label_show_name'), True),
            'label_show_barcode': self._coerce_bool(config.get('label_show_barcode'), True),
            'label_title_font_size': self._coerce_dimension(config.get('label_title_font_size'), 10, minimum=6, maximum=48),
            'label_barcode_font_size': self._coerce_dimension(config.get('label_barcode_font_size'), 10, minimum=6, maximum=36),
            'label_barcode_height': self._coerce_dimension(config.get('label_barcode_height'), 34, minimum=20, maximum=140),
            'label_barcode_scale': self._coerce_float(config.get('label_barcode_scale'), 1.8, minimum=0.6, maximum=4.0),
            'label_title_lines': self._coerce_dimension(config.get('label_title_lines'), 2, minimum=1, maximum=4),
            'label_show_barcode_text': self._coerce_bool(config.get('label_show_barcode_text'), True)
        }

    def _load_label_font(self, size):
        from PIL import ImageFont

        candidate_fonts = [
            "arial.ttf",
            "Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ]
        for font_name in candidate_fonts:
            try:
                return ImageFont.truetype(font_name, size)
            except (IOError, OSError):
                continue
        return ImageFont.load_default()

    def _wrap_label_text(self, text, draw, font, max_width, max_lines):
        text = str(text or '').strip()
        if not text:
            return []

        words = text.split()
        if not words:
            return []

        lines = []
        current = words[0]

        def _line_width(line_text):
            bbox = draw.textbbox((0, 0), line_text, font=font)
            return bbox[2] - bbox[0]

        for word in words[1:]:
            trial = f"{current} {word}".strip()
            if _line_width(trial) <= max_width:
                current = trial
                continue

            lines.append(current)
            current = word
            if len(lines) >= max_lines - 1:
                break

        remaining_words = words[len(' '.join(lines + [current]).split()):]
        final_line = current
        if remaining_words:
            final_line = f"{final_line} {' '.join(remaining_words)}".strip()

        if _line_width(final_line) > max_width:
            while len(final_line) > 3 and _line_width(final_line + '...') > max_width:
                final_line = final_line[:-1].rstrip()
            if final_line != current or remaining_words:
                final_line = (final_line + '...').strip()

        lines.append(final_line)
        return lines[:max_lines]

    def generate_barcode_image(self, upc, item_description=None, width=400, height=200, layout_override=None):
        """Generate barcode image from UPC"""
        try:
            layout = self.get_label_layout(layout_override)
            barcode_value = str(upc or '').strip()
            clean_upc = ''.join(filter(str.isdigit, barcode_value))

            if not barcode_value:
                raise ValueError('UPC is required')

            from PIL import ImageDraw, ImageOps

            render_dpi = 300 if self._is_brother_ql_printer() else 203
            px_per_mm = render_dpi / 25.4
            label_width_px = max(1, round(self._coerce_dimension(layout.get('label_width'), 65, minimum=20, maximum=120) * px_per_mm))
            label_height_px = max(1, round(self._coerce_dimension(layout.get('label_height'), 30, minimum=5, maximum=300) * px_per_mm))
            content_width = max(40, label_width_px - (round(2.0 * px_per_mm) * 2))

            show_barcode = self._coerce_bool(layout.get('label_show_barcode'), True)
            show_barcode_text = self._coerce_bool(layout.get('label_show_barcode_text'), True)
            show_name = self._coerce_bool(layout.get('label_show_name'), True)
            title_text = str(item_description or '').strip() if show_name else ''
            title_font_size_mm = self._coerce_dimension(layout.get('label_title_font_size'), 10, minimum=6, maximum=48) * 0.9
            barcode_font_size_mm = self._coerce_dimension(layout.get('label_barcode_font_size'), 10, minimum=6, maximum=36) * 0.92
            title_font_px = max(12, round(title_font_size_mm * px_per_mm))
            barcode_font_px = max(10, round(barcode_font_size_mm * px_per_mm))
            block_gap_px = max(2, round(1.4 * px_per_mm))
            text_gap_px = max(1, round(0.45 * px_per_mm))
            is_numeric_standard = barcode_value.isdigit() and len(barcode_value) in (8, 12, 13)

            def _crop_to_ink(image):
                ink_box = ImageOps.invert(image.convert('L')).getbbox()
                if not ink_box:
                    return image
                return image.crop(ink_box)

            def _render_text_block(text, font_px, max_lines, break_words=False):
                text = str(text or '').strip()
                if not text:
                    return None
                font = self._load_label_font(font_px)
                measure_canvas = Image.new('L', (content_width, max(font_px * max_lines, 32)), 255)
                measure_draw = ImageDraw.Draw(measure_canvas)
                if break_words:
                    fitted_font = font
                    fitted_size = font_px
                    bbox = measure_draw.textbbox((0, 0), text, font=fitted_font)
                    while fitted_size > 8 and (bbox[2] - bbox[0]) > content_width:
                        fitted_size -= 1
                        fitted_font = self._load_label_font(fitted_size)
                        bbox = measure_draw.textbbox((0, 0), text, font=fitted_font)
                    font = fitted_font
                    lines = [text]
                else:
                    lines = self._wrap_label_text(text, measure_draw, font, content_width, max_lines)
                    if not lines:
                        return None

                line_height = max(round(font.size * 1.08), font.size + 2)
                img_height = max(line_height * len(lines), font.size + 6)
                img = Image.new('L', (content_width, img_height), 255)
                draw = ImageDraw.Draw(img)
                y = 0
                for line in lines:
                    bbox = draw.textbbox((0, 0), line, font=font)
                    line_width = bbox[2] - bbox[0]
                    line_height_actual = bbox[3] - bbox[1]
                    x = max(0, (content_width - line_width) // 2)
                    line_y = max(0, y + ((line_height - line_height_actual) // 2))
                    draw.text((x, line_y), line, fill=0, font=font)
                    y += line_height
                return _crop_to_ink(img).convert('RGB')

            def _render_barcode_only():
                if is_numeric_standard and len(clean_upc) == 12:
                    barcode_class = barcode.get_barcode_class('upc')
                    barcode_payload = clean_upc
                elif is_numeric_standard and len(clean_upc) == 13:
                    barcode_class = barcode.get_barcode_class('ean13')
                    barcode_payload = clean_upc
                elif is_numeric_standard and len(clean_upc) == 8:
                    barcode_class = barcode.get_barcode_class('ean8')
                    barcode_payload = clean_upc
                else:
                    barcode_class = barcode.get_barcode_class('code128')
                    barcode_payload = barcode_value

                writer = ImageWriter()
                css_px_per_mm = 96 / 25.4
                module_width_mm = max(1.2, self._coerce_float(layout.get('label_barcode_scale'), 1.8, minimum=0.6, maximum=4.0) * 1.35) / css_px_per_mm
                module_height_mm = max(6.0, self._coerce_dimension(layout.get('label_barcode_height'), 34, minimum=20, maximum=140) * 0.95)
                barcode_options = {
                    'module_width': module_width_mm,
                    'module_height': module_height_mm,
                    'quiet_zone': 0,
                    'font_size': 0,
                    'text_distance': 0,
                    'write_text': False,
                    'dpi': render_dpi,
                    'background': 'white',
                    'foreground': 'black'
                }
                writer.set_options(barcode_options)
                barcode_instance = barcode_class(barcode_payload, writer=writer)
                buffer = io.BytesIO()
                try:
                    barcode_instance.write(buffer, barcode_options)
                except TypeError:
                    barcode_instance.write(buffer)
                buffer.seek(0)
                barcode_img = Image.open(buffer).convert('L')
                barcode_img = _crop_to_ink(barcode_img)
                if barcode_img.width <= 0 or barcode_img.height <= 0:
                    return Image.new('RGB', (content_width, max(20, round(6 * px_per_mm))), 'white')
                scale = content_width / float(barcode_img.width)
                scaled_width = max(1, round(barcode_img.width * scale))
                scaled_height = max(1, round(barcode_img.height * scale))
                return barcode_img.resize((scaled_width, scaled_height), Image.LANCZOS).convert('RGB')

            blocks = []
            if title_text:
                title_block = _render_text_block(
                    title_text,
                    title_font_px,
                    self._coerce_dimension(layout.get('label_title_lines'), 2, minimum=1, maximum=4)
                )
                if title_block is not None:
                    blocks.append(title_block)

            if show_barcode:
                barcode_blocks = []
                barcode_block = _render_barcode_only()
                if barcode_block is not None:
                    barcode_blocks.append(barcode_block)
                if show_barcode_text:
                    barcode_text_block = _render_text_block(barcode_value, barcode_font_px, 1, break_words=True)
                    if barcode_text_block is not None:
                        barcode_blocks.append(barcode_text_block)
                if barcode_blocks:
                    stacked_height = sum(block.height for block in barcode_blocks) + (text_gap_px * (len(barcode_blocks) - 1))
                    stacked_width = max(block.width for block in barcode_blocks)
                    barcode_group = Image.new('RGB', (stacked_width, stacked_height), 'white')
                    cursor_y = 0
                    for block in barcode_blocks:
                        x = max(0, (stacked_width - block.width) // 2)
                        barcode_group.paste(block, (x, cursor_y))
                        cursor_y += block.height + text_gap_px
                    blocks.append(barcode_group)
            elif show_barcode_text:
                barcode_text_only = _render_text_block(barcode_value, barcode_font_px, 1, break_words=True)
                if barcode_text_only is not None:
                    blocks.append(barcode_text_only)

            canvas = Image.new('RGB', (label_width_px, label_height_px), 'white')
            if not blocks:
                return canvas

            total_height = sum(block.height for block in blocks) + (block_gap_px * (len(blocks) - 1))
            start_y = max(0, (label_height_px - total_height) // 2)
            cursor_y = start_y
            for block in blocks:
                paste_block = block
                if block.width > content_width:
                    scale = content_width / float(block.width)
                    paste_block = block.resize((content_width, max(1, round(block.height * scale))), Image.LANCZOS)
                if cursor_y >= label_height_px:
                    break
                available_height = label_height_px - cursor_y
                if paste_block.height > available_height:
                    paste_block = paste_block.crop((0, 0, paste_block.width, available_height))
                x = max(0, (label_width_px - paste_block.width) // 2)
                canvas.paste(paste_block, (x, cursor_y))
                cursor_y += paste_block.height + block_gap_px

            return canvas
        except Exception as e:
            print(f"Error generating barcode: {e}")
            raise

    def _is_brother_ql_printer(self):
        """True when the configured printer model is a Brother QL label printer."""
        model = str(self.printer_model or self.printer_name or '').upper().strip()
        return 'QL-' in model

    def _get_brother_ql_label_id(self, config_override=None):
        """Return the brother_ql label identifier to use.

        Uses the explicitly saved brother_ql_label when set (e.g. '62red', '29x90').
        Falls back to mapping label_width to the nearest continuous-roll identifier.
        """
        config = self.get_config_snapshot()
        if isinstance(config_override, dict):
            config.update(config_override)

        explicit = str(config.get('brother_ql_label') or self.brother_ql_label or '').strip()
        if explicit:
            return explicit
        width = self._coerce_dimension(config.get('label_width'), self.label_width or 62, minimum=12, maximum=120)
        label_map = {12: '12', 17: '17', 29: '29', 38: '38',
                     50: '50', 54: '54', 62: '62', 102: '102'}
        closest = min(label_map.keys(), key=lambda k: abs(k - width))
        return label_map[closest]

    def _print_via_brother_ql(self, img, layout_override=None):
        """Send a PIL image to the Brother QL printer using the native raster protocol."""
        import re
        import logging
        log = logging.getLogger(__name__)

        if not BROTHER_QL_AVAILABLE:
            raise Exception(
                "brother_ql is required for Brother QL printers. "
                "Install with: pip install brother_ql"
            )

        # Place barcode on a canvas sized to label_width × label_height mm at 300 DPI.
        # Scale to fill the full label width first (matching the preview), falling back
        # to height-fit only if the barcode would overflow the label height.
        # Preserving bar width is critical — horizontal compression causes partial scan failures.
        layout = self.get_label_layout(layout_override)
        effective_width_mm = self._coerce_dimension(layout.get('label_width'), self.label_width or 62, minimum=12, maximum=120)
        effective_height_mm = self._coerce_dimension(layout.get('label_height'), self.label_height or 30, minimum=5, maximum=300)
        PRINT_DPI = 300
        target_w = max(1, round(effective_width_mm * PRINT_DPI / 25.4))
        target_h = max(1, round(effective_height_mm * PRINT_DPI / 25.4))

        # Convert mode before compositing so we can always paste onto a white canvas
        if img.mode == 'RGBA':
            bg = Image.new('RGB', img.size, 'white')
            bg.paste(img, mask=img.split()[3])
            img = bg
        if img.mode != 'L':
            img = img.convert('L')

        # Scale to fill the full label width (matching the preview), then crop to height.
        # Never shrink to fit height — that compresses bar widths and breaks scanning.
        fit_w = target_w
        fit_h = max(1, round(img.height * target_w / img.width))

        import logging as _lg
        _lg.getLogger(__name__).warning(
            f"[brother_ql] scaling {img.size} → ({fit_w}, {fit_h}), canvas ({target_w}, {target_h}) "
            f"for {effective_width_mm}×{effective_height_mm}mm @{PRINT_DPI}dpi"
        )
        resized = img.resize((fit_w, fit_h), Image.LANCZOS)

        # Threshold to pure black/white — LANCZOS produces gray anti-alias pixels which
        # brother_ql routes into the red ink channel when red=True, causing colored bars.
        resized = resized.point(lambda p: 0 if p < 128 else 255)

        # Find the bounding box of actual black pixels (bars + text), ignoring white quiet zones.
        # ImageOps.invert makes black→white so getbbox() returns the region with content.
        from PIL import ImageOps
        bbox = ImageOps.invert(resized).getbbox()
        if bbox:
            content_top = max(0, bbox[1] - 2)
            content_bottom = min(fit_h, bbox[3] + 2)
            content = resized.crop((0, content_top, fit_w, content_bottom))
        else:
            content = resized

        content_h = content.height
        canvas = Image.new('L', (target_w, target_h), 255)
        if content_h <= target_h:
            # Content fits — center vertically
            dst_y = (target_h - content_h) // 2
            canvas.paste(content, (0, dst_y))
        else:
            # Content taller than label — crop to label height.
            # Never scale down: that narrows the bars and shrinks the barcode.
            # The label height acts as a cut boundary, same as CSS overflow:hidden in preview.
            canvas.paste(content.crop((0, 0, fit_w, target_h)), (0, 0))
        img = canvas

        # Extract just the model ID — user may have saved "Brother QL-820NWBc"
        # but brother_ql expects "QL-820NWB" or "QL-820NWBc"
        raw_model = str(self.printer_model or 'QL-820NWB').strip()
        m = re.search(r'QL-\S+', raw_model, re.IGNORECASE)
        model = m.group(0) if m else raw_model

        label_id = self._get_brother_ql_label_id(layout_override)
        log.warning(f"[brother_ql] model={model!r} label={label_id!r} image={img.size}")

        # Try the exact model name; if unknown, fall back by stripping trailing 'c'
        try:
            qlr = BrotherQLRaster(model)
        except Exception:
            fallback = re.sub(r'c$', '', model, flags=re.IGNORECASE)
            if fallback != model:
                log.warning(f"[brother_ql] model {model!r} not found, trying {fallback!r}")
                qlr = BrotherQLRaster(fallback)
                model = fallback
            else:
                raise

        # red=True generates a second color plane required for black+red rolls (e.g. 62red)
        use_red = label_id.endswith('red')
        log.warning(f"[brother_ql] red={use_red}")
        _bql_convert(
            qlr=qlr,
            images=[img],
            label=label_id,
            rotate='auto',
            threshold=70,
            dither=False,
            compress=False,
            red=use_red,
            cut_now=False,
            cut_every_n_labels=1,
        )

        if not getattr(qlr, 'data', None):
            raise Exception(
                f"brother_ql produced no raster data for model={model!r}, label={label_id!r}. "
                f"Verify the label width ({effective_width_mm}mm) matches the media loaded."
            )

        log.warning(f"[brother_ql] raster ready: {len(qlr.data)} bytes")

        if self.printer_type == 'network' and self.network_ip:
            ip = str(self.network_ip).strip()
            port = int(self.network_port or 9100)
            identifier = f'tcp://{ip}:{port}'
            backend = 'network'
        elif self.printer_address:
            identifier = str(self.printer_address).strip()
            backend = 'linux_kernel' if identifier.startswith('/dev/') else 'pyusb'
        else:
            raise Exception("No printer address or network IP configured for Brother QL")

        log.warning(f"[brother_ql] sending to {identifier}")
        result = _bql_send(
            instructions=qlr.data,
            printer_identifier=identifier,
            backend_identifier=backend,
            blocking=True,
        )
        log.warning(f"[brother_ql] result: {result}")

        # brother_ql's upstream README notes that the network backend cannot read printer
        # state back from the device, so "wrong roll" / end-of-roll signals are not
        # reliable over TCP. If the job was sent on the network backend, don't turn
        # the ambiguous result into a user-facing hard failure.
        if backend == 'network':
            if isinstance(result, dict) and not result.get('did_print', True):
                log.warning(
                    "[brother_ql] network backend returned an ambiguous status after send: %s",
                    result
                )
            return True

        if isinstance(result, dict) and not result.get('did_print', True):
            msg = result.get('error_message') or 'Printer rejected job — check label media matches settings'
            raise Exception(
                f"brother_ql: {msg}. Current label type is '{label_id}'. "
                f"If you are using 62mm continuous roll, set Brother QL Label Type to '62' "
                f"(or '62red' for the black+red roll)."
            )

    def print_barcode(self, upc, item_description=None, quantity=1, layout_override=None):
        """Print barcode label to printer (network, Bluetooth, or USB)"""

        try:
            img = self.generate_barcode_image(upc, item_description, layout_override=layout_override)

            # Brother QL printers use a native raster protocol, not ESC/POS.
            if self._is_brother_ql_printer():
                for _ in range(max(1, int(quantity))):
                    self._print_via_brother_ql(img, layout_override=layout_override)
                return True

            if not ESCPOS_AVAILABLE:
                raise Exception("python-escpos not installed")

            self.ensure_connection()

            if self.printer_mode == 'thermal':
                print("Printing in THERMAL mode...")

                img = img.convert('L')

                max_width = 384
                if img.width > max_width:
                    ratio = max_width / img.width
                    new_height = int(img.height * ratio)
                    img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)

                img = img.convert('1')

                for i in range(quantity):
                    retry_count = 0
                    max_retries = 2

                    while retry_count <= max_retries:
                        try:
                            self.printer.set(align='center')
                            self.printer.image(img, impl='bitImageColumn')
                            self.printer.text('\n')
                            self.printer.cut()
                            break
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
                                raise

            else:
                print("Printing in INKJET mode...")

                max_width = 800
                if img.width > max_width:
                    ratio = max_width / img.width
                    new_height = int(img.height * ratio)
                    img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)

                img = img.convert('RGB')

                for i in range(quantity):
                    retry_count = 0
                    max_retries = 2

                    while retry_count <= max_retries:
                        try:
                            self.printer.set(align='center')
                            self.printer.image(img, high_density_vertical=True, high_density_horizontal=True)
                            self.printer.text('\n\n')
                            try:
                                self.printer.cut()
                            except Exception:
                                self.printer.text('\n' * 5)
                            break
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
                                raise

            return True
        except Exception as e:
            print(f"Error printing barcode: {e}")
            self.disconnect_printer()
            raise

    def test_print(self, layout_override=None, sample_upc="123456789012", sample_description="Test Item"):
        """Print a test label"""
        # Brother QL printers use raster protocol — route through print_barcode which handles it
        if self._is_brother_ql_printer():
            img = self.generate_barcode_image(sample_upc, sample_description, layout_override=layout_override)
            self._print_via_brother_ql(img, layout_override=layout_override)
            return True

        retry_count = 0
        max_retries = 2

        while retry_count <= max_retries:
            try:
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

                self.printer.text("\nTest Barcode:\n")
                test_img = self.generate_barcode_image(sample_upc, sample_description, layout_override=layout_override)

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
                else:
                    test_img = test_img.convert('RGB')
                    max_width = 800
                    if test_img.width > max_width:
                        ratio = max_width / test_img.width
                        new_height = int(test_img.height * ratio)
                        test_img = test_img.resize((max_width, new_height), Image.Resampling.LANCZOS)
                    self.printer.set(align='center')
                    self.printer.image(test_img, high_density_vertical=True, high_density_horizontal=True)

                self.printer.text("\n")

                try:
                    self.printer.cut()
                except Exception:
                    self.printer.text('\n' * 3)

                return True

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
                '/dev/ttyACM0', '/dev/ttyACM1',
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
                    except Exception:
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

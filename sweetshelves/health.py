"""Health for Sweet Shelves."""

import datetime
import os
import sqlite3
import time
from . import runtime as ss_runtime


def collect_health_stats():
    """Collect all health statistics for the app"""
    import platform
    import psutil
    
    stats = {}
    
    # Server uptime (Flask process uptime)
    try:
        if not hasattr(ss_runtime.app, 'start_time'):
            ss_runtime.app.start_time = time.time()
        uptime_seconds = time.time() - ss_runtime.app.start_time
        days = int(uptime_seconds // 86400)
        hours = int((uptime_seconds % 86400) // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        stats['uptime'] = f"{days}d {hours}h {minutes}m"
    except Exception:
        stats['uptime'] = 'Unknown'
    
    # System info
    stats['python_version'] = platform.python_version()
    stats['timestamp'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # Memory usage
    try:
        memory = psutil.virtual_memory()
        stats['memory_used_gb'] = round(memory.used / (1024**3), 2)
        stats['memory_total_gb'] = round(memory.total / (1024**3), 2)
        stats['memory_free_gb'] = round(memory.available / (1024**3), 2)
        stats['memory_percent'] = round(memory.percent, 1)
    except Exception:
        stats['memory_used_gb'] = 0
        stats['memory_total_gb'] = 0
        stats['memory_free_gb'] = 0
        stats['memory_percent'] = 0
    
    # CPU usage
    try:
        # Current CPU usage (1 second sample)
        stats['cpu_percent_current'] = round(psutil.cpu_percent(interval=1), 1)
        # Average CPU usage (since boot or process start)
        stats['cpu_percent_avg'] = round(psutil.cpu_percent(interval=0), 1)
    except Exception:
        stats['cpu_percent_current'] = 0
        stats['cpu_percent_avg'] = 0
    
    # CPU temperature (if available)
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            # Try to get CPU temp from common sensor names
            cpu_temp = None
            for name in ['coretemp', 'cpu_thermal', 'cpu-thermal', 'k10temp']:
                if name in temps and temps[name]:
                    cpu_temp = temps[name][0].current
                    break
            
            if cpu_temp:
                stats['cpu_temp_current'] = round(cpu_temp, 1)
                # Calculate average from all cores if available
                all_temps = [sensor.current for sensors in temps.values() for sensor in sensors]
                stats['cpu_temp_avg'] = round(sum(all_temps) / len(all_temps), 1) if all_temps else cpu_temp
            else:
                stats['cpu_temp_current'] = None
                stats['cpu_temp_avg'] = None
        else:
            stats['cpu_temp_current'] = None
            stats['cpu_temp_avg'] = None
    except Exception:
        stats['cpu_temp_current'] = None
        stats['cpu_temp_avg'] = None
    
    # Power status (battery/AC)
    try:
        battery = psutil.sensors_battery()
        if battery:
            stats['power_plugged'] = battery.power_plugged
            stats['battery_percent'] = round(battery.percent, 1)
            stats['battery_time_left'] = None
            if not battery.power_plugged and battery.secsleft != psutil.POWER_TIME_UNLIMITED:
                # Convert seconds to hours:minutes
                hours = int(battery.secsleft // 3600)
                minutes = int((battery.secsleft % 3600) // 60)
                stats['battery_time_left'] = f"{hours}h {minutes}m"
        else:
            stats['power_plugged'] = None
            stats['battery_percent'] = None
            stats['battery_time_left'] = None
    except Exception:
        stats['power_plugged'] = None
        stats['battery_percent'] = None
        stats['battery_time_left'] = None
    
    # Raspberry Pi undervoltage detection with persistent tracking
    stats['undervoltage_detected'] = False
    stats['undervoltage_now'] = False
    stats['undervoltage_count'] = 0
    stats['throttle_count'] = 0
    stats['last_undervoltage_time'] = None
    
    try:
        # Check for Raspberry Pi throttling status (includes undervoltage)
        import subprocess
        result = subprocess.run(['vcgencmd', 'get_throttled'], capture_output=True, text=True, timeout=2)
        if result.returncode == 0:
            # Parse throttled status (hex value)
            throttled_hex = result.stdout.strip().split('=')[1]
            throttled = int(throttled_hex, 16)
            
            # Bit 0: Undervoltage currently detected
            # Bit 16: Undervoltage has occurred since boot
            stats['undervoltage_now'] = bool(throttled & 0x1)
            stats['undervoltage_detected'] = bool(throttled & 0x10000)
            
            # Store throttle status for detailed reporting
            stats['throttle_status'] = {
                'undervoltage_now': bool(throttled & 0x1),
                'arm_frequency_capped_now': bool(throttled & 0x2),
                'currently_throttled': bool(throttled & 0x4),
                'soft_temp_limit_active': bool(throttled & 0x8),
                'undervoltage_occurred': bool(throttled & 0x10000),
                'arm_frequency_capped_occurred': bool(throttled & 0x20000),
                'throttling_occurred': bool(throttled & 0x40000),
                'soft_temp_limit_occurred': bool(throttled & 0x80000)
            }
            
            # Track undervoltage events in database
            conn = None
            try:
                conn = sqlite3.connect('sync_settings.db')
                cur = conn.cursor()
                cur.execute('CREATE TABLE IF NOT EXISTS power_events (key TEXT PRIMARY KEY, value TEXT)')
                
                # Get last known state
                cur.execute('SELECT value FROM power_events WHERE key = ?', ('last_throttle_state',))
                row = cur.fetchone()
                last_state = int(row[0]) if row else 0
                
                # Check if undervoltage state changed from off to on (new event)
                if stats['undervoltage_now'] and not (last_state & 0x1):
                    # Increment undervoltage counter
                    cur.execute('SELECT value FROM power_events WHERE key = ?', ('undervoltage_count',))
                    row = cur.fetchone()
                    count = int(row[0]) if row else 0
                    count += 1
                    cur.execute('INSERT OR REPLACE INTO power_events (key, value) VALUES (?, ?)', 
                               ('undervoltage_count', str(count)))
                    cur.execute('INSERT OR REPLACE INTO power_events (key, value) VALUES (?, ?)', 
                               ('last_undervoltage_time', datetime.datetime.now().isoformat()))
                    stats['undervoltage_count'] = count
                    stats['last_undervoltage_time'] = datetime.datetime.now().isoformat()
                else:
                    # Get existing count
                    cur.execute('SELECT value FROM power_events WHERE key = ?', ('undervoltage_count',))
                    row = cur.fetchone()
                    stats['undervoltage_count'] = int(row[0]) if row else 0
                    
                    cur.execute('SELECT value FROM power_events WHERE key = ?', ('last_undervoltage_time',))
                    row = cur.fetchone()
                    stats['last_undervoltage_time'] = row[0] if row else None
                
                # Check if throttling state changed
                if stats['throttle_status']['currently_throttled'] and not (last_state & 0x4):
                    cur.execute('SELECT value FROM power_events WHERE key = ?', ('throttle_count',))
                    row = cur.fetchone()
                    count = int(row[0]) if row else 0
                    count += 1
                    cur.execute('INSERT OR REPLACE INTO power_events (key, value) VALUES (?, ?)', 
                               ('throttle_count', str(count)))
                    stats['throttle_count'] = count
                else:
                    cur.execute('SELECT value FROM power_events WHERE key = ?', ('throttle_count',))
                    row = cur.fetchone()
                    stats['throttle_count'] = int(row[0]) if row else 0
                
                # Update last known state
                cur.execute('INSERT OR REPLACE INTO power_events (key, value) VALUES (?, ?)', 
                           ('last_throttle_state', str(throttled)))
                
                conn.commit()
            except Exception as e:
                print(f"Error tracking power events: {e}")
                pass
            finally:
                if conn is not None:
                    conn.close()
    except Exception:
        # Not a Pi or vcgencmd not available
        stats['throttle_status'] = None
    
    # Disk space
    try:
        import shutil
        total, used, free = shutil.disk_usage('.')
        stats['disk_free_gb'] = round(free / (1024**3), 2)
        stats['disk_total_gb'] = round(total / (1024**3), 2)
        stats['disk_percent'] = round((used / total) * 100, 1)
    except Exception:
        stats['disk_free_gb'] = 0
        stats['disk_total_gb'] = 0
        stats['disk_percent'] = 0
    
    # Database sizes
    db_sizes = {}
    for db_name in ['sold.db', 'bol.db', 'amazonStore.db', 'ebayStore.db', 'searchRack.db', 'rawbol.db']:
        try:
            size_bytes = os.path.getsize(db_name)
            db_sizes[db_name] = round(size_bytes / (1024**2), 2)  # MB
        except Exception:
            db_sizes[db_name] = 0
    stats['db_sizes'] = db_sizes
    
    # Static folder size
    try:
        static_size = 0
        for dirpath, dirnames, filenames in os.walk('static'):
            for filename in filenames:
                filepath = os.path.join(dirpath, filename)
                static_size += os.path.getsize(filepath)
        stats['static_folder_size_mb'] = round(static_size / (1024**2), 2)
    except Exception:
        stats['static_folder_size_mb'] = 0
    
    # Picture position folder size and count
    try:
        picture_position_path = os.path.join('static', 'picture_position')
        if os.path.exists(picture_position_path):
            picture_size = 0
            picture_count = 0
            for dirpath, dirnames, filenames in os.walk(picture_position_path):
                for filename in filenames:
                    filepath = os.path.join(dirpath, filename)
                    picture_size += os.path.getsize(filepath)
                    picture_count += 1
            stats['picture_position_size_mb'] = round(picture_size / (1024**2), 2)
            stats['picture_position_count'] = picture_count
        else:
            stats['picture_position_size_mb'] = 0
            stats['picture_position_count'] = 0
    except Exception:
        stats['picture_position_size_mb'] = 0
        stats['picture_position_count'] = 0
    
    # Sync status (last sync times from sync_settings.db)
    try:
        conn = sqlite3.connect('sync_settings.db')
        cur = conn.cursor()
        cur.execute('CREATE TABLE IF NOT EXISTS sync_status (key TEXT PRIMARY KEY, value TEXT)')
        cur.execute('SELECT key, value FROM sync_status')
        sync_data = dict(cur.fetchall())
        
        stats['ebay_orders_last'] = sync_data.get('ebay_orders_last', 'Never')
        stats['ebay_listings_last'] = sync_data.get('ebay_listings_last', 'Never')
        stats['amazon_orders_last'] = sync_data.get('amazon_orders_last', 'Never')
        stats['amazon_listings_last'] = sync_data.get('amazon_listings_last', 'Never')
        stats['amazon_upcs_last'] = sync_data.get('amazon_upcs_last', 'Never')
        stats['auto_sync_enabled'] = sync_data.get('auto_sync_enabled', 'false') == 'true'
        
    except Exception:
        stats['ebay_orders_last'] = 'Unknown'
        stats['ebay_listings_last'] = 'Unknown'
        stats['amazon_orders_last'] = 'Unknown'
        stats['amazon_listings_last'] = 'Unknown'
        stats['amazon_upcs_last'] = 'Unknown'
        stats['auto_sync_enabled'] = False
    finally:
        conn.close()
    
    # System warnings
    warnings = []
    
    # Check for low disk space
    if stats['disk_percent'] > 90:
        warnings.append(f"⚠️ Low disk space: {stats['disk_percent']}% used")
    
    # Check for high memory usage
    if stats['memory_percent'] > 90:
        warnings.append(f"⚠️ High memory usage: {stats['memory_percent']}% used")
    
    # Check for high CPU temperature
    if stats['cpu_temp_current'] and stats['cpu_temp_current'] > 80:
        warnings.append(f"⚠️ High CPU temperature: {stats['cpu_temp_current']}°C")
    
    # Check for power issues
    if stats['power_plugged'] is False:
        if stats['battery_percent'] and stats['battery_percent'] < 20:
            warnings.append(f"🔋 CRITICAL: Battery low at {stats['battery_percent']}%! {stats['battery_time_left'] or 'Unknown time'} remaining")
        elif stats['battery_percent'] and stats['battery_percent'] < 50:
            warnings.append(f"🔋 WARNING: Running on battery - {stats['battery_percent']}% remaining")
        else:
            warnings.append(f"🔋 Running on battery power ({stats['battery_percent']}%)")
    
    # Check for Raspberry Pi undervoltage
    if stats['undervoltage_now']:
        count_text = f" (Event #{stats['undervoltage_count']})" if stats['undervoltage_count'] > 0 else ""
        warnings.append(f"⚡ CRITICAL: Undervoltage detected NOW!{count_text} Power supply insufficient!")
    elif stats['undervoltage_detected']:
        if stats['undervoltage_count'] > 0:
            warnings.append(f"⚡ WARNING: {stats['undervoltage_count']} undervoltage events detected - check power supply")
        else:
            warnings.append("⚡ WARNING: Undervoltage detected since boot - check power supply")
    
    # Check for other Pi throttling issues
    if stats.get('throttle_status'):
        ts = stats['throttle_status']
        if ts['currently_throttled']:
            throttle_text = f" ({stats['throttle_count']} events)" if stats['throttle_count'] > 0 else ""
            warnings.append(f"🐌 Performance throttled{throttle_text} due to power/temperature issues")
        if ts['soft_temp_limit_active']:
            warnings.append("🌡️ Soft temperature limit active - system thermal throttling")
    
    # Check for stale syncs (> 24 hours)
    for sync_key, sync_label in [
        ('ebay_orders_last', 'eBay Orders'),
        ('amazon_orders_last', 'Amazon Orders')
    ]:
        last_sync = stats.get(sync_key, 'Never')
        if last_sync not in ['Never', 'Unknown']:
            try:
                last_sync_dt = datetime.datetime.fromisoformat(last_sync)
                hours_ago = (datetime.datetime.now() - last_sync_dt).total_seconds() / 3600
                if hours_ago > 24:
                    warnings.append(f"⚠️ {sync_label} not synced in {int(hours_ago)} hours")
            except Exception:
                pass
    
    stats['warnings'] = warnings
    
    return stats


def generate_health_email_html(stats):
    """Generate HTML email body for health report"""
    warnings_html = ""
    if stats['warnings']:
        warnings_html = "<h3 style='color: #e74c3c;'>⚠️ WARNINGS</h3><ul style='margin: 10px 0;'>"
        for warning in stats['warnings']:
            warnings_html += f"<li style='color: #e74c3c;'>{warning}</li>"
        warnings_html += "</ul>"
    else:
        warnings_html = "<h3 style='color: #27ae60;'>✅ No Warnings</h3><p style='color: #7f8c8d;'>All systems operating normally</p>"
    
    disk_color = '#27ae60' if stats['disk_percent'] < 80 else ('#f39c12' if stats['disk_percent'] < 90 else '#e74c3c')
    memory_color = '#27ae60' if stats['memory_percent'] < 80 else ('#f39c12' if stats['memory_percent'] < 90 else '#e74c3c')
    cpu_color = '#27ae60' if stats['cpu_percent_current'] < 70 else ('#f39c12' if stats['cpu_percent_current'] < 90 else '#e74c3c')
    sync_status_color = '#27ae60' if stats['auto_sync_enabled'] else '#95a5a6'
    
    # CPU temperature display
    cpu_temp_html = ""
    if stats['cpu_temp_current'] is not None:
        temp_color = '#27ae60' if stats['cpu_temp_current'] < 70 else ('#f39c12' if stats['cpu_temp_current'] < 80 else '#e74c3c')
        cpu_temp_html = f"<div class='metric'><span class='label'>CPU Temperature:</span> <span class='value' style='color: {temp_color}; font-weight: bold;'>{stats['cpu_temp_current']}°C (avg: {stats['cpu_temp_avg']}°C)</span></div>"
    
    # Power status display
    power_html = ""
    if stats['power_plugged'] is not None:
        if stats['power_plugged']:
            power_html = "<div class='metric'><span class='label'>Power:</span> <span class='value' style='color: #27ae60; font-weight: bold;'>🔌 AC Power</span></div>"
        else:
            battery_color = '#e74c3c' if stats['battery_percent'] < 20 else ('#f39c12' if stats['battery_percent'] < 50 else '#27ae60')
            time_left_text = f" ({stats['battery_time_left']} left)" if stats['battery_time_left'] else ""
            power_html = f"<div class='metric'><span class='label'>Power:</span> <span class='value' style='color: {battery_color}; font-weight: bold;'>🔋 Battery {stats['battery_percent']}%{time_left_text}</span></div>"
    
    # Undervoltage status display (for Raspberry Pi)
    undervoltage_html = ""
    if stats.get('throttle_status'):
        ts = stats['throttle_status']
        if stats['undervoltage_now']:
            count_badge = f" <span style='background: #c0392b; color: white; padding: 2px 6px; border-radius: 3px; font-size: 11px;'>×{stats['undervoltage_count']}</span>" if stats['undervoltage_count'] > 0 else ""
            undervoltage_html = f"<div class='metric'><span class='label'>Voltage:</span> <span class='value' style='color: #e74c3c; font-weight: bold;'>⚡ UNDERVOLTAGE NOW!{count_badge}</span></div>"
        elif stats['undervoltage_detected']:
            if stats['undervoltage_count'] > 0:
                undervoltage_html = f"<div class='metric'><span class='label'>Voltage:</span> <span class='value' style='color: #f39c12; font-weight: bold;'>⚡ {stats['undervoltage_count']} events detected</span></div>"
            else:
                undervoltage_html = "<div class='metric'><span class='label'>Voltage:</span> <span class='value' style='color: #f39c12; font-weight: bold;'>⚡ Undervoltage occurred</span></div>"
        else:
            undervoltage_html = "<div class='metric'><span class='label'>Voltage:</span> <span class='value' style='color: #27ae60; font-weight: bold;'>✓ Normal</span></div>"
    
    html = f"""
    <html>
    <head>
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; line-height: 1.6; color: #333; max-width: 800px; margin: 0 auto; padding: 20px; }}
            .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; border-radius: 10px; text-align: center; margin-bottom: 30px; }}
            .section {{ margin: 20px 0; padding: 20px; background: #f8f9fa; border-left: 4px solid #3498db; border-radius: 8px; }}
            .metric {{ margin: 12px 0; padding: 10px 0; border-bottom: 1px solid #e0e0e0; display: flex; justify-content: space-between; }}
            .metric:last-child {{ border-bottom: none; }}
            .label {{ font-weight: 600; color: #555; }}
            .value {{ color: #2c3e50; font-weight: 500; }}
            h2 {{ color: #2c3e50; margin-top: 0; padding-bottom: 10px; border-bottom: 2px solid #3498db; }}
            .footer {{ margin-top: 30px; padding: 20px; background: #ecf0f1; border-radius: 8px; font-size: 13px; color: #7f8c8d; }}
        </style>
    </head>
    <body>
        <div class='header'>
            <h1 style='margin: 0; font-size: 28px;'>📊 Store App Health Report</h1>
            <p style='margin: 10px 0 0 0; opacity: 0.9;'>{stats['timestamp']}</p>
        </div>
        
        <div class='section'>
            <h2>🏥 SERVER HEALTH</h2>
            <div class='metric'><span class='label'>Uptime:</span> <span class='value'>{stats['uptime']}</span></div>
            <div class='metric'><span class='label'>Python Version:</span> <span class='value'>{stats['python_version']}</span></div>
            <div class='metric'><span class='label'>Memory:</span> <span class='value' style='color: {memory_color}; font-weight: bold;'>{stats['memory_free_gb']} GB free ({stats['memory_used_gb']}/{stats['memory_total_gb']} GB used, {stats['memory_percent']}%)</span></div>
            <div class='metric'><span class='label'>CPU Usage:</span> <span class='value' style='color: {cpu_color}; font-weight: bold;'>{stats['cpu_percent_current']}% current (avg: {stats['cpu_percent_avg']}%)</span></div>
            {cpu_temp_html}
            {power_html}
            {undervoltage_html}
            <div class='metric'><span class='label'>Disk Space:</span> <span class='value' style='color: {disk_color}; font-weight: bold;'>{stats['disk_free_gb']} GB free ({100-stats['disk_percent']:.1f}% available)</span></div>
        </div>
        
        <div class='section'>
            <h2>🔄 SYNC STATUS</h2>
            <div class='metric'><span class='label'>Auto-sync:</span> <span class='value' style='color: {sync_status_color}; font-weight: bold;'>{'✅ Enabled' if stats['auto_sync_enabled'] else '❌ Disabled'}</span></div>
            <div class='metric'><span class='label'>eBay Orders:</span> <span class='value'>{format_time_ago(stats['ebay_orders_last'])}</span></div>
            <div class='metric'><span class='label'>Amazon Orders:</span> <span class='value'>{format_time_ago(stats['amazon_orders_last'])}</span></div>
            <div class='metric'><span class='label'>Amazon UPCs:</span> <span class='value'>{format_time_ago(stats['amazon_upcs_last'])}</span></div>
        </div>
        
        <div class='section' style='border-left-color: {"#e74c3c" if stats["warnings"] else "#27ae60"};'>
            {warnings_html}
        </div>
        
        <div class='footer'>
            <p style='margin: 0 0 10px 0; font-weight: bold;'>📁 Database Sizes:</p>
            <ul style='margin: 5px 0; padding-left: 20px;'>
                {''.join([f"<li>{db}: <strong>{size} MB</strong></li>" for db, size in stats['db_sizes'].items()])}
            </ul>
            <p style='margin: 15px 0 10px 0; font-weight: bold;'>📂 Folder Sizes:</p>
            <ul style='margin: 5px 0; padding-left: 20px;'>
                <li>Static Folder: <strong>{stats['static_folder_size_mb']} MB</strong></li>
                <li>Picture Position: <strong>{stats['picture_position_size_mb']} MB</strong> ({stats['picture_position_count']} pictures)</li>
            </ul>
        </div>
    </body>
    </html>
    """
    return html


def generate_health_email_plain(stats):
    """Generate plain text email body for health report"""
    warnings_text = "\n".join(stats['warnings']) if stats['warnings'] else "✅ None"
    
    cpu_temp_text = ""
    if stats['cpu_temp_current'] is not None:
        cpu_temp_text = f"\n├─ CPU Temp: {stats['cpu_temp_current']}°C (avg: {stats['cpu_temp_avg']}°C)"
    
    power_text = ""
    if stats['power_plugged'] is not None:
        if stats['power_plugged']:
            power_text = "\n├─ Power: 🔌 AC Power"
        else:
            time_left = f" ({stats['battery_time_left']} left)" if stats['battery_time_left'] else ""
            power_text = f"\n├─ Power: 🔋 Battery {stats['battery_percent']}%{time_left}"
    
    # Undervoltage status (for Raspberry Pi)
    voltage_text = ""
    if stats.get('throttle_status'):
        if stats['undervoltage_now']:
            count_text = f" (×{stats['undervoltage_count']})" if stats['undervoltage_count'] > 0 else ""
            voltage_text = f"\n├─ Voltage: ⚡ UNDERVOLTAGE NOW!{count_text}"
        elif stats['undervoltage_detected']:
            if stats['undervoltage_count'] > 0:
                voltage_text = f"\n├─ Voltage: ⚡ {stats['undervoltage_count']} events detected"
            else:
                voltage_text = "\n├─ Voltage: ⚡ Undervoltage occurred"
        else:
            voltage_text = "\n├─ Voltage: ✓ Normal"
    
    text = f"""
📊 STORE APP HEALTH REPORT
Generated: {stats['timestamp']}

🏥 SERVER HEALTH
├─ Uptime: {stats['uptime']}
├─ Python: {stats['python_version']}
├─ Memory: {stats['memory_free_gb']} GB free ({stats['memory_used_gb']}/{stats['memory_total_gb']} GB used, {stats['memory_percent']}%)
├─ CPU Usage: {stats['cpu_percent_current']}% current (avg: {stats['cpu_percent_avg']}%){cpu_temp_text}{power_text}{voltage_text}
└─ Disk: {stats['disk_free_gb']} GB free ({100-stats['disk_percent']:.1f}%)

🔄 SYNC STATUS
├─ Auto-sync: {'✅ Enabled' if stats['auto_sync_enabled'] else '❌ Disabled'}
├─ eBay Orders: {format_time_ago(stats['ebay_orders_last'])}
├─ Amazon Orders: {format_time_ago(stats['amazon_orders_last'])}
└─ Amazon UPCs: {format_time_ago(stats['amazon_upcs_last'])}

⚠️ WARNINGS
{warnings_text}

📁 DATABASE SIZES
{', '.join([f"{db}: {size}MB" for db, size in stats['db_sizes'].items()])}

📂 FOLDER SIZES
Static: {stats['static_folder_size_mb']} MB
Picture Position: {stats['picture_position_size_mb']} MB ({stats['picture_position_count']} pictures)
"""
    return text


def format_time_ago(timestamp_str):
    """Format timestamp as human-readable time ago"""
    if timestamp_str in ['Never', 'Unknown']:
        return timestamp_str
    
    try:
        dt = datetime.datetime.fromisoformat(timestamp_str)
        now = datetime.datetime.now()
        diff = now - dt
        
        if diff.total_seconds() < 60:
            return "✅ Just now"
        elif diff.total_seconds() < 3600:
            mins = int(diff.total_seconds() / 60)
            return f"✅ {mins}m ago"
        elif diff.total_seconds() < 86400:
            hours = int(diff.total_seconds() / 3600)
            return f"⏰ {hours}h ago"
        else:
            days = int(diff.total_seconds() / 86400)
            return f"⚠️ {days}d ago"
    except Exception:
        return timestamp_str

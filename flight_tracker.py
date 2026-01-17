import time
import requests
import argparse
import sys
import os
import math
import termios
import tty
import select
from rich.live import Live
from rich.table import Table
from rich.console import Console, Group
from rich import box
from rich.panel import Panel
from rich.layout import Layout
from datetime import datetime
import threading

class KeyListener:
    """Context manager for non-blocking keyboard input."""
    def __enter__(self):
        self.old_settings = termios.tcgetattr(sys.stdin)
        self.start()
        return self

    def __exit__(self, type, value, traceback):
        self.stop()

    def start(self):
        tty.setcbreak(sys.stdin.fileno())

    def stop(self):
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self.old_settings)

    def data_available(self):
        return select.select([sys.stdin], [], [], 0) == ([sys.stdin], [], [])
    
    def read_char(self):
        if self.data_available():
            return sys.stdin.read(1)
        return None

# Default Dump1090 URL
DEFAULT_URL = "http://daphnis:8080/data/aircraft.json"

# Bristol Airport Coordinates
# Defaults from Config
CONFIG = {
    "url": DEFAULT_URL,
    "interval": 5,
    "rows": 15,
    "location_name": "Bristol Airport",
    "location_lat": 51.3827,
    "location_lon": -2.7191,
    # Column Colors
    "col_callsign": "bold cyan",
    "col_flag": "white",
    "col_airline": "bold green",
    "col_route": "cyan",
    "col_dist": "white",
    "col_alt": "yellow",
    "col_vr": "yellow",
    "col_speed": "blue",
    "col_heading": "magenta"
}

AIRLINES = {}
ROUTES = {}
PENDING_LOOKUPS = set()

def load_config():
    """
    Loads configuration from config.txt.
    """
    global CONFIG, LOCATION_NAME, DEFAULT_LAT, DEFAULT_LON
    file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.txt")
    
    if os.path.exists(file_path):
        try:
            with open(file_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line:
                        key, value = line.split("=", 1)
                        key = key.strip()
                        value = value.strip()
                        if key in CONFIG:
                            # Type conversion
                            if key in ["interval", "rows"]:
                                try:
                                    CONFIG[key] = int(value)
                                except: pass
                            elif key in ["location_lat", "location_lon"]:
                                try:
                                    CONFIG[key] = float(value)
                                except: pass
                            else:
                                CONFIG[key] = value
        except Exception as e:
            print(f"Error loading config.txt: {e}")
    else:
        # Create default config file if it doesn't exist
        save_config()
            
    # Apply to globals where needed (for now, mainly for generate_table access if we don't pass CONFIG)
    # But better to just update the global variables used by logic
    LOCATION_NAME = CONFIG["location_name"]
    DEFAULT_LAT = CONFIG["location_lat"]
    DEFAULT_LON = CONFIG["location_lon"]

def save_config():
    """
    Saves current configuration to config.txt.
    """
    file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.txt")
    try:
        with open(file_path, "w") as f:
            f.write(f"url={CONFIG['url']}\n")
            f.write(f"interval={CONFIG['interval']}\n")
            f.write(f"rows={CONFIG['rows']}\n")
            f.write(f"location_name={CONFIG['location_name']}\n")
            f.write(f"location_lat={CONFIG['location_lat']}\n")
            f.write(f"location_lon={CONFIG['location_lon']}\n")
            # Colors
            f.write(f"col_callsign={CONFIG['col_callsign']}\n")
            f.write(f"col_flag={CONFIG['col_flag']}\n")
            f.write(f"col_airline={CONFIG['col_airline']}\n")
            f.write(f"col_route={CONFIG['col_route']}\n")
            f.write(f"col_dist={CONFIG['col_dist']}\n")
            f.write(f"col_alt={CONFIG['col_alt']}\n")
            f.write(f"col_vr={CONFIG['col_vr']}\n")
            f.write(f"col_speed={CONFIG['col_speed']}\n")
            f.write(f"col_heading={CONFIG['col_heading']}\n")
    except Exception as e:
        pass

def fetch_flight_data(url):
    """
    Fetches flight data from the local dump1090-fa instance.
    """
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        return data.get("aircraft", [])
    except requests.RequestException as e:
        return None

def fetch_route_thread(callsign):
    """
    Background thread to fetch route and airline data from api.adsbdb.com
    """
    global ROUTES, PENDING_LOOKUPS, AIRLINES
    url = f"https://api.adsbdb.com/v0/callsign/{callsign}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            fr = data.get("response", {}).get("flightroute", {})
            
            # 1. Routes
            origin = fr.get("origin", {}).get("iata_code")
            dest = fr.get("destination", {}).get("iata_code")
            
            if origin and dest:
                route_str = f"{origin}/{dest}"
                ROUTES[callsign] = route_str
                
                # Persist to routes.txt
                file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "routes.txt")
                try:
                    with open(file_path, "a") as f:
                        f.write(f"\n{callsign},{origin},{dest}")
                except Exception:
                    pass

            # 2. Airlines
            al = fr.get("airline", {})
            icao = al.get("icao")
            name = al.get("name")
            
            if icao and name:
                # Check if we already have this airline
                if icao not in AIRLINES:
                    AIRLINES[icao] = name
                    # Persist to airlines.txt
                    file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "airlines.txt")
                    try:
                        with open(file_path, "a") as f:
                            f.write(f"\n{icao},{name}")
                    except Exception:
                        pass

    except Exception:
        pass # Silent fail in thread
    finally:
        if callsign in PENDING_LOOKUPS:
            PENDING_LOOKUPS.remove(callsign)

def calculate_distance(lat1, lon1, lat2, lon2):
    """
    Calculate the great circle distance between two points 
    on the earth (specified in decimal degrees) in nautical miles.
    """
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return float('inf')

    # Convert decimal degrees to radians 
    lon1, lat1, lon2, lat2 = map(math.radians, [lon1, lat1, lon2, lat2])

    # Haversine formula 
    dlon = lon2 - lon1 
    dlat = lat2 - lat1 
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a)) 
    r = 3440.065 # Radius of earth in nautical miles
    return c * r

def generate_table(flights, source_url, max_rows=10):
    """
    Generates a Rich Table with flight data from dump1090.
    """
    # Simply "Flight Tracker" as requested
    table = Table(title=f"Flight Tracker: {LOCATION_NAME}", box=box.HORIZONTALS)

    table.add_column("Call Sign", style=CONFIG["col_callsign"], width=10)
    table.add_column("Flag", style=CONFIG["col_flag"], justify="center", width=5)
    table.add_column("Airline", style=CONFIG["col_airline"], justify="left", width=24)
    table.add_column("Route", style=CONFIG["col_route"], justify="left", width=10)
    table.add_column("Heading", justify="right", style=CONFIG["col_heading"])
    table.add_column("Dist (nm)", justify="right", style=CONFIG["col_dist"], width=6)
    table.add_column("Alt (ft)", justify="right", style=CONFIG["col_alt"])
    table.add_column("VR (fpm)", justify="right", style=CONFIG["col_vr"])
    table.add_column("Speed (kts)", justify="right", style=CONFIG["col_speed"])
    
    if flights is None:
        table.add_row("ERROR", "", "", "", "", "", "", "", "", f"Could not connect to {source_url}")
    elif not flights:
        table.add_row("-", "-", "-", "-", "-", "-", "-", "-", "-", "No aircraft seen recently")
    else:
        # Calculate distances and sort
        flights_with_dist = []
        for f in flights:
            dist = calculate_distance(f.get("lat"), f.get("lon"), DEFAULT_LAT, DEFAULT_LON)
            flights_with_dist.append((f, dist))
        
        # Sort by distance
        flights_with_dist.sort(key=lambda x: x[1])
        
        # Take nearest N (max_rows)
        # Handle cases where requested rows > available flights safely via slice
        nearest_flights = flights_with_dist[:max_rows]

        for f, dist in nearest_flights:
            callsign = f.get("flight", "").strip()
            if not callsign:
                callsign = "-"
            
            hex_code = f.get("hex", "").upper()
            flag = get_flag(hex_code)
            airline = get_airline(callsign)
            
            # Route Lookup
            route = ROUTES.get(callsign)
            
            missing_data = False
            if not route:
                route = "?"
                missing_data = True
            
            if not airline:
                missing_data = True

            # Trigger background lookup if missing data and not pending
            if missing_data and callsign != "-" and callsign not in PENDING_LOOKUPS:
                PENDING_LOOKUPS.add(callsign)
                threading.Thread(target=fetch_route_thread, args=(callsign,), daemon=True).start()

            if dist == float('inf'):
                dist_str = "-"
            else:
                dist_str = f"{dist:.1f}"

            altitude = f.get('alt_baro')
            if isinstance(altitude, (int, float)):
                alt = f"{altitude:,}"
            else:
                alt = "---"

            v_rate = f.get('baro_rate')
            if isinstance(v_rate, (int, float)):
                vr = f"{v_rate:+,}"
            else:
                vr = "-"

            speed = f"{f.get('gs', '---')}"
            track = f"{f.get('track', '---')}"
            
            table.add_row(
                callsign,
                flag,
                airline,
                route,
                track,
                dist_str,
                alt,
                vr,
                speed
            )

    return table

def get_airline(callsign):
    """
    Deduces airline name from callsign prefix.
    """
    if not callsign or len(callsign) < 3:
        return ""
    
    # Extract first 3 letters
    prefix = callsign[:3].upper()
    
    
    global AIRLINES
    return AIRLINES.get(prefix, "")

def load_airlines():
    """
    Loads airline codes from airlines.txt into the global AIRLINES dictionary.
    """
    global AIRLINES
    AIRLINES = {}
    file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "airlines.txt")
    
    if os.path.exists(file_path):
        try:
            with open(file_path, "r") as f:
                for line in f:
                    if "," in line:
                        parts = line.strip().split(",", 1)
                        if len(parts) == 2:
                            code = parts[0].strip().upper()
                            name = parts[1].strip()
                            AIRLINES[code] = name
            return f"Loaded {len(AIRLINES)} airlines from {file_path}"
        except Exception as e:
            return f"Error loading airlines.txt: {e}"
    else:
        return "airlines.txt not found. Airline deduction will be limited."

def sort_airlines():
    """
    Sorts airlines.txt alphabetically by the 3-letter code.
    """
    file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "airlines.txt")
    if not os.path.exists(file_path):
        return "airlines.txt not found."
    
    try:
        lines = []
        with open(file_path, "r") as f:
            lines = f.readlines()
        
        # Parse and sort
        data = []
        valid_lines = []
        comments = []
        
        for line in lines:
            # simple check for comment or data
            if "," in line:
                parts = line.strip().split(",", 1)
                data.append((parts[0].strip().upper(), parts[1].strip()))
            else:
                # keep empty lines or garbage at end? Or just ignore/preserve
                # Strategy: Keep non-data lines separate or at top? 
                # Request says "sort by alphabetical first 3 letters".
                # We will just sort the valid data lines.
                pass
        
        data.sort(key=lambda x: x[0])
        
        with open(file_path, "w") as f:
            for code, name in data:
                f.write(f"{code},{name}\n")
        
        # Reload to apply changes
        load_airlines()
        return "airlines.txt sorted and reloaded."
    except Exception as e:
        return f"Error sorting airlines.txt: {e}"

def load_routes():
    """
    Loads routes from routes.txt.
    Expected format: CALLSIGN,ORIGIN,DESTINATION
    """
    global ROUTES
    ROUTES = {}
    file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "routes.txt")
    
    if os.path.exists(file_path):
        try:
            with open(file_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split(",")
                    if len(parts) >= 3:
                        callsign = parts[0].strip().upper() # Use upper for lookup key
                        origin = parts[1].strip()
                        dest = parts[2].strip()
                        ROUTES[callsign] = f"{origin}/{dest}"
        except Exception as e:
            print(f"Error loading routes.txt: {e}")

def get_flag(hex_code):
    """
    Returns a country flag emoji based on ICAO 24-bit hex code.
    This is a simplified mapping for common ranges in Europe/UK.
    """
    try:
        if not hex_code:
            return "🇪🇺"
        
        # Convert hex to integer for comparison if needed, but string prefix match is easier for simple logic
        # UK: 400000 - 43FFFF
        if hex_code.startswith(("40", "41", "42", "43")):
            return "🇬🇧"
        # Ireland: 4CA... (approx range starts with 4C)
        elif hex_code.startswith("4C"):
            return "🇮🇪"
        # France: 380000 - 3BFFFF
        elif hex_code.startswith(("38", "39", "3A", "3B")):
            return "🇫🇷"
        # Germany: 3C0000 - 3DFFFF
        elif hex_code.startswith(("3C", "3D")):
            return "🇩🇪"
        # USA: A00000 - AFFFFF
        elif hex_code.startswith("A"):
            return "🇺🇸"
        # Netherlands: 48xxxx
        elif hex_code.startswith("48"):
            return "🇳🇱"
        # Belgium: 44xxxx
        elif hex_code.startswith("44"):
            return "🇧🇪"
        # Spain: 34xxxx
        elif hex_code.startswith("34"):
            return "🇪🇸"
        # Italy: 30xxxx
        elif hex_code.startswith("30"):
            return "🇮🇹"
        # Portugal: 49xxxx
        elif hex_code.startswith("49"):
            return "🇵🇹"
        # Poland: 48xxxx clashes with NL? Wait. 
        # Netherlands: 480000-487FFF. Poland: 488000-48FFFF.
        # Let's simple check 488+
        elif hex_code.startswith("48") and hex_code > "487FFF":
             return "🇵🇱"
        
        return "🇪🇺"
    except:
        return "🇪🇺"



def main():
    # Clear terminal window
    os.system('clear')
    
    load_config()
    load_airlines()
    load_routes()

    parser = argparse.ArgumentParser(description="dump1090-fa Flight Tracker")
    parser.add_argument("--url", default=None, help=f"URL to aircraft.json")
    parser.add_argument("--interval", type=int, default=None, help="Update interval in seconds")
    args = parser.parse_args()

    
    # Defaults from Config or Args
    current_url = args.url if args.url else CONFIG["url"]
    current_interval = args.interval if args.interval else CONFIG["interval"]
    current_max_rows = CONFIG["rows"]
    show_help = True
    
    # Notification state
    notification_msg = None
    notification_start_time = 0
    
    # Input state
    input_mode = None # None, 'interval', 'url', 'rows'
    input_buffer = ""

    console = Console()
    # Removed startup print as requested

    def get_renderable(flights, url, show_help_panel, notif_msg, inp_mode, inp_buf, max_rows):
        table = generate_table(flights, url, max_rows)
        items = [table]
        
        if show_help_panel:
            last_updated = datetime.now().strftime('%H:%M:%S')
            help_text = (
                f"Updated: {last_updated}\n\n"
                "[bold]Interactive Commands:[/bold]\n"
                "[bold green]h[/bold green]: Toggle this help\n"
                f"[bold green]i[/bold green]: Change update interval (Current: {CONFIG['interval']}s)\n"
                f"[bold green]n[/bold green]: Change lines displayed (Current: {CONFIG['rows']})\n"
                "[bold green]s[/bold green]: Sort & reload airlines.txt\n"
                "[bold green]u[/bold green]: Change dump1090 URL\n"
                "[bold red]q[/bold red]: Quit"
            )
            items.append(Panel(help_text, title="Help & Status", border_style="green", box=box.ROUNDED))
            
        if notif_msg:
             items.append(Panel(notif_msg, title="Status", border_style="blue", box=box.ROUNDED))
        
        if inp_mode:
            # Determine title
            if inp_mode == 'interval': title = "Enter New Interval (1-60)"
            elif inp_mode == 'url': title = "Enter New URL"
            elif inp_mode == 'rows': title = "Enter Number of Lines (1-25)"
            else: title = "Input"
            
            content = f"{inp_buf}█"
            items.append(Panel(content, title=title, border_style="yellow", box=box.ROUNDED))
             
        return Group(*items)

    # Using KeyListener context manager
    with KeyListener() as listener:
        flights = []  # Initialize empty flight list
        with Live(get_renderable(flights, current_url, show_help, notification_msg, input_mode, input_buffer, current_max_rows), refresh_per_second=10) as live:
            last_update = 0
            while True:
                # Handle Input
                char = listener.read_char()
                if char:
                    # Input Mode Handling
                    if input_mode:
                        if char == '\n' or char == '\r': # Enter
                            if input_mode == 'interval':
                                try:
                                    val = int(input_buffer)
                                    if 1 <= val <= 60:
                                        current_interval = val
                                        CONFIG["interval"] = val
                                        save_config()
                                        notification_msg = f"Interval updated to {current_interval}s"
                                    else:
                                        notification_msg = "Invalid interval. Must be 1-60."
                                except ValueError:
                                    notification_msg = "Invalid input."
                            elif input_mode == 'url':
                                if input_buffer.strip():
                                    current_url = input_buffer.strip()
                                    CONFIG["url"] = current_url
                                    save_config()
                                    notification_msg = f"URL updated to {current_url}"
                            elif input_mode == 'rows':
                                try:
                                    val = int(input_buffer)
                                    if 1 <= val <= 25:
                                        current_max_rows = val
                                        CONFIG["rows"] = val
                                        save_config()
                                        notification_msg = f"Rows updated to {current_max_rows}"
                                    else:
                                        notification_msg = "Invalid number. Must be 1-25."
                                except ValueError:
                                    notification_msg = "Invalid input."
                            
                            # Reset input mode
                            notification_start_time = time.time()
                            input_mode = None
                            input_buffer = ""
                        elif char == '\x7f' or ord(char) == 127: # Backspace
                            input_buffer = input_buffer[:-1]
                        elif char == '\x1b': # Escape (simple check)
                            input_mode = None
                            input_buffer = ""
                        elif len(char) == 1 and char.isprintable():
                            input_buffer += char
                        
                        live.update(get_renderable(flights, current_url, show_help, notification_msg, input_mode, input_buffer, current_max_rows))
                    
                    # Normal Command Handling
                    else:
                        if char.lower() == 'q':
                            break
                        elif char.lower() == 'h':
                            show_help = not show_help
                            live.update(get_renderable(flights, current_url, show_help, notification_msg, input_mode, input_buffer, current_max_rows))
                        elif char.lower() == 's':
                            msg = sort_airlines()
                            notification_msg = msg
                            notification_start_time = time.time()
                            live.update(get_renderable(flights, current_url, show_help, notification_msg, input_mode, input_buffer, current_max_rows))
                        elif char.lower() == 'i':
                            input_mode = 'interval'
                            input_buffer = ""
                            live.update(get_renderable(flights, current_url, show_help, notification_msg, input_mode, input_buffer, current_max_rows))
                        elif char.lower() == 'n':
                            input_mode = 'rows'
                            input_buffer = ""
                            live.update(get_renderable(flights, current_url, show_help, notification_msg, input_mode, input_buffer, current_max_rows))
                        elif char.lower() == 'u':
                             input_mode = 'url'
                             input_buffer = ""
                             live.update(get_renderable(flights, current_url, show_help, notification_msg, input_mode, input_buffer, current_max_rows))

                # Handle Updates
                now = time.time()
                
                # Clear notification after 3 seconds
                if notification_msg and (now - notification_start_time > 3):
                    notification_msg = None
                    live.update(get_renderable(flights, current_url, show_help, notification_msg, input_mode, input_buffer, current_max_rows))

                if now - last_update >= current_interval:
                    flights = fetch_flight_data(current_url)
                    live.update(get_renderable(flights, current_url, show_help, notification_msg, input_mode, input_buffer, current_max_rows))
                    last_update = now
                
                # Small sleep to save CPU
                time.sleep(0.1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nExiting Flight Tracker.")

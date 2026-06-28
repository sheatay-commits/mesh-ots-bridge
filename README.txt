================================================================================
  mesh-ots-bridge — Meshtastic ↔ OpenTakServer Bridge
  Raspberry Pi OS Bookworm
================================================================================

WHAT THIS DOES
--------------
Bridges a Meshtastic device connected directly to your Raspberry Pi (via USB
serial or UART HAT) with OpenTakServer running on the same Pi.  Meshtastic
nodes show up on ATAK/WinTAK as moving map icons, and CoT events from ATAK
(positions, chat, map markers) are forwarded back onto the mesh.

The bridge runs as a systemd service (starts on boot) and a small desktop GUI
launches automatically when you log into the Pi's desktop.


PREREQUISITES
-------------
  - Raspberry Pi OS Bookworm (tested on 64-bit Lite + desktop)
  - OpenTakServer already installed and running on the Pi
  - Python 3.11+ (included with Bookworm)
  - Internet access on the Pi for the initial install (to download pip packages)
  - Your Meshtastic device plugged in via USB OR a Meshtastic HAT on the GPIO
  - Your Pi user account is: ss


STEP 1 — GET THE FILES ONTO YOUR PI
-------------------------------------
Option A: GitHub (recommended)
  On this Windows machine, push the project to a new GitHub repo:

    1. Go to https://github.com/new and create a repo (e.g. "mesh-ots-bridge")
    2. Open PowerShell in C:\Users\SS\mesh-ots-bridge\ and run:

         git init
         git add .
         git commit -m "Initial commit"
         git remote add origin https://github.com/YOUR_USERNAME/mesh-ots-bridge.git
         git push -u origin main

  On the Pi, open a terminal and run:

         git clone https://github.com/YOUR_USERNAME/mesh-ots-bridge.git
         cd mesh-ots-bridge

Option B: SCP (no GitHub account needed)
  From this Windows machine (replace PI_IP with your Pi's IP address):

         scp -r C:\Users\SS\mesh-ots-bridge ss@PI_IP:~/mesh-ots-bridge

  Then on the Pi:

         cd ~/mesh-ots-bridge

Option C: USB drive
  Copy the mesh-ots-bridge folder to a USB drive, plug into the Pi, then:

         cp -r /media/ss/YOUR_DRIVE/mesh-ots-bridge ~/
         cd ~/mesh-ots-bridge


STEP 2 — RUN THE INSTALLER
---------------------------
On the Pi, in the mesh-ots-bridge directory:

    sudo bash install.sh

This will:
  - Install python3-tk, python3-pip via apt
  - Install meshtastic, flask, pyserial via pip3
  - Copy files to /opt/mesh-ots-bridge/
  - Enable and start the mesh-ots-bridge systemd service
  - Install the GUI autostart entry for the desktop
  - Add ss to the dialout group (for serial port access)
  - Create a sudoers rule so the GUI can restart the service without a password

NOTE: The dialout group change requires a logout/login to take effect for the
      running session.  The service itself starts immediately.


STEP 3 — FIRST-TIME CONFIGURATION
-----------------------------------
After install, open the GUI (it auto-launches on desktop login, or run manually):

    python3 /opt/mesh-ots-bridge/gui.py

  1. Config tab — set your callsign (default: MESH-GW) and verify OTS settings:
       OTS Host: 127.0.0.1
       OTS Port: 8088
       OTS SSL:  unchecked (use 8089 + checked if you have SSL set up in OTS)
     Click "Apply & Restart" to save.

  2. Traffic tab — select your serial mode:
       USB   — Meshtastic device on USB (/dev/ttyUSB0)
       UART  — Meshtastic HAT on GPIO (/dev/ttyAMA0)
       TCP   — meshtasticd daemon managing the HAT (localhost:4403)
     The status bar at the top turns green when connected.

  3. Channels tab — click "Refresh from Device" to load your Meshtastic
     channel list.  Check the channels you want to bridge.  Click
     "Apply Channels" when done (restarts the service automatically).

  4. Nodes tab — shows all Meshtastic nodes the radio has heard, with
     callsign and last-heard time.  Updates every 2 seconds.


SERIAL MODE NOTES
-----------------
USB (/dev/ttyUSB0)
  Standard USB connection.  Most common for testing.  If your device shows up
  as /dev/ttyUSB1 etc., update "serial_port_usb" in the Config tab.

UART HAT (/dev/ttyAMA0)
  For GPIO-connected HATs (e.g. RAK2287, WisGate).  You may need to disable
  the Pi's serial console first:
    sudo raspi-config → Interface Options → Serial Port
    → "Login shell over serial?" NO
    → "Serial port hardware enabled?" YES

TCP (localhost:4403)
  Use this when meshtasticd (the Meshtastic daemon) is running on the Pi and
  owns the serial port.  meshtasticd listens on TCP 4403 by default.
  Install meshtasticd separately: https://meshtastic.org/docs/software/linux-native/


CHECKING SERVICE STATUS
------------------------
    # Is the bridge running?
    systemctl status mesh-ots-bridge

    # Watch live logs
    journalctl -u mesh-ots-bridge -f

    # Test the REST API the GUI uses
    curl http://localhost:5199/status


TRAFFIC DISPLAY
---------------
  ▲ green entries = packets received from the Meshtastic mesh, forwarded to OTS
  ▼ blue entries  = CoT events received from OTS, forwarded to the mesh
  Channel name shown in [brackets] if the packet arrived on a named channel.

  Supported CoT types (configurable in Config tab, "CoT Allowed" field):
    a-f-   Position/PLI (moving map icons in ATAK)
    b-t-f  GeoChat (text messages)
    b-m-p- Map markers / POIs
  Add prefixes separated by commas to allow additional types.
  Remove a prefix to block that type from crossing the bridge.

  Map markers (b-m-p-*) are text-encoded on the mesh as:
    [MRK] !CALLSIGN lat,lon Marker Name
  This is intentional — Meshtastic has no native marker packet type.
  These appear as structured text on the mesh side and as proper map
  markers in ATAK.


UPDATING
--------
Option A (GitHub):
    cd ~/mesh-ots-bridge
    git pull
    sudo bash install.sh

Option B (manual):
    Copy new files to /opt/mesh-ots-bridge/ and restart:
    sudo systemctl restart mesh-ots-bridge


TROUBLESHOOTING
---------------
GUI shows "daemon offline" on both status dots:
  → The daemon isn't running.  Check: systemctl status mesh-ots-bridge
  → Start it: sudo systemctl start mesh-ots-bridge

Serial status stays red:
  → Check the device is plugged in: ls /dev/ttyUSB* or ls /dev/ttyAMA*
  → Confirm ss is in the dialout group: groups ss
    If not: sudo usermod -aG dialout ss  then log out and back in.
  → Check the port path in Config tab matches your actual device.

OTS status stays red:
  → Confirm OpenTakServer is running: systemctl status opentakserver
  → Confirm OTS host/port in Config tab (default 127.0.0.1:8088)
    NOTE: The bridge connects to OTS on the raw TCP CoT port (8088), NOT
    through the nginx web UI.  Keep ots_host as 127.0.0.1 even though
    the web UI is accessed from other devices at https://192.168.1.163
  → Test OTS is up: curl -k https://192.168.1.163/dashboard
  → Test the CoT port directly: nc -zv 127.0.0.1 8088

Channels tab shows empty after "Refresh from Device":
  → Serial must be connected (green) before channels can be read from the radio.

"permission denied" on serial port:
  → Log out and back in after the installer adds you to dialout.
  → Or run: sudo chmod a+rw /dev/ttyUSB0  (temporary fix)

Service restarts but GUI buttons do nothing:
  → Check sudoers file: sudo cat /etc/sudoers.d/mesh-ots-bridge
    It should contain: ss ALL=(ALL) NOPASSWD: /usr/bin/systemctl ...


FILE LOCATIONS (after install)
-------------------------------
  /opt/mesh-ots-bridge/       — all program files
  /opt/mesh-ots-bridge/config.json — runtime config (edited via GUI)
  /etc/systemd/system/mesh-ots-bridge.service — systemd unit
  /etc/xdg/autostart/mesh-ots-bridge-gui.desktop — desktop autostart
  /etc/sudoers.d/mesh-ots-bridge — narrow sudo rule for service control


================================================================================

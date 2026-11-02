TCP Congestion Control Simulator - local demo (CBIT CN Case Study 8)
=====================================================================

Requirements: Python 3.8 or newer. No extra packages needed.

How to run
  1. Open a terminal / command prompt in this folder.
  2. Run:   python app.py        (on some systems: python3 app.py)
  3. Your browser opens http://localhost:8000 automatically.
     If it does not, open that address yourself.
  4. Press Ctrl+C in the terminal to stop the server.

Windows: you can also just double-click run_demo.bat.

What to show
  - Move the sliders (bandwidth, RTT, packet loss, buffer) and press
    "Run simulation", or click a preset such as "Lossy Wi-Fi (1%)".
  - The table shows throughput, utilisation, congestion window, packet
    loss, RTT and loss-recovery time for each algorithm.
  - The charts show the congestion window and throughput over time.
  - Tick "Changing network" to see how each algorithm reacts when the
    link capacity drops and then rises.

Files
  app.py      - small local web server (Python standard library only)
  tcpsim.py   - the TCP simulator used in the report
  index.html  - the user interface

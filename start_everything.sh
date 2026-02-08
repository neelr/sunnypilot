#!/bin/bash
# Start web steering control with WebRTC video for openpilot
# Run this on the comma device, then access via browser at http://<device-ip>:3000

cd /data/openpilot

# Enable joystick debug mode (openpilot manager will start joystickd)
echo -n "1" > /data/params/d/JoystickDebugMode

# Kill any existing processes
pkill -f "encoderd --stream" 2>/dev/null || true
pkill -f webrtcd 2>/dev/null || true
pkill -f web_steer 2>/dev/null || true

# Set Python path
export PYTHONPATH=/data/openpilot:/data/openpilot/msgq_repo

# Start encoder for livestreaming (creates road + driver camera streams)
/data/openpilot/system/loggerd/encoderd --stream &
sleep 2

# Start WebRTC daemon
/usr/local/venv/bin/python -m openpilot.system.webrtc.webrtcd --port 5001 &

# Start web steering server on port 3000
/usr/local/venv/bin/python /data/openpilot/tools/joystick/web_steer.py &

echo "Started all services!"
echo "Access web steering at http://$(hostname -I | awk '{print $1}'):3000"
echo ""
echo "To port forward from your laptop:"
echo "  ssh -L 3000:localhost:3000 -N comma@<device-ip>"

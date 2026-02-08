#!/bin/bash
# Start web steering on comma device and port forward to localhost:3000

DEVICE="comma@172.20.10.5"

echo "Access web steering at: http://localhost:3000"
echo "Press Ctrl+C to stop"
echo ""

ssh -L 3000:localhost:3000 $DEVICE "/data/openpilot/start_everything.sh; bash"

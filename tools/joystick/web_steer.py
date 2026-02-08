#!/usr/bin/env python3
"""
Web-based steering control with WebRTC video for openpilot.
Run on comma device, access via browser at http://<device-ip>:3000
"""

import json
import logging
from dataclasses import asdict

from aiohttp import web, ClientSession

from openpilot.system.webrtc.webrtcd import StreamRequestBody

logger = logging.getLogger("web_steer")
logging.basicConfig(level=logging.INFO)

WEBRTCD_HOST, WEBRTCD_PORT = "localhost", 5001

HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Steering Control</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, sans-serif;
            background: #000;
            color: #fff;
            margin: 0;
            padding: 0;
            height: 100vh;
            display: flex;
            flex-direction: column;
        }
        .video-container {
            flex: 1;
            display: flex;
            align-items: center;
            justify-content: center;
            background: #111;
            overflow: hidden;
        }
        video#video {
            max-width: 100%;
            max-height: 100%;
        }
        video#driver {
            position: absolute;
            top: 10px;
            left: 10px;
            width: 180px;
            border: 2px solid #4CAF50;
            border-radius: 8px;
        }
        .controls {
            padding: 20px;
            background: #1a1a1a;
        }
        .key-controls {
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 30px;
        }
        .key-indicator {
            font-size: 48px;
            width: 80px;
            height: 80px;
            display: flex;
            align-items: center;
            justify-content: center;
            background: #333;
            border-radius: 12px;
            transition: all 0.1s;
            user-select: none;
        }
        .key-indicator.active {
            background: #4CAF50;
            color: #000;
            transform: scale(1.1);
            box-shadow: 0 0 20px rgba(76, 175, 80, 0.5);
        }
        .torque-value {
            font-size: 48px;
            font-weight: bold;
            font-family: monospace;
            color: #4CAF50;
            min-width: 120px;
            text-align: center;
        }
        .status {
            margin-top: 10px;
            padding: 8px 15px;
            background: #333;
            border-radius: 5px;
            font-size: 14px;
            text-align: center;
        }
        .status.connected { background: #2e7d32; }
        .status.error { background: #c62828; }
        button {
            padding: 12px 30px;
            font-size: 16px;
            background: #2196F3;
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
        }
        button:hover { background: #1976D2; }
        .telemetry {
            display: flex;
            justify-content: center;
            gap: 30px;
            margin-top: 10px;
            font-size: 18px;
            font-family: monospace;
        }
        .telemetry span {
            padding: 5px 15px;
            background: #333;
            border-radius: 5px;
        }
    </style>
</head>
<body>
    <div class="video-container">
        <video id="video" autoplay playsinline muted></video>
        <video id="driver" autoplay playsinline muted></video>
    </div>

    <div class="controls">
        <div class="key-controls">
            <div class="key-indicator" id="keyLeft">\u2190</div>
            <div class="torque-value" id="steerValue">0.00</div>
            <div class="key-indicator" id="keyRight">\u2192</div>
        </div>
        <div class="status" id="status">Connecting...</div>
        <div class="telemetry">
            <span id="speed">Speed: --</span>
            <span id="steerAngle">Steer: --</span>
            <span id="gasStatus">Gas: --</span>
            <span id="brakeStatus">Brake: --</span>
        </div>
    </div>

    <script>
        let pc = null;
        let dc = null;
        let steerValue = 0;
        let sendInterval = null;

        // Keyboard state
        let keysPressed = { left: false, right: false };
        const RAMP_RATE = 0.016;    // ~1s to full at 60fps
        const RETURN_RATE = 0.025;  // ~0.6s return to center

        const steerDisplay = document.getElementById('steerValue');
        const status = document.getElementById('status');
        const video = document.getElementById('video');
        const driver = document.getElementById('driver');
        const keyLeft = document.getElementById('keyLeft');
        const keyRight = document.getElementById('keyRight');

        // Keyboard event handlers
        document.addEventListener('keydown', (e) => {
            if (e.key === 'ArrowLeft') { keysPressed.left = true; e.preventDefault(); }
            if (e.key === 'ArrowRight') { keysPressed.right = true; e.preventDefault(); }
            if (e.code === 'Space') { steerValue = 0; e.preventDefault(); }
        });

        document.addEventListener('keyup', (e) => {
            if (e.key === 'ArrowLeft') keysPressed.left = false;
            if (e.key === 'ArrowRight') keysPressed.right = false;
        });

        // Animation loop for smooth ramping
        function updateLoop() {
            let targetTorque = 0;
            if (keysPressed.left && !keysPressed.right) targetTorque = -1;
            else if (keysPressed.right && !keysPressed.left) targetTorque = 1;

            if (steerValue < targetTorque) {
                steerValue = Math.min(steerValue + RAMP_RATE, targetTorque);
            } else if (steerValue > targetTorque) {
                steerValue = Math.max(steerValue - RETURN_RATE, targetTorque);
            }

            steerDisplay.textContent = steerValue.toFixed(2);
            keyLeft.classList.toggle('active', keysPressed.left);
            keyRight.classList.toggle('active', keysPressed.right);

            requestAnimationFrame(updateLoop);
        }
        requestAnimationFrame(updateLoop);

        function setStatus(msg, type) {
            status.textContent = msg;
            status.className = 'status ' + (type || '');
        }

        function sendControls() {
            if (dc && dc.readyState === 'open') {
                dc.send(JSON.stringify({
                    type: "testJoystick",
                    data: { axes: [0, -steerValue], buttons: [false] }
                }));
            }
        }

        async function start() {
            try {
                setStatus('Creating connection...');

                pc = new RTCPeerConnection({ sdpSemantics: 'unified-plan' });

                // Add 2 transceivers for 2 video tracks (road + driver)
                pc.addTransceiver('video', { direction: 'recvonly' });
                pc.addTransceiver('video', { direction: 'recvonly' });

                // Handle incoming video tracks - create separate MediaStream for each track
                pc.addEventListener('track', (evt) => {
                    if (evt.track.kind === 'video') {
                        const mid = evt.transceiver.mid;
                        const stream = new MediaStream([evt.track]);
                        console.log('Track received, mid:', mid, 'track id:', evt.track.id);
                        // First transceiver (mid=0) is road, second (mid=1) is driver
                        if (mid === '0') {
                            video.srcObject = stream;
                            setStatus('Road camera connected', 'connected');
                        } else if (mid === '1') {
                            driver.srcObject = stream;
                            setStatus('Both cameras connected', 'connected');
                        }
                    }
                });

                pc.addEventListener('connectionstatechange', () => {
                    if (pc.connectionState === 'disconnected' || pc.connectionState === 'failed') {
                        setStatus('Connection lost', 'error');
                    }
                });

                // Create data channel for controls
                dc = pc.createDataChannel('data', { ordered: true });
                dc.onopen = () => {
                    setStatus('Connected - steering active', 'connected');
                    sendInterval = setInterval(sendControls, 50);
                };
                dc.onclose = () => {
                    clearInterval(sendInterval);
                    setStatus('Data channel closed', 'error');
                };
                dc.onmessage = (evt) => {
                    const msg = JSON.parse(new TextDecoder().decode(evt.data));
                    if (msg.type === 'carState') {
                        document.getElementById('speed').textContent =
                            'Speed: ' + (msg.data.vEgo * 2.237).toFixed(1) + ' mph';
                        document.getElementById('steerAngle').textContent =
                            'Steer: ' + msg.data.steeringAngleDeg.toFixed(1) + '\u00B0';
                        const gasEl = document.getElementById('gasStatus');
                        gasEl.textContent = 'Gas: ' + (msg.data.gasPressed ? 'ON' : 'OFF');
                        gasEl.style.color = msg.data.gasPressed ? '#4CAF50' : '#888';
                        const brakeEl = document.getElementById('brakeStatus');
                        brakeEl.textContent = 'Brake: ' + (msg.data.brakePressed ? 'ON' : 'OFF');
                        brakeEl.style.color = msg.data.brakePressed ? '#f44336' : '#888';
                    }
                };

                // Create offer (transceivers already added above)
                const offer = await pc.createOffer();
                await pc.setLocalDescription(offer);

                // Wait for ICE gathering
                await new Promise((resolve) => {
                    if (pc.iceGatheringState === 'complete') {
                        resolve();
                    } else {
                        pc.addEventListener('icegatheringstatechange', () => {
                            if (pc.iceGatheringState === 'complete') resolve();
                        });
                    }
                });

                setStatus('Negotiating...');

                // Send offer to server
                const response = await fetch('/offer', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ sdp: pc.localDescription.sdp, type: pc.localDescription.type })
                });

                if (!response.ok) throw new Error('Offer failed: ' + response.status);

                const answer = await response.json();
                await pc.setRemoteDescription(answer);

                setStatus('Waiting for video...', 'connected');

            } catch (err) {
                setStatus('Error: ' + err.message, 'error');
                console.error(err);
            }
        }

        // Start on page load
        start();
    </script>
</body>
</html>
"""


async def index(request: web.Request):
    return web.Response(content_type="text/html", text=HTML_PAGE)


async def offer(request: web.Request):
    try:
        params = await request.json()
        body = StreamRequestBody(params["sdp"], ["road", "driver"], ["testJoystick"], ["carState"])
        body_json = json.dumps(asdict(body))

        logger.info("Sending offer to webrtcd...")
        webrtcd_url = f"http://{WEBRTCD_HOST}:{WEBRTCD_PORT}/stream"

        async with ClientSession() as session:
            async with session.post(webrtcd_url, data=body_json) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error(f"webrtcd error: {error_text}")
                    return web.json_response({"error": error_text}, status=500)
                answer = await resp.json()
                return web.json_response(answer)
    except Exception as e:
        logger.exception("Offer failed")
        return web.json_response({"error": str(e)}, status=500)


def main():
    print("Starting web steering control with WebRTC...")
    print("Access at http://localhost:3000")
    print("Make sure webrtcd and encoderd --stream are running!")

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_post("/offer", offer)

    web.run_app(app, host="0.0.0.0", port=3000, access_log=None)


if __name__ == "__main__":
    main()

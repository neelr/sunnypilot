#!/usr/bin/env python3
"""
Web-based steering control with WebRTC video for openpilot.
Run on comma device, access via browser at http://<device-ip>:3000
"""

import json
import logging
from dataclasses import asdict
from pathlib import Path

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
            position: relative;
        }
        video#video {
            max-width: 100%;
            max-height: 100%;
        }
        video#wide {
            position: absolute;
            top: 10px;
            right: 10px;
            width: 25%;
            border: 2px solid #2196F3;
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
            align-items: center;
            gap: 40px;
            margin-top: 15px;
        }
        .gauge-container {
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 8px;
        }
        .gauge-label {
            font-size: 12px;
            color: #888;
            text-transform: uppercase;
        }
        .pedal-indicator {
            width: 50px;
            height: 50px;
            border-radius: 50%;
            background: #333;
            border: 3px solid #555;
            transition: all 0.15s ease;
        }
        .pedal-indicator.gas-active {
            background: #4CAF50;
            border-color: #4CAF50;
            box-shadow: 0 0 20px rgba(76, 175, 80, 0.7);
        }
        .pedal-indicator.brake-active {
            background: #f44336;
            border-color: #f44336;
            box-shadow: 0 0 20px rgba(244, 67, 54, 0.7);
        }
        .accel-gauge {
            width: 100px;
            height: 60px;
        }
        .accel-value {
            font-size: 16px;
            font-family: monospace;
            color: #fff;
        }
        .steer-wheel {
            width: 70px;
            height: 70px;
            position: relative;
            transition: transform 0.1s ease-out;
        }
        .steer-wheel img {
            width: 100%;
            height: 100%;
            filter: invert(60%) sepia(0%) saturate(0%) brightness(80%);
        }
        .steer-wheel .red-dot {
            position: absolute;
            top: -5px;
            left: 50%;
            transform: translateX(-50%);
            width: 12px;
            height: 12px;
            background: #f44336;
            border-radius: 50%;
            box-shadow: 0 0 8px rgba(244, 67, 54, 0.7);
        }
        .steer-value {
            font-size: 14px;
            font-family: monospace;
            color: #fff;
        }
    </style>
</head>
<body>
    <div class="video-container">
        <video id="video" autoplay playsinline muted></video>
        <video id="wide" autoplay playsinline muted></video>
    </div>

    <div class="controls">
        <div class="key-controls">
            <div class="key-indicator" id="keyLeft">\u2190</div>
            <div class="key-indicator" id="keyRight">\u2192</div>
        </div>
        <div class="telemetry">
            <div class="gauge-container">
                <div class="pedal-indicator" id="gasIndicator"></div>
                <span class="gauge-label">Gas</span>
            </div>
            <div class="gauge-container">
                <svg class="accel-gauge" viewBox="0 0 100 60">
                    <path d="M 10 55 A 40 40 0 0 1 90 55" fill="none" stroke="#333" stroke-width="8" stroke-linecap="round"/>
                    <path d="M 10 55 A 40 40 0 0 1 90 55" fill="none" stroke="url(#accelGradient)" stroke-width="8" stroke-linecap="round" stroke-dasharray="126" stroke-dashoffset="63" id="accelArc"/>
                    <defs>
                        <linearGradient id="accelGradient" x1="0%" y1="0%" x2="100%" y2="0%">
                            <stop offset="0%" style="stop-color:#f44336"/>
                            <stop offset="50%" style="stop-color:#888"/>
                            <stop offset="100%" style="stop-color:#4CAF50"/>
                        </linearGradient>
                    </defs>
                    <line x1="50" y1="55" x2="50" y2="20" stroke="#fff" stroke-width="3" stroke-linecap="round" id="accelNeedle" transform="rotate(0, 50, 55)"/>
                    <circle cx="50" cy="55" r="6" fill="#fff"/>
                </svg>
                <span class="accel-value" id="accelValue">0.0 m/s²</span>
            </div>
            <div class="gauge-container">
                <div class="steer-wheel" id="steerWheel">
                    <div class="red-dot"></div>
                    <img src="/steering-wheel.svg" alt="steering wheel">
                </div>
                <span class="steer-value" id="steerAngle">0°</span>
            </div>
            <div class="gauge-container">
                <div class="pedal-indicator" id="brakeIndicator"></div>
                <span class="gauge-label">Brake</span>
            </div>
        </div>
    </div>

    <script>
        let pc = null;
        let dc = null;
        let steerValue = 0;
        let sendInterval = null;

        // Keyboard state
        let keysPressed = { left: false, right: false };

        // Recording state
        let isRecording = false;
        let recordingStart = 0;
        let telemetryFrames = [];
        let roadRecorder = null;
        let wideRecorder = null;
        let roadChunks = [];
        let wideChunks = [];
        let lastCarState = { gas: false, brake: false, accel: 0, angle: 0 };

        // File System Access API
        let dirHandle = null;
        let chunkIndex = 0;
        let chunkInterval = null;
        const CHUNK_INTERVAL_MS = 5 * 60 * 1000; // 5 minutes
        const RAMP_RATE = 0.016;    // ~1s to full at 60fps
        const RETURN_RATE = 0.025;  // ~0.6s return to center

        const video = document.getElementById('video');
        const keyLeft = document.getElementById('keyLeft');
        const keyRight = document.getElementById('keyRight');

        // Keyboard event handlers
        document.addEventListener('keydown', (e) => {
            if (e.key === 'ArrowLeft') { keysPressed.left = true; e.preventDefault(); }
            if (e.key === 'ArrowRight') { keysPressed.right = true; e.preventDefault(); }
            if (e.code === 'Space') { steerValue = 0; e.preventDefault(); }
            if (e.key === 'r' || e.key === 'R') { toggleRecording(); e.preventDefault(); }
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

            keyLeft.classList.toggle('active', keysPressed.left);
            keyRight.classList.toggle('active', keysPressed.right);

            requestAnimationFrame(updateLoop);
        }
        requestAnimationFrame(updateLoop);

        function sendControls() {
            if (dc && dc.readyState === 'open') {
                dc.send(JSON.stringify({
                    type: "testJoystick",
                    data: { axes: [0, -steerValue], buttons: [false] }
                }));
            }
        }

        async function writeFile(filename, blob) {
            if (!dirHandle) return;
            try {
                const fileHandle = await dirHandle.getFileHandle(filename, { create: true });
                const writable = await fileHandle.createWritable();
                await writable.write(blob);
                await writable.close();
                console.log('Saved: ' + filename);
            } catch (e) {
                console.error('Failed to write ' + filename, e);
            }
        }

        async function saveChunk() {
            chunkIndex++;
            const chunkName = `chunk${String(chunkIndex).padStart(3, '0')}`;

            // Stop current recorders to finalize chunks
            const roadPromise = new Promise(resolve => {
                if (roadRecorder && roadRecorder.state !== 'inactive') {
                    roadRecorder.onstop = resolve;
                    roadRecorder.stop();
                } else resolve();
            });
            const widePromise = new Promise(resolve => {
                if (wideRecorder && wideRecorder.state !== 'inactive') {
                    wideRecorder.onstop = resolve;
                    wideRecorder.stop();
                } else resolve();
            });

            await Promise.all([roadPromise, widePromise]);

            // Save video chunks
            if (roadChunks.length > 0) {
                await writeFile(`road_${recordingStart}_${chunkName}.webm`, new Blob(roadChunks, { type: 'video/webm' }));
                roadChunks = [];
            }
            if (wideChunks.length > 0) {
                await writeFile(`wide_${recordingStart}_${chunkName}.webm`, new Blob(wideChunks, { type: 'video/webm' }));
                wideChunks = [];
            }

            // Save telemetry chunk
            if (telemetryFrames.length > 0) {
                const telemetry = { startTime: recordingStart, chunkIndex, frames: telemetryFrames };
                await writeFile(`telemetry_${recordingStart}_${chunkName}.json`, new Blob([JSON.stringify(telemetry)], { type: 'application/json' }));
                telemetryFrames = [];
            }

            // Restart recorders if still recording
            if (isRecording) {
                startVideoRecorders();
            }
        }

        function startVideoRecorders() {
            const wide = document.getElementById('wide');

            if (video.srcObject) {
                roadRecorder = new MediaRecorder(video.srcObject, { mimeType: 'video/webm' });
                roadRecorder.ondataavailable = (e) => { if (e.data.size > 0) roadChunks.push(e.data); };
                roadRecorder.start();
            }

            if (wide.srcObject) {
                wideRecorder = new MediaRecorder(wide.srcObject, { mimeType: 'video/webm' });
                wideRecorder.ondataavailable = (e) => { if (e.data.size > 0) wideChunks.push(e.data); };
                wideRecorder.start();
            }
        }

        function videoFrameCallback(now, metadata) {
            if (isRecording) {
                telemetryFrames.push({
                    videoTime: metadata.mediaTime,
                    frame: metadata.presentedFrames,
                    keys: { left: keysPressed.left, right: keysPressed.right },
                    steer: steerValue,
                    ...lastCarState
                });
            }
            video.requestVideoFrameCallback(videoFrameCallback);
        }

        async function startRecording() {
            try {
                // Prompt user to select folder
                dirHandle = await window.showDirectoryPicker({ mode: 'readwrite' });
            } catch (e) {
                console.log('Recording cancelled - no folder selected');
                return;
            }

            recordingStart = Date.now();
            isRecording = true;
            telemetryFrames = [];
            roadChunks = [];
            wideChunks = [];
            chunkIndex = 0;

            // Start video recorders
            startVideoRecorders();

            // Start video-frame-aligned telemetry logging
            if (video.requestVideoFrameCallback) {
                video.requestVideoFrameCallback(videoFrameCallback);
            }

            // Auto-save every 5 minutes
            chunkInterval = setInterval(saveChunk, CHUNK_INTERVAL_MS);

            console.log('Recording started - saving to selected folder every 5 min');
        }

        async function stopRecording() {
            isRecording = false;
            clearInterval(chunkInterval);

            // Save final chunk
            await saveChunk();

            dirHandle = null;
            console.log('Recording stopped');
        }

        function toggleRecording() {
            if (isRecording) {
                stopRecording();
            } else {
                startRecording();
            }
        }

        async function start() {
            try {
                pc = new RTCPeerConnection({ sdpSemantics: 'unified-plan' });

                // Add 2 transceivers for 2 video tracks (road, wideRoad)
                pc.addTransceiver('video', { direction: 'recvonly' });
                pc.addTransceiver('video', { direction: 'recvonly' });

                const wide = document.getElementById('wide');

                // Handle incoming video tracks - create separate MediaStream for each track
                pc.addEventListener('track', (evt) => {
                    if (evt.track.kind === 'video') {
                        const mid = evt.transceiver.mid;
                        const stream = new MediaStream([evt.track]);
                        console.log('Track received, mid:', mid, 'track id:', evt.track.id);
                        // mid=0 is road, mid=1 is wideRoad
                        if (mid === '0') {
                            video.srcObject = stream;
                        } else if (mid === '1') {
                            wide.srcObject = stream;
                        }
                    }
                });

                pc.addEventListener('connectionstatechange', () => {
                    if (pc.connectionState === 'disconnected' || pc.connectionState === 'failed') {
                        console.log('Connection lost');
                    }
                });

                // Create data channel for controls
                dc = pc.createDataChannel('data', { ordered: true });
                dc.onopen = () => {
                    console.log('Connected - steering active');
                    sendInterval = setInterval(sendControls, 50);
                };
                dc.onclose = () => {
                    clearInterval(sendInterval);
                    console.log('Data channel closed');
                };
                dc.onmessage = (evt) => {
                    const msg = JSON.parse(new TextDecoder().decode(evt.data));
                    if (msg.type === 'carState') {
                        // Update lastCarState for recording
                        lastCarState = {
                            gas: msg.data.gasPressed,
                            brake: msg.data.brakePressed,
                            accel: msg.data.aEgo || 0,
                            angle: msg.data.steeringAngleDeg || 0
                        };

                        // Gas/Brake indicators
                        const gasEl = document.getElementById('gasIndicator');
                        gasEl.classList.toggle('gas-active', msg.data.gasPressed);
                        const brakeEl = document.getElementById('brakeIndicator');
                        brakeEl.classList.toggle('brake-active', msg.data.brakePressed);

                        // Acceleration gauge (-4 to +4 m/s² mapped to -90° to +90°)
                        const aEgo = Math.max(-4, Math.min(4, msg.data.aEgo || 0));
                        const accelAngle = (aEgo / 4) * 90;
                        document.getElementById('accelNeedle').setAttribute('transform', 'rotate(' + accelAngle + ', 50, 55)');
                        document.getElementById('accelValue').textContent = aEgo.toFixed(1) + ' m/s\u00B2';

                        // Steering wheel rotation
                        const steerDeg = msg.data.steeringAngleDeg || 0;
                        document.getElementById('steerWheel').style.transform = 'rotate(' + (-steerDeg) + 'deg)';
                        document.getElementById('steerAngle').textContent = steerDeg.toFixed(0) + '\u00B0';
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

                // Send offer to server
                const response = await fetch('/offer', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ sdp: pc.localDescription.sdp, type: pc.localDescription.type })
                });

                if (!response.ok) throw new Error('Offer failed: ' + response.status);

                const answer = await response.json();
                await pc.setRemoteDescription(answer);

            } catch (err) {
                console.error('Error:', err);
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


async def steering_wheel_svg(request: web.Request):
    svg_path = Path(__file__).parent / "steering-wheel-icon.svg"
    return web.Response(content_type="image/svg+xml", text=svg_path.read_text())


async def offer(request: web.Request):
    try:
        params = await request.json()
        body = StreamRequestBody(params["sdp"], ["road", "wideRoad"], ["testJoystick"], ["carState"])
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
    app.router.add_get("/steering-wheel.svg", steering_wheel_svg)
    app.router.add_post("/offer", offer)

    web.run_app(app, host="0.0.0.0", port=3000, access_log=None)


if __name__ == "__main__":
    main()

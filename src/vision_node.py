"""
vision_node.py
Simulated Vision Node for Distributed Vision-Control System.
Tracks face and publishes movement commands via MQTT.
Topic: vision/team213/movement
"""

print("Benax Tracking System — starting vision node...", flush=True)

import time
import argparse
import cv2
import json
import numpy as np
import paho.mqtt.client as mqtt
from pathlib import Path
import sys
import base64

# Add src to path if needed
ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT))

print("Loading face recognition libraries (first run may take ~30s)...", flush=True)

# Import Face Locking modules
from src.haar_5pt import Haar5ptDetector
from src.recognize import ArcFaceEmbedderONNX, FaceDBMatcher, load_db_npz
from src.face_locking import FaceLockSystem, LockState
from src.camera import DEFAULT_USB_CAMERA, open_camera, print_cameras
from src.session_log import SessionLogger, resolve_speaker_name, to_spec_status

from src.network_config import ESP32_STREAM_URL, MQTT_BROKER, MQTT_PORT, TEAM_ID

TOPIC_MOVEMENT = f"vision/{TEAM_ID}/movement"
TOPIC_SNAPSHOT = f"vision/{TEAM_ID}/snapshot"
TOPIC_HEARTBEAT = f"vision/{TEAM_ID}/heartbeat"
DEFAULT_BROKER = MQTT_BROKER
PORT = MQTT_PORT

# Horizontal tracking (normalized 0=left, 1=right of frame)
CENTER_ZONE_LEFT = 0.40
CENTER_ZONE_RIGHT = 0.60
CENTER_EXIT_LEFT = 0.35
CENTER_EXIT_RIGHT = 0.65
TRACK_LEFT = 0.32
TRACK_RIGHT = 0.68
CX_SMOOTHING = 0.22
STATUS_DEBOUNCE_FRAMES = 6
MOVE_DEBOUNCE_FRAMES = 4


class VisionNode:
    def __init__(self, broker, port, target_name):
        # MQTT Setup
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"{TEAM_ID}_vision_node",
        )
        self.client.on_connect = self.on_connect
        self.client.connect(broker, port, 60)
        self.client.loop_start()
        
        # Face Recognition & Locking Setup
        print("Initializing Face Recognition...")
        self.det = Haar5ptDetector(min_size=(70, 70))
        self.embedder = ArcFaceEmbedderONNX(input_size=(112, 112))
        
        # Load Database
        db_path = ROOT / "data/db/face_db.npz"
        if not db_path.exists():
            print(f"ERROR: Face DB not found at {db_path}. Run enroll.py first!")
            sys.exit(1)
            
        db = load_db_npz(db_path)
        resolved = resolve_speaker_name(target_name, list(db.keys()))
        if resolved != target_name:
            print(f"Resolved speaker name '{target_name}' -> '{resolved}'")
        if resolved not in db:
            print(f"WARNING: Target '{target_name}' not in database. Available: {list(db.keys())}")
        else:
            target_name = resolved

        self.matcher = FaceDBMatcher(db, dist_thresh=0.60)
        self.system = FaceLockSystem(target_name, self.matcher, self.det)
        self.session_log: SessionLogger | None = None
        
        self.running = True
        self.last_heartbeat = 0
        self.last_publish_time = 0
        self.mqtt_topic = TOPIC_MOVEMENT
        self.snapshot_sent = False
        self.smoothed_cx = 0.5
        self.in_center_zone = False
        self._status_candidate = "NO_FACE"
        self._status_candidate_count = 0
        self._published_status = "NO_FACE"

    def _decide_tracking_status(self, cx_norm: float) -> str:
        """Smooth face x and apply hysteresis so centering does not jitter."""
        self.smoothed_cx = (
            CX_SMOOTHING * cx_norm + (1.0 - CX_SMOOTHING) * self.smoothed_cx
        )
        x = self.smoothed_cx

        if self.in_center_zone:
            if x < CENTER_EXIT_LEFT or x > CENTER_EXIT_RIGHT:
                self.in_center_zone = False
            else:
                return "CENTERED"

        if x < TRACK_LEFT:
            return "MOVE_LEFT"
        if x > TRACK_RIGHT:
            return "MOVE_RIGHT"
        if CENTER_ZONE_LEFT <= x <= CENTER_ZONE_RIGHT:
            self.in_center_zone = True
            return "CENTERED"

        if x < 0.5:
            return "MOVE_LEFT"
        return "MOVE_RIGHT"

    def _debounced_status(self, raw_status: str, has_target: bool) -> str:
        if not has_target:
            self._status_candidate = "NO_FACE"
            self._status_candidate_count = STATUS_DEBOUNCE_FRAMES
            self._published_status = "NO_FACE"
            return "NO_FACE"

        # Snap out of NO_FACE immediately when target appears
        if self._published_status == "NO_FACE":
            self._published_status = raw_status
            self._status_candidate = raw_status
            self._status_candidate_count = MOVE_DEBOUNCE_FRAMES
            return raw_status

        # Hold CENTERED longer — harder to leave center zone
        if self._published_status == "CENTERED" and raw_status == "CENTERED":
            return "CENTERED"

        need = (
            MOVE_DEBOUNCE_FRAMES
            if raw_status in ("MOVE_LEFT", "MOVE_RIGHT")
            else STATUS_DEBOUNCE_FRAMES
        )
        if raw_status == self._status_candidate:
            self._status_candidate_count += 1
        else:
            self._status_candidate = raw_status
            self._status_candidate_count = 1

        if self._status_candidate_count >= need:
            self._published_status = self._status_candidate
        return self._published_status

    def _draw_center_zone(self, vis, status: str) -> None:
        h, w = vis.shape[:2]
        x1 = int(CENTER_ZONE_LEFT * w)
        x2 = int(CENTER_ZONE_RIGHT * w)
        cx = w // 2

        zone_color = (0, 255, 0) if status == "CENTERED" else (0, 200, 255)
        cv2.rectangle(vis, (x1, 0), (x2, h), zone_color, 2)
        cv2.line(vis, (cx, 0), (cx, h), (0, 255, 255), 1)
        cv2.putText(
            vis,
            "CENTER",
            (x1 + 6, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            zone_color,
            2,
            cv2.LINE_AA,
        )
        if status != "NO_FACE":
            fx = int(self.smoothed_cx * w)
            cv2.circle(vis, (fx, h - 20), 6, (0, 0, 255), -1)

    def on_connect(self, client, userdata, flags, reason_code, properties):
        print(f"Connected to MQTT Broker with result code {reason_code}")
        self.publish_heartbeat()

    def publish_movement(self, status, confidence=0.0, target=None, locked=False, face_image=None):
        # Keep movement messages small so ESP32 (512-byte MQTT buffer) can parse them.
        payload = {
            "status": status,
            "spec_status": to_spec_status(status),
            "confidence": round(float(confidence), 4),
            "target": target,
            "locked": locked,
            "timestamp": time.time(),
        }
        self.client.publish(self.mqtt_topic, json.dumps(payload))

        if face_image is not None:
            _, buffer = cv2.imencode('.jpg', face_image, [cv2.IMWRITE_JPEG_QUALITY, 70])
            snap = {
                "target": target,
                "face_image": base64.b64encode(buffer).decode('utf-8'),
                "timestamp": time.time(),
            }
            self.client.publish(TOPIC_SNAPSHOT, json.dumps(snap))

        print(f"Published: {status} (image: {'yes' if face_image is not None else 'no'})")

    def publish_heartbeat(self):
        payload = {
            "node": "pc_vision",
            "status": "ONLINE",
            "timestamp": time.time()
        }
        self.client.publish(TOPIC_HEARTBEAT, json.dumps(payload))

    def run(self, camera_source="0", auto_discover: bool = False):
        cap = open_camera(camera_source, auto_discover=auto_discover)
        self.session_log = SessionLogger(self.system.target_name)

        print(f"Benax Tracking System running. Target: {self.system.target_name}")
        print(f"Camera source: {camera_source}")
        print(f"Publishing to {TOPIC_MOVEMENT}")

        try:
            self._run_loop(cap)
        finally:
            cap.release()
            cv2.destroyAllWindows()
            self.client.loop_stop()
            if self.session_log:
                self.session_log.close()

    def _run_loop(self, cap):
        while self.running:
            ret, frame = cap.read()
            if not ret: break
            
            # Flip for mirror effect
            frame = cv2.flip(frame, 1)
            H, W = frame.shape[:2]
            
            # Process Frame using FaceLockSystem
            # Note: process_frame now returns (vis_frame, target_face_obj)
            vis, target_face, target_sim = self.system.process_frame(frame, self.embedder)

            status = "NO_FACE"
            face_crop = None
            confidence = 0.0

            if target_face:
                confidence = float(target_sim)
                # Target is found and locked
                f = target_face
                
                # Extract face crop for dashboard (only if not sent yet)
                if not self.snapshot_sent:
                    x1, y1, x2, y2 = int(f.x1), int(f.y1), int(f.x2), int(f.y2)
                    # Add padding
                    pad = 20
                    x1 = max(0, x1 - pad)
                    y1 = max(0, y1 - pad)
                    x2 = min(W, x2 + pad)
                    y2 = min(H, y2 + pad)
                    face_crop = frame[y1:y2, x1:x2]
                    self.snapshot_sent = True  # Mark as sent
                    print("Face snapshot captured and will be sent")
                
                cx_norm = ((f.x1 + f.x2) / 2.0) / W
                status = self._decide_tracking_status(cx_norm)
            else:
                self.smoothed_cx = 0.35 * 0.5 + 0.65 * self.smoothed_cx
                self.in_center_zone = False
                if self.snapshot_sent:
                    self.snapshot_sent = False
                    print("Target lost - snapshot flag reset")

            has_target = self.system.state == LockState.LOCKED
            status = self._debounced_status(status, has_target)
            self._draw_center_zone(vis, status)
            
            # --- RATE LIMITING (10Hz move, 5Hz when centered) ---
            current_time = time.time()
            publish_interval = 0.2 if status == "CENTERED" else 0.1
            if current_time - self.last_publish_time >= publish_interval:
                is_locked = has_target
                self.publish_movement(
                    status,
                    confidence=confidence,
                    target=self.system.target_name,
                    locked=is_locked,
                    face_image=face_crop,
                )
                if self.session_log:
                    self.session_log.write(
                        self.system.target_name,
                        confidence,
                        status,
                        is_locked,
                    )
                self.last_publish_time = current_time
            
            # Heartbeat every 5s
            if time.time() - self.last_heartbeat > 5:
                self.publish_heartbeat()
                self.last_heartbeat = time.time()
            
            cv2.imshow("Benax Tracking System", vis)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
        
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--broker", type=str, default=DEFAULT_BROKER, help="MQTT Broker Address")
    parser.add_argument("--name", type=str, default="andrew", help="Target name to lock onto")
    parser.add_argument(
        "--camera",
        type=str,
        default=str(DEFAULT_USB_CAMERA),
        help="USB index (0=built-in, 1=external robot cam) or ESP32 URL",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Scan the LAN for ESP32-CAM if --camera URL is unreachable",
    )
    parser.add_argument(
        "--list-cameras",
        action="store_true",
        help="List USB camera indices and exit",
    )
    args = parser.parse_args()

    if args.list_cameras:
        print("Available USB cameras:")
        print_cameras()
        raise SystemExit(0)

    node = VisionNode(args.broker, PORT, args.name)
    node.run(camera_source=args.camera, auto_discover=args.discover)

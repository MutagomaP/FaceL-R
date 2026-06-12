# ESP32 Firmware

## Which sketch to flash

| Board | Sketch | Camera stream |
|-------|--------|----------------|
| **ESP32-CAM** (camera + servo mount) | `camera_servo/camera_servo.ino` | `http://<ESP32-IP>/stream` |
| **ESP32 Dev Module** (servo only, PC camera) | `vision_servo/vision_servo.ino` | — |

## Arduino IDE setup

1. **Board manager:** ESP32 by Espressif (v2.x+)
2. **Libraries:** PubSubClient, ESP32Servo
3. **ESP32-CAM:** Board = *AI Thinker ESP32-CAM*, PSRAM = *Enabled*
4. Update WiFi `ssid` / `password` and `mqtt_server` in the sketch

## Wiring (servo)

| Servo | ESP32 |
|-------|--------|
| Signal | GPIO **12** (ESP32-CAM) or GPIO **14** (Dev Module) |
| VCC | 5V (external supply recommended) |
| GND | GND (common with ESP32) |

## After flashing

1. Open Serial Monitor (115200 baud) and note the IP address.
2. Test stream in browser: `http://<ESP32-IP>/stream`
3. On PC:

```bash
python -m src.camera --camera http://<ESP32-IP>/stream
python -m src.enroll --camera http://<ESP32-IP>/stream
python src/vision_node.py --broker 157.173.101.159 --name Patience --camera http://<ESP32-IP>/stream
```

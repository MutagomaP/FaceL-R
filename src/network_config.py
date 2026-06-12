"""Shared network settings — keep in sync with esp32/*.ino sketches."""

# Wi-Fi (Tecno pop hotspot)
WIFI_SSID = "Tecno pop"
WIFI_PASSWORD = "tecnopop"

# LAN (Tecno pop: PC is typically 192.168.1.x)
LAN_GATEWAY = "192.168.1.1"
PC_IP = "192.168.1.100"
ESP32_IP = "192.168.1.50"
ESP32_STREAM_URL = f"http://{ESP32_IP}/stream"

# MQTT (VPS broker — same for PC and ESP32)
MQTT_BROKER = "157.173.101.159"
MQTT_PORT = 1883
TEAM_ID = "team213"

/*
 * ESP32-CAM: MJPEG stream + MQTT servo control
 * Board: AI Thinker ESP32-CAM (Arduino IDE)
 * Libraries: WiFi, PubSubClient, ESP32Servo, esp32-camera (built-in)
 *
 * Camera stream: http://<ESP32-IP>/stream
 * PC vision:     python src/vision_node.py --camera http://<ESP32-IP>/stream --name Patience
 *
 * Servo signal -> GPIO12 (safe on ESP32-CAM; avoid GPIO14 on AI-Thinker)
 * Servo VCC    -> 5V external supply recommended
 * Servo GND    -> GND
 */

#include "esp_camera.h"
#include <WiFi.h>
#include <ESPmDNS.h>
#include <WebServer.h>
#include <PubSubClient.h>
#include <ESP32Servo.h>

// --- WiFi / MQTT ---
const char* ssid = "Tecno pop";
const char* password = "tecnopop";

// Tecno pop LAN: PC=192.168.1.100, ESP32=192.168.1.50, gateway=192.168.1.1
#define USE_STATIC_IP 1
#if USE_STATIC_IP
  IPAddress local_IP(192, 168, 1, 50);
  IPAddress gateway(192, 168, 1, 1);
  IPAddress subnet(255, 255, 255, 0);
#endif

const char* mqtt_server = "157.173.101.159";
const int mqtt_port = 1883;
const char* client_id = "esp32cam_team213";
const char* topic_movement = "vision/team213/movement";
const char* topic_heartbeat = "vision/team213/heartbeat";

// --- Servo ---
Servo myServo;
const int servoPin = 12;
int currentAngle = 90;
bool isSearching = true;
unsigned long lastSweepTime = 0;
int sweepStep = 2;
unsigned long lastFaceDetectTime = 0;
const unsigned long FACE_TIMEOUT = 2000;

WiFiClient espClient;
PubSubClient mqtt(espClient);
WebServer server(80);

// --- ESP32-CAM (AI-Thinker) pin map ---
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

void setup_wifi() {
  WiFi.mode(WIFI_STA);
#if USE_STATIC_IP
  WiFi.config(local_IP, gateway, subnet);
#endif
  WiFi.begin(ssid, password);
  Serial.print("Connecting WiFi (");
  Serial.print(ssid);
  Serial.print(")");
  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries < 40) {
    delay(500);
    Serial.print(".");
    tries++;
  }
  Serial.println();
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi FAILED — check ssid/password in sketch and re-flash.");
    return;
  }
  Serial.print("WiFi OK  IP: ");
  Serial.println(WiFi.localIP());
  Serial.print("Stream: http://");
  Serial.print(WiFi.localIP());
  Serial.println("/stream");
  if (MDNS.begin("esp32cam")) {
    Serial.println("mDNS:   http://esp32cam.local/stream");
  }
}

bool init_camera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.frame_size = FRAMESIZE_VGA;
  config.pixel_format = PIXFORMAT_JPEG;
  config.grab_mode = CAMERA_GRAB_LATEST;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.jpeg_quality = 12;
  config.fb_count = 2;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x\n", err);
    return false;
  }
  return true;
}

const int SERVO_STEP = 3;

void moveServo(int delta) {
  currentAngle += delta;
  if (currentAngle < 0) currentAngle = 0;
  if (currentAngle > 180) currentAngle = 180;
  myServo.write(currentAngle);
  Serial.printf("Servo -> %d deg (delta %+d)\n", currentAngle, delta);
}

void mqtt_callback(char* topic, byte* payload, unsigned int length) {
  if (length > 512) {
    Serial.printf("MQTT msg too large (%u bytes), ignored\n", length);
    return;
  }

  String message = "";
  message.reserve(length);
  for (unsigned int i = 0; i < length; i++) {
    message += (char)payload[i];
  }

  if (message.indexOf("\"MOVE_LEFT\"") >= 0 || message.indexOf("\"status\": \"MOVE_LEFT\"") >= 0) {
    Serial.println("CMD: MOVE_LEFT");
    isSearching = false;
    lastFaceDetectTime = millis();
    moveServo(SERVO_STEP);
  } else if (message.indexOf("\"MOVE_RIGHT\"") >= 0 || message.indexOf("\"status\": \"MOVE_RIGHT\"") >= 0) {
    Serial.println("CMD: MOVE_RIGHT");
    isSearching = false;
    lastFaceDetectTime = millis();
    moveServo(-SERVO_STEP);
  } else if (message.indexOf("\"CENTERED\"") >= 0) {
    Serial.println("CMD: CENTERED");
    isSearching = false;
    lastFaceDetectTime = millis();
  } else if (message.indexOf("\"NO_FACE\"") >= 0) {
    Serial.println("CMD: NO_FACE");
    isSearching = true;
  }
}

void mqtt_reconnect() {
  while (!mqtt.connected()) {
    if (mqtt.connect(client_id)) {
      mqtt.subscribe(topic_movement);
      Serial.println("MQTT connected");
    } else {
      delay(2000);
    }
  }
}

void handle_jpg_stream() {
  WiFiClient client = server.client();
  String response = "HTTP/1.1 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary=frame\r\n\r\n";
  server.sendContent(response);

  while (client.connected()) {
    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) {
      continue;
    }
    client.print("--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ");
    client.print(fb->len);
    client.print("\r\n\r\n");
    client.write(fb->buf, fb->len);
    client.print("\r\n");
    esp_camera_fb_return(fb);
    delay(1);
  }
}

void setup() {
  Serial.begin(115200);

  if (!init_camera()) {
    while (true) {
      delay(1000);
    }
  }

  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  ESP32PWM::allocateTimer(2);
  ESP32PWM::allocateTimer(3);
  myServo.setPeriodHertz(50);
  myServo.attach(servoPin, 500, 2400);
  myServo.write(currentAngle);

  setup_wifi();

  mqtt.setServer(mqtt_server, mqtt_port);
  mqtt.setCallback(mqtt_callback);
  mqtt.setBufferSize(1024);

  server.on("/stream", HTTP_GET, handle_jpg_stream);
  server.on("/", HTTP_GET, []() {
    server.send(200, "text/plain", "ESP32-CAM stream: /stream");
  });
  server.begin();

  Serial.println("Camera server ready");
}

void loop() {
  server.handleClient();

  if (!mqtt.connected()) {
    mqtt_reconnect();
  }
  mqtt.loop();

  unsigned long now = millis();

  if (!isSearching && (now - lastFaceDetectTime > FACE_TIMEOUT)) {
    isSearching = true;
  }

  if (isSearching && (now - lastSweepTime > 30)) {
    lastSweepTime = now;
    currentAngle += sweepStep;
    if (currentAngle >= 180) {
      currentAngle = 180;
      sweepStep = -2;
    } else if (currentAngle <= 0) {
      currentAngle = 0;
      sweepStep = 2;
    }
    myServo.write(currentAngle);
  }

  static unsigned long lastHeartbeat = 0;
  if (now - lastHeartbeat > 5000) {
    lastHeartbeat = now;
    mqtt.publish(topic_heartbeat, "{\"node\":\"esp32cam\",\"status\":\"ONLINE\"}");
  }
}

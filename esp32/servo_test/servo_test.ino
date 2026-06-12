/*
 * Standalone servo sweep test — ESP32-CAM (AI-Thinker)
 * Board: AI Thinker ESP32-CAM
 * Library: ESP32Servo (NOT the standard Arduino Servo library)
 *
 * Servo signal -> GPIO12
 * Upload this sketch alone (separate folder from camera_servo).
 */

#include <ESP32Servo.h>

Servo myServo;
const int servoPin = 5;

void setup() {
  Serial.begin(115200);
  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  ESP32PWM::allocateTimer(2);
  ESP32PWM::allocateTimer(3);
  myServo.setPeriodHertz(50);
  myServo.attach(servoPin, 500, 2400);
  Serial.println("Servo test on GPIO12 — sweep 0 -> 90 -> 180");
}

void loop() {
  myServo.write(0);
  Serial.println("angle 0");
  delay(1000);

  myServo.write(90);
  Serial.println("angle 90");
  delay(1000);

  myServo.write(180);
  Serial.println("angle 180");
  delay(1000);
}

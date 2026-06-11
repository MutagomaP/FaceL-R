from machine import Pin, PWM
from umqtt.simple import MQTTClient
import time
import ujson

# Configuration
# VPS IP for MQTT Broker
MQTT_BROKER = "10.12.75.223"  # Local PC IP for testing 10.12.73.80
# switch to team213 to match the Arduino firmware below
CLIENT_ID = "esp8266_team213"
TOPIC_SUB = b"vision/team213/movement"
TOPIC_PUB = b"vision/team213/heartbeat"

# Servo Configuration
# SG90 Servo: 50Hz PWM. Duty cycle usually 40-115 for 0-180 degrees.
SERVO_PIN = 14 # D5 on NodeMCU
servo = PWM(Pin(SERVO_PIN), freq=50)

# Positions (Duty values for 0, 90, 180 degrees approx)
# 10-bit resolution (0-1023)
# Min (0 deg) ~ 0.5ms/20ms * 1023 = ~26
# Max (180 deg) ~ 2.4ms/20ms * 1023 = ~123
# Center (90 deg) ~ 1.45ms/20ms * 1023 = ~74
MIN_DUTY = 40
MAX_DUTY = 115
CENTER_DUTY = 77

current_duty = CENTER_DUTY

# --- searching / sweep configuration ---
is_searching = True             # rotate continuously until a face command arrives
last_face_time = 0.0            # timestamp of last received face/lock message
FACE_TIMEOUT = 2.0              # seconds without a face -> resume search
sweep_dir = 1                   # 1 or -1 step direction while sweeping
sweep_step = 5                  # duty increment during sweep
last_sweep_time = 0.0           # for rate limiting of sweep
SWEEP_INTERVAL = 0.05           # seconds between incremental moves

def set_servo(duty):
    global current_duty
    # Clamp
    if duty < MIN_DUTY: duty = MIN_DUTY
    if duty > MAX_DUTY: duty = MAX_DUTY
    servo.duty(duty)
    current_duty = duty
    print("Servo Duty:", duty)

def sub_cb(topic, msg):
    global current_duty, is_searching, last_face_time
    print((topic, msg))
    
    try:
        data = ujson.loads(msg)
        status = data.get("status", "")
        locked = data.get("locked", False)
        
        step = 5 # Move 5 units per command
        
        # update search/watchdog state
        if status in ("MOVE_LEFT", "MOVE_RIGHT", "CENTERED"):
            is_searching = False
            last_face_time = time.time()
        elif status == "NO_FACE":
            # explicit instruction to resume sweeping
            is_searching = True
        
        if status == "MOVE_LEFT":
            # rotation direction may depend on servo mounting
            set_servo(current_duty + step)
        elif status == "MOVE_RIGHT":
            set_servo(current_duty - step)
        elif status == "CENTERED":
            # stop movement when target is centred/locked
            pass
        # else NO_FACE or unknown -> let search logic in main loop handle rotation
            
    except Exception as e:
        print("Error parsing JSON:", e)

def main():
    print("Starting MQTT Client...")
    
    # Initialize Servo to Center
    set_servo(CENTER_DUTY)
    
    try:
        client = MQTTClient(CLIENT_ID, MQTT_BROKER)
        client.set_callback(sub_cb)
        client.connect()
        print("Connected to MQTT Broker:", MQTT_BROKER)
        client.subscribe(TOPIC_SUB)
        print("Subscribed to:", TOPIC_SUB)
        
        last_heartbeat = 0
        
        while True:
            # Check for messages
            client.check_msg()
            
            now = time.time()
            # watchdog: if enough time has passed without a face command, resume searching
            if not is_searching and (now - last_face_time) > FACE_TIMEOUT:
                print("No recent face commands, resuming sweep")
                is_searching = True

            # sweep when searching
            if is_searching and (now - last_sweep_time) > SWEEP_INTERVAL:
                last_sweep_time = now
                next_duty = current_duty + sweep_dir * sweep_step
                if next_duty >= MAX_DUTY or next_duty <= MIN_DUTY:
                    sweep_dir *= -1
                set_servo(current_duty + sweep_dir * sweep_step)

            # Heartbeat every 10s
            if now - last_heartbeat > 10:
                payload = ujson.dumps({"node": "esp8266", "status": "ONLINE", "uptime": now})
                client.publish(TOPIC_PUB, payload)
                last_heartbeat = now
                
            time.sleep(0.1)
            
    except Exception as e:
        print("Error:", e)
        print("Rebooting in 5 seconds...")
        time.sleep(5)
        import machine
        machine.reset()

if __name__ == "__main__":
    main()

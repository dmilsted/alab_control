#include <Arduino.h>
#include <Ethernet.h>
#include <EthernetUdp.h>
#include <Wire.h>
#include <Stepper.h>

/*
 * List of Possible UDP Commands:
 * - ROTATE_X: Rotates the stepper motor by X degrees (e.g., "ROTATE_90" for 90 degrees).
 *   Terminal example: echo "ROTATE_90" | nc -u -w2 192.168.0.15 8888
 * 
 * - PUMP_Y: Turns on the pump for Y milliseconds and then automatically turns it off (e.g., "PUMP_5000" for 5000 ms).
 *   Terminal example: echo "PUMP_5000" | nc -u -w2 192.168.0.15 8888
 */

#define stepPin1 5
#define stepPin2 6
#define stepPin3 7
#define stepPin4 8
#define relayPin 9

byte mac[] = { 0xDE, 0xAD, 0xB3, 0x4D, 0x7A, 0x77 };
IPAddress ip(192, 168, 0, 47);
IPAddress gateway(192, 168, 0, 1);
IPAddress subnet(255, 255, 255, 0);
const unsigned int udpPort = 8888;

EthernetUDP Udp;

const int stepsPerRevolution = 200; // Adjust this for your specific stepper motor
Stepper myStepper(stepsPerRevolution, stepPin1, stepPin3, stepPin2, stepPin4);

void(* resetFunc) (void) = 0;//declare reset function at address 0

void setup() {
  Serial.println(F("Arduino is starting..."));
  Serial.begin(9600);
  delay(500);  // Allow some time for Serial connection to stabilize

  //disabling SD card
  pinMode(4, OUTPUT);
  digitalWrite(4, HIGH);

  // Initialize Ethernet
  Ethernet.begin(mac, ip);
  Ethernet.begin(mac, ip, gateway, subnet);
  delay(3000);
  Udp.begin(udpPort);

  Serial.println(F("Ethernet initialized."));
  Serial.print(F("IP Address: "));
  Serial.println(Ethernet.localIP());
  delay(1000);
  if(Ethernet.localIP() != ip){
    Serial.println(F("Wrong IP. Resetting arduino board..."));
    delay(1500);
    resetFunc(); //call reset 
  }

  myStepper.setSpeed(30); // Set the motor speed (RPM) to a slower value

  pinMode(relayPin, OUTPUT);
  digitalWrite(relayPin, LOW);

  Serial.println(F("Arduino just started."));
}



void rotateStepper(int degrees) {
  int steps = (degrees * stepsPerRevolution) / 360; // Convert degrees to steps
  myStepper.step(steps);
  Serial.print(F("Stepper rotated by "));
  Serial.print(degrees);
  Serial.println(F(" degrees."));
  Udp.beginPacket(Udp.remoteIP(), Udp.remotePort());
  Udp.print(F("Stepper rotated by "));
  Udp.print(degrees);
  Udp.println(F(" degrees."));
  Udp.endPacket();
}

void controlPump(int durationMs) {
  digitalWrite(relayPin, HIGH);
  Serial.print(F("Pump is ON for "));
  Serial.print(durationMs);
  Serial.println(F(" ms."));
  Udp.beginPacket(Udp.remoteIP(), Udp.remotePort());
  Udp.print(F("Pump is ON for "));
  Udp.print(durationMs);
  Udp.println(F(" ms."));
  Udp.endPacket();

  delay(durationMs);

  digitalWrite(relayPin, LOW);
  Serial.println(F("Pump is OFF."));
  Udp.beginPacket(Udp.remoteIP(), Udp.remotePort());
  Udp.println(F("Pump is OFF."));
  Udp.endPacket();
}

void processSerialCommands() {
  if (Serial.available() > 0) {
    String serialCommand = Serial.readStringUntil('\n');
    serialCommand.trim();
    Serial.print(F("Received Serial command: "));
    Serial.println(serialCommand);  // Debug output
    processCommand(serialCommand);
  }
}

void processCommand(String command) {
  if (command.startsWith(F("ROTATE"))) {
    int angle = command.substring(7).toInt();
    rotateStepper(angle);
    Serial.println(F("Stepper rotated via command."));
  } else if (command.startsWith(F("PUMP"))) {
    int duration = command.substring(5).toInt(); // Extract the duration in ms
    if (duration > 0) {
      controlPump(duration);
      Serial.println(F("Pump activated for specified duration."));
    } else {
      Serial.println(F("Invalid pump duration."));
      Udp.beginPacket(Udp.remoteIP(), Udp.remotePort());
      Udp.println(F("Invalid pump duration."));
      Udp.endPacket();
    }
  } else {
    Serial.print(F("Unknown command: "));
    Serial.println(command);
    Udp.beginPacket(Udp.remoteIP(), Udp.remotePort());
    Udp.print(F("Unknown command: "));
    Udp.println(command);
    Udp.endPacket();
  }
}

void loop() {
  int packetSize = Udp.parsePacket();
  if (packetSize) {
    char udpData[packetSize + 1];
    Udp.read(udpData, packetSize);
    udpData[packetSize] = '\0'; // Null-terminate the received data

    String command = String(udpData);
    command.trim();

    Serial.print(F("Received UDP command: "));
    Serial.println(command);  // Debug output

    processCommand(command);
  }

  // Process Serial commands
  processSerialCommands();
}

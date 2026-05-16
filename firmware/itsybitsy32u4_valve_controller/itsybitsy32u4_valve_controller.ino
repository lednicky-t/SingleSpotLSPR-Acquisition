/*
  LSPR valve controller for Adafruit ItsyBitsy 5V 32u4 16 MHz.

  App-compatible serial protocol:
    asn -> protocol/version string
    mod -> model string
    vi  -> legacy version string
    vl  -> open / left
    vr  -> close / right
    va0 -> output off

  Hardware:
    Valve control output: A3
*/

const char PROTOCOL_VERSION[] = "LSPR-VALVE-ITSYBITSY32U4-1.0";
const char MODEL_NAME[] = "ItsyBitsy 32u4 valve controller";

const uint8_t VALVE_PIN = A3;
const bool OPEN_IS_HIGH = true;

String commandBuffer;

void setValveOpen(bool open) {
  const bool active = OPEN_IS_HIGH ? open : !open;
  digitalWrite(VALVE_PIN, active ? HIGH : LOW);
}

void handleCommand(String command) {
  command.trim();
  command.toLowerCase();

  if (command.length() == 0) {
    return;
  }

  if (command == "asn" || command == "vi") {
    Serial.println(PROTOCOL_VERSION);
    return;
  }

  if (command == "mod") {
    Serial.println(MODEL_NAME);
    return;
  }

  if (command == "vl" || command == "left" || command == "open" || command == "o") {
    setValveOpen(true);
    Serial.println("ok");
    return;
  }

  if (command == "vr" || command == "right" || command == "close" || command == "c") {
    setValveOpen(false);
    Serial.println("ok");
    return;
  }

  if (command == "va0" || command == "off" || command == "stop") {
    setValveOpen(false);
    Serial.println("ok");
    return;
  }

  Serial.println("err");
}

void setup() {
  pinMode(VALVE_PIN, OUTPUT);
  setValveOpen(false);

  Serial.begin(115200);
}

void loop() {
  while (Serial.available() > 0) {
    const char c = static_cast<char>(Serial.read());

    if (c == '\n' || c == '\r') {
      if (commandBuffer.length() > 0) {
        handleCommand(commandBuffer);
        commandBuffer = "";
      }
      continue;
    }

    if (commandBuffer.length() < 64) {
      commandBuffer += c;
    } else {
      commandBuffer = "";
      Serial.println("err");
    }
  }
}

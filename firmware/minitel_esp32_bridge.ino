/*
 * minitel_esp32_bridge.ino
 * ------------------------------------------------------------------
 * Pont TRANSPARENT entre le Minitel (UART DIN5, 1200 7E1) et le service
 * minitel-gpt heberge sur le VPS, via WebSocket securise (wss).
 *
 * L'ESP32 ne fait AUCUN traitement Videotex : il relaie les octets bruts.
 *   Minitel -> UART RX -> WebSocket (frame binaire) -> serveur
 *   serveur  -> WebSocket (frame binaire) -> UART TX -> Minitel
 *
 * Librairie requise : "WebSockets" de Markus Sattler (Links2004)
 *   -> Gestionnaire de bibliotheques Arduino : chercher "WebSockets by Markus Sattler"
 *
 * ============================ CABLAGE ============================
 * Exemple valide sur un Minitel 1B Matra (carte VGP5). Le brochage DIN-5
 * du Minitel N'EST PAS sequentiel : de gauche a droite vu de face (broches
 * visibles, connecteur oriente detrompeur en bas), c'est 1 - 4 - 2 - 5 - 3.
 *
 * Donnees (via level shifter, cote A = ESP32 3,3 V, cote B = Minitel 5 V) :
 *   DIN broche 1 (Minitel RX)  <-  ESP32 TX (GPIO17)   [via level shifter]
 *   DIN broche 3 (Minitel TX)  ->  ESP32 RX (GPIO16)   [via level shifter]
 *   DIN broche 2 (GND)         <-> ESP32 GND (masse commune avec le buck
 *                                   et le level shifter)
 *
 * Alimentation (optionnel, evite une alim externe separee) :
 *   DIN broche 5 (~8,5 a 13 V selon le Minitel) -> buck converter regle
 *   sur 5 V -> pin 5V/VIN de l'ESP32 + cote HV (VCCB) du level shifter.
 *   Un condensateur 470-1000 uF sur le rail 5 V pres de l'ESP32 est
 *   recommande. Cote LV (VCCA) du level shifter sur le 3V3 de l'ESP32.
 *   Broche 4 : ne pas toucher.
 *   ATTENTION : cette alimentation sur la broche 5 depend du circuit video
 *   interne du Minitel 1B. Les modeles a base de VGP5 (comme ici) la
 *   fournissent ; les plus anciens a base de VGP2 ne l'ont PAS -> prevoir
 *   une alimentation externe pour l'ESP32 dans ce cas.
 *
 * !!! IMPORTANT NIVEAUX LOGIQUES !!!
 * Le port peri-info du Minitel est en 5 V (c'est pourquoi le projet d'origine
 * met le cavalier FTDI sur 5 V). Or les GPIO de l'ESP32 sont en 3.3 V et NE
 * SONT PAS tolerants 5 V. Il FAUT un adaptateur de niveau logique bidirectionnel
 * (ex. module a base de BSS138, ou TXS0108E) entre le Minitel et l'ESP32 :
 *   - obligatoire sur la ligne Minitel TX (5 V) -> ESP32 RX, sinon tu grilles le GPIO
 *   - recommande aussi sur ESP32 TX (3.3 V) -> Minitel RX pour une marge propre
 * ================================================================
 */

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <WebSocketsClient.h>

// ---------- A CONFIGURER ----------
const char* WIFI_SSID     = "TON_WIFI";
const char* WIFI_PASSWORD = "TON_MDP_WIFI";

const char* WS_HOST = "minitel.mondomaine.fr";   // ton serveur
const int   WS_PORT = 443;                       // wss
// Si WS_TOKEN est configure cote serveur (variable d'env WS_TOKEN), le
// jeton doit etre passe ici en query string, sinon le serveur refuse la
// connexion. Laisser "/ws" si WS_TOKEN n'est pas configure cote serveur.
const char* WS_PATH = "/ws?token=TON_TOKEN";
// ----------------------------------

// UART2 vers le Minitel : 1200 bauds, 7 bits de donnees, parite paire, 1 stop.
#define MINITEL_RX 16   // ESP32 RX  <- Minitel TX (broche DIN 3)
#define MINITEL_TX 17   // ESP32 TX  -> Minitel RX (broche DIN 1)
HardwareSerial Minitel(2);

WebSocketsClient webSocket;
bool wsConnected = false;

// Petit tampon pour regrouper les octets clavier avant envoi (moins de frames)
uint8_t txBuf[64];
size_t  txLen = 0;
unsigned long lastByteMs = 0;

void flushTx() {
  if (txLen > 0 && wsConnected) {
    webSocket.sendBIN(txBuf, txLen);
    txLen = 0;
  }
}

void onWsEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_CONNECTED:
      wsConnected = true;
      Serial.println("[WS] connecte au serveur minitel-gpt");
      break;
    case WStype_DISCONNECTED:
      wsConnected = false;
      Serial.println("[WS] deconnecte");
      break;
    case WStype_BIN:
    case WStype_TEXT:
      // Octets Videotex venant du serveur -> ecran Minitel, tels quels.
      Minitel.write(payload, length);
      break;
    default:
      break;
  }
}

void setup() {
  Serial.begin(115200);                       // console de debug USB
  Minitel.begin(1200, SERIAL_7E1, MINITEL_RX, MINITEL_TX);

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("[WiFi] connexion");
  while (WiFi.status() != WL_CONNECTED) {
    delay(400); Serial.print(".");
  }
  Serial.printf("\n[WiFi] OK, IP %s\n", WiFi.localIP().toString().c_str());

  // wss:// -> beginSSL. Pour un demarrage simple on ne verifie pas le cert.
  // (ton reverse proxy presente un vrai cert Let's Encrypt ; pour durcir, tu
  //  peux ensuite fournir l'empreinte via webSocket.setSSLFingerprint.)
  webSocket.beginSSL(WS_HOST, WS_PORT, WS_PATH);
  webSocket.onEvent(onWsEvent);
  webSocket.setReconnectInterval(3000);       // reconnexion auto
}

void loop() {
  webSocket.loop();

  // Minitel -> serveur : on lit le clavier et on empile
  while (Minitel.available()) {
    uint8_t b = (uint8_t) Minitel.read();
    if (txLen < sizeof(txBuf)) txBuf[txLen++] = b;
    lastByteMs = millis();
    if (txLen >= sizeof(txBuf)) flushTx();     // tampon plein -> envoi
  }
  // envoi si le tampon "repose" depuis 15 ms (fin de rafale de frappe)
  if (txLen > 0 && (millis() - lastByteMs) > 15) flushTx();
}

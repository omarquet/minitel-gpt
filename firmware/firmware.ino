/*
 * minitel_esp32_bridge.ino
 * ------------------------------------------------------------------
 * Pont TRANSPARENT entre le Minitel (UART DIN5, 1200 7E1) et le service
 * minitel-gpt heberge sur le VPS Coolify, via WebSocket securise (wss).
 *
 * L'ESP32 ne fait AUCUN traitement Videotex : il relaie les octets bruts.
 *   Minitel -> UART RX -> WebSocket (frame binaire) -> serveur
 *   serveur  -> WebSocket (frame binaire) -> UART TX -> Minitel
 *
 * Librairie requise : "WebSockets" de Markus Sattler (Links2004)
 *   -> Gestionnaire de bibliotheques Arduino : chercher "WebSockets by Markus Sattler"
 *
 * CIBLE : ESP32-C3 (RISC-V). ATTENTION, le C3 n'a que DEUX UART (UART0 et
 * UART1) : il n'existe pas d'UART2. UART0 sert a la console USB/debug, on
 * utilise donc UART1. De plus, sur le C3 les GPIO11 a GPIO17 sont reserves a
 * la memoire flash : les GPIO16/17 du projet d'origine sont INUTILISABLES.
 *
 * ============================ CABLAGE ============================
 * DIN5 peri-informatique Minitel  <->  ESP32-C3 (UART1)
 *   DIN broche 1 (Minitel RX)  <-  ESP32-C3 TX (GPIO5)  [via level shifter]
 *   DIN broche 3 (Minitel TX)  ->  ESP32-C3 RX (GPIO4)  [via level shifter]
 *   DIN broche 2 (GND)         <-> ESP32 GND
 *   DIN broche 4 : ne pas toucher.
 *
 * !!! ORDRE DE BRANCHEMENT !!!
 * Le VB du TXS0108E est pris sur la broche 5V de l'ESP32-C3, qui EST le VBUS
 * de l'USB : USB debranche, elle est flottante. Si on branche le DIN en
 * premier, le 5 V du Minitel remonte par les diodes de protection et alimente
 * partiellement la carte (la LED d'alim s'allume faiblement) : rails a une
 * tension partielle, tout est dans un etat indetermine, et le TXS0108E voit
 * VB s'etablir avant VA, l'inverse de ce que demande sa datasheet.
 *   -> BRANCHER : USB d'abord, DIN ensuite.
 *   -> DEBRANCHER : DIN d'abord, USB en dernier.
 *
 * !!! DIN BROCHE 5 : 12 V MESURES sur ce Minitel 2 Alcatel !!!
 * Ne JAMAIS relier cette broche a l'ESP32, ni sur 3V3, ni sur 5V/VIN : sur
 * les petites cartes C3, 5V est cablee au VBUS de l'USB et le regulateur
 * 3,3 V embarque plafonne vers 6 V d'entree -> carte detruite.
 * Pour un montage autonome alimente par le Minitel (sans USB) :
 *   broche 5 -> buck DC-DC 12 V vers 5 V (MP1584, LM2596), tension VERIFIEE
 *   au voltmetre avant branchement -> pin 5V de l'ESP32 + VB du TXS0108E.
 *   Condensateur 470-1000 uF sur le rail 5 V pres de l'ESP32 (les pics de
 *   courant du WiFi font decrocher les petits bucks).
 *   NE PAS cumuler l'USB du Mac et le buck : conflit VBUS / sortie buck sur
 *   le meme rail. Debrancher l'un pour utiliser l'autre (ou diode Schottky
 *   en serie sur la sortie du buck).
 *   Le courant disponible sur la broche 5 est faible et mal documente : le
 *   mesurer en charge WiFi avant de considerer le montage fiable.
 *
 * Broches C3 a EVITER : GPIO11-17 (flash), GPIO18/19 (USB natif),
 * GPIO20/21 (UART0 console), GPIO2/9 (strapping au boot).
 * GPIO4 et GPIO5 sont libres et sans contrainte de boot.
 * GPIO8 est un strapping pin mais porte la LED de statut de la carte : elle
 * n'est pilotee qu'apres le boot, et son etat de repos est l'etat haut.
 *
 * !!! IMPORTANT NIVEAUX LOGIQUES !!!
 * Le port peri-info du Minitel est en 5 V (c'est pourquoi le projet d'origine
 * met le cavalier FTDI sur 5 V). Or les GPIO de l'ESP32-C3 sont en 3.3 V et NE
 * SONT PAS tolerants 5 V. Il FAUT un adaptateur de niveau logique bidirectionnel
 * (ex. module a base de BSS138, ou TXS0108E) entre le Minitel et l'ESP32 :
 *   - obligatoire sur la ligne Minitel TX (5 V) -> ESP32 RX, sinon tu grilles le GPIO
 *   - recommande aussi sur ESP32 TX (3.3 V) -> Minitel RX pour une marge propre
 * ================================================================
 */

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <WebSocketsClient.h>
#include <esp_system.h>   // esp_reset_reason() : cause du dernier demarrage
// WIFI_SSID / WIFI_PASSWORD / WS_TOKEN_ENC. Fichier ignore par git : le creer
// a partir de secrets.h.example (meme dossier).
#include "secrets.h"

// ============ A CONFIGURER ============
// Mettre 1 pour tester en LOCAL (serveur docker sur ton Mac, ws:// non chiffre)
// Mettre 0 pour la PROD sur Coolify (wss:// chiffre)
#define USE_LOCAL 0

// 0 = fonctionnement normal.
// 1 = trace sur le moniteur serie tout ce qui arrive du Minitel.
//     Rien = le Minitel n'emet pas. Bons caracteres = liaison OK.
//     Octets incoherents = mauvaise vitesse/format.
// 2 = comme 1, plus emission du motif de test toutes les 2 s. Permet de
//     sonder la ligne au voltmetre et de faire un test de bouclage
//     (GPIO4 relie a GPIO5) sans dependre du timing du RESET.
#define DEBUG_UART 0

#if USE_LOCAL
  // IP locale de ton Mac. La trouver avec :
  //   ipconfig getifaddr en0
  const char* WS_HOST = "192.168.1.116";
  const int   WS_PORT = 8080;
#else
  const char* WS_HOST = "minitel.playground.aqoba.fr";
  const int   WS_PORT = 443;
#endif

// Pour isoler un souci materiel : basculer sur "/ws-echo", ou le serveur
// renvoie les octets tels quels sans appeler le LLM. Revenir a "/ws" ensuite.
#define WS_ENDPOINT "/ws"

// Le serveur (WS_TOKEN) ferme la connexion en SILENCE si le token est absent
// ou faux : symptome = "[WS] connecte" puis "[WS] deconnecte" en boucle.
const char* WS_PATH = WS_ENDPOINT "?token=" WS_TOKEN_ENC;
// ======================================

// UART1 vers le Minitel : 1200 bauds, 7 bits de donnees, parite paire, 1 stop.
// (UART1 et non UART2 : le C3 ne possede pas de troisieme UART.)
// Reseaux connus, essayes dans l'ordre. Les entrees 2 et 3 n'existent que si
// secrets.h les definit : un secrets.h d'avant cette liste continue de
// marcher tel quel, avec un seul reseau.
struct WifiNet { const char* ssid; const char* pass; };
static const WifiNet KNOWN_NETS[] = {
  { WIFI_SSID, WIFI_PASSWORD },
#ifdef WIFI_SSID2
  { WIFI_SSID2, WIFI_PASSWORD2 },
#endif
#ifdef WIFI_SSID3
  { WIFI_SSID3, WIFI_PASSWORD3 },
#endif
};
static const uint8_t KNOWN_COUNT = sizeof(KNOWN_NETS) / sizeof(KNOWN_NETS[0]);
// Reseau actuellement utilise : le filet de reconnexion du loop doit relancer
// CELUI-LA, pas le premier de la liste.
static uint8_t currentNet = 0;
// Delai laisse a chaque reseau avant de passer au suivant. Assez long pour un
// DHCP lent, assez court pour faire le tour d'une liste de trois sans lasser.
#define WIFI_TRY_MS 12000

#define MINITEL_RX 4    // ESP32-C3 RX  <- Minitel TX (broche DIN 3)
#define MINITEL_TX 5    // ESP32-C3 TX  -> Minitel RX (broche DIN 1)
HardwareSerial Minitel(1);

// LED de statut integree a la carte, sur GPIO8, en logique INVERSEE.
// GPIO8 est une broche de strapping (doit etre a l'etat haut au boot) : on ne
// la pilote qu'apres le demarrage, et l'etat de repos LED_OFF est justement
// HIGH, donc un reset pendant une phase eteinte demarre normalement.
// Codage : flash bref toutes les 2 s = tout va bien (prouve aussi que loop()
// tourne encore), clignotement lent = WiFi OK mais WebSocket coupee,
// clignotement rapide = WiFi perdu.
#define STATUS_LED 8
#define LED_ON     LOW
#define LED_OFF    HIGH

WebSocketsClient webSocket;
bool wsConnected = false;

// Petit tampon pour regrouper les octets clavier avant envoi (moins de frames)
uint8_t txBuf[64];
size_t  txLen = 0;
unsigned long lastByteMs = 0;

// Non bloquant : la phase se deduit de millis(), aucune variable d'etat.
void updateStatusLed() {
  unsigned long onMs, periodMs;
  if (WiFi.status() != WL_CONNECTED) {
    onMs = 100; periodMs = 200;            // rapide : pas de WiFi
  } else if (!wsConnected) {
    onMs = 500; periodMs = 1000;           // lent : WiFi OK, WebSocket coupee
  } else {
    onMs = 50;  periodMs = 2000;           // flash bref : tout va bien
  }
  digitalWrite(STATUS_LED, (millis() % periodMs) < onMs ? LED_ON : LED_OFF);
}

void flushTx() {
  if (txLen == 0) return;
  if (wsConnected) {
    webSocket.sendBIN(txBuf, txLen);
  }
  // Deconnecte : on JETTE ce qui a ete tape pendant la coupure. Sinon le
  // tampon reste plein (frappes suivantes perdues en silence) et se deverse
  // dans la session neuve d'apres reconnexion, comme une question fantome.
  txLen = 0;
}

void onWsEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_CONNECTED:
      wsConnected = true;
      Serial.println("[WS] connecte au serveur minitel-gpt");
      break;
    case WStype_DISCONNECTED:
      wsConnected = false;
      Serial.printf("[WS] deconnecte (WiFi status=%d, RSSI=%d dBm)\n",
                    WiFi.status(), WiFi.RSSI());
      break;
    case WStype_ERROR:
      Serial.printf("[WS] erreur (%u octets)\n", (unsigned) length);
      break;
    case WStype_BIN:
    case WStype_TEXT:
      // Octets Videotex venant du serveur -> ecran Minitel, tels quels.
#if DEBUG_UART
      // Trace compacte du sens descendant : taille + 16 premiers octets.
      Serial.printf("[TX] %u o :", (unsigned) length);
      for (size_t i = 0; i < length && i < 16; i++) Serial.printf(" %02X", payload[i]);
      Serial.println(length > 16 ? " ..." : "");
#endif
      Minitel.write(payload, length);
      break;
    default:
      break;
  }
}

// Essaie chaque reseau connu a tour de role. Retourne des le premier qui
// repond. La LED continue de clignoter pendant l'attente : sans moniteur
// serie, c'est le seul signe de vie.
bool connectKnown() {
  for (uint8_t i = 0; i < KNOWN_COUNT; i++) {
    Serial.printf("[WiFi] essai %u/%u : %s\n", i + 1, KNOWN_COUNT, KNOWN_NETS[i].ssid);
    WiFi.disconnect();
    WiFi.begin(KNOWN_NETS[i].ssid, KNOWN_NETS[i].pass);
    unsigned long t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < WIFI_TRY_MS) {
      updateStatusLed();
      delay(10);
    }
    if (WiFi.status() == WL_CONNECTED) {
      currentNet = i;
      return true;
    }
    Serial.println(" -> pas de reponse");
  }
  return false;
}

void setup() {
  Serial.begin(115200);                       // console de debug (USB CDC sur C3)
  // USB CDC : le port n'est pret que ~1 s apres le boot. Sans cette attente,
  // les premiers messages sont perdus et le moniteur serie parait muet.
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 2000) delay(10);
  Minitel.begin(1200, SERIAL_7E1, MINITEL_RX, MINITEL_TX);

  // Cause du dernier demarrage : distingue un redemarrage volontaire du filet
  // WiFi (SW) d'un plantage (PANIC), d'un watchdog, ou d'une alimentation qui
  // decroche (BROWNOUT) - ces derniers n'ont rien a voir avec le WiFi.
  const char* cause;
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON:  cause = "mise sous tension";                    break;
    case ESP_RST_EXT:      cause = "bouton RESET";                         break;
    case ESP_RST_SW:       cause = "ESP.restart() (filet WiFi)";           break;
    case ESP_RST_PANIC:    cause = "PLANTAGE (exception)";                 break;
    case ESP_RST_INT_WDT:  cause = "WATCHDOG interruptions";               break;
    case ESP_RST_TASK_WDT: cause = "WATCHDOG tache";                       break;
    case ESP_RST_WDT:      cause = "WATCHDOG";                             break;
    case ESP_RST_BROWNOUT: cause = "BROWNOUT (alimentation insuffisante)"; break;
    case ESP_RST_DEEPSLEEP:cause = "sortie de veille profonde";            break;
    default:               cause = "inconnue";                             break;
  }
  Serial.printf("[BOOT] cause du dernier demarrage : %s\n", cause);

  pinMode(STATUS_LED, OUTPUT);
  digitalWrite(STATUS_LED, LED_OFF);

#if DEBUG_UART
  // Test du sens ESP32 -> Minitel, independant du WiFi et du serveur : on
  // efface l'ecran et on ecrit une ligne. Si elle s'affiche, la moitie
  // descendante de la liaison (GPIO5 -> shifter -> DIN 1) est bonne.
  Serial.println("[UART] DEBUG_UART actif : envoi du motif de test au Minitel");
  Minitel.write(0x0C);                        // FF : effacement de l'ecran
  Minitel.print("TEST ESP32 OK");
  Minitel.write(0x0D); Minitel.write(0x0A);   // CR LF
#endif

  // Tour de la liste, indefiniment : le Minitel peut etre allume avant la box.
  // Le tour entier prend KNOWN_COUNT x 12 s, la LED clignotant pendant tout ce
  // temps ; aucun redemarrage n'est necessaire quand le reseau revient.
  while (!connectKnown()) {
    Serial.println("[WiFi] aucun reseau connu n'a repondu, nouveau tour");
  }
  Serial.printf("[WiFi] OK sur %s, IP %s\n", KNOWN_NETS[currentNet].ssid,
                WiFi.localIP().toString().c_str());
  // On n'affiche PAS WS_PATH en entier : il contient le token.
  Serial.printf("[WS] cible : %s:%d%s?token=***\n", WS_HOST, WS_PORT, WS_ENDPOINT);

#if USE_LOCAL
  // ws:// en clair : parfait pour tester sur le reseau local.
  webSocket.begin(WS_HOST, WS_PORT, WS_PATH);
#else
  // wss:// -> beginSSL. Traefik/Coolify presente un vrai cert Let's Encrypt.
  // Pour durcir ensuite : webSocket.setSSLFingerprint(...).
  webSocket.beginSSL(WS_HOST, WS_PORT, WS_PATH);
#endif
  webSocket.onEvent(onWsEvent);
  webSocket.setReconnectInterval(3000);       // reconnexion auto
  // Ping applicatif : detecte un lien mort en ~30 s au lieu d'attendre que
  // TCP s'en apercoive (ping 15 s, pong attendu sous 3 s, 2 echecs tolerees).
  webSocket.enableHeartbeat(15000, 3000, 2);
}

void loop() {
  webSocket.loop();
  updateStatusLed();

#if DEBUG_UART >= 2
  // Emission periodique du motif de test : donne un signal permanent a sonder
  // sur la ligne TX, et fait defiler des [RX] en continu si GPIO4 et GPIO5
  // sont relies (bouclage).
  static unsigned long lastTest = 0;
  if (millis() - lastTest > 2000) {
    lastTest = millis();
    Minitel.print("TEST ESP32 OK");
    Minitel.write(0x0D); Minitel.write(0x0A);
  }
#endif

  // Filet WiFi : l'auto-reconnexion du core couvre les coupures ordinaires
  // (box qui redemarre, hors de portee), mais PAS les raisons AUTH_FAIL ou
  // ASSOC_LEAVE, qui laisseraient la carte morte jusqu'a un reset physique.
  static unsigned long wifiDownSince = 0;
  static bool wifiRetried = false;
  if (WiFi.status() != WL_CONNECTED) {
    if (wifiDownSince == 0) {
      wifiDownSince = millis();
      wifiRetried = false;
      Serial.println("[WiFi] perdu, attente de la reconnexion automatique");
    } else if (!wifiRetried && millis() - wifiDownSince > 30000) {
      // L'auto-reconnexion du core peut s'enliser : on la relance une fois
      // explicitement avant d'envisager le redemarrage.
      wifiRetried = true;
      Serial.println("[WiFi] 30 s sans reseau -> relance de la connexion");
      WiFi.disconnect();
      WiFi.begin(KNOWN_NETS[currentNet].ssid, KNOWN_NETS[currentNet].pass);
    } else if (millis() - wifiDownSince > 120000) {
      // Le redemarrage refait le tour complet de la liste, ce que la relance
      // ci-dessus ne fait pas : c'est ainsi qu'on bascule sur le reseau de
      // secours quand le reseau habituel disparait pour de bon.
      Serial.println("[WiFi] 2 min sans reseau -> redemarrage");
      Serial.flush();
      ESP.restart();
    }
  } else {
    if (wifiDownSince != 0) {
      Serial.printf("[WiFi] retabli apres %lu s\n",
                    (millis() - wifiDownSince) / 1000);
    }
    wifiDownSince = 0;
  }

  // Minitel -> serveur : on lit le clavier et on empile
  while (Minitel.available()) {
    uint8_t b = (uint8_t) Minitel.read();
#if DEBUG_UART
    Serial.printf("[RX] 0x%02X %c\n", b, (b >= 32 && b < 127) ? (char) b : '.');
#endif
    if (txLen < sizeof(txBuf)) txBuf[txLen++] = b;
    lastByteMs = millis();
    if (txLen >= sizeof(txBuf)) flushTx();     // tampon plein -> envoi
  }
  // envoi si le tampon "repose" depuis 15 ms (fin de rafale de frappe)
  if (txLen > 0 && (millis() - lastByteMs) > 15) flushTx();
}

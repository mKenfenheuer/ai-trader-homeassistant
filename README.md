# AI Trader für Home Assistant

Diese Integration holt die Depots der [AI-Trader-App](https://gitlab.ksol.it/mKenfenheuer/ai-trader) nach Home Assistant. Jedes Depot erscheint als eigenes Gerät, mit Werten, Positionen, offenen Vorschlägen, Verlauf und Kalender. Vorschläge des Agenten lassen sich per Push-Nachricht freigeben oder ablehnen.

## Voraussetzungen

- Home Assistant 2025.11 oder neuer
- AI Trader mit der Token-API (`/api/v1`), ab dem Stand mit dem Bereich „Home Assistant (API)“ in den Einstellungen
- Home Assistant erreicht die App über HTTP(S), zum Beispiel `https://trader.example.com`

## Installation

**HACS** Unter HACS → Benutzerdefinierte Repositories `https://github.com/mKenfenheuer/ai-trader-homeassistant` als Typ „Integration“ eintragen, „AI Trader“ installieren und Home Assistant neu starten.

**Manuell** Den Ordner `custom_components/ai_trader` nach `config/custom_components/` kopieren und Home Assistant neu starten.

## Einrichtung

1. In der App unter Einstellungen → Home Assistant (API) einen Token erzeugen. Die App zeigt ihn nur einmal an.
2. In Home Assistant unter Einstellungen → Geräte & Dienste → Integration hinzufügen „AI Trader“ wählen.
3. Adresse der App und Token eintragen.

Ein neuer Token in der App ersetzt den alten sofort. Home Assistant meldet das und fragt nach dem neuen Token. Das Abfrageintervall (Standard 60 Sekunden) steht in den Optionen der Integration.

## Geräte und Entitäten

**Depot** Jedes Depot ist ein Gerät; Papierdepots und Trading-212-Konten gleichermaßen. Es hat diese Entitäten:

| Entität | Inhalt |
|---|---|
| Depotwert, Barmittel, Investiert | aktuelle Werte in Depotwährung |
| Nettoeinzahlungen, Gesamtgewinn, Gesamtrendite | Gewinn gegenüber den Einzahlungen |
| Veränderung heute (€ und %) | Tagesveränderung der Positionen |
| Einstandswert, unrealisierter und realisierter Gewinn | wie in der App |
| Positionen | Anzahl, die Positionen als Attribut |
| Offene Vorschläge, Offene Orders | Anzahl, Details mit Nummer, Seite, Stückzahl und Begründung als Attribut |
| Letzter Lauf | Zeitpunkt, mit Engine, Status und Bericht des Agenten als Attribut |
| 7-Tage-Prognose, 80 % unten/oben | Median und 80-%-Band des Depotwerts am Ende des Prognosefensters |
| eine Entität je Position | Marktwert, als Attribute Stückzahl, Kurs, Gewinn, Gewicht, Tagesveränderung, Modellscore, Prognose und Quartalstermin |
| Lauf aktiv, Vorschläge warten | Binärsensoren |
| Agentenlauf starten, Regellauf starten | Schaltflächen |
| Kalender | Käufe, Verkäufe, Ein- und Auszahlungen, Läufe, geplante Orders, ablaufende Vorschläge, Quartalszahlen der Positionen und geplante Läufe vor Börsenöffnung |
| Broker-Abgleich, Problem beim Broker-Abgleich | nur bei Trading 212, als Diagnose |

Neue Depots und Positionen erscheinen ohne Neustart. Eine verkaufte Position wird „nicht verfügbar“ und behält ihren Verlauf.

**AI Trader** Ein weiteres Gerät bündelt den Markt: Börse offen, Status, nächste Öffnung und nächster Handelsschluss, Marktrisiko des ML-Modells (Wahrscheinlichkeit für −5 % im S&P 500 binnen 20 Tagen, Stufe, VIX, erwartete Volatilität) und einen Börsenkalender mit den Feiertagen.

## Verlauf

Die App kennt den Depotwert seit der ersten Einzahlung, Home Assistant erst ab der Einrichtung. Beim ersten Start füllt die Integration deshalb die Langzeitstatistik von Depotwert, Barmitteln, Investiert, Nettoeinzahlungen, Gesamtgewinn und Gesamtrendite mit einem Wert pro Handelstag aus der App. Danach zeichnet Home Assistant selbst auf. Der Dienst `ai_trader.import_history` wiederholt den Import, etwa nach dem Löschen der Statistik.

## Ereignisse

| Ereignis | Wann | Daten |
|---|---|---|
| `ai_trader_signal_pending` | ein neuer Vorschlag wartet auf Freigabe | `signal_id`, `portfolio_id`, `portfolio`, `side`, `name`, `isin`, `quantity`, `price`, `amount`, `currency`, `reason`, `expires_at`, `title`, `message`, `url` |
| `ai_trader_signal_closed` | ein Vorschlag ist nicht mehr offen (freigegeben, abgelehnt, abgelaufen) | `signal_id` |
| `ai_trader_run_finished` | ein Lauf des Agenten oder der Regelengine ist fertig | `portfolio_id`, `portfolio`, `run_id`, `engine`, `status`, `summary`, `error`, `started_at`, `finished_at` |

Welche Vorschläge und Läufe schon gemeldet wurden, merkt sich die Integration über Neustarts hinweg. Beim allerersten Abruf meldet sie nichts, damit nach der Einrichtung keine Flut alter Vorschläge entsteht.

## Dienste

| Dienst | Felder |
|---|---|
| `ai_trader.approve_signal` | `signal_id` |
| `ai_trader.reject_signal` | `signal_id` |
| `ai_trader.run` | `portfolio_id`, optional `engine` (`agent` oder `rules`) und `instruction` |
| `ai_trader.import_history` | `portfolio_id` |

Bei mehreren App-Instanzen wählt `config_entry_id` die richtige aus. Außerhalb der Handelszeit plant die App eine freigegebene Order für die nächste Öffnung, wie bei einer Freigabe in der App.

## Freigabe per Push

Der Blueprint [approve_signals.yaml](blueprints/automation/ai_trader/approve_signals.yaml) schickt jeden neuen Vorschlag an die Companion-App, mit den Aktionen „Freigeben“ und „Ablehnen“. Ist der Vorschlag anderweitig entschieden, entfernt er die Nachricht.

[![Blueprint importieren](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FmKenfenheuer%2Fai-trader-homeassistant%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fai_trader%2Fapprove_signals.yaml)

Ein einfaches Beispiel ohne Blueprint meldet fertige Läufe:

```yaml
automation:
  - alias: AI Trader – Lauf fertig
    triggers:
      - trigger: event
        event_type: ai_trader_run_finished
    actions:
      - action: notify.notify
        data:
          title: "{{ trigger.event.data.portfolio }}: Lauf {{ 'fehlgeschlagen' if trigger.event.data.status == 'failed' else 'fertig' }}"
          message: "{{ (trigger.event.data.error or trigger.event.data.summary or '')[:500] }}"
```

## Sicherheit

Der Token erlaubt Lesen, das Freigeben und Ablehnen von Vorschlägen und das Starten von Läufen. Manuelle Orders, Ein- und Auszahlungen oder Einstellungen sind über die API nicht möglich. Die App speichert nur den SHA-256-Hash des Tokens. Bei einem Echtgeldkonto führt eine Freigabe zu einer echten Order. Wer Home Assistant oder den Token kontrolliert, kann also handeln.

## Entwicklung

```bash
uv venv --python 3.13
uv pip install pytest-homeassistant-custom-component
.venv/bin/python -m pytest -q
uvx ruff check . && uvx ruff format --check .
```

Die Tests laufen gegen ein echtes Home Assistant mit Recorder; die App simuliert `tests/fixtures/summary.json`.

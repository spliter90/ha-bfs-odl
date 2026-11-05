# BfS Ortsdosisleistung (ODL) für Home Assistant

Custom Integration für [Home Assistant](https://www.home-assistant.io/), die die Ortsdosisleistung (Gamma-Umgebungsstrahlung) aus dem ODL-Messnetz des [Bundesamts für Strahlenschutz](https://odlinfo.bfs.de/) ausliest. Das Messnetz umfasst rund 1.700 Sonden in ganz Deutschland. Die Daten sind frei und ohne API-Schlüssel verfügbar.

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![Validate](https://github.com/spliter90/ha-bfs-odl/actions/workflows/validate.yml/badge.svg)](https://github.com/spliter90/ha-bfs-odl/actions/workflows/validate.yml)

## Funktionen

- Einrichtung komplett über die Oberfläche, ohne YAML
  - **Standort:** die 10 nächsten Sonden zum Home-Assistant-Standort mit Entfernung
  - **Kennung oder Suche:** 9-stellige Kennung (z. B. `064340121`), Ortsname oder Postleitzahl
- Mehrere Sonden als getrennte Einträge, jede Sonde ist ein eigenes Gerät
- Sensoren pro Sonde:

| Entität | Beschreibung |
|---|---|
| Ortsdosisleistung | Aktueller 1-Stunden-Wert in µSv/h, 3 Nachkommastellen, mit Attributen (Name, Kennung, PLZ, Höhe über NN, validiert, Koordinaten) |
| Kosmischer Anteil | Anteil der kosmischen Strahlung in µSv/h |
| Terrestrischer Anteil | Anteil der Strahlung aus dem Boden in µSv/h |
| 7-Tage-Mittel | Gleitendes Mittel der Stundenwerte der letzten 7 Tage |
| Zeitpunkt der Messung | Ende des Messintervalls (Diagnose) |
| Status der Sonde | In Betrieb / Defekt / Testbetrieb (Diagnose) |
| Erhöhte Strahlung | Binärsensor, siehe unten |

- **Erhöhte Strahlung** schaltet ein, wenn der aktuelle Wert
  - über einer festen Schwelle liegt (Standard 0,3 µSv/h) **oder**
  - mehr als X % über dem gleitenden 7-Tage-Mittel liegt (Standard 50 %).

  Die Attribute zeigen Schwelle, Mittel, Grenze und Auslöser (`threshold`, `mean`).
- Diagnose-Download unter *Einstellungen → Geräte & Dienste → BfS ODL → ⋮ → Diagnose herunterladen*

### So werden die Daten abgerufen

- Pro Sonde wird nur deren Wert abgefragt (WFS-Filter auf die Kennung). Akzeptiert der Server den `CQL_FILTER` nicht, nutzt die Integration den standardisierten OGC-Filter. Funktioniert keiner von beiden, lädt sie die Gesamtliste, speichert sie 15 Minuten zwischen und teilt sie mit allen Einträgen. Die funktionierende Methode wird gemerkt.
- Das 7-Tage-Mittel stammt aus der BfS-Zeitreihe (`odlinfo_timeseries_odl_1h`), die höchstens alle 6 Stunden abgerufen wird. Zusätzlich speichert die Integration jeden abgerufenen Stundenwert dauerhaft. Steht die Zeitreihe nicht zur Verfügung, baut sich das Mittel so selbst auf. Es wird angezeigt, sobald mindestens 24 Stundenwerte vorliegen.
- Abrufintervall: standardmäßig 30 Minuten, einstellbar ab 10 Minuten. Das BfS veröffentlicht einmal pro Stunde neue Werte.

### Wann sind Entitäten „nicht verfügbar“?

- Der BfS-Dienst ist nicht erreichbar oder liefert Fehler (alle Entitäten).
- Die Sonde ist defekt, liefert keinen Wert oder der letzte Wert ist älter als 12 Stunden (Messwerte und Binärsensor; Status und Zeitpunkt bleiben sichtbar).
- Die Sonde wurde aus dem Datensatz entfernt.

Die Integration stürzt in keinem dieser Fälle ab; sobald wieder Daten kommen, sind die Entitäten automatisch zurück.

## Installation über HACS (benutzerdefiniertes Repository)

Voraussetzung: [HACS](https://hacs.xyz/docs/use/) ist installiert, Home Assistant ab Version 2025.8.

1. In Home Assistant **HACS** öffnen.
2. Oben rechts auf **⋮ → Benutzerdefinierte Repositories** klicken.
3. Als Repository `https://github.com/spliter90/ha-bfs-odl` eintragen, als Typ **Integration** wählen und **Hinzufügen** klicken.
4. In HACS nach **BfS Ortsdosisleistung (ODL)** suchen, öffnen und **Herunterladen** klicken.
5. Home Assistant neu starten (*Einstellungen → System → ⏻ → Home Assistant neu starten*).
6. *Einstellungen → Geräte & Dienste → Integration hinzufügen → BfS Ortsdosisleistung (ODL)*.
7. **Nächste Sonden zum Home-Assistant-Standort** oder **Kennung eingeben oder nach Namen suchen** wählen und die Sonde auswählen.

Für weitere Sonden Schritt 6 und 7 wiederholen.

### Manuelle Installation

Den Ordner `custom_components/bfs_odl` nach `config/custom_components/bfs_odl` kopieren und Home Assistant neu starten.

## Optionen

*Einstellungen → Geräte & Dienste → BfS ODL → Konfigurieren*

| Option | Standard | Bereich |
|---|---|---|
| Abrufintervall | 30 min | 10–360 min |
| Feste Schwelle | 0,3 µSv/h | 0,01–100 µSv/h |
| Prozent über dem 7-Tage-Mittel | 50 % | 1–1000 % |

Nach dem Speichern wird der Eintrag automatisch neu geladen.

## Lovelace-Beispiel

Die Entitäts-IDs ergeben sich aus dem Namen der Sonde und der Sprache von Home Assistant. Für die Sonde Wehrheim mit deutscher Oberfläche:

```yaml
type: vertical-stack
cards:
  - type: gauge
    entity: sensor.wehrheim_ortsdosisleistung
    name: Ortsdosisleistung Wehrheim
    min: 0
    max: 0.5
    needle: true
    severity:
      green: 0
      yellow: 0.2
      red: 0.3

  - type: conditional
    conditions:
      - condition: state
        entity: binary_sensor.wehrheim_erhohte_strahlung
        state: "on"
    card:
      type: markdown
      content: >
        ⚠️ **Erhöhte Strahlung in Wehrheim:**
        {{ states('sensor.wehrheim_ortsdosisleistung') }} µSv/h
        (7-Tage-Mittel {{ states('sensor.wehrheim_7_tage_mittel') }} µSv/h).
        Ausgelöst durch: {{ state_attr('binary_sensor.wehrheim_erhohte_strahlung', 'triggered_by') | join(', ') }}

  - type: entities
    title: ODL-Sonde Wehrheim
    entities:
      - entity: sensor.wehrheim_ortsdosisleistung
      - entity: sensor.wehrheim_kosmischer_anteil
      - entity: sensor.wehrheim_terrestrischer_anteil
      - entity: sensor.wehrheim_7_tage_mittel
      - entity: binary_sensor.wehrheim_erhohte_strahlung
      - entity: sensor.wehrheim_status_der_sonde
      - entity: sensor.wehrheim_zeitpunkt_der_messung
        format: relative

  - type: history-graph
    title: Verlauf (7 Tage)
    hours_to_show: 168
    entities:
      - entity: sensor.wehrheim_ortsdosisleistung
        name: Gesamt
      - entity: sensor.wehrheim_7_tage_mittel
        name: 7-Tage-Mittel
```

Da der Hauptsensor `latitude` und `longitude` als Attribute hat, erscheint die Sonde auch auf der **Karten-Karte** (`type: map`).

## Automatisierungsbeispiel

```yaml
alias: Warnung bei erhöhter Strahlung
triggers:
  - trigger: state
    entity_id: binary_sensor.wehrheim_erhohte_strahlung
    to: "on"
    for: "01:00:00"
actions:
  - action: notify.notify
    data:
      title: Erhöhte Ortsdosisleistung
      message: >
        Wehrheim meldet {{ states('sensor.wehrheim_ortsdosisleistung') }} µSv/h.
        Details: https://odlinfo.bfs.de/
```

Die Wartezeit von einer Stunde filtert einzelne Ausreißer. Natürliche Schwankungen entstehen vor allem durch Regen: Er wäscht Zerfallsprodukte des natürlichen Edelgases Radon aus der Luft und kann die Ortsdosisleistung für einige Stunden deutlich erhöhen, teils auf das Doppelte. Mit dem Standard von 50 % über dem Mittel schaltet der Binärsensor bei starkem Regen deshalb gelegentlich ein. Wer das nicht möchte, erhöht den Prozentwert (z. B. auf 150 %) und verlässt sich auf die feste Schwelle.

## Entwicklung und Tests

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest
```

Die Tests nutzen `pytest-homeassistant-custom-component` mit gemockten BfS-Antworten und prüfen Config-Flow, Optionen, Coordinator, Filter-Fallbacks, Ausfälle und Diagnose.

## Datenquelle und Lizenz der Daten

Daten: Bundesamt für Strahlenschutz (BfS), [ODL-Info](https://odlinfo.bfs.de/), WFS-Dienst `https://www.imis.bfs.de/ogc/opendata/ows`, Es gelten die [Nutzungsbedingungen des BfS](https://odlinfo.bfs.de/ODL/EN/service/data-interface/data-interface_node.html). Diese Integration ist kein offizielles Produkt des BfS.

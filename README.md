# Umwelt & Netz (BfS / Energy-Charts) für Home Assistant

Custom Integration für [Home Assistant](https://www.home-assistant.io/) mit zwei freien Datenquellen ohne API-Schlüssel:

- **Strahlung:** Ortsdosisleistung (Gamma-Umgebungsstrahlung) aus dem ODL-Messnetz des [Bundesamts für Strahlenschutz](https://odlinfo.bfs.de/) mit rund 1.700 Sonden in ganz Deutschland.
- **Stromnetz:** Netzampel (Ökostrom-Signal), Netzfrequenz sowie Last und Erzeugung in Deutschland von [Energy-Charts](https://www.energy-charts.info/) (Fraunhofer ISE).

Die Domain heißt weiterhin `bfs_odl`. Bestehende Sonden laufen nach dem Update unverändert weiter.

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![Validate](https://github.com/spliter90/ha-bfs-odl/actions/workflows/validate.yml/badge.svg)](https://github.com/spliter90/ha-bfs-odl/actions/workflows/validate.yml)

## Strahlung (BfS)

- Einrichtung komplett über die Oberfläche, ohne YAML: *Integration hinzufügen → Strahlungsmesssonde (BfS)*
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
- Diagnose-Download unter *Einstellungen → Geräte & Dienste → Umwelt & Netz → ⋮ → Diagnose herunterladen*

### So werden die Daten abgerufen

- Pro Sonde wird nur deren Wert abgefragt (WFS-Filter auf die Kennung). Akzeptiert der Server den `CQL_FILTER` nicht, nutzt die Integration den standardisierten OGC-Filter. Funktioniert keiner von beiden, lädt sie die Gesamtliste, speichert sie 15 Minuten zwischen und teilt sie mit allen Einträgen. Die funktionierende Methode wird gemerkt.
- Das 7-Tage-Mittel stammt aus der BfS-Zeitreihe (`odlinfo_timeseries_odl_1h`), die höchstens alle 6 Stunden abgerufen wird. Zusätzlich speichert die Integration jeden abgerufenen Stundenwert dauerhaft. Steht die Zeitreihe nicht zur Verfügung, baut sich das Mittel so selbst auf. Es wird angezeigt, sobald mindestens 24 Stundenwerte vorliegen.
- Abrufintervall: standardmäßig 30 Minuten, einstellbar ab 10 Minuten. Das BfS veröffentlicht einmal pro Stunde neue Werte.

### Wann sind Entitäten „nicht verfügbar“?

- Der BfS-Dienst ist nicht erreichbar oder liefert Fehler (alle Entitäten).
- Die Sonde ist defekt, liefert keinen Wert oder der letzte Wert ist älter als 12 Stunden (Messwerte und Binärsensor; Status und Zeitpunkt bleiben sichtbar).
- Die Sonde wurde aus dem Datensatz entfernt.

Die Integration stürzt in keinem dieser Fälle ab; sobald wieder Daten kommen, sind die Entitäten automatisch zurück.

## Stromnetz (Energy-Charts)

Einrichtung: *Integration hinzufügen → Umwelt & Netz → Stromnetz (Energy-Charts)*. Dort gibst du die Postleitzahl ein und schaltest Netzampel, Frequenz und Erzeugung einzeln ein oder aus. Home Assistant speichert selbst keine Postleitzahl; vorgeschlagen wird deshalb die PLZ der BfS-Sonde, die deinem Standort am nächsten liegt. Alles landet auf einem Gerät **Stromnetz Deutschland (Energy-Charts)**. Das Stromnetz lässt sich einmal einrichten.

### Netzampel

| Entität | Beschreibung |
|---|---|
| Netzampel | Grün, Gelb, Rot oder Rot (Netzengpass) für die aktuelle Viertelstunde. Attribut `forecast` mit der Prognose der nächsten 24 h (Beginn, Ende, Anteil, Signal) |
| Ökostromanteil | Anteil Erneuerbarer laut Ampel für die aktuelle Viertelstunde in % |
| Nächstes grünes Zeitfenster | Beginn des nächsten zusammenhängenden grünen Zeitraums (Timestamp), Attribute `end` und `duration_minutes`. Ist es gerade grün, steht hier der Beginn des laufenden Zeitraums |
| Gute Zeit für Stromverbrauch | Binärsensor, an bei grüner Ampel |

Bedeutung der Signalwerte laut Energy-Charts: `2` = Grün (hoher Anteil Erneuerbarer), `1` = Gelb (durchschnittlich), `0` = Rot (geringer Anteil), `-1` = Rot wegen Netzengpass. Das aktuelle Zeitfenster wird über die Uhrzeit bestimmt, nicht über den letzten Listeneintrag; zum Wechsel jeder Viertelstunde rechnet die Integration die Zustände neu, ohne die API erneut abzufragen. Laut Energy-Charts-Doku wertet der Dienst die Postleitzahl derzeit noch nicht regional aus; sie wird trotzdem mitgeschickt.

### Netzfrequenz

| Entität | Beschreibung |
|---|---|
| Netzfrequenz | Neuester verfügbarer Sekundenwert in Hz, 3 Nachkommastellen. Attribute `measured_at` (Zeitpunkt des Messwerts) und `data_age_minutes` |
| Frequenzabweichung | Abweichung von 50 Hz in mHz |
| Frequenz Minimum (1 h) / Frequenz Maximum (1 h) | Extremwerte der **letzten Stunde der verfügbaren Daten**, also der Stunde vor dem neuesten Messwert. Attribute `window_start`, `window_end` und `window_basis: latest_data` |
| Frequenz Datenalter | Minuten seit dem neuesten Messwert (Diagnose). Bleibt auch bei veralteten Daten sichtbar |
| Netzfrequenz auffällig | Binärsensor, an bei mehr als ±100 mHz Abweichung (einstellbar) |

Gemessen wird die Frequenz vom Fraunhofer ISE in Freiburg. Die API-Doku nennt nur diese eine Messstelle für das europäische Verbundnetz, daher gibt es keine Regionsauswahl.

**Energy-Charts liefert die Frequenz mit rund 50 Minuten Verzug aus.** Die Werte sind also keine Live-Anzeige, sondern zeigen den Stand von vor etwa einer Stunde; das Datenalter zeigt, wie groß der Verzug gerade ist. So arbeitet die Integration damit:

- Beim ersten Abruf und nach jedem Neustart lädt sie die letzten 4 Stunden (mindestens 3 Stunden, bei längerer Stale-Grenze entsprechend mehr). Danach fragt sie jede Minute nur die neuen Sekundenwerte ab.
- Im Speicher bleiben die 3 Stunden vor dem neuesten Messwert.
- Minimum und Maximum beziehen sich auf die Stunde vor dem neuesten Messwert, nicht auf die Stunde vor „jetzt“.
- Ein einzelner fehlgeschlagener Abruf macht die Entitäten nicht unavailable. Unavailable werden sie erst, wenn der neueste Messwert älter als die **Stale-Grenze** ist (Standard 3 Stunden, einstellbar von 30 Minuten bis 12 Stunden).

#### Wenn Energy-Charts keine Frequenz liefert

Seit dem 05.11.2026 antwortet `/frequency` für aktuelle Zeiträume zeitweise mit HTTP 404 („no content available“). Ab Version 1.1.2 geht die Integration so damit um:

- Der Stromnetz-Eintrag wird trotzdem eingerichtet. Netzampel und Erzeugung laufen normal; nur die Frequenz-Entitäten sind nicht verfügbar.
- Fehlen Daten bei einem späteren Abruf, rechnet die Integration mit den vorhandenen weiter, bis diese älter als die Stale-Grenze sind. Danach werden die Frequenz-Entitäten unavailable, „Frequenz Datenalter“ zählt weiter.
- Der letzte Messwert und der Ausfallzustand werden gespeichert. Nach einem Neustart zeigt das Datenalter deshalb weiter, wie alt die letzten Daten sind.
- Im Log steht pro Ausfall genau eine Warnung, sobald keine verwertbaren Daten mehr vorliegen, und eine Info, wenn sie zurückkommen.
- Fehlen die Daten länger als 6 Stunden (frühestens nach der Stale-Grenze), erscheint unter *Einstellungen → System → Reparaturen* die Meldung „Energy-Charts liefert seit … keine Netzfrequenz“. Sie verschwindet von selbst, wenn wieder Daten kommen, oder wenn du die Frequenz in den Optionen abschaltest.
- Ist beim Start kein früherer Messwert bekannt, gilt als Beginn des Ausfalls der Zeitpunkt, an dem die Integration ihn bemerkt hat.

### Last und Erzeugung

Sensoren in GW: Netzlast, Residuallast, Wind an Land, Wind auf See, Solar, Biomasse, Wasserkraft (Laufwasser und Speicher, ohne Pumpspeicher), Braunkohle, Steinkohle, Erdgas, Import-Export-Saldo sowie **Anteil Erneuerbare an der Last** in %.

- Quelle ist `/total_power` (inklusive Eigenversorgung der Industrie). Liefert der Endpunkt nichts Brauchbares, nutzt die Integration `/public_power` (öffentliches Netz) und merkt sich das.
- Die neuesten Viertelstunden sind bei Energy-Charts oft noch leer. Jeder Sensor zeigt deshalb den letzten vorhandenen Wert; das Attribut `data_time` sagt, für welche Viertelstunde er gilt. `production_types` nennt die verwendeten Energy-Charts-Reihen.
- Den Anteil Erneuerbarer an der Last übernimmt die Integration direkt, wenn Energy-Charts ihn mitliefert. Sonst berechnet sie ihn aus Wind, Solar, Biomasse, Wasserkraft und Geothermie geteilt durch die Last.
- **Import-Export-Saldo: positiv = Export, negativ = Import.** Energy-Charts zählt umgekehrt: Laut API-Doku zum grenzüberschreitenden Stromhandel bedeuten dort positive Werte Import und negative Export. Die Integration dreht das Vorzeichen deshalb um. Das Attribut `direction` sagt zusätzlich „Import“ oder „Export“.

Alle Messwerte haben `state_class: measurement` und landen in den Langzeitstatistiken. Frequenz und Leistungen haben die Geräteklassen `frequency` bzw. `power`. Prozentwerte und die Abweichung in mHz haben keine Geräteklasse, weil Home Assistant dafür keine passende anbietet (`power_factor` wäre fachlich falsch, `frequency` kennt kein mHz).

### Wann sind Stromnetz-Entitäten „nicht verfügbar“?

- Energy-Charts ist nicht erreichbar oder liefert Fehler. Betroffen sind nur die Entitäten des jeweiligen Endpunkts; die anderen laufen weiter. Das gilt auch beim Start: Solange mindestens eine Quelle Daten liefert, wird der Eintrag eingerichtet; die ausgefallene Quelle erholt sich im normalen Abrufintervall von selbst. Nur wenn alle eingeschalteten Quellen scheitern, versucht Home Assistant die Einrichtung später erneut.
- Für eine Größe gibt es in den letzten 24 Stunden keinen Wert.
- Die Frequenz ist älter als die Stale-Grenze (Standard 3 Stunden). Für die Frequenz-Entitäten zählt nur das Datenalter, nicht einzelne fehlgeschlagene Abrufe.

## Installation über HACS (benutzerdefiniertes Repository)

Voraussetzung: [HACS](https://hacs.xyz/docs/use/) ist installiert, Home Assistant ab Version 2025.8.

1. In Home Assistant **HACS** öffnen.
2. Oben rechts auf **⋮ → Benutzerdefinierte Repositories** klicken.
3. Als Repository `https://github.com/spliter90/ha-bfs-odl` eintragen, als Typ **Integration** wählen und **Hinzufügen** klicken.
4. In HACS nach **Umwelt & Netz (BfS / Energy-Charts)** suchen, öffnen und **Herunterladen** klicken.
5. Home Assistant neu starten (*Einstellungen → System → ⏻ → Home Assistant neu starten*).
6. *Einstellungen → Geräte & Dienste → Integration hinzufügen → Umwelt & Netz (BfS / Energy-Charts)*.
7. **Strahlungsmesssonde (BfS)** oder **Stromnetz (Energy-Charts)** wählen und den Schritten folgen.

Für weitere Sonden Schritt 6 und 7 wiederholen.

### Update von Version 1.0

Die Domain, die Einträge, die eindeutigen IDs und die Entitäts-IDs der Sonden bleiben gleich; eine Migration ist nicht nötig. Nach dem Update über HACS und einem Neustart heißt die Integration in der Oberfläche „Umwelt & Netz (BfS / Energy-Charts)“. Das Stromnetz fügst du als zusätzlichen Eintrag hinzu.

### Manuelle Installation

Den Ordner `custom_components/bfs_odl` nach `config/custom_components/bfs_odl` kopieren und Home Assistant neu starten.

## Optionen

*Einstellungen → Geräte & Dienste → Umwelt & Netz → beim Eintrag auf das Zahnrad tippen*

**Strahlungsmesssonde**

| Option | Standard | Bereich |
|---|---|---|
| Abrufintervall | 30 min | 10–360 min |
| Feste Schwelle | 0,3 µSv/h | 0,01–100 µSv/h |
| Prozent über dem 7-Tage-Mittel | 50 % | 1–1000 % |

**Stromnetz**

| Option | Standard | Bereich |
|---|---|---|
| Postleitzahl | aus der Einrichtung | 5 Ziffern |
| Netzampel / Netzfrequenz / Netzlast und Erzeugung | an | an / aus |
| Abrufintervall Netzampel | 15 min | 5–240 min |
| Abrufintervall Frequenz | 60 s | 30–3600 s |
| Abrufintervall Erzeugung | 15 min | 5–240 min |
| Grenze Frequenzabweichung | 100 mHz | 10–1000 mHz |
| Frequenzdaten gültig für (Stale-Grenze) | 180 min | 30–720 min |

Nach dem Speichern wird der Eintrag automatisch neu geladen. Schaltest du einen Teil ab, verschwinden dessen Entitäten.

## Lovelace-Beispiele

### Strahlung

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

### Stromnetz mit `custom:plotly-graph`

Voraussetzung ist die Karte [Plotly Graph Card](https://github.com/dbuezas/lovelace-plotly-graph-card), installierbar über *HACS → Frontend*. Die Entitäts-IDs gelten für eine deutsche Oberfläche.

```yaml
type: vertical-stack
cards:
  - type: tile
    entity: sensor.stromnetz_deutschland_energy_charts_netzampel
    name: Netzampel

  - type: entities
    entities:
      - entity: binary_sensor.stromnetz_deutschland_energy_charts_gute_zeit_fur_stromverbrauch
      - entity: sensor.stromnetz_deutschland_energy_charts_nachstes_grunes_zeitfenster
        format: relative
      - entity: sensor.stromnetz_deutschland_energy_charts_okostromanteil
      - entity: sensor.stromnetz_deutschland_energy_charts_anteil_erneuerbare_an_der_last

  - type: custom:plotly-graph
    title: Netzfrequenz (letzte 3 Stunden)
    hours_to_show: 3
    refresh_interval: 60
    entities:
      - entity: sensor.stromnetz_deutschland_energy_charts_netzfrequenz
        name: Frequenz
        line:
          color: "#1e88e5"
          width: 1.5
          shape: spline
    layout:
      height: 260
      showlegend: false
      yaxis:
        title: Hz
        range: [49.85, 50.15]
      shapes:
        # Sollwert 50 Hz
        - type: line
          xref: paper
          x0: 0
          x1: 1
          y0: 50
          y1: 50
          line:
            color: "#e53935"
            width: 1.5
            dash: dash
        # Grenzen ±100 mHz (Standard des Binärsensors)
        - type: line
          xref: paper
          x0: 0
          x1: 1
          y0: 49.9
          y1: 49.9
          line:
            color: "#fb8c00"
            width: 1
            dash: dot
        - type: line
          xref: paper
          x0: 0
          x1: 1
          y0: 50.1
          y1: 50.1
          line:
            color: "#fb8c00"
            width: 1
            dash: dot

  - type: custom:plotly-graph
    title: Erzeugung und Last Deutschland (48 Stunden)
    hours_to_show: 48
    refresh_interval: 900
    entities:
      - entity: sensor.stromnetz_deutschland_energy_charts_braunkohle
        name: Braunkohle
        stackgroup: erzeugung
        line: { width: 0, shape: hv, color: "#795548" }
      - entity: sensor.stromnetz_deutschland_energy_charts_steinkohle
        name: Steinkohle
        stackgroup: erzeugung
        line: { width: 0, shape: hv, color: "#424242" }
      - entity: sensor.stromnetz_deutschland_energy_charts_erdgas
        name: Erdgas
        stackgroup: erzeugung
        line: { width: 0, shape: hv, color: "#ff7043" }
      - entity: sensor.stromnetz_deutschland_energy_charts_biomasse
        name: Biomasse
        stackgroup: erzeugung
        line: { width: 0, shape: hv, color: "#43a047" }
      - entity: sensor.stromnetz_deutschland_energy_charts_wasserkraft
        name: Wasserkraft
        stackgroup: erzeugung
        line: { width: 0, shape: hv, color: "#1e88e5" }
      - entity: sensor.stromnetz_deutschland_energy_charts_wind_auf_see
        name: Wind auf See
        stackgroup: erzeugung
        line: { width: 0, shape: hv, color: "#00838f" }
      - entity: sensor.stromnetz_deutschland_energy_charts_wind_an_land
        name: Wind an Land
        stackgroup: erzeugung
        line: { width: 0, shape: hv, color: "#4dd0e1" }
      - entity: sensor.stromnetz_deutschland_energy_charts_solar
        name: Solar
        stackgroup: erzeugung
        line: { width: 0, shape: hv, color: "#fdd835" }
      - entity: sensor.stromnetz_deutschland_energy_charts_netzlast
        name: Netzlast
        line: { width: 2, shape: hv, color: "#e53935" }
    layout:
      height: 340
      yaxis:
        title: GW
        rangemode: tozero
      legend:
        orientation: h
```

Hinweis: Home Assistant speichert die Werte zum Zeitpunkt des Abrufs. Weil Energy-Charts die neuesten Viertelstunden oft erst mit Verzug füllt, ist die Kurve gegenüber `data_time` um diesen Verzug nach rechts verschoben. Das gilt auch für die Frequenzkurve, die gegenüber `measured_at` rund 50 Minuten nach rechts versetzt ist. Der Import-Export-Saldo fehlt im Stapel absichtlich, weil er negativ werden kann.

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

## Changelog

### 1.1.2

- **Eine ausgefallene Datenquelle blockiert den Stromnetz-Eintrag nicht mehr.** Bisher landete der ganze Eintrag in „Einrichtung wird wiederholt“, wenn beim Start eine Quelle scheiterte, und alle Sensoren waren nicht verfügbar. Jetzt reicht eine funktionierende Quelle; nur wenn alle scheitern, wird die Einrichtung wiederholt.
- **Netzfrequenz ohne Daten:** HTTP 404 und leere Antworten führen nicht mehr zu einem Abruffehler. Die Werte bleiben bis zur Stale-Grenze gültig, danach sind die Frequenz-Entitäten nicht verfügbar und „Frequenz Datenalter“ zählt weiter, auch über einen Neustart hinweg.
- **Log:** pro Ausfall eine Warnung und bei der Rückkehr eine Info statt einer Meldung pro Abruf.
- **Reparaturen:** Fehlen Frequenzdaten länger als 6 Stunden, erscheint ein Hinweis „Energy-Charts liefert seit … keine Netzfrequenz“. Er verschwindet automatisch.
- **Diagnose:** pro Quelle letzter erfolgreicher Abruf, letzter Fehler mit Zeitpunkt und Zeitpunkt des neuesten Datenpunkts; bei der Frequenz zusätzlich der Ausfallzustand.
- Domain, eindeutige IDs, Entitäts-IDs und Optionen bleiben unverändert.

### 1.1.1

- **Netzfrequenz repariert:** Energy-Charts liefert die Frequenz mit rund 50 Minuten Verzug. Die bisherige Grenze von 15 Minuten machte deshalb alle Frequenz-Entitäten dauerhaft unavailable.
  - Die Stale-Grenze liegt jetzt bei 3 Stunden und ist in den Optionen einstellbar (30 min bis 12 h).
  - Minimum und Maximum beziehen sich auf die Stunde vor dem neuesten Messwert. Der Puffer hält 3 Stunden.
  - Der erste Abruf und der Abruf nach einem Neustart reichen mindestens 3 Stunden zurück.
  - Ein einzelner fehlgeschlagener Abruf macht die Frequenz-Entitäten nicht mehr unavailable.
- **Neu:** Sensor „Frequenz Datenalter“ (Diagnose) und Attribut `measured_at` am Binärsensor; Attribute `window_start`, `window_end` und `window_basis` an Minimum und Maximum.
- **Import-Export-Saldo:** Das Vorzeichen ist jetzt positiv = Export, negativ = Import (Energy-Charts selbst zählt umgekehrt). Neues Attribut `direction`. Achtung: In der Langzeitstatistik ändert sich damit ab dem Update das Vorzeichen.
- Strahlung (BfS), Netzampel und die übrige Erzeugung sind unverändert. Domain, eindeutige IDs und Entitäts-IDs bleiben gleich.

### 1.1.0

- Stromnetz (Energy-Charts) als zweite Datenquelle: Netzampel, Netzfrequenz, Last und Erzeugung.
- Neuer Anzeigename „Umwelt & Netz (BfS / Energy-Charts)“.

### 1.0.0

- Erste Version: Ortsdosisleistung des BfS.

## Entwicklung und Tests

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest
```

Die Tests nutzen `pytest-homeassistant-custom-component` mit gemockten Antworten von BfS und Energy-Charts. Sie prüfen den Menü-Flow, Optionen, alle Coordinators, die Fallbacks, Ausfälle und die Diagnose. Ein eigener Test stellt sicher, dass bestehende Sonden ihre eindeutigen IDs und Entitäts-IDs behalten.

## Datenquellen und Lizenzen

- Strahlung: Bundesamt für Strahlenschutz (BfS), [ODL-Info](https://odlinfo.bfs.de/), WFS-Dienst `https://www.imis.bfs.de/ogc/opendata/ows`. Es gelten die [Nutzungsbedingungen des BfS](https://odlinfo.bfs.de/ODL/EN/service/data-interface/data-interface_node.html).
- Stromnetz: Daten: Energy-Charts, Fraunhofer ISE, [api.energy-charts.info](https://api.energy-charts.info/), Lizenz [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

Diese Integration ist kein offizielles Produkt des BfS oder des Fraunhofer ISE.

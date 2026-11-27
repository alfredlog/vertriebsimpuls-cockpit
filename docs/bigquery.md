# BigQuery (optional)

DuckDB ist die Standard-Datenbank des Projekts (lokal, Tests, Dashboard). BigQuery
ist eine optionale „Prod“-Variante für dasselbe Sternschema. Die Begründung steht in
[ADR 0001](adr/0001-duckdb-and-bigquery.md).

## Einrichtung

```bash
uv sync --group bigquery
gcloud auth application-default login
```

Die Anmeldung läuft über Application Default Credentials. Es gibt keine
Schlüsseldateien im Repository; `.gitignore` schließt typische Schlüsselnamen aus.

```bash
# Sternschema laden (Datensatz "vic", Standort EU)
uv run --group bigquery python -m vic.bq load --project <projekt-id>
# im BigQuery-Sandbox-Modus (ohne Abrechnungskonto)
uv run --group bigquery python -m vic.bq load --project <projekt-id> --sandbox
# Bytes pro Beispielabfrage messen (Dry Run, kostenlos)
uv run --group bigquery python -m vic.bq dry-run --project <projekt-id>
# Bytes ohne Google-Konto berechnen (Schätzung, siehe unten)
uv run --group bigquery python -m vic.bq estimate
```

Tests mit gemocktem Client: `uv run --group bigquery pytest -m bigquery`. Der normale
Testlauf importiert keinen BigQuery-Code.

## Laden: Ladejobs statt DML

Jede Tabelle wird mit einem **Ladejob** (`load_table_from_dataframe`,
`WRITE_TRUNCATE`) vollständig ersetzt. Gründe:

- Die **BigQuery-Sandbox unterstützt keine DML** (`INSERT`, `UPDATE`, `DELETE`,
  `MERGE`) und **kein Streaming**. Ladejobs sind keine DML.
- Ein Neuladen ist idempotent: Jede Tabelle wird komplett überschrieben.
- Es gibt keine tabellenübergreifende Transaktion. Bricht ein Lauf ab, können
  Tabellen aus zwei Läufen stammen; ein erneuter Lauf behebt das.

Geldbeträge sind `NUMERIC`. Der Python-Client akzeptiert dafür nur `Decimal`-Werte;
mit `float` scheitert die Arrow-Konvertierung (`ArrowInvalid`). `to_bigquery_frame`
wandelt deshalb Beträge in `Decimal` und Datumswerte in `date` um.

## Partitionierung, Clustering und die Sandbox-Falle

Mit Abrechnungskonto wird `fact_trades` **monatlich nach `trade_date` partitioniert**
(ohne Ablaufzeit) und **nach `client_id` geclustert**.

In der Sandbox laufen Tabellen und Partitionen nach **60 Tagen** ab. Bei Tabellen mit
Datumsspalten-Partitionierung zählt die Ablaufzeit **ab dem Partitionsdatum**, nicht
ab dem Ladezeitpunkt. Partitionen, die beim Laden schon älter sind, gelten sofort als
abgelaufen. Unsere Geschäfte reichen zwei Jahre zurück: Eine partitionierte
`fact_trades` würde in der Sandbox fast alle Zeilen verlieren.

Deshalb gilt mit `--sandbox`:

- `fact_trades` wird **nicht partitioniert**, sondern nach `trade_date, client_id`
  geclustert. Die nach Datum sortierten Blöcke erlauben weiterhin Pruning bei
  Datumsfiltern, nur ohne exakte Kostenschätzung vorab.
- Alle Tabellen laufen nach 60 Tagen ab; danach erneut laden.
- Die Sandbox hat **10 GiB Speicher auf Lebenszeit**, der beim Löschen nicht
  zurückgegeben wird. Der Demo-Datensatz hat nur wenige MB, häufiges Neuladen ist
  trotzdem unnötig.

## SQL-Dialekt: Unterschiede DuckDB ↔ BigQuery in diesem Projekt

| Thema | DuckDB (Projekt) | BigQuery |
|---|---|---|
| Tage zwischen zwei Daten | `date_diff('day', start, ende)` | `DATE_DIFF(ende, start, DAY)` – **umgekehrte Reihenfolge** |
| Auf Monat kürzen | `date_trunc('month', d)` | `DATE_TRUNC(d, MONTH)` |
| Datum ± Tage | `as_of - 90`, `as_of + 30` | `DATE_SUB(@as_of, INTERVAL 90 DAY)`, `DATE_ADD(...)` |
| Datum − 1 Monat | `as_of - INTERVAL 1 MONTH` ergibt TIMESTAMP, daher `CAST(... AS DATE)` | `DATE_SUB(@as_of, INTERVAL 1 MONTH)` bleibt DATE |
| Parameter | `?` oder `$name` | `@name` (`ScalarQueryParameter`) |
| Wert zum Maximum | `arg_max(status, ts)` | `MAX_BY(status, ts)` |
| Bedingtes Zählen | `count(*) FILTER (WHERE ...)` | `COUNTIF(...)` |
| Aufzählungstypen | `CREATE TYPE ... AS ENUM` | gibt es nicht → `STRING`; erlaubte Werte prüfen die pandera-Checks |
| Geld | `DECIMAL(18,2)` | `NUMERIC` (Präzision 38, Skala 9) |
| Schlüssel | Primär- und Fremdschlüssel werden **erzwungen** | nur `NOT ENFORCED`; Integrität über Qualitätsprüfungen |
| Neu laden | `DROP SCHEMA vic CASCADE` + Inserts in **einer Transaktion** | Ladejobs `WRITE_TRUNCATE` je Tabelle, keine gemeinsame Transaktion |
| Namen | `catalog.schema.tabelle` (Datei als Katalog `vic_db` angehängt) | `projekt.datensatz.tabelle` |
| Top-N je Gruppe | `QUALIFY row_number() ...` | ebenfalls `QUALIFY` (eine Fensterfunktion muss vorkommen) |

## Wie BigQuery abrechnet

Bei der On-Demand-Abrechnung zählt die **Menge der verarbeiteten Bytes**, nicht die
Laufzeit und nicht die Zahl der Ergebniszeilen:

- BigQuery speichert spaltenweise. Gelesen werden nur die **verwendeten Spalten**;
  `SELECT *` liest alle. `LIMIT` reduziert die gelesenen Bytes nicht.
- Pro Abfrage gelten **mindestens 10 MB je referenzierter Tabelle und 10 MB je
  Abfrage**; aufgerundet wird auf volle MB.
- Kostenlos sind 1 TiB verarbeitete Daten pro Monat, auch in der Sandbox.
- Die Größe wird logisch berechnet: `INT64`, `DATE`, `FLOAT64` je 8 Byte, `NUMERIC`
  16 Byte, `BOOL` 1 Byte, `STRING` 2 Byte + UTF-8-Länge, `NULL` 0 Byte.

So senken wir die Kosten:

1. **Nur benötigte Spalten** auswählen.
2. **Partitionierung:** Ein Filter direkt auf `trade_date` (ohne Funktion um die
   Spalte) lässt BigQuery nur die Monatspartitionen des Zeitfensters lesen. Das zeigt
   schon der Dry Run vor der Ausführung.
3. **Clustering:** Innerhalb der Partitionen sind die Blöcke nach `client_id`
   sortiert; Filter auf Kunden lesen weniger Blöcke. Laut Dokumentation lässt sich
   dieser Effekt **vor** der Ausführung nicht genau schätzen; er zeigt sich erst in
   den tatsächlich verarbeiteten Bytes.
4. **Dry Run** vor teuren Abfragen: `QueryJobConfig(dry_run=True)` liefert
   `total_bytes_processed`, ohne etwas auszuführen oder abzurechnen.

## Beispielabfragen und verarbeitete Bytes

Abfragen in `src/vic/sql/bigquery/`, Stichtag `@as_of = 2026-11-20`, Datensatz mit
Seed 42 (300 Kunden, 7.898 Geschäfte, 7.036 Positionen):

| Abfrage | Tabellen | `SELECT *` | genutzte Spalten | + Partitionierung | mind. abgerechnet |
|---|---:|---:|---:|---:|---:|
| `volume_per_segment_month` | 2 | 380 KB | 246 KB | 127 KB | 20 MB |
| `upcoming_maturities` | 3 | 411 KB | 395 KB | 395 KB | 30 MB |
| `top_products_per_segment` | 3 | 381 KB | 293 KB | 150 KB | 30 MB |

**Wichtig:** Diese Werte sind **berechnet** (`python -m vic.bq estimate`, nach den
Datentypgrößen von BigQuery), **nicht gemessen**. Ein Dry Run braucht ein
Google-Cloud-Projekt mit geladenen Tabellen, das für diese Dokumentation nicht zur
Verfügung stand. Die gemessenen Werte liefert
`uv run --group bigquery python -m vic.bq dry-run --project <projekt-id>`.
Sie sollten nah an der Spalte „+ Partitionierung“ liegen (im Sandbox-Modus, ohne
Partitionen, eher an „genutzte Spalten“).

Was die Zahlen zeigen:

- **Spaltenauswahl:** Die Monatsvolumen-Abfrage liest 246 statt 380 KB (−35 %).
- **Partitionierung:** Das 12-Monats-Fenster halbiert die Bytes von `fact_trades`
  noch einmal (246 → 127 KB). Die Fälligkeiten-Abfrage profitiert nicht, weil
  `fact_positions` nicht partitioniert ist; eine Partitionierung nach
  `maturity_date` wäre der nächste Schritt.
- **Ehrlich gesagt:** Bei einem Datensatz dieser Größe bestimmt das **Minimum von
  10 MB je Tabelle** die Rechnung (20–30 MB statt weniger hundert KB). Die
  Optimierungen lohnen sich erst bei echten Datenmengen, ab Gigabytes; das Projekt
  zeigt das Muster, nicht die Ersparnis.

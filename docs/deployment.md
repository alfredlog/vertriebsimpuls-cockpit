# Deployment auf einem VPS

Ziel: das Cockpit unter `https://cockpit.a-fed.org` auf einem Ubuntu-VPS
betreiben. Docker Compose betreibt die App und den nächtlichen Pipeline-Job.
Caddy läuft direkt auf dem Host, holt das TLS-Zertifikat und leitet die
Anfragen an die App weiter.

```
Internet ──443──▶ Caddy (Host) ──▶ 127.0.0.1:8050 ──▶ Container "app" (gunicorn)
                                                         │
                         Volume vic-data:/data/vic.duckdb │
                                                         │
cron 03:15 ──▶ Container "pipeline" (läuft einmal, beendet sich) ──┘
```

## Architekturentscheidungen

**Ein gunicorn-Worker mit mehreren Threads (`--workers 1 --threads 4`).**
DuckDB erlaubt pro Datei nur einen Prozess. Ein zweiter Prozess, der die
Datei öffnet, scheitert mit `Could not set lock on file`. Jeder gunicorn-Worker
ist ein eigener Prozess, deshalb würden sich mehrere Worker gegenseitig
blockieren. Threads teilen sich einen Prozess. `app/data.py` lässt ihre
Abfragen nacheinander laufen. Bei einer Demo mit wenigen Nutzern reicht das.

**Kurzlebige Verbindungen.** Die App öffnet die Datenbank für jede Abfrage und
schließt sie sofort wieder. Das kostet etwa 15 ms pro Abfrage. Eine dauerhaft
offene Verbindung würde den nächtlichen Pipeline-Container aussperren.

**Was passiert bei einer Kollision?** Die Pipeline hält die Datei während
eines Laufs etwa 3–10 Sekunden.

- Fragt die App in dieser Zeit an, wartet sie bis zu 15 s auf die Datei
  (`LOCK_TIMEOUT_S` in `app/data.py`) und antwortet danach normal.
- Dauert es länger, schlägt die Abfrage fehl. Der Dash-Callback liefert dann
  HTTP 500, und die Seite zeigt keine neuen Daten. Ein Neuladen nach dem Lauf
  behebt das. Ein in dieser Zeit geklicktes Feedback geht verloren und muss
  erneut geklickt werden.
- Startet die Pipeline, während die App gerade eine Abfrage ausführt, wartet
  sie bis zu 30 s (`PIPELINE_LOCK_TIMEOUT_S`). Danach bricht sie mit einem
  Fehler ab, und cron schreibt ihn ins Log.
- Die Daten bleiben in jedem Fall konsistent. Das Neuladen läuft in einer
  Transaktion, und die Eingangsprüfungen laufen vor dem Laden. Ein
  abgebrochener Lauf lässt den Stand des Vortags stehen.

Der Job läuft um 03:15 Uhr, wenn kaum jemand das Cockpit nutzt.

**Healthcheck ohne Datenbank.** `/healthz` prüft nur, ob der Prozess antwortet.
Mit einer Datenbankabfrage würde ein langer Pipeline-Lauf den Container
fälschlich als „unhealthy“ markieren.

**Erster Start.** Fehlt `/data/vic.duckdb` im Volume, führt der Entrypoint die
Pipeline einmal aus, inklusive Recommender-Evaluation. Erst danach startet
gunicorn. Scheitert dieser Lauf, löscht der Entrypoint die halb geschriebene
Datei. Der nächste Containerstart versucht es dann erneut.

## 1. DNS

Beim DNS-Anbieter einen A-Record anlegen. Bei IPv6 zusätzlich einen
AAAA-Record:

| Typ  | Name      | Wert             |
|------|-----------|------------------|
| A    | `cockpit` | IPv4 des VPS     |
| AAAA | `cockpit` | IPv6 des VPS     |

So prüfen Sie, ob der Eintrag aktiv ist:

```bash
dig +short cockpit.a-fed.org
```

## 2. Docker auf Ubuntu installieren

Installieren Sie Docker aus dem offiziellen Repository von Docker. Das
Ubuntu-Paket `docker.io` enthält kein Compose v2.

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"   # danach neu anmelden
```

Firewall: Öffnen Sie nur SSH, HTTP und HTTPS. Port 8050 bleibt zu, denn
Compose bindet die App nur an `127.0.0.1`. Docker-Ports an `0.0.0.0`
würden ufw umgehen.

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443
sudo ufw enable
```

## 3. Repository klonen und starten

```bash
sudo mkdir -p /opt/vertriebsimpuls-cockpit
sudo chown "$USER" /opt/vertriebsimpuls-cockpit
git clone <repo-url> /opt/vertriebsimpuls-cockpit
cd /opt/vertriebsimpuls-cockpit
docker compose up -d --build
```

Der erste Start dauert etwas länger, weil die Pipeline einmal läuft. Danach
sollte der Container den Status „healthy“ haben:

```bash
docker compose ps
curl -fsS http://127.0.0.1:8050/healthz
```

## 4. Caddy einrichten

Caddy aus dem offiziellen Repository installieren (siehe
caddyserver.com/docs/install#debian-ubuntu-raspbian). Danach den Site-Block
aus `deploy/Caddyfile.example` in `/etc/caddy/Caddyfile` übernehmen. Den
Platzhalter-Block `:80`, falls vorhanden, entfernen.

```bash
sudo cp deploy/Caddyfile.example /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy holt das Let's-Encrypt-Zertifikat beim ersten Aufruf. Dafür müssen der
DNS-Eintrag aktiv und die Ports 80 und 443 offen sein.

```bash
curl -I https://cockpit.a-fed.org/healthz
```

Eine Content-Security-Policy setzt der Block bewusst nicht. Dash nutzt
Inline-Skripte, und Tailwind kommt per CDN. Eine CSP bräuchte deshalb
`'unsafe-inline'` und brächte wenig Schutz.

## 5. Nächtliche Pipeline per cron

Mit `crontab -e` folgende Zeile eintragen:

```
15 3 * * * cd /opt/vertriebsimpuls-cockpit && docker compose --profile jobs run --rm pipeline >> "$HOME/vic-pipeline.log" 2>&1
```

Der Job lädt die Daten neu, berechnet die Impulse und evaluiert die
Recommender. Der nächste Lauf verwendet dann das beste Modell. Danach beendet
sich der Container.

Der Stichtag ist standardmäßig `AS_OF_DEFAULT` (20.11.2026). Damit bleibt die
Demo reproduzierbar. Soll der Stichtag mitlaufen, hängen Sie
`--db /data/vic.duckdb --evaluate --as-of $(date +\%F)` an den Befehl an. In
crontab muss `%` als `\%` geschrieben werden.

So starten Sie den Job einmal von Hand:

```bash
docker compose --profile jobs run --rm pipeline
```

## 6. Update

```bash
cd /opt/vertriebsimpuls-cockpit
git pull
docker compose up -d --build
docker image prune -f
```

Die Datenbank liegt im Volume `vic-data` und bleibt bei einem Update erhalten.
Gibt es Schemaänderungen, laufen sie mit dem nächsten Pipeline-Lauf ein, denn
der Lauf baut das Schema `vic` neu auf. Wollen Sie das sofort, starten Sie den
Job von Hand.

Zum Zurücksetzen auf einen frischen Stand löschen Sie das Volume. Dabei gehen
das Feedback und die Evaluationshistorie verloren:

```bash
docker compose down -v
docker compose up -d --build
```

## 7. Logs

```bash
docker compose logs -f app          # gunicorn-Zugriffe und Fehler
docker compose logs --tail 100 app
tail -f ~/vic-pipeline.log          # nächtliche Pipeline
sudo journalctl -u caddy -f         # Caddy, Zertifikate
docker inspect --format '{{json .State.Health}}' vertriebsimpuls-cockpit-app-1
```

# Connexxion Groepsvervoer voor Home Assistant

Toon de ritten van het **Connexxion Groepsvervoer** (leerlingen- en cliëntenvervoer, het ouderportaal op [connexxiongroepsvervoer.nl](https://www.connexxiongroepsvervoer.nl)) in Home Assistant: instaptijd, aankomsttijd, status en op de dag zelf de chauffeur, het kenteken en de live verwachte tijden.

Connexxion heeft geen publieke API. Dit script logt in zoals de website dat doet en leest dezelfde gegevens uit. Het gebruikt alleen de Python-standaardbibliotheek, dus er hoeft niets extra geïnstalleerd te worden.

> Niet officieel, en niet verbonden aan Connexxion of Transdev. Als het portaal verandert, kan het script stoppen met werken.

## Installatie

1. Kopieer `connexxion/connexxion.py` naar `/config/connexxion/connexxion.py`.
2. Kopieer `packages/connexxion.yaml` naar je packages-map. Heb je nog geen packages? Zet dan in `configuration.yaml`:
   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```
3. Voeg je inloggegevens van het portaal toe aan `secrets.yaml`, links uitgelijnd zonder spaties ervoor:
   ```yaml
   connexxion_username: 'jouw@email.nl'
   connexxion_password: 'wachtwoord'
   ```
4. Test het script, bijvoorbeeld in de Terminal & SSH-add-on (`apk add python3 py3-yaml` als Python daar ontbreekt):
   ```bash
   python3 /config/connexxion/connexxion.py | grep -o '"status": "[^"]*"\|"fout": [^,}]*'
   ```
   Je hoort `"status": "ok"` te zien.
5. Herstart Home Assistant.

## Sensoren

| Entiteit | Inhoud |
|---|---|
| `sensor.connexxion_taxi` | `ok` of `fout`; alle ritten staan in de attributen (`vandaag`, `volgende`, `ritten`, `fout`, `bijgewerkt`) |
| `sensor.connexxion_taxi_ophalen` | Instaptijd van de rit van vandaag (live tijd als die er is), anders `Geen rit`. Attributen: gepland, van, status, chauffeur, kenteken |
| `sensor.connexxion_taxi_thuisbrengen` | Aankomsttijd van de rit van vandaag (live tijd als die er is), anders `Geen rit`. Attributen: gepland, naar, chauffeur, kenteken |
| `sensor.connexxion_taxi_volgende_rit` | Tijdstip (`timestamp`) van de eerstvolgende rit, met dag, datum, tijden, van/naar en status |

Elke rit in de attributen bevat onder meer `datum`, `dag`, `ophalen`, `aankomst`, `van`, `naar`, `status`/`status_tekst` en, op de dag zelf, `chauffeur`, `kenteken`, `live_ophalen`, `live_aankomst` en de positie van het voertuig (`voertuig_lat`, `voertuig_lon`).

## Verversen

De automatisering in de package haalt de planning elke ochtend om 06:30 op. Op een dag met een rit ververst ze daarnaast elke 5 minuten, van 45 minuten vóór het ophalen tot 15 minuten na de aankomst. Pas de tijden gerust aan in `packages/connexxion.yaml`.

Het script bewaart het inlogtoken in `/config/connexxion/.token.json` (alleen leesbaar voor de eigenaar), zodat het niet bij elke verversing opnieuw inlogt.

## Opties

```
python3 connexxion.py [--secrets PAD] [--klant ID] [--dagen N] [--cache PAD]
```

- `--secrets`: pad naar `secrets.yaml` (standaard `/config/secrets.yaml`)
- `--klant`: customer-ID van de reiziger, als er meerdere reizigers aan je account gekoppeld zijn (standaard de eerste; de ID's staan in de uitvoer onder `gekoppelde_reizigers`)
- `--dagen`: aantal werkdagen vooruit (standaard 14)
- `--cache`: waar het token bewaard wordt

## Problemen

- **Sensor is `unavailable`**: Home Assistant kon het commando niet uitvoeren. Kijk in Instellingen → Systeem → Logs bij `command_line`, en controleer of het script echt op `/config/connexxion/connexxion.py` staat.
- **`fout`: inloggen mislukt**: controleer `connexxion_username` en `connexxion_password` in `secrets.yaml`. Ze moeten links uitgelijnd staan.
- De uitvoer van het script bevat adressen. Deel die niet zomaar.

## Licentie

MIT, zie [LICENSE](LICENSE).

# 🎓 Praksa — studentske prakse u Hrvatskoj

Web aplikacija koja na jednom mjestu skuplja **studentske prakse i studentske poslove iz Hrvatske**, omogućuje pretragu po gradu i području te šalje **obavijest na mail** kad se pojavi novi oglas koji odgovara spremljenoj pretrazi. Tvrtke mogu i same besplatno objaviti praksu, a objava ide nakon odobrenja.

## Izvori oglasa (samo Hrvatska)

Koriste se samo izvori koji svoje oglase objavljuju za strojno čitanje. Svaki oglas vodi na izvornu objavu.

| Izvor | Kako | Što daje |
|---|---|---|
| **Studentski centar Zagreb** | javni WordPress REST API (`/wp-json/wp/v2/jobs`) | oko 600 aktivnih studentskih poslova: tvrtka, adresa, satnica, razdoblje rada, rok prijave, opis |
| **Studentski centar Dubrovnik** | javni WordPress REST API (`/wp-json/wp/v2/student_poslovi`) | studentski poslovi iz zadnjih 45 dana (samo naslov i kategorija) |
| **Posao.hr** | službeni RSS feed | najnoviji oglasi, filtrirani na prakse i oglase za studente |
| **Objave tvrtki** | forma na stranici | prakse iz cijele Hrvatske, objavljuju se nakon odobrenja |

Oglasi se dohvaćaju svakih 60 min (`FETCH_INTERVAL_MINUTES`). Oglasi kojima je istekao rok prijave ili koje je SC Zagreb maknuo automatski se skrivaju.

Svaki oglas dobiva:
- **vrstu**: *Praksa* (praksa, stručna praksa, intern, trainee…) ili *Studentski posao*
- **grad**: prepoznaje se iz adrese
- **područje**: IT, Marketing, Administracija, Trgovina, Ugostiteljstvo i turizam, Skladište/proizvodnja, Instrukcije, Ostalo

## Funkcije

- **Pretraga**: ključne riječi, grad, područje, vrsta, izvor, rad od kuće. Prakse se prikazuju prve.
- **Obavijesti na mail s potvrdom (double opt-in)**: student dobije link za potvrdu, a tek nakon potvrde prima obavijesti. Svaki mail ima link za odjavu.
- **Objava za tvrtke uz moderaciju**: oglas nije javan dok ga admin ne odobri. Admin dobije mail s linkom na stranicu s gumbima *Odobri* i *Odbij*. Odluka ide preko POST-a, pa je skeneri linkova u mailu ne mogu slučajno odobriti. Tvrtka dobije potvrdu primitka i obavijest o objavi.

## Pokretanje lokalno

```
cd praksa/backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # upiši barem ADMIN_EMAIL i ADMIN_TOKEN
uvicorn main:app --port 8002
```
Otvori http://127.0.0.1:8002. Prvo dohvaćanje traje 10-20 sekundi.

Bez email ključeva aplikacija radi u **dev načinu**: mailovi (i linkovi za potvrdu i odobravanje) zapisuju se u `backend/dev_emails.log`. Za pravo slanje dodaj `RESEND_API_KEY` ([resend.com](https://resend.com), besplatno) ili SMTP podatke.

## API

| Metoda | Putanja | Opis |
|---|---|---|
| GET | `/api/internships?q=&city=&category=&kind=praksa\|studentski&remote=true&source=&page=` | pretraga |
| GET | `/api/meta` | gradovi, područja, izvori, brojke |
| POST | `/api/internships` | objava tvrtke (čeka odobrenje) |
| POST | `/api/alerts` | nova obavijest (šalje mail za potvrdu) |
| GET | `/api/alerts/confirm/{token}`, `/api/alerts/unsubscribe/{token}` | potvrda / odjava |
| GET/POST | `/api/admin/moderate/{token}` | pregled / odobravanje oglasa (link iz maila) |
| GET | `/api/admin/pending` | oglasi koji čekaju (header `X-Admin-Token`) |
| POST | `/api/admin/fetch` | ručno dohvaćanje (header `X-Admin-Token`) |

## Što još nedostaje za više hrvatskih oglasa

1. **Ostali studentski centri** (Split, Rijeka, Osijek, Zadar, Pula, Varaždin) nemaju javni API ili ga nisam našao. Dodavanje novog izvora svodi se na jednu funkciju u `sources.py`.
2. **MojPosao i studentski-poslovi.hr** najveći su oglasnici, ali nemaju javni feed. Scrapanje bi bilo krhko i vjerojatno protivno njihovim uvjetima, pa je bolje zatražiti partnerstvo ili feed.
3. **Karijerni centri fakulteta** (npr. FER, EFZG, FFZG) često objavljuju prakse. Isplati ih se kontaktirati da objavljuju i ovdje ili da daju RSS.
4. **Zaštita od spama**: za javno pokretanje dodaj rate-limit ili captchu na forme.
5. **Deploy**: `Procfile` je spreman (Render/Fly.io, root direktorij `praksa/backend`). Za produkciju prebaci SQLite na Postgres.

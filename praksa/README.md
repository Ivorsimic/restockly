# 🎓 Praksa — studentske prakse u Hrvatskoj

Tražilica **isključivo studentskih praksi u Hrvatskoj**. Ima tri dijela:

1. **Otvorene prakse**: oglasi koji su trenutno otvoreni, automatski prikupljeni iz više izvora.
2. **Programi praksi**: ručno provjeren katalog godišnjih programa (HNB, HANFA, Infobip, ENT Summer Camp, IAESTE…) s oznakom kada se obično prijavljuje.
3. **Obavijesti na mail**: za nove prakse i kad se otvore prijave za programe iz tvog područja.

## Zašto ovo ne postoji

Istraženo u studenom 2026.:
- **Studentski.hr** prakse objavljuje kao članke, bez pretrage i filtera.
- **DING** i **Starter (eSTUDENT)** sadrže samo oglase svojih partnera.
- **MojPosao** ima filter "Praksa", ali miješa prakse s ostalim poslovima.
- Inicijativa HUP-a i EBRD-a te MladiRade više ne rade.
- Fakulteti vode svaki svoj popis.

Nitko ne skuplja prakse iz svih izvora i ne prati kada se otvaraju godišnji programi.

## Izvori otvorenih praksi

Koriste se samo izvori koji to dopuštaju, a svaki oglas vodi na izvornu objavu.

| Izvor | Kako | Napomena |
|---|---|---|
| **Infobip** | javni Workday API stranice za zapošljavanje, filter Hrvatska | ~15 praksi (Zagreb, Split, Rijeka, Vodnjan) |
| **Rimac Group** | stranica karijera | internshipi u Zagrebu |
| **Studentski centar Zagreb** | javni WordPress API | samo oglasi koji su prakse |
| **Posao.hr** | službeni RSS | samo prakse; feed ima zadnjih 30 oglasa |
| **FESB, EFOS, Sveučilište u Rijeci** | RSS kategorija "Prakse" / "Karijere" | obavijesti fakulteta o praksama, vidljive 120 dana |
| **Objave tvrtki** | forma na stranici | objavljuju se nakon odobrenja admina |

**Namjerno se NE preuzimaju** MojPosao i Studentski.hr: njihovi uvjeti korištenja izričito zabranjuju prenošenje sadržaja bez pisane suglasnosti. Za njih treba dogovoriti partnerstvo.

### Kako dodati novu tvrtku

- **Workday** (npr. Infobip): na stranici karijera tvrtke pronađi link oblika `https://<tenant>.wdN.myworkdayjobs.com/en-US/<Site>/job/...` i dodaj redak u `WORKDAY_EMPLOYERS` u `backend/sources.py`. Filter za Hrvatsku pronalazi se automatski.
- **RSS fakulteta**: dodaj redak u `FACULTY_FEEDS`.
- **Bilo koji drugi izvor**: jedna funkcija u `sources.py` koja vraća listu oglasa, plus redak u `FETCHERS`.

## Katalog programa praksi (`backend/programs.json`)

Za svaki program se bilježi: organizacija, opis, područja, gradovi, plaćeno ili ne, trajanje, uvjeti, mjeseci prijava (`window_months`: lista mjeseci, `"all"` ili `null`), napomena o rokovima, službeni link i **izvori podataka**. Status se računa iz mjeseca u godini: "otvoreno sada", "uskoro", "tijekom cijele godine" ili "obično u…".

Kad mjesec prijava za program počne, svi pretplatnici čiji kriteriji odgovaraju programu dobiju **jednu** obavijest godišnje.

Katalog treba održavati: jednom godišnje provjeri rokove i dodaj nove programe. Svaki unos mora imati izvor.

## Pokretanje lokalno

```
cd praksa/backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # upiši barem ADMIN_EMAIL i ADMIN_TOKEN
uvicorn main:app --port 8002
```
Otvori http://127.0.0.1:8002. Prvo dohvaćanje traje oko 30 sekundi.

Bez email ključeva aplikacija radi u **dev načinu**: mailovi (s linkovima za potvrdu i odobravanje) zapisuju se u `backend/dev_emails.log`. Za pravo slanje dodaj `RESEND_API_KEY` ([resend.com](https://resend.com)) ili SMTP podatke.

## Funkcije

- Pretraga otvorenih praksi po ključnim riječima, gradu, području, izvoru, plaćenosti i radu od kuće
- Istekli oglasi i oni koje je izvor maknuo automatski se skrivaju. Ako izvor privremeno ne radi, njegovi oglasi ostaju vidljivi.
- Obavijesti na mail s potvrdom pretplate (double opt-in) i odjavom jednim klikom
- Objave tvrtki se prikazuju tek nakon odobrenja. Admin dobije link na stranicu s gumbima *Odobri* i *Odbij*; odluka ide preko POST-a, pa je skeneri linkova ne mogu slučajno odobriti.

## API

| Metoda | Putanja | Opis |
|---|---|---|
| GET | `/api/internships?q=&city=&category=&paid=true&remote=true&source=&page=` | otvorene prakse |
| GET | `/api/programs` | katalog programa sa statusom |
| GET | `/api/meta` | gradovi, područja, izvori, brojke |
| POST | `/api/internships` | objava tvrtke (čeka odobrenje) |
| POST | `/api/alerts` | nova obavijest (šalje mail za potvrdu) |
| GET | `/api/alerts/confirm/{token}`, `/api/alerts/unsubscribe/{token}` | potvrda / odjava |
| GET/POST | `/api/admin/moderate/{token}` | pregled / odobravanje oglasa |
| GET | `/api/admin/pending`, POST `/api/admin/fetch` | header `X-Admin-Token` |

## Sljedeći koraci

1. **Više tvrtki na Workdayu i sličnim sustavima.** Za svaku veliku tvrtku koja zapošljava studente u Hrvatskoj treba pronaći koji sustav koristi. Svaka dodana tvrtka su nove prakse bez ručnog rada.
2. **Proširiti katalog** na 50 i više programa: Big 4, A1, INA, Podravka, Pliva, Končar, ministarstva, EU institucije za studente.
3. **Partnerstva**: MojPosao, DING, studentski-poslovi.hr i karijerni centri fakulteta za feed praksi.
4. **Fakulteti**: zamoliti karijerne centre da objavljuju ovdje ili daju RSS svoje kategorije praksi.
5. **Deploy**: `Procfile` je spreman (Render/Fly.io, root `praksa/backend`); za produkciju prebaci SQLite na Postgres i dodaj rate-limit na forme.

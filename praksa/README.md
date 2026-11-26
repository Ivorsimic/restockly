# 🎓 Praksa — tražilica studentskih praksi

Web aplikacija koja na jednom mjestu skuplja **studentske prakse i studentske poslove**, omogućuje pretragu i filtriranje, te šalje **obavijest na mail** kad se pojavi nova praksa koja odgovara spremljenoj pretrazi. Tvrtke mogu i same besplatno objaviti praksu.

## Što radi

- **Automatsko prikupljanje oglasa** svakih 60 min (podesivo) iz besplatnih javnih API-ja:
  - [Arbeitnow](https://www.arbeitnow.com) (Europa, uglavnom DE/FR/NL, najviše praksi)
  - [Remotive](https://remotive.com) (remote, poziva se najviše svakih 6 h, kako izvor traži)
  - [Remote OK](https://remoteok.com) (remote; uvjeti korištenja traže link natrag, a on je u podnožju stranice)
- **Prepoznavanje praksi**: od svih oglasa zadržavaju se samo oni namijenjeni studentima (intern, internship, praksa, praktikant, Praktikum, trainee, stage/alternance, Werkstudent, working student, studentski…). Svaki oglas označava se kao *Praksa* ili *Studentski posao*.
- **Pretraga**: ključne riječi, lokacija, vrsta, izvor, samo remote, s paginacijom.
- **Obavijesti na mail**: student unese mail i kriterije te dobiva sažetak novih oglasa. Svaki mail ima link za odjavu.
- **Objava prakse** za tvrtke (forma, bez registracije). Objavljena praksa odmah ide studentima s odgovarajućim obavijestima.

## Pokretanje lokalno

```
cd praksa/backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # opcionalno
uvicorn main:app --port 8002
```
Otvori http://127.0.0.1:8002. Prvo dohvaćanje oglasa traje desetak sekundi.

Bez email ključeva aplikacija radi u **dev načinu**: mailovi se ne šalju nego zapisuju u `backend/dev_emails.log`. Za pravo slanje dodaj `RESEND_API_KEY` ([resend.com](https://resend.com), besplatno) ili SMTP podatke u `.env`.

## API

| Metoda | Putanja | Opis |
|---|---|---|
| GET | `/api/internships?q=&location=&kind=praksa\|studentski&remote=true&source=&page=` | pretraga |
| POST | `/api/internships` | objava prakse (tvrtke) |
| POST | `/api/alerts` | uključi obavijest na mail |
| GET | `/api/alerts/unsubscribe/{token}` | odjava |
| GET | `/api/stats` | broj oglasa po izvoru, zadnje dohvaćanje |
| POST | `/api/admin/fetch` | ručno dohvaćanje (header `X-Admin-Token` = `ADMIN_TOKEN`) |

## Ograničenja i sljedeći koraci

1. **Hrvatski oglasi**: javni API-ji imaju uglavnom oglase iz zapadne Europe i remote oglase. Hrvatske stranice (MojPosao, Posao.hr, studentski-poslovi.hr, studentski centri) nemaju javni API, a scrapanje je krhko i vjerojatno protivno njihovim uvjetima. Bolje ih je kontaktirati za partnerstvo/feed. Dotad oglase iz Hrvatske pokriva forma "Objavi praksu", pa se isplati obratiti tvrtkama i fakultetima (karijerni centri).
2. **Moderacija**: objave tvrtki odmah su javne. Za javno pokretanje dodaj potvrdu maila ili ručno odobravanje (stupac `approved` već postoji).
3. **Potvrda pretplate (double opt-in)** za obavijesti, da netko ne može upisati tuđi mail.
4. Komercijalna upotreba Arbeitnow API-ja po njihovim uvjetima zahtijeva dogovor s njima.
5. Deploy: isto kao Restockly (Render/Fly.io, `Procfile` je spreman, root direktorij `praksa/backend`). SQLite se na besplatnom Renderu briše pri redeployu, pa za produkciju treba prijeći na Postgres.

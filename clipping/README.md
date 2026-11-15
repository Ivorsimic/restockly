# Clipping – kompletan starter kit

Sve što ti treba da kreneš s clippingom: **alat koji automatski reže klipove** iz dugih videa, **vodič kako se zarađuje** i **tablica za praćenje zarade**.

```
clipping/
├── clipper.py        ← alat: link/video → gotovi vertikalni klipovi s titlovima
├── setup.sh          ← instalacija (Mac/Linux)
├── setup.bat         ← instalacija (Windows)
├── requirements.txt
└── tracker.csv       ← tablica za praćenje objava, pregleda i isplata (otvori u Excelu/Google Sheets)
```

---

## 1. Što je clipping i kako se zarađuje

Kreatori (streameri, podcasteri, poduzetnici, glazbenici, aplikacije) plaćaju ljude da iz njihovog dugog sadržaja izrežu kratke klipove i objave ih na **TikTok, Instagram Reels i YouTube Shorts**. Plaćen si **po pregledima** – obično nekakav iznos po 1000 pregleda (CPM). Česti rasponi su otprilike **0,50 – 3 $ na 1000 pregleda**, ali svaka kampanja ima svoje uvjete, minimum pregleda i maksimalnu isplatu – uvijek pročitaj pravila.

**Primjer računice:** klip s 200.000 pregleda × 1 $ / 1000 = 200 $. Većina klipova dobije malo pregleda – zarada dolazi od *volumena* (puno klipova) i nekoliko klipova koji "puknu".

### Gdje naći kampanje
- **Whop → Content Rewards** – najveće tržište clipping kampanja; prijaviš se, predaš link objave, isplata po pregledima.
- **Discord serveri** pojedinih kreatora / clipping agencija – često imaju vlastite kampanje.
- **Direktno** – javi se manjim kreatorima (podcasti, YouTuberi) i ponudi da im radiš klipove za fiksnu cijenu ili postotak.

> ⚠️ **Klipaj samo sadržaj za koji imaš dopuštenje** (aktivna kampanja ili dogovor s kreatorom). Tuđi sadržaj bez dopuštenja = skidanje videa i ban računa zbog autorskih prava, i nitko te neće platiti.

---

## 2. Postavljanje računa (dan 1)

1. **Napravi nove, posebne račune** na TikToku, Instagramu i YouTubeu – jedan "niche" po računu (npr. samo jedan podcast ili jedna tema). Ime u stilu `@imekreatora.clips` ili tematsko.
2. **Profilna, bio, link** – izgledaj kao fan stranica, ne spam.
3. **Zagrijavanje računa (3–5 dana):** prije prve objave 15–30 min dnevno skrolaj i lajkaj sadržaj iz svoje niše, tako algoritam shvati kome te pokazati.
4. **Jedan uređaj = mali broj računa.** Ne radi 20 računa s istog mobitela/IP-a.
5. TikTok: postavi regiju/publiku na tržište kampanje (najčešće SAD/engleski govorno područje) – kampanje često broje samo određene zemlje, provjeri pravila.

---

## 3. Instalacija alata (jednom)

**Windows:** instaliraj [Python 3.10+](https://www.python.org/downloads/) (označi *"Add Python to PATH"*), pa dvoklik na `setup.bat`.

**Mac / Linux:**
```bash
cd clipping
bash setup.sh
```

### AI odabir momenata (preporučeno)
Bez ključa alat bira momente jednostavnom heuristikom. S Claude API ključem bira *puno* bolje (traži hook u prve 3 sekunde, payoff, samostalnu cjelinu) i piše opis + hashtagove.

1. Napravi ključ na [console.anthropic.com](https://console.anthropic.com) (plaća se po korištenju – jedan sat podcasta je obično nekoliko centi do desetak centi).
2. Postavi ga:
   - Mac/Linux: `export ANTHROPIC_API_KEY=sk-ant-...`
   - Windows (CMD): `setx ANTHROPIC_API_KEY sk-ant-...` pa ponovno otvori CMD.

---

## 4. Korištenje

```bash
# aktiviraj okruženje (svaki put kad otvoriš terminal)
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# YouTube / Twitch / Kick VOD link → 5 klipova
python clipper.py "https://www.youtube.com/watch?v=XXXX"

# lokalni file, 8 klipova od 20 do 45 sekundi
python clipper.py podcast.mp4 --clips 8 --min 20 --max 45

# jedna osoba u kadru → izreži sredinu umjesto zamućene pozadine
python clipper.py intervju.mp4 --style crop

# hrvatski govor, precizniji (sporiji) model transkripcije
python clipper.py video.mp4 --language hr --whisper-model medium
```

Rezultat je u `output/<ime-videa>/`:
- `clip_01.mp4`, `clip_02.mp4` … – 1080×1920, 30 fps, titlovi riječ-po-riječ (aktivna riječ žuta), hook naslov na vrhu prve 3,5 s, normaliziran zvuk
- `clip_01.txt` … – opis + hashtagovi spremni za copy-paste
- `PREGLED.md` – svi klipovi s ocjenom i obrazloženjem

Transkript se sprema u `work/`, pa ponovno pokretanje na istom videu (npr. s drugim `--style`) ne transkribira ispočetka.

| Opcija | Što radi |
|---|---|
| `--clips N` | broj klipova (default 5) |
| `--min / --max` | trajanje klipa u sekundama (default 20–60) |
| `--style blur\|crop` | `blur`: cijeli kadar + zamućena pozadina (više ljudi u kadru); `crop`: izrez sredine |
| `--language hr` | jezik govora (default auto) |
| `--whisper-model` | `tiny`/`base`/`small`/`medium`/`large-v3` – veći = točniji titlovi, sporije |
| `--no-ai` | bez Claude-a, samo heuristika |
| `--words 3` | broj riječi u liniji titla |
| `--color yellow` | boja aktivne riječi: yellow/green/red/cyan |
| `--font "Arial Black"` | font titlova (mora biti instaliran; dobri: Montserrat Black, Arial Black, Impact) |
| `--no-subs`, `--no-hook` | isključi titlove / hook naslov |
| `--keep-ass` | zadrži `.ass` datoteku titlova da ih ručno ispraviš |

> Alat napravi 80% posla. **Uvijek pogledaj klip prije objave** – Whisper ponekad krivo čuje ime ili riječ. Za finalno poliranje (zoom, emoji, glazba) možeš klip ubaciti u CapCut.

---

## 5. Što čini klip viralnim

1. **Hook u prve 1–2 sekunde** – kontroverzna izjava, pitanje, broj, emocija. Nikad ne počinji s "pa…", "dakle…" ili pozdravom.
2. **Samostalna priča** – gledatelj mora razumjeti bez konteksta i dobiti *payoff* (odgovor, poantu, punchline).
3. **Reži odmah nakon poante** – kraći klip = veći postotak odgledanog = više pregleda.
4. **Titlovi uvijek** – većina gleda bez zvuka.
5. **Hook tekst na vrhu** koji izaziva znatiželju ("Zašto nitko ne priča o ovome").
6. **Opis** – 1 rečenica + 3–6 hashtagova iz niše.

---

## 6. Dnevna rutina

| Kad | Što |
|---|---|
| Jutro (30 min) | provjeri kampanje, odaberi 1–2 nova videa kreatora |
| | `python clipper.py <link> --clips 10` |
| (20 min) | pogledaj klipove, izbaci slabe, ispravi greške |
| Kroz dan | objavi **2–4 klipa po računu dnevno**, razmak 2–3 h; najbolje vrijeme je kad je publika kampanje budna (za SAD: ~15–03 h po našem) |
| Večer (15 min) | upiši linkove i preglede u `tracker.csv`, predaj objave u kampanju |
| Tjedno | pogledaj koji klipovi su prošli najbolje i radi više takvih (tema, dužina, stil hooka) |

**Realna očekivanja:** prvih 1–3 tjedna računi se "probijaju" i pregledi su mali. Ne odustaj nakon 10 klipova – konzistentnost (100+ klipova) je ono što donosi rezultat.

---

## 7. Isplate i porez (Hrvatska)

- Isplate idu najčešće preko Whopa / PayPala / Stripea / kripta – postavi račun na vrijeme.
- U `tracker.csv` vodi svaku isplatu – trebat će ti za porez.
- Prihod od clippinga je oporeziv. Za manje iznose to je obično "drugi dohodak", a kad postane redovan – razmisli o **paušalnom obrtu**. Javi se knjigovođi ili Poreznoj upravi da ti kažu točno što za tvoj slučaj.

---

## 8. Česti problemi

| Problem | Rješenje |
|---|---|
| `'ffmpeg' nije instaliran` | ponovno pokreni setup; na Windowsu zatvori i otvori CMD nakon instalacije |
| Skidanje s YouTubea ne radi | `pip install -U yt-dlp` (YouTube često mijenja stvari, yt-dlp se brzo ažurira) |
| Transkripcija je spora | `--whisper-model base` ili `tiny`; na računalu s NVIDIA karticom automatski koristi GPU |
| Krivi titlovi | `--whisper-model medium` i `--language` točno postavljen; ili `--keep-ass` pa ispravi tekst |
| Font izgleda obično | instaliraj npr. Montserrat (Google Fonts) i `--font "Montserrat Black"` |
| Klip počinje usred rečenice | povećaj `--min`/`--max` ili koristi AI odabir (ključ) |

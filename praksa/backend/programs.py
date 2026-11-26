"""Curated catalogue of recurring internship programmes (programs.json).

Each programme has an application window: a list of months (1-12) when applications usually open,
"all" for year-round applications, or null when the timing is unknown.
"""
import json
from datetime import date
from pathlib import Path

PROGRAMS_PATH = Path(__file__).with_name("programs.json")
MONTHS = ["siječanj", "veljača", "ožujak", "travanj", "svibanj", "lipanj", "srpanj", "kolovoz", "rujan",
          "listopad", "studeni", "prosinac"]
MONTHS_GEN = ["siječnja", "veljače", "ožujka", "travnja", "svibnja", "lipnja", "srpnja", "kolovoza", "rujna",
              "listopada", "studenog", "prosinca"]


def load() -> list[dict]:
    return json.loads(PROGRAMS_PATH.read_text(encoding="utf-8"))


def status(program: dict, today: date | None = None) -> dict:
    """{'code': open|soon|all|closed|unknown, 'label': text for the UI, 'months_until': int|None}"""
    today = today or date.today()
    w = program.get("window_months")
    if w == "all":
        return {"code": "all", "label": "Prijave tijekom cijele godine", "months_until": 0}
    if not w:
        return {"code": "unknown", "label": "Termin prijava: prati službenu stranicu", "months_until": None}
    if today.month in w:
        return {"code": "open", "label": "Prijave obično otvorene sada", "months_until": 0}
    ahead = min((m - today.month) % 12 for m in w)
    first = (today.month + ahead - 1) % 12
    if ahead <= 3:
        return {"code": "soon", "label": f"Prijave obično od {MONTHS_GEN[first]} (za ~{ahead} mj.)", "months_until": ahead}
    span = ", ".join(MONTHS[m - 1] for m in w)
    return {"code": "closed", "label": f"Prijave obično: {span}", "months_until": ahead}


def with_status(today: date | None = None) -> list[dict]:
    order = {"open": 0, "all": 1, "soon": 2, "unknown": 3, "closed": 4}
    items = [{**p, "status": status(p, today)} for p in load()]
    items.sort(key=lambda p: (order[p["status"]["code"]], p["status"]["months_until"] or 0, p["organization"]))
    return items

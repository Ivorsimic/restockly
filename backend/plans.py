PLANS = {
    "free": {
        "label": "Free",
        "watch_limit": 3,
        "check_interval_minutes": 60,
        "price_eur": 0,
        "stripe_price_id": None,
        "channels": ["email"],
    },
    "pro": {
        "label": "Pro",
        "watch_limit": 25,
        "check_interval_minutes": 5,
        "price_eur": 8,
        "stripe_price_id": "price_1UXAXhBV9EEiAUD0tOD2fbQb",
        "channels": ["email"],
    },
    "speed": {
        "label": "Speed",
        "watch_limit": 999,
        "check_interval_minutes": 1,
        "price_eur": 22,
        "stripe_price_id": "price_1UXAYqBV9EEiAUD0Vq7yXhHg",
        "channels": ["email", "webhook"],
    },
}

DEFAULT_PLAN = "free"


def get_plan(plan_id: str) -> dict:
    return PLANS.get(plan_id, PLANS[DEFAULT_PLAN])

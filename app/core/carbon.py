"""Hard-coded carbon model for the Impact Wallet + net-carbon gate.

Anchored to ~15M metric tons of CO2 from US product returns/yr (Optoro /
reverse-logistics research). Per-channel values are demo estimates anchored to
that framing. See plan.md §7 "Carbon model".
"""

# kg CO2e saved vs baseline (new purchase + warehouse return + restock)
CO2_SAVED_KG_BY_CHANNEL: dict[str, float] = {
    "exchange": 1.8,
    "rescue": 2.4,
    "p2p_resale": 3.1,
    "refurb": 2.0,
    "donate": 1.5,
    "recycle": 0.6,
    "restock": 0.0,
}

# kg CO2 per km of last-mile delivery (light vehicle).
DELIVERY_CO2_PER_KM: float = 0.12

# Green credits awarded per kg CO2 saved (demo economy).
CREDITS_PER_KG_CO2: float = 10.0


def credits_for_co2(co2_saved_kg: float) -> float:
    return round(max(0.0, co2_saved_kg) * CREDITS_PER_KG_CO2, 2)


def net_co2_saved(channel: str, delivery_km: float = 0.0) -> float:
    """Net CO2 saved for a disposition, subtracting last-mile delivery cost."""
    base = CO2_SAVED_KG_BY_CHANNEL.get(channel, 0.0)
    return round(base - (delivery_km * DELIVERY_CO2_PER_KM), 3)

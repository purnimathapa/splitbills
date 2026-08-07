from flask import current_app


def fetch_live_rate(base_currency: str = "INR", target_currency: str = "USD") -> float:
    """Fetch a live conversion rate from Config.EXCHANGE_API_BASE."""
    base = current_app.config.get("EXCHANGE_API_BASE")
    if not base:
        raise RuntimeError("No exchange API base configured")

    try:
        import importlib
        requests = importlib.import_module("requests")
    except Exception:
        raise RuntimeError("requests library not available")

    url = f"{base.rstrip('/')}/latest"
    params = {"base": base_currency, "symbols": target_currency}
    resp = requests.get(url, params=params, timeout=5)
    resp.raise_for_status()
    data = resp.json()
    rate = data.get("rates", {}).get(target_currency)
    if rate is None:
        raise RuntimeError("Rate not found in response")
    return float(rate)

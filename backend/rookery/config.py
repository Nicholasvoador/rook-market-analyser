"""Settings (JSON, non-secret) + secrets (env file, chmod 600). Both live in ~/.config/rookery.

Nothing personal is hard-coded: name, timezone, home currency and wallets live in local settings / the local DB.
"""
import copy
import json
import os
import threading
from datetime import datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

CONFIG_DIR = Path(os.environ.get("ROOKERY_CONFIG", Path.home() / ".config" / "rookery"))
DATA_DIR = Path(os.environ.get("ROOKERY_DATA", Path.home() / ".local" / "share" / "rookery"))
SETTINGS_FILE = CONFIG_DIR / "settings.json"
SECRETS_FILE = CONFIG_DIR / "secrets.env"
DB_FILE = DATA_DIR / "rookery.db"
MODEL_DIR = DATA_DIR / "models"
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
APP_NAME = "Rook Market Analyser"

for d in (CONFIG_DIR, DATA_DIR, MODEL_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Keys the UI may set. Values never leave the backend (GET returns only "is set").
SECRET_KEYS = {
    "HERMES_API_URL": "Hermes API server URL",
    "HERMES_API_KEY": "Hermes API server key",
    "ELFA_API_KEY": "Elfa API key (X/Telegram mindshare, smart mentions, narratives)",
    "SOLANA_RPC_URL": "Custom Solana RPC URL (optional; Helius/QuickNode URLs usually embed a key)",
    "COINGECKO_API_KEY": "CoinGecko demo/pro key (optional, raises rate limits)",
    "CRYPTOPANIC_API_KEY": "CryptoPanic key (optional, curated crypto news votes)",
    "BRAPI_TOKEN": "brapi.dev token (optional, more B3 tickers + history)",
}

DEFAULTS = {
    "profile": {
        "name": "",            # how the AI addresses you; empty = "you"
        "timezone": "",        # IANA name; empty = system timezone
        "home_currency": "USD",  # USD or BRL (adds a BRL column / conversions)
    },
    "ai": {
        "primary": {"provider": "", "model": "", "reasoning_effort": "high"},  # empty = Hermes default
        "fallback": {"provider": "antigravity-subscription-directsdk", "model": "gemini-3.8-flash",
                     "reasoning_effort": "max"},
        "expert_effort": "max",
        "timeout_s": 120,
        "first_token_timeout_s": 60,
        "cli_fallback": True,
        "auto_brief_hours": 6,  # AI market brief cadence (0 = off)
        "style": "standard",    # standard | plain (explain jargon, simpler wording)
    },
    "display": {"currency": "USD", "show_brl": False, "density": "comfortable", "accent": "#ff8a3d",
                "ui_scale": 1.15, "high_contrast": False, "reduce_motion": False},
    "watchlist": {
        "crypto": ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "HYPE", "SUI", "LINK", "AVAX", "TON", "ADA"],
        "stocks": ["SPY", "QQQ", "NVDA", "AAPL", "MSFT", "TSLA", "COIN", "MSTR", "PETR4.SA", "VALE3.SA"],
        "dex": [],  # on-chain tokens not listed on CEXs: [{"chain": "solana", "mint", "sym", "name"}]
    },
    "macro": [
        {"sym": "^GSPC", "label": "S&P 500"}, {"sym": "^NDX", "label": "Nasdaq 100"},
        {"sym": "^VIX", "label": "VIX"}, {"sym": "DX-Y.NYB", "label": "DXY"},
        {"sym": "^TNX", "label": "US10Y"}, {"sym": "GC=F", "label": "Gold"},
        {"sym": "CL=F", "label": "WTI"}, {"sym": "BRL=X", "label": "USD/BRL"},
        {"sym": "^BVSP", "label": "Ibovespa"},
    ],
    "predict": {"enabled": True, "symbols": ["BTC", "ETH", "SOL"], "horizons_h": [4, 24, 168],
                "issue_every_min": 30, "retrain_every_h": 6},
    "signals": {"track": True, "backtest_bars": 4000},
    "risk": {"profile": "moderate", "max_position_pct": 20, "target_vol_pct": 40},
    "data": {"exchange_priority": ["binance", "bybit", "okx", "coinbase"], "adaptive_latency": True,
             "news_refresh_s": 240, "market_refresh_s": 60},
    "elfa": {
        "enabled": True,
        "monthly_credits": 1000,  # plan allowance (free tier = 1000)
        "reserve_pct": 8,         # never auto-spend the last N% (kept for on-demand lookups)
        "cadence_h": {"trending": 3, "cas": 12, "news": 8, "narratives": 24},
    },
    "solana": {"hide_dust_usd": 1.0, "hide_unverified": False, "refresh_s": 300},
    "alerts": {"desktop_notify": True},
}

_lock = threading.Lock()


def _deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings():
    with _lock:
        try:
            user = json.loads(SETTINGS_FILE.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            user = {}
        return _deep_merge(DEFAULTS, user)


def save_settings(patch):
    with _lock:
        try:
            cur = json.loads(SETTINGS_FILE.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            cur = {}
        merged = _deep_merge(cur, patch)
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(merged, indent=2))
        tmp.replace(SETTINGS_FILE)
    return load_settings()


def local_tz(settings=None) -> tzinfo:
    name = ((settings or load_settings()).get("profile") or {}).get("timezone") or ""
    if name:
        try:
            return ZoneInfo(name)
        except Exception:  # noqa: BLE001
            pass
    return datetime.now().astimezone().tzinfo or ZoneInfo("UTC")


def user_name(settings=None):
    return (((settings or load_settings()).get("profile") or {}).get("name") or "").strip()


def _read_env():
    out = {}
    try:
        for line in SECRETS_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return out


def secret(name, default=""):
    return os.environ.get(name) or _read_env().get(name, default)


def secrets_status():
    env = _read_env()
    return {k: {"label": lbl, "set": bool(os.environ.get(k) or env.get(k))} for k, lbl in SECRET_KEYS.items()}


def set_secrets(values):
    bad = [k for k in values if k not in SECRET_KEYS]
    if bad:
        raise ValueError(f"unknown key(s): {', '.join(bad)}")
    if any(v and any(ch in str(v) for ch in ("\r", "\n", "\0")) for v in values.values()):
        raise ValueError("key values must be a single line")
    with _lock:
        env = _read_env()
        for k, v in values.items():
            if k not in SECRET_KEYS:
                continue
            if v is None or v == "":
                env.pop(k, None)
            else:
                env[k] = str(v).strip()
        tmp = SECRETS_FILE.with_suffix(".tmp")
        tmp.write_text("".join(f"{k}={v}\n" for k, v in env.items()))
        os.chmod(tmp, 0o600)
        tmp.replace(SECRETS_FILE)

"""Client attribution and pricing rules loaded from clients.toml."""
import datetime, re, tomllib
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .billing import TERM_KEYS

UNASSIGNED = "(unassigned)"
PRICE_FIELDS = ("input", "output", "cache_write", "cache_read")
DEFAULT_PRICES = Path(__file__).parent / "prices.toml"

class ConfigError(Exception):
    """clients.toml is unreadable or invalid; the message says where and why."""

def builtin_prices():
    with open(DEFAULT_PRICES, "rb") as f:
        return tomllib.load(f)["prices"]

def _number(value, where, minimum=0.0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < minimum:
        raise ConfigError(f"{where} must be a number ≥ {minimum:g}, got {value!r}")
    return float(value)

def _patterns(values, where):
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise ConfigError(f"{where} must be a list of strings, e.g. [\"/clients/acme/\"]")
    compiled = []
    for v in values:
        try:
            compiled.append(re.compile(v))
        except re.error as e:
            raise ConfigError(f"{where}: '{v}' is not a valid regular expression ({e}). "
                              "Tip: put patterns in single quotes and escape . ( ) [ ] with \\") from None
    return compiled

def load_config(path):
    """Read and validate clients.toml. Returns (cfg, rules); raises ConfigError."""
    try:
        with open(path, "rb") as f:
            cfg = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"{path} not found. Create one with: python -m agentcost init") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path} is not valid TOML: {e}") from None

    _number(cfg.get("currency_rate", 1.0), "currency_rate", minimum=0.000001)
    default_markup = _number(cfg.get("default_markup", 1.0), "default_markup")
    try:
        ZoneInfo(cfg.get("timezone", "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f"timezone {cfg.get('timezone')!r} is unknown; use a name like "
                          "\"Europe/Berlin\" (on Windows: pip install tzdata)") from None

    # Built-in prices first; entries in clients.toml override or add models.
    user_prices = cfg.get("prices", {})
    if not isinstance(user_prices, dict):
        raise ConfigError("[prices] must be a table of models")
    for model, p in user_prices.items():
        if not isinstance(p, dict):
            raise ConfigError(f'[prices."{model}"] must be a table with {", ".join(PRICE_FIELDS)}')
        for field in PRICE_FIELDS:
            if field not in p:
                raise ConfigError(f'[prices."{model}"] is missing {field}')
            _number(p[field], f'[prices."{model}"] {field}')
        if "cache_write_1h" in p:
            _number(p["cache_write_1h"], f'[prices."{model}"] cache_write_1h')
    cfg["prices"] = {**builtin_prices(), **user_prices}
    cfg["user_prices"] = set(user_prices)

    sub = cfg.get("subscription")
    if sub is not None:
        if not isinstance(sub, dict) or "monthly_fee" not in sub:
            raise ConfigError("[subscription] needs monthly_fee (and optionally name, since)")
        _number(sub["monthly_fee"], "[subscription] monthly_fee")
        if "since" in sub and not isinstance(sub["since"], datetime.date):
            raise ConfigError("[subscription] since must be a date like 2026-05-01 (no quotes)")

    clients = cfg.get("client", [])
    if not isinstance(clients, list):
        raise ConfigError("clients must be written as [[client]] blocks")
    rules, names = [], set()
    for i, c in enumerate(clients, 1):
        name = c.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"[[client]] number {i} has no name")
        if name in names:
            raise ConfigError(f"client name '{name}' is used twice; merge the rules into one "
                              "[[client]] (several cwd patterns are allowed)")
        names.add(name)
        terms = {k: _number(c[k], f"client '{name}' {k}") for k in TERM_KEYS if k in c}
        if "fixed_fee" in terms and ("min_fee" in terms or "cap" in terms):
            raise ConfigError(f"client '{name}': fixed_fee cannot be combined with min_fee or cap")
        if "min_fee" in terms and "cap" in terms and terms["min_fee"] > terms["cap"]:
            raise ConfigError(f"client '{name}': min_fee ({terms['min_fee']:g}) is above cap ({terms['cap']:g})")
        rules.append({
            "name": name,
            "terms": terms,
            "cwd": _patterns(c.get("cwd", []), f"client '{name}' cwd"),
            "branch": _patterns(c.get("branch", []), f"client '{name}' branch"),
            "markup": _number(c.get("markup", default_markup), f"client '{name}' markup"),
        })
    return cfg, rules

def normalize_path(cwd):
    """Use "/" on every OS, so one rule works for Windows and Unix paths."""
    return (cwd or "").replace("\\", "/")

def cwd_pattern(cwd):
    """Regex matching exactly this folder and its subfolders (not 'acme' for 'acme2')."""
    path = normalize_path(cwd).rstrip("/")
    special = set(".^$*+?{}[]\\|()")
    escaped = "".join("\\" + c if c in special else c for c in path)
    # Windows drive letters and folder names are case-insensitive.
    prefix = "(?i)" if re.match(r"^[A-Za-z]:/", path) else ""
    return f"{prefix}{escaped}(/|$)"

def toml_string(s):
    """TOML literal string where possible, so regex backslashes stay readable."""
    if "'" not in s and "\n" not in s:
        return f"'{s}'"
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

def attribute(rules, cwd, branch):
    # First matching rule wins; branch rules are checked before cwd rules
    # so a shared monorepo can still be split by branch naming (e.g. acme/*).
    cwd = normalize_path(cwd)
    for r in rules:
        if branch and any(p.search(branch) for p in r["branch"]):
            return r["name"]
    for r in rules:
        if cwd and any(p.search(cwd) for p in r["cwd"]):
            return r["name"]
    return UNASSIGNED

def price_for(prices, model):
    # Longest matching prefix, so "claude-opus-4-1" covers dated variants.
    best = None
    for prefix in prices:
        if model.startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    return prices.get(best) if best else None

def turn_cost(turn, price):
    """Cost of one turn in the price list's currency (prices are per million tokens).

    "cw" is all cache writes; the 1-hour share "cw1h" costs cache_write_1h
    (default 2x input), the rest the 5-minute cache_write price.
    """
    cw1h = min(turn.get("cw1h", 0) or 0, turn["cw"])
    write_1h = price.get("cache_write_1h", 2 * price["input"])
    return (turn["in"] * price["input"] + turn["out"] * price["output"]
            + (turn["cw"] - cw1h) * price["cache_write"] + cw1h * write_1h
            + turn["cr"] * price["cache_read"]) / 1e6

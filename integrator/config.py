from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path


@dataclass
class Settings:
    mode: str = "demo"
    seller_id: str = ""
    api_key: str = ""
    api_secret: str = ""
    cui: str = ""
    fgo_key: str = ""
    series_ro: str = ""
    series_bg: str = ""
    series_gr: str = ""
    platform_url: str = ""
    mapping_path: str = ""
    unified_mapping_path: str = ""
    markets: str = "RO,BG,GR"
    fiscal_confirmed: bool = True  # Explicitly confirmed by the project owner.
    fiscal_year: str = str(date.today().year)
    previous_sales_ron: str = ""
    opening_sales_ron: str = ""
    other_sales_ron: str = "0"
    fx_date: str = ""
    eur_ron: str = ""
    bgn_ron: str = ""
    subsidy_policy: str = "include"  # Invoice target supplied by user: sales less seller discount.
    cash_vat: bool = False
    eur_direct_fgo: bool = False
    emag_markets: str = "RO,BG,HU"
    emag_user_ro: str = ""
    emag_password_ro: str = ""
    emag_user_bg: str = ""
    emag_password_bg: str = ""
    emag_user_hu: str = ""
    emag_password_hu: str = ""
    emag_series_ro: str = ""
    emag_series_bg: str = ""
    emag_series_hu: str = ""
    emag_mapping_path: str = ""
    emag_shipping_code: str = ""
    emag_shipping_tax_mode: str = ""
    huf_ron: str = ""

    def validate(self):
        if self.mode not in {"demo", "test", "production"}:
            raise ValueError("Mediu necunoscut.")
        countries = [x.strip() for x in self.markets.split(",") if x.strip()]
        if not countries or len(set(countries)) != len(countries) or any(x not in {"RO", "BG", "GR"} for x in countries):
            raise ValueError("Piețele trebuie să fie o listă fără duplicate: RO,BG,GR.")
        if self.subsidy_policy not in {"review", "include"}:
            raise ValueError("Politică reduceri necunoscută.")
        if not self.emag_markets or any(x.strip() not in {"RO", "BG", "HU"} for x in self.emag_markets.split(",")):
            raise ValueError("Piețele eMAG acceptate sunt RO,BG,HU.")
        if self.emag_shipping_tax_mode not in {"", "cu_tva", "fara_tva"}:
            raise ValueError("Modul taxei de transport eMAG este invalid.")

    def emag_scope(self):
        identity = self.cui
        return self.mode + "-emag-" + hashlib.sha256(identity.encode()).hexdigest()[:16]

    def fgo_scope(self):
        return self.mode + "-fgo-" + hashlib.sha256(self.cui.strip().upper().removeprefix("RO").encode()).hexdigest()[:16]

    def require_credentials(self, provider):
        self.validate()
        if self.mode == "demo":
            return
        if provider == "trendyol":
            if not self.seller_id.isdigit() or int(self.seller_id) <= 0 or not self.api_key or not self.api_secret:
                raise ValueError("Completează Seller ID, API Key și API Secret Trendyol în Setări.")
        else:
            if not self.cui.isdigit() or not self.fgo_key:
                raise ValueError("Completează CUI fără RO și cheia privată FGO în Setări.")
            from urllib.parse import urlsplit
            u = urlsplit(self.platform_url)
            if u.scheme != "https" or not u.hostname or u.username or u.password:
                raise ValueError("Completează URL-ul HTTPS al integrării în Setări, conform contului FGO.")

    def scope(self):
        if self.mode == "demo":
            return "demo"
        return self.mode + "-" + hashlib.sha256(f"{self.cui}:{self.seller_id}".encode()).hexdigest()[:16]


class Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data: bytes, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Salvarea cheilor este disponibilă numai pe Windows (DPAPI).")
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    func = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    func.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    func.restype = wintypes.BOOL
    if not func(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel.LocalFree(target.pbData)


SECRETS = {"api_key", "api_secret", "fgo_key", "emag_password_ro", "emag_password_bg", "emag_password_hu"}


def save_settings(path: Path, settings: Settings):
    settings.validate()
    data = asdict(settings)
    for key in SECRETS:
        data[key] = "dpapi:" + base64.b64encode(_dpapi(data[key].encode())).decode() if data[key] else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if path.exists():
        shutil.copy2(path, path.with_suffix(".backup.json"))
    os.replace(temp, path)


def load_settings(path: Path) -> Settings:
    if not path.exists():
        return Settings()
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in SECRETS:
        if data.get(key):
            if not data[key].startswith("dpapi:"):
                raise ValueError("Format neprotejat de credențiale. Reconfigurează fișierul de setări.")
            data[key] = _dpapi(base64.b64decode(data[key][6:]), True).decode()
    result = Settings(**{k: v for k, v in data.items() if k in Settings.__dataclass_fields__})
    result.validate()
    return result


def data_root():
    # A portable installation must use the same data for Explorer and helper launches.
    if getattr(sys, "frozen", False):
        portable = Path(sys.executable).resolve().parent / "Date"
        if portable.is_dir():
            return portable
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "TrendyolFGO"


class InstanceLock:
    """Keep a byte-range lock for the whole application lifetime."""
    def __init__(self, directory: Path):
        import msvcrt
        directory.mkdir(parents=True, exist_ok=True)
        self.file = (directory / "app.lock").open("a+b")
        self.file.seek(0)
        self.file.write(b"0")
        self.file.flush()
        self.file.seek(0)
        try:
            msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.file.close()
            raise RuntimeError("Aplicația este deja deschisă pentru acest folder de date.")

    def close(self):
        self.file.close()

from __future__ import annotations

from dataclasses import asdict
from contextlib import contextmanager, closing
from datetime import datetime, date, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

from .domain import package_id, source_claims, signature

REISSUABLE = {"rejected", "missing"}


def claim_conflicts(a, b):
    if a == b:
        return True
    # Keep old line-level reservations effective until their snapshots can be migrated.
    return (a.startswith("item:") and b == a.split(":", 2)[1]) or (b.startswith("item:") and a == b.split(":", 2)[1])


def encoded(value):
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, raw TEXT NOT NULL, synced TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS invoices (
                id TEXT PRIMARY KEY, state TEXT NOT NULL, draft TEXT NOT NULL,
                invoice TEXT, error TEXT NOT NULL DEFAULT '', updated TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS line_claims (line_id TEXT PRIMARY KEY, package_id TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, time TEXT NOT NULL, package_id TEXT NOT NULL, message TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS invoice_archive (id INTEGER PRIMARY KEY, package_id TEXT NOT NULL, archived TEXT NOT NULL, reason TEXT NOT NULL, draft TEXT NOT NULL, invoice TEXT);
            ''')
        self.migrate_item_claims()

    def migrate_item_claims(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in db.execute("SELECT i.id,i.draft,o.raw FROM invoices i JOIN orders o ON o.id=i.id WHERE i.state NOT IN ('rejected','missing')").fetchall():
                draft, raw = json.loads(row["draft"]), json.loads(row["raw"])
                if raw.get("_provider") == "emag" or draft["fingerprint"] != signature(raw):
                    continue
                claims = source_claims(raw)
                if claims == draft["source_lines"]:
                    continue
                # A conflicting migration remains conservatively blocked.
                owners = db.execute("SELECT line_id,package_id FROM line_claims WHERE package_id<>?", (row["id"],)).fetchall()
                if any(claim_conflicts(c, r["line_id"]) for c in claims for r in owners):
                    continue
                draft["source_lines"] = claims
                db.execute("DELETE FROM line_claims WHERE package_id=?", (row["id"],))
                db.executemany("INSERT INTO line_claims VALUES (?,?)", [(c, row["id"]) for c in claims])
                db.execute("UPDATE invoices SET draft=? WHERE id=?", (encoded(draft), row["id"]))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE invoices SET state='uncertain', error='Aplicația s-a închis în timpul emiterii. Verifică factura în FGO.' WHERE state='issuing'")
            db.execute("UPDATE invoices SET state='upload_uncertain', error='Încărcare întreruptă. Reia pentru verificarea linkului din Trendyol.' WHERE state='uploading'")

    def put_orders(self, rows):
        with self.connect() as db:
            db.executemany("INSERT INTO orders VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET raw=excluded.raw, synced=excluded.synced", [(package_id(r), encoded(r), self.now()) for r in rows])

    def orders(self):
        with self.connect() as db:
            return [json.loads(r["raw"]) for r in db.execute("SELECT raw FROM orders ORDER BY synced DESC,id DESC")]

    def order(self, pid):
        with self.connect() as db:
            row = db.execute("SELECT raw FROM orders WHERE id=?", (pid,)).fetchone()
        if not row:
            raise ValueError("Comanda nu există în istoricul local.")
        return json.loads(row["raw"])

    def records(self):
        with self.connect() as db:
            return {r["id"]: self._record(r) for r in db.execute("SELECT * FROM invoices ORDER BY updated DESC")}

    def record(self, pid):
        with self.connect() as db:
            row = db.execute("SELECT * FROM invoices WHERE id=?", (pid,)).fetchone()
        return self._record(row) if row else None

    @staticmethod
    def _record(row):
        return {**dict(row), "draft": json.loads(row["draft"]), "invoice": json.loads(row["invoice"]) if row["invoice"] else None}

    def assert_available(self, pid, line_ids, origins=()):
        record = self.record(pid)
        if record and record["state"] not in REISSUABLE:
            raise ValueError("Pachet deja procesat sau cu rezultat incert. Folosește Istoric pentru verificare/asociere.")
        for origin in origins or []:
            rec = self.record(str(origin))
            if rec and rec["state"] not in REISSUABLE:
                raise ValueError("Pachetul provine dintr-un pachet deja facturat. Verifică împărțirea sau anularea.")
        with self.connect() as db:
            self.assert_claims(db, pid, line_ids)

    @staticmethod
    def assert_claims(db, pid, line_ids):
        for row in db.execute("SELECT line_id,package_id FROM line_claims WHERE package_id<>?", (pid,)):
            if any(claim_conflicts(line_id, row["line_id"]) for line_id in line_ids):
                raise ValueError(f"Produsul a fost deja rezervat/facturat în pachetul {row['package_id']}.")

    def reserve(self, draft):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rec = db.execute("SELECT state FROM invoices WHERE id=?", (draft.package_id,)).fetchone()
            if rec and rec["state"] not in REISSUABLE:
                raise ValueError("Pachet deja procesat; emiterea duplicată este blocată.")
            self.assert_claims(db, draft.package_id, draft.source_lines)
            for line_id in draft.source_lines:
                db.execute("INSERT OR IGNORE INTO line_claims VALUES (?, ?)", (line_id, draft.package_id))
            db.execute("INSERT INTO invoices VALUES (?, 'issuing', ?, NULL, '', ?) ON CONFLICT(id) DO UPDATE SET state='issuing',draft=excluded.draft,invoice=NULL,error='',updated=excluded.updated", (draft.package_id, encoded(asdict(draft)), self.now()))

    def mark_missing(self, pid):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rec = db.execute("SELECT * FROM invoices WHERE id=?", (pid,)).fetchone()
            if not rec or not rec["invoice"]:
                return
            reason = "FGO confirmă lipsa facturii; Trendyol nu are factură. Poți reface previzualizarea și emite."
            db.execute("INSERT INTO invoice_archive(package_id,archived,reason,draft,invoice) VALUES (?,?,?,?,?)", (pid,self.now(),reason,rec["draft"],rec["invoice"]))
            db.execute("UPDATE invoices SET state='missing',invoice=NULL,error=?,updated=? WHERE id=?", (reason,self.now(),pid))
            db.execute("DELETE FROM line_claims WHERE package_id=?", (pid,))
            db.execute("INSERT INTO events(time,package_id,message) VALUES (?,?,?)", (self.now(),pid,reason))

    def transition(self, pid, state, invoice=None, error=""):
        with self.connect() as db:
            db.execute("UPDATE invoices SET state=?, invoice=COALESCE(?,invoice), error=?,updated=? WHERE id=?", (state, encoded(invoice) if invoice else None, error, self.now(), pid))
            if state == "rejected":
                db.execute("DELETE FROM line_claims WHERE package_id=?", (pid,))
            db.execute("INSERT INTO events(time,package_id,message) VALUES (?,?,?)", (self.now(), pid, state + (": "+error if error else "")))

    def used_ron(self, year=None):
        year = str(year or date.today().year)
        total = Decimal("0")
        for rec in self.records().values():
            if rec["state"] in REISSUABLE or not rec["draft"]["payload"]["DataEmitere"].startswith(year+"-"):
                continue
            value = rec["draft"].get("net_ron")
            if value is None:
                raise ValueError("Istoricul conține facturi EUR emise direct în FGO, fără echivalent RON local. Monitorizarea plafonului nu poate fi reluată cu un total incomplet; aceste facturi necesită reconciliere în RON. Pentru comenzi EUR poți folosi în continuare bifa EUR direct în FGO.")
            total += Decimal(value)
        return total

    def events(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 300")]

    def backup(self, path):
        with self.connect() as source, closing(sqlite3.connect(path)) as target:
            source.backup(target)

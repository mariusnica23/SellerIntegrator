"""Durable mappings and article metadata, isolated by company and environment."""
from contextlib import contextmanager
from dataclasses import asdict, replace
from decimal import Decimal
import json
import sqlite3

from .mapping import Product


class Catalog:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS mappings (
                    scope TEXT NOT NULL, barcode TEXT NOT NULL, product TEXT NOT NULL,
                    PRIMARY KEY(scope, barcode));
                CREATE TABLE IF NOT EXISTS metadata (
                    scope TEXT NOT NULL, kind TEXT NOT NULL, code TEXT NOT NULL, value TEXT NOT NULL,
                    PRIMARY KEY(scope, kind, code));
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    def mappings(self, scope):
        with self.connect() as db:
            rows = db.execute("SELECT product FROM mappings WHERE scope=?", (scope,)).fetchall()
        result = {}
        for row in rows:
            values = json.loads(row[0])
            values["vat"] = Decimal(values["vat"])
            product = Product(**values)
            result[product.barcode] = product
        return result

    def merge_mappings(self, scope, incoming):
        # An omitted row never deletes an existing association.
        with self.connect() as db:
            db.executemany("INSERT INTO mappings VALUES (?,?,?) ON CONFLICT(scope,barcode) DO UPDATE SET product=excluded.product",
                           [(scope, code, json.dumps(asdict(p), default=str, ensure_ascii=False))
                            for code, p in incoming.items() if code != "__transport__"])
        return self.mappings(scope)

    def get(self, scope, kind, code):
        with self.connect() as db:
            row = db.execute("SELECT value FROM metadata WHERE scope=? AND kind=? AND code=?", (scope, kind, code)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, scope, kind, code, value):
        with self.connect() as db:
            db.execute("INSERT INTO metadata VALUES (?,?,?,?) ON CONFLICT(scope,kind,code) DO UPDATE SET value=excluded.value",
                       (scope, kind, code, json.dumps(value, default=str, ensure_ascii=False)))

    def forget(self, scope, kind, code):
        with self.connect() as db:
            db.execute("DELETE FROM metadata WHERE scope=? AND kind=? AND code=?", (scope, kind, code))

    def article(self, scope, code):
        value = self.get(scope, "fgo", code)
        if value:
            value["vat"] = Decimal(value["vat"])
        return value

    def hydrate(self, scope, mapping):
        with self.connect() as db:
            rows = db.execute("SELECT code,value FROM metadata WHERE scope=? AND kind='fgo'", (scope,)).fetchall()
        articles = {code:json.loads(value) for code,value in rows}
        for article in articles.values():
            article["vat"] = Decimal(article["vat"])
        result = {}
        for key, product in mapping.items():
            article = articles.get(product.fgo_code)
            if article:
                product = replace(product, **article, fgo_verified=True)
            elif product.fgo_code:
                product = replace(product, name="", unit="", fgo_verified=False)
            result[key] = product
        return result

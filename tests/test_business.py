from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from integrator.api import ApiError, FgoAPI, TrendyolAPI, Transport, json_wire, invoice_wire_payload
from integrator.config import Settings, load_settings, save_settings
from integrator.demo import demo_mapping, demo_orders, DemoTrendyol
from integrator.domain import build_draft, fiscal_budget, signature, LIMIT_RON
from integrator.mapping import load_mapping
from integrator.service import Service
from integrator.store import Store
from support import workspace_temp


class BusinessRules(unittest.TestCase):
    def setUp(self):
        self.settings = Settings()
        self.raw = demo_orders()[0]
        self.mapping = demo_mapping()

    def test_gross_price_includes_vat_not_added_twice(self):
        d = build_draft(self.raw, self.mapping, self.settings)
        self.assertEqual(d.total, Decimal("242.00"))
        self.assertEqual(d.net, Decimal("200.00"))
        self.assertEqual(d.vat, Decimal("42.00"))
        self.assertEqual(d.payload["Continut"][0]["PretTotal"], Decimal("242"))
        self.assertNotIn("PretUnitar", d.payload["Continut"][0])

    def test_screenshot_amounts_sales_less_seller_discount(self):
        for sales, discount, expected in [("166.35", "7.07", "159.28"), ("131.95", "34.64", "97.31"), ("34.90", "4.18", "30.72")]:
            with self.subTest(expected=expected):
                raw = deepcopy(self.raw)
                raw.update(packageGrossAmount=sales, packageSellerDiscount=discount, packageTotalPrice=expected)
                raw["lines"][0].update(quantity=1, lineUnitPrice=expected)
                self.assertEqual(build_draft(raw, self.mapping, self.settings).total, Decimal(expected))

    def test_subsidy_not_subtracted_from_seller_invoice(self):
        raw = deepcopy(self.raw)
        raw.update(packageGrossAmount="600", packageSellerDiscount="60", packageTotalPrice="490", packageTyDiscount="50")
        raw["lines"][0].update(quantity=1, lineUnitPrice="490", lineTyDiscount="50", discountDetails=[{"lineItemPrice": "490", "lineItemTyDiscount": "50"}])
        draft = build_draft(raw, self.mapping, self.settings)
        self.assertEqual(draft.total, Decimal("540"))
        self.assertEqual(draft.customer_paid, Decimal("490"))
        with self.assertRaisesRegex(ValueError, "Reducere finanțată"):
            build_draft(raw, self.mapping, replace(self.settings, subsidy_policy="review"))

    def test_multiple_units_uneven_discounts(self):
        raw = deepcopy(self.raw)
        raw.update(packageGrossAmount="242", packageSellerDiscount="41.98", packageTotalPrice="200.02")
        raw["lines"][0].update(lineUnitPrice="100.01", discountDetails=[{"lineItemPrice": "100.00"}, {"lineItemPrice": "100.02"}])
        self.assertEqual(build_draft(raw, self.mapping, self.settings).total, Decimal("200.02"))

    def test_pending_statuses_only_shipped_delivered(self):
        for status in ["Created", "Picking", "Invoiced", "Awaiting", "AtCollectionPoint", "Cancelled", "Returned", "UnPacked", "UnDelivered", ""]:
            with self.subTest(status=status), self.assertRaises(ValueError):
                build_draft({**self.raw, "shipmentPackageStatus": status}, self.mapping, self.settings)
        for status in ["Shipped", "Delivered"]:
            build_draft({**self.raw, "shipmentPackageStatus": status}, self.mapping, self.settings)

    def test_missing_mapping_blocks_no_fallback_name(self):
        with self.assertRaisesRegex(ValueError, "nemapat"):
            build_draft(self.raw, {}, self.settings)

    def test_cancelled_line_blocks(self):
        self.raw["lines"][0]["orderLineItemStatusName"] = "Cancelled"
        with self.assertRaisesRegex(ValueError, "stare produs"):
            build_draft(self.raw, self.mapping, self.settings)

    def test_b2b_and_foreign_warehouse_models_block(self):
        for field, value in [("commercial", True), ("commercial", None), ("micro", True), ("invoiceLink", "https://example.com/invoice.pdf"), ("totalSgrFee", 1)]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                build_draft({**self.raw, field: value}, self.mapping, self.settings)

    def test_country_not_inferred_from_market(self):
        self.raw["shipmentAddress"]["countryCode"] = "DE"
        with self.assertRaisesRegex(ValueError, "Țara de livrare"):
            build_draft(self.raw, self.mapping, self.settings)

    def test_unknown_county_and_mismatched_billing_block(self):
        self.raw["invoiceAddress"] = {**self.raw["invoiceAddress"], "city": "Cluj-Napoca", "countyName": ""}
        with self.assertRaisesRegex(ValueError, "județul"):
            build_draft(self.raw, self.mapping, self.settings)
        self.raw["invoiceAddress"]["countryCode"] = "GR"
        with self.assertRaisesRegex(ValueError, "Țara adresei"):
            build_draft(self.raw, self.mapping, self.settings)

    def test_missing_or_mismatched_total_blocks(self):
        for field, value in [("packageTotalPrice", "243"), ("packageGrossAmount", None), ("packageSellerDiscount", "-1"), ("packageTyDiscount", "9")]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                build_draft({**self.raw, field: value}, self.mapping, self.settings)

    def test_currency_preserved_for_bulgaria_greece(self):
        for raw in demo_orders()[1:3]:
            d = build_draft(raw, self.mapping, self.settings)
            self.assertEqual(d.payload["Valuta"], "EUR")
            self.assertEqual(d.payload["Continut"][0]["CotaTVA"], Decimal("21"))

    def test_international_supplier_placeholder_uses_line_seller(self):
        raw = deepcopy(self.raw)
        raw["supplierId"] = 0
        raw["lines"][0].update(sellerId=1234, merchantId=1234)
        settings = replace(self.settings, mode="production", seller_id="1234", series_ro="RO")
        build_draft(raw, self.mapping, settings)
        raw["lines"][0]["sellerId"] = 5678
        with self.assertRaisesRegex(ValueError, "Seller ID"):
            build_draft(raw, self.mapping, settings)

    def test_stable_external_id_and_status_independent_signature(self):
        d1 = build_draft(self.raw, self.mapping, self.settings)
        raw = deepcopy(self.raw)
        raw["shipmentPackageStatus"] = "Shipped"
        raw["lines"][0]["orderLineItemStatusName"] = "Shipped"
        d2 = build_draft(raw, self.mapping, self.settings)
        self.assertEqual(d1.fingerprint, d2.fingerprint)
        self.assertEqual(d1.payload["IdExtern"], d2.payload["IdExtern"])
        self.assertEqual(len(d1.payload["IdExtern"]), 36)
        raw["lines"][0]["quantity"] = 99
        self.assertNotEqual(signature(raw), d1.fingerprint)

    def test_threshold_current_prior_year_and_missing_data(self):
        s = replace(self.settings, mode="production", previous_sales_ron="0", opening_sales_ron="0")
        fiscal_budget(s, LIMIT_RON-1, Decimal("1"))
        with self.assertRaisesRegex(ValueError, "depășit"):
            fiscal_budget(s, LIMIT_RON, Decimal("0.01"))
        with self.assertRaisesRegex(ValueError, "depășit"):
            fiscal_budget(replace(s, previous_sales_ron="46337.01"), 0, 0)
        with self.assertRaises(ValueError):
            fiscal_budget(replace(s, previous_sales_ron=""), 0, 0)
        with self.assertRaises(ValueError):
            fiscal_budget(replace(s, fiscal_year=str(date.today().year-1)), 0, 0)
        with self.assertRaises(ValueError):
            fiscal_budget(replace(s, other_sales_ron="NaN"), 0, 0)

    def test_fx_must_be_explicit_and_dated(self):
        s = replace(self.settings, mode="production", series_bg="BG", eur_ron="5.09", fx_date=date.today().isoformat())
        d = build_draft(demo_orders()[1], self.mapping, s)
        self.assertEqual(d.net_ron, Decimal("101.80"))
        with self.assertRaisesRegex(ValueError, "cursul"):
            build_draft(demo_orders()[1], self.mapping, replace(s, fx_date="2000-01-01"))

    def test_direct_eur_preserves_invoice_values_without_local_fx(self):
        settings = replace(self.settings, mode="production", series_bg="BG", series_gr="GR", eur_direct_fgo=True, fx_date="", eur_ron="invalid")
        for raw in demo_orders()[1:3]:
            draft = build_draft(raw, self.mapping, settings)
            regular = build_draft(raw, self.mapping, replace(settings, eur_direct_fgo=False, fx_date=date.today().isoformat(), eur_ron="5.26"))
            self.assertEqual(draft.payload, regular.payload)
            self.assertEqual(draft.total, Decimal(raw["packageTotalPrice"]))
            self.assertEqual(draft.currency, "EUR")
            self.assertIsNone(draft.net_ron)
            self.assertNotIn("Curs", draft.payload)

    def test_direct_eur_does_not_convert_ron_or_skip_bgn_rate(self):
        settings = replace(self.settings, mode="production", series_ro="RO", series_bg="BG", eur_direct_fgo=True)
        ron = build_draft(self.raw, self.mapping, settings)
        self.assertEqual(ron.payload["Valuta"], "RON")
        self.assertEqual(ron.total, Decimal("242"))
        self.assertEqual(ron.net_ron, 0)
        bgn = demo_orders()[1]
        bgn["currencyCode"] = bgn["lines"][0]["currencyCode"] = "BGN"
        with self.assertRaisesRegex(ValueError, "cursul"):
            build_draft(bgn, self.mapping, settings)

    def test_direct_eur_keeps_mapping_total_status_and_fiscal_regime_checks(self):
        settings = replace(self.settings, mode="production", series_bg="BG", eur_direct_fgo=True)
        raw = demo_orders()[1]
        for change in ({"shipmentPackageStatus": "Cancelled"}, {"commercial": True}, {"packageGrossAmount": "999"}, {"invoiceLink": "https://example.test/existing.pdf"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                build_draft({**raw, **change}, self.mapping, settings)
        with self.assertRaisesRegex(ValueError, "nemapat"):
            build_draft(raw, {}, settings)
        with self.assertRaisesRegex(ValueError, "Regimul fiscal"):
            build_draft(raw, self.mapping, replace(settings, fiscal_confirmed=False))

    def test_decimal_wire_is_number_not_string(self):
        raw = json_wire({"sum": Decimal("123456.78901"), "quantity": Decimal("2"), "name": "Șurub"})
        decoded = json.loads(raw, parse_float=Decimal)
        self.assertEqual(decoded["sum"], Decimal("123456.78901"))
        self.assertEqual(decoded["quantity"], 2)


class PersistenceAndWorkflow(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(workspace_temp())
        self.store = Store(self.temp/"history.sqlite3")
        self.store.put_orders(demo_orders())
        self.s = Settings()
        self.service = Service(self.s, self.store, demo_mapping())

    def test_issue_upload_repeat_is_idempotent(self):
        draft = self.service.draft(self.store.order("990001"))
        self.service.issue(draft)
        self.service.upload("990001")
        self.assertEqual(self.store.record("990001")["state"], "uploaded")
        self.assertEqual(self.service.upload("990001"), "Link confirmat în Trendyol")
        with self.assertRaises(ValueError):
            self.service.issue(draft)

    def test_direct_eur_mixed_batch_without_threshold_inputs_and_wire_currency(self):
        settings = replace(self.s, mode="production", seller_id="1234", cui="1234", fgo_key="example", platform_url="https://example.test", series_ro="RO", series_bg="BG", series_gr="GR", eur_direct_fgo=True, fiscal_year="old", previous_sales_ron="", opening_sales_ron="", other_sales_ron="invalid", eur_ron="", fx_date="")
        class CaptureTransport:
            def __init__(self):
                self.payloads = []
            def call(self, method, url, **kwargs):
                self.payloads.append(json.loads(json_wire(kwargs["body"])))
                return 200, {"Success": True, "Factura": {"Serie": kwargs["body"]["Serie"], "Numar": str(len(self.payloads)), "Link": "https://example.test/invoice.pdf"}}
        transport = CaptureTransport()
        fgo = FgoAPI(settings, transport)
        service = Service(settings, self.store, demo_mapping(), trendyol=DemoTrendyol(self.store), fgo=fgo)
        drafts, errors = service.prepare(["990001", "990002", "990003"])
        self.assertEqual(errors, [])
        self.assertEqual(len(drafts), 3)
        with patch.object(fgo, "validate_articles"):
            for draft in drafts:
                service.issue(draft)
        self.assertEqual([p["Valuta"] for p in transport.payloads], ["RON", "EUR", "EUR"])
        self.assertEqual([p["Continut"][0]["PretTotal"] for p in transport.payloads], [242, 24.2, 72.6])
        self.assertEqual([p["Continut"][0]["CotaTVA"] for p in transport.payloads], [21, 21, 21])
        self.assertIsNone(self.store.record("990002")["draft"]["net_ron"])
        with self.assertRaisesRegex(ValueError, "deja procesat"):
            service.draft(self.store.order("990002"))
        self.assertEqual(len(transport.payloads), 3)

    def test_unknown_eur_equivalent_cannot_silently_undercount_threshold(self):
        settings = replace(self.s, eur_direct_fgo=True)
        service = Service(settings, self.store, demo_mapping())
        draft = service.draft(self.store.order("990002"))
        self.store.reserve(draft)
        self.store.recover()
        with self.assertRaisesRegex(ValueError, "echivalent RON"):
            self.store.used_ron()
        # Another direct-EUR order remains eligible, while normal monitoring blocks.
        service.draft(self.store.order("990003"))
        with self.assertRaisesRegex(ValueError, "echivalent RON"):
            self.service.draft(self.store.order("990003"))
        self.store.transition(draft.package_id, "rejected")
        self.assertEqual(self.store.used_ron(), 0)

    def test_disabling_direct_eur_restores_required_fx_and_threshold_checks(self):
        settings = replace(self.s, mode="production", series_bg="BG", eur_direct_fgo=False)
        service = Service(settings, self.store, demo_mapping())
        with self.assertRaisesRegex(ValueError, "cursul"):
            service.draft(self.store.order("990002"))
        service.settings = replace(settings, fx_date=date.today().isoformat(), eur_ron="5.26")
        with self.assertRaises(ValueError):
            service.draft(self.store.order("990002"))
        service.settings = replace(service.settings, previous_sales_ron="0", opening_sales_ron="0")
        self.assertEqual(service.draft(self.store.order("990002")).net_ron, Decimal("105.20"))

    def test_direct_mode_changed_since_preview_does_not_emit(self):
        draft = self.service.draft(self.store.order("990002"))
        self.service.settings = replace(self.s, eur_direct_fgo=True)
        with self.assertRaisesRegex(ValueError, "modificat"):
            self.service.issue(draft)
        self.assertIsNone(self.store.record("990002"))

    def test_timeout_persists_uncertain_and_prevents_retry(self):
        class TimeoutFgo:
            def issue(self, payload):
                raise ApiError("Timeout", uncertain=True)
        self.service._fgo = TimeoutFgo()
        d = self.service.draft(self.store.order("990001"))
        with self.assertRaises(ApiError):
            self.service.issue(d)
        self.assertEqual(self.store.record("990001")["state"], "uncertain")
        with self.assertRaisesRegex(ValueError, "deja procesat"):
            self.service.draft(self.store.order("990001"))
        self.service.confirm_not_issued("990001")
        self.assertEqual(self.store.record("990001")["state"], "rejected")
        self.assertEqual(self.service.draft(self.store.order("990001")).payload["IdExtern"], d.payload["IdExtern"])

    def test_crash_recovery_keeps_reservations(self):
        d = self.service.draft(self.store.order("990002"))
        self.store.reserve(d)
        self.store.recover()
        self.assertEqual(self.store.record("990002")["state"], "uncertain")
        self.assertEqual(self.store.used_ron(), Decimal("100.00"))

    def test_known_rejection_releases_fiscal_reserve(self):
        d = self.service.draft(self.store.order("990002"))
        self.store.reserve(d)
        self.store.transition(d.package_id, "rejected")
        self.assertEqual(self.store.used_ron(), 0)

    def test_changed_package_does_not_emit(self):
        d = self.service.draft(self.store.order("990001"))
        raw = self.store.order("990001")
        raw["invoiceAddress"]["fullName"] = "Alt client"
        self.store.put_orders([raw])
        with self.assertRaisesRegex(ValueError, "modificat"):
            self.service.issue(d)
        self.assertIsNone(self.store.record("990001"))

    def test_duplicate_split_line_is_blocked(self):
        d = self.service.draft(self.store.order("990001"))
        self.service.issue(d)
        other = self.store.order("990001")
        other["shipmentPackageId"] = 990010
        with self.assertRaisesRegex(ValueError, "deja rezervat"):
            self.service.draft(other)

    def test_origin_package_reference_is_blocked(self):
        self.service.issue(self.service.draft(self.store.order("990001")))
        other = self.store.order("990002")
        other["originPackageIds"] = [990001]
        with self.assertRaisesRegex(ValueError, "provine"):
            self.service.draft(other)

    def test_upload_conflict_is_not_success(self):
        self.service.issue(self.service.draft(self.store.order("990001")))
        raw = self.store.order("990001")
        raw["invoiceLink"] = "https://example.com/another.pdf"
        self.store.put_orders([raw])
        with self.assertRaisesRegex(ValueError, "alt link"):
            self.service.upload("990001")
        self.assertEqual(self.store.record("990001")["state"], "remote_invoice")

    def test_lost_upload_response_reconciles_remote_link(self):
        self.service.issue(self.service.draft(self.store.order("990001")))
        self.store.transition("990001", "upload_uncertain")
        raw = self.store.order("990001")
        raw["invoiceLink"] = self.store.record("990001")["invoice"]["url"]
        self.store.put_orders([raw])
        self.assertEqual(self.service.upload("990001"), "Link confirmat în Trendyol")
        self.assertEqual(self.store.record("990001")["state"], "uploaded")

    def test_entire_batch_reports_unmapped_before_writes(self):
        drafts, errors = self.service.prepare(["990001", "990004"])
        self.assertEqual(len(drafts), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(self.store.records(), {})

    def test_backup_contains_persisted_invoice(self):
        self.service.issue(self.service.draft(self.store.order("990001")))
        target = self.temp/"copy.sqlite3"
        self.store.backup(target)
        copied = Store(target)
        self.assertEqual(copied.record("990001")["state"], "issued")

    def test_dpapi_roundtrip_no_plaintext_secrets(self):
        path = self.temp/"settings.json"
        settings = replace(self.s, api_key="key-example-1234", api_secret="secret-example-5678", fgo_key="private-example-9012")
        save_settings(path, settings)
        data = path.read_text()
        for secret in [settings.api_key, settings.api_secret, settings.fgo_key]:
            self.assertNotIn(secret, data)
        self.assertEqual(load_settings(path), settings)


class ContractTests(unittest.TestCase):
    def test_fgo_vat_scale_is_normalized_without_changing_invoice_amounts(self):
        class Capture:
            def call(self, method, url, **kwargs):
                if url.endswith("/articol/get"):
                    return 200, {"Success": True, "Result": {"CodConta": "8679613643151", "Nume": "Articol test", "UM": "H87", "CotaTva": Decimal("0.2100")}}
                self.wire = json_wire(kwargs["body"])
                return 200, {"Success": True, "Factura": {"Serie": "T", "Numar": "0001", "Link": "https://example.com/invoice.pdf"}}
        transport = Capture()
        api = FgoAPI(Settings(mode="test", cui="123", fgo_key="test", platform_url="https://example.com"), transport)
        product = api.get_article("8679613643151")
        self.assertEqual(str(product["vat"]), "21")
        payload = {"Client": {"Denumire": "Client test"}, "Continut": [{"Denumire": "Articol test", "CotaTVA": Decimal("21.0000"), "PretTotal": Decimal("159.28"), "NrProduse": Decimal("2"), "UM": "H87"}, {"Denumire": "Produs redus", "CotaTVA": Decimal("11.00"), "PretTotal": Decimal("11.10"), "NrProduse": Decimal("1"), "UM": "BUC"}]}
        original = deepcopy(payload)
        api.issue(payload)
        self.assertIn('"CotaTVA":21,', transport.wire)
        self.assertIn('"CotaTVA":11,', transport.wire)
        self.assertNotIn('21.0000', transport.wire)
        sent = json.loads(transport.wire, parse_float=Decimal)
        self.assertEqual(sent["Continut"][0]["PretTotal"], Decimal("159.28"))
        self.assertEqual(sent["Continut"][0]["NrProduse"], 2)
        self.assertEqual(payload, original)
        self.assertEqual(str(payload["Continut"][0]["CotaTVA"]), "21.0000")
        with self.assertRaises(ValueError):
            invoice_wire_payload({"Continut": [{"CotaTVA": Decimal("0.21")}]})

    def test_fgo_article_changes_block_invoice_until_preview_is_refreshed(self):
        class Capture:
            def call(self, method, url, **kwargs):
                return 200, {"Success": True, "Result": {"Nume": "Denumire FGO", "UM": "BUC", "CotaTva": Decimal("0.21")}}
        api = FgoAPI(Settings(mode="test", cui="123", fgo_key="test", platform_url="https://example.com"), Capture())
        with self.assertRaisesRegex(ValueError, "s-a modificat"):
            api.validate_articles([{"CodArticol": "A1", "Denumire": "Denumire veche", "UM": "BUC", "CotaTVA": Decimal("21")}])
        api.validate_articles([{"CodArticol": "A1", "Denumire": "Denumire FGO", "UM": "BUC", "CotaTVA": Decimal("21")}])
        self.assertEqual(api.get_article("A1")["vat"], Decimal("21"))

    def test_unresolved_fgo_code_cannot_be_invoiced(self):
        mapping = demo_mapping()
        p = mapping["0001234567890"]
        mapping[p.barcode] = replace(p, fgo_verified=False)
        with self.assertRaisesRegex(ValueError, "nu este verificat"):
            build_draft(demo_orders()[0], mapping, Settings(mode="production", series_ro="RO"))

    def test_resolve_codes_fetches_name_unit_and_vat_once_per_code(self):
        class Catalog:
            def __init__(self):
                self.calls = []
            def get_article(self, code):
                self.calls.append(code)
                return {"name": "Articol din FGO", "unit": "BUC", "vat": Decimal("21")}
        mapping = demo_mapping()
        mapping["5940000000022"] = replace(mapping["5940000000022"], fgo_code="ORG-01")
        api = Catalog()
        service = Service(Settings(mode="production"), None, mapping, fgo=api)
        self.assertEqual(service.resolve_articles(), [])
        self.assertEqual(api.calls, ["ORG-01"])
        for product in service.mapping.values():
            self.assertTrue(product.fgo_verified)
            self.assertEqual(product.name, "Articol din FGO")
        with patch.object(api, "get_article", side_effect=ApiError("Acces refuzat")):
            self.assertTrue(service.resolve_articles())
        self.assertTrue(all(not p.fgo_verified for p in service.mapping.values()))

    def test_fgo_wrong_code_or_unsupported_vat_is_rejected(self):
        for result in ({"Nume": "Produs", "UM": "BUC", "CotaTva": "0.19"}, {"Nume": "Produs", "UM": "BUC", "CotaTva": "0.21", "CodConta": "OTHER"}, {"Nume": "Produs", "CotaTva": "0.21"}):
            class Capture:
                def call(self, *args, **kwargs):
                    return 200, {"Success": True, "Result": result}
            api = FgoAPI(Settings(mode="test", cui="123", fgo_key="test", platform_url="https://example.com"), Capture())
            with self.assertRaises(ValueError):
                api.get_article("A1")

    def test_fgo_authenticated_json_contract(self):
        class Capture:
            def call(self, method, url, **kwargs):
                self.request = (method, url, kwargs)
                return 200, {"Success": True, "Factura": {"Serie": "T", "Numar": "0001", "Link": "https://example.com/invoice.pdf"}}
        transport = Capture()
        s = Settings(mode="test", cui="2864518", fgo_key="1234567890", platform_url="https://example.com")
        api = FgoAPI(s, transport)
        api.issue({"Client": {"Denumire": "Ionescu Popescu"}, "Continut": []})
        method, url, kw = transport.request
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://api-testuat.fgo.ro/v1/factura/emitere")
        import hashlib
        self.assertEqual(kw["body"]["Hash"], hashlib.sha1(b"28645181234567890Ionescu Popescu").hexdigest().upper())
        self.assertNotIn("1234567890", json.dumps(kw["body"]))

    def test_trendyol_v2_country_header_pagination_and_upload(self):
        class Capture:
            def __init__(self):
                self.requests = []
            def call(self, method, url, headers, body=None, **kwargs):
                self.requests.append((method, url, headers, body))
                if method == "POST":
                    return 201, {}
                from urllib.parse import parse_qs, urlsplit
                page = int(parse_qs(urlsplit(url).query)["page"][0])
                return 200, {"content": [dict(demo_orders()[page], shipmentPackageId=800+page)], "totalPages": 2, "totalElements": 2}
        t = Capture()
        api = TrendyolAPI(Settings(mode="production", seller_id="1234", api_key="a", api_secret="b"), t)
        orders = api.fetch("GR", date.today(), date.today())
        self.assertEqual(len(orders), 2)
        self.assertEqual(t.requests[0][2]["storeFrontCode"], "GR")
        self.assertEqual(t.requests[0][2]["User-Agent"], "1234 - SelfIntegration")
        self.assertIn("/v2/orders?", t.requests[0][1])
        api.upload("800", "GR", "https://example.com/invoice.pdf")
        self.assertEqual(t.requests[-1][3], {"shipmentPackageId": 800, "invoiceLink": "https://example.com/invoice.pdf"})

    def test_financial_post_not_retried_after_timeout(self):
        t = Transport()
        with patch.object(t.opener, "open", side_effect=TimeoutError) as opened, patch("integrator.api.time.sleep"):
            with self.assertRaises(ApiError) as failure:
                t.call("POST", "https://example.com", body={})
        self.assertEqual(opened.call_count, 1)
        self.assertTrue(failure.exception.uncertain)


if __name__ == "__main__":
    unittest.main()

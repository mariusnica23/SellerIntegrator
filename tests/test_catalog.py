from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from unittest import TestCase
from unittest.mock import Mock, patch

from integrator.api import FgoAPI, ApiError
from integrator.catalog import Catalog
from integrator.config import Settings, save_settings, load_settings
from integrator.emag import EmagAPI
from integrator.mapping import Product, load_shared_mapping, write_shared_mapping
from integrator.service import Service
from integrator.ui import App
from support import workspace_temp
from test_emag_reports import emag_order


ARTICLE = {"name":"Produs salvat", "unit":"BUC", "vat":Decimal("21")}


def settings(**kwargs):
    return replace(Settings(mode="production", cui="10000000", seller_id="123", fgo_key="example", platform_url="https://example.test"), **kwargs)


class CatalogTests(TestCase):
    def test_merge_retains_omitted_rows_and_changed_code_drops_old_details(self):
        with workspace_temp() as folder:
            c = Catalog(folder/"catalog.sqlite3")
            s = settings()
            c.merge_mappings(s.scope(), {"F-2":Product("F-2","",fgo_code="OLD"), "TYB-EXAMPLE":Product("TYB-EXAMPLE","",fgo_code="KEEP")})
            c.put(s.fgo_scope(), "fgo", "OLD", ARTICLE)
            merged = c.merge_mappings(s.scope(), {"F-2":Product("F-2","",fgo_code="NEW")})
            restored = Catalog(c.path)
            result = restored.hydrate(s.fgo_scope(),restored.mappings(s.scope()))
            self.assertEqual(len(result),2)
            self.assertEqual(result["TYB-EXAMPLE"].fgo_code,"KEEP")
            self.assertEqual(result["F-2"].fgo_code,"NEW")
            self.assertFalse(result["F-2"].fgo_verified)
            self.assertEqual(result["F-2"].name, "")

    def test_fgo_fetched_once_across_imports_restarts_and_platforms(self):
        with workspace_temp() as folder:
            c = Catalog(folder/"catalog.sqlite3")
            s = settings()
            api = Mock()
            api.get_article.return_value = ARTICLE
            first = {"A":Product("A","",fgo_code="SKU")}
            self.assertEqual(Service(s,None,first,fgo=api,catalog=c).resolve_articles(),[])
            api.get_article.assert_called_once_with("SKU")
            reopened=Catalog(c.path)
            mapping={"B":Product("B","",fgo_code="SKU"),"C":Product("C","",fgo_code="NEW")}
            service=Service(s,None,mapping,fgo=api,catalog=reopened)
            self.assertEqual(service.mapping["B"].name, ARTICLE["name"])
            self.assertEqual(service.resolve_articles(),[])
            self.assertEqual([c.args[0] for c in api.get_article.call_args_list],["SKU","NEW"])
            again=Service(s,None,mapping,fgo=api,catalog=Catalog(c.path))
            self.assertEqual(again.resolve_articles(),[])
            self.assertEqual(api.get_article.call_count,2)

    def test_cached_fgo_validation_performs_no_transport_calls(self):
        with workspace_temp() as folder:
            c=Catalog(folder/"catalog.sqlite3");s=settings()
            c.put(s.fgo_scope(),"fgo","SKU",ARTICLE)
            transport=Mock()
            api=FgoAPI(s,transport,catalog=c)
            api.validate_articles([{"CodArticol":"SKU","Denumire":ARTICLE["name"],"UM":"BUC","CotaTVA":Decimal("21")}])
            transport.call.assert_not_called()

    def test_force_refresh_replaces_details_and_failure_does_not_look_verified(self):
        with workspace_temp() as folder:
            c=Catalog(folder/"catalog.sqlite3");s=settings()
            c.put(s.fgo_scope(),"fgo","SKU",ARTICLE)
            api=Mock()
            api.get_article.return_value={**ARTICLE,"name":"Nume nou"}
            mapping={"A":Product("A","",fgo_code="SKU")}
            service=Service(s,None,mapping,fgo=api,catalog=c)
            self.assertEqual(service.resolve_articles(force=True),[])
            self.assertEqual(c.article(s.fgo_scope(),"SKU")["name"],"Nume nou")
            api.get_article.side_effect=ApiError("Acces refuzat")
            self.assertTrue(service.resolve_articles(force=True))
            self.assertIsNone(c.article(s.fgo_scope(),"SKU"))
            self.assertFalse(service.mapping["A"].fgo_verified)

    def test_cache_is_isolated_by_company_and_environment(self):
        with workspace_temp() as folder:
            c=Catalog(folder/"catalog.sqlite3");s=settings()
            c.put(s.fgo_scope(),"fgo","SKU",ARTICLE)
            for other in (replace(s,cui="20000000"),replace(s,mode="test")):
                self.assertIsNone(c.article(other.fgo_scope(),"SKU"))
            self.assertEqual(replace(s,cui="RO10000000").fgo_scope(),s.fgo_scope())

    def test_emag_offer_ean_survives_restart_and_invoice_preflight(self):
        with workspace_temp() as folder:
            c=Catalog(folder/"catalog.sqlite3")
            s=settings(unified_mapping_path="common.xlsx",emag_user_ro="example-user")
            api=EmagAPI(s,catalog=c)
            with patch.object(api,"call",return_value={"results":[{"id":123,"ean":["0059400000001"]}]}) as call:
                api.enrich_eans(emag_order()["_emag"],"RO")
                call.assert_called_once()
            reopened=EmagAPI(s,catalog=Catalog(c.path))
            def read(market,path,payload):
                if path=="/order/read":return {"results":[emag_order()["_emag"]]}
                if path=="/order/attachments/read":return {"results":[]}
                raise AssertionError("Repeated product offer lookup")
            with patch.object(reopened,"call",side_effect=read) as call:
                result=reopened.get_package("emag:RO:12345","RO")
                self.assertEqual(result["lines"][0]["barcode"],"0059400000001")
                self.assertEqual(call.call_count,2)
            new_account=EmagAPI(replace(s,emag_user_ro="other-user"),catalog=c)
            with patch.object(new_account,"call",side_effect=ValueError("new account requires lookup")):
                with self.assertRaisesRegex(ValueError,"new account"):
                    new_account.enrich_eans(emag_order()["_emag"],"RO")

    def test_alphanumeric_trendyol_identifiers_roundtrip(self):
        with workspace_temp() as folder:
            path=folder/"common.xlsx"
            write_shared_mapping(path,[("SKU","F-2",""),("OTHER","TYB-EXAMPLE-123","")])
            self.assertEqual(set(load_shared_mapping(path)["trendyol"]),{"F-2","TYB-EXAMPLE-123"})
            write_shared_mapping(path,[("SKU","","F-2")])
            with self.assertRaisesRegex(ValueError,"EAN emag invalid"):
                load_shared_mapping(path)


class PersistentMappingUiTests(TestCase):
    def test_partial_import_and_restart_retain_both_platforms_and_cached_articles(self):
        with workspace_temp() as folder:
            initial=folder/"initial.xlsx"
            write_shared_mapping(initial,[("SKU","F-2","0059400000001"),("KEEP","TYB-EXAMPLE","")])
            s=settings(unified_mapping_path=str(initial))
            save_settings(folder/"settings.json",s)
            Catalog(folder/"catalog.sqlite3").put(s.fgo_scope(),"fgo","SKU",ARTICLE)
            app=App(folder)
            try:
                app.withdraw()
                partial=folder/"partial.xlsx"
                write_shared_mapping(partial,[("SKU","1234567890123",""),("NEW","TYB-EXAMPLE","")])
                app.apply_mapping_import(partial)
                self.assertEqual(set(app.mapping),{"F-2","TYB-EXAMPLE","1234567890123"})
                self.assertEqual(app.mapping["TYB-EXAMPLE"].fgo_code,"NEW")
                self.assertFalse(app.mapping["TYB-EXAMPLE"].fgo_verified)
                self.assertEqual(app.mapping["F-2"].name,ARTICLE["name"])
                self.assertEqual(app.mapping["1234567890123"].name,ARTICLE["name"])
                self.assertEqual(app.emag_tab.mapping["0059400000001"].name,ARTICLE["name"])
                saved=app.settings.unified_mapping_path
                self.assertEqual(len(load_shared_mapping(saved)["trendyol"]),3)
            finally:
                app.destroy()
            # The persistent system retains the map even if the imported Excel disappears.
            from pathlib import Path
            Path(saved).unlink()
            reopened=App(folder)
            try:
                reopened.withdraw()
                self.assertEqual(len(reopened.mapping),3)
                self.assertEqual(reopened.mapping["F-2"].name,ARTICLE["name"])
                self.assertEqual(len(reopened.emag_tab.mapping),1)
                self.assertFalse(reopened.load_error)
            finally:
                reopened.destroy()

"""eMAG workspace; financial actions always pass through the shared preview."""
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
import shutil
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import webbrowser

from .config import Settings, save_settings
from .domain import build_draft, package_id
from .emag import EmagService, invoice_order
from .mapping import load_mapping
from .store import Store, REISSUABLE


class EmagTab(ttk.Frame):
    def __init__(self, app):
        super().__init__(app.notebook, padding=18)
        self.app = app
        self.profile = None
        self.mapping = {}
        self.load_error = ""
        self.open_profile()
        ttk.Label(self, text="eMAG România • Bulgaria • Ungaria", style="Section.TLabel").pack(anchor="w")
        ttk.Label(self, text="Comenzi finalizate (status API 4), expediate de vânzător. Selectează cu Ctrl / Shift; verifică fiecare factură înainte de emitere.", wraplength=1080).pack(anchor="w", pady=(5,10))
        filters = ttk.Frame(self)
        filters.pack(fill="x", pady=(0,10))
        self.start = tk.StringVar(master=app, value=(date.today()-timedelta(days=6)).isoformat())
        self.end = tk.StringVar(master=app, value=date.today().isoformat())
        for label,var in [("Modificate din",self.start),("până în",self.end)]:
            ttk.Label(filters,text=label).pack(side="left",padx=(0,5))
            ttk.Entry(filters,textvariable=var,width=11).pack(side="left",padx=(0,10))
        ttk.Button(filters,text="Preia comenzi eMAG",command=self.sync).pack(side="left")
        ttk.Button(filters,text="Configurare eMAG",command=self.configure_accounts).pack(side="right")
        sub = ttk.Notebook(self)
        sub.pack(fill="both",expand=True)
        orders, mapping, history = (ttk.Frame(sub,padding=8) for _ in range(3))
        for frame,label in [(orders,"Comenzi & facturare"),(mapping,"Mapare articole"),(history,"Istoric eMAG")]:
            sub.add(frame,text=label)
        self.tree = app.make_tree(orders,[("id","Comandă",125),("market","Piață",65),("customer","Client",190),("status","Status eMAG",100),("amount","De facturat",120),("state","Factură / verificare",450)])
        bar = ttk.Frame(orders); bar.pack(fill="x",pady=(10,0))
        ttk.Button(bar,text="Selectează pregătite",command=lambda:self.tree.selection_set(self.ready)).pack(side="left")
        ttk.Button(bar,text="Previzualizare",command=lambda:self.preview(False)).pack(side="left",padx=6)
        ttk.Button(bar,text="Facturează comenzile selectate",command=lambda:self.preview(True),style="Primary.TButton").pack(side="left",padx=6)
        ttk.Button(bar,text="Încarcă facturile în eMAG",command=self.upload).pack(side="left",padx=6)
        ttk.Label(mapping,text="Excel: product_id și cod_fgo, ambele ca TEXT. Opțional RO:123 / BG:123 / HU:123 pentru coduri diferite între piețe.\nproduct_id este codul vânzătorului returnat de eMAG API; îl poți exporta mai jos din comenzile preluate.",wraplength=1060).pack(anchor="w",pady=(0,10))
        bar = ttk.Frame(mapping); bar.pack(fill="x",pady=(0,10))
        ttk.Button(bar,text="Importă maparea eMAG",command=self.import_mapping).pack(side="left")
        ttk.Button(bar,text="Exportă model cu produsele preluate",command=self.export_template).pack(side="left",padx=6)
        ttk.Button(bar,text="Preia articolele din FGO",command=self.fetch_articles).pack(side="left",padx=6)
        self.mapping_tree=app.make_tree(mapping,[("id","product_id eMAG",170),("code","Cod articol FGO",170),("name","Denumire FGO",400),("vat","TVA %",80)])
        self.history=app.make_tree(history,[("id","Comandă",170),("invoice","Factură FGO",150),("total","Total",130),("state","Stare",200),("message","Detalii",400)])
        bar=ttk.Frame(history);bar.pack(fill="x",pady=(10,0))
        ttk.Button(bar,text="Deschide factura",command=self.open_invoice).pack(side="left")
        ttk.Button(bar,text="Asociază factura existentă",command=self.reconcile).pack(side="left",padx=6)
        ttk.Button(bar,text="Am verificat: factura NU există",command=self.release).pack(side="left",padx=6)
        self.message=tk.StringVar(master=app)
        ttk.Label(self,textvariable=self.message,wraplength=1080).pack(anchor="w",pady=(10,0))

    def configure_accounts(self):
        self.app.notebook.select(self.app.params_tab)
        self.app.param_notebook.select(self.app.settings_tab)
        self.app.connection_notebook.select(self.app.emag_settings_tab)

    def open_profile(self):
        settings=self.app.settings
        profile=(settings.emag_scope(),settings.emag_mapping_path)
        if self.profile == profile:
            return
        self.profile=profile
        self.store=Store(self.app.data_dir / "profiles" / profile[0] / "history.sqlite3")
        self.store.recover()
        self.mapping={};self.load_error=""
        if settings.emag_mapping_path:
            try: self.mapping=load_mapping(settings.emag_mapping_path,identifier="product_id")
            except Exception as exc: self.load_error=f"Maparea eMAG nu a fost încărcată: {exc}"

    def service(self):
        return EmagService(self.app.settings,self.store,dict(self.mapping),other_stores=(self.app.store,))

    def selected(self, history=False):
        ids=list((self.history if history else self.tree).selection())
        if not ids: messagebox.showinfo("eMAG","Selectează comenzile din tabel.",parent=self.app)
        return ids

    def refresh(self):
        from .ui import STATES
        self.open_profile()
        selected=self.tree.selection(); self.tree.delete(*self.tree.get_children())
        self.ready=[];records=self.store.records()
        for raw in self.store.orders():
            pid=package_id(raw);rec=records.get(pid);total="—"
            status=STATES.get(rec["state"],rec["state"]).replace("Trendyol","eMAG") if rec else "De verificat"
            try:
                normalized,_=invoice_order(raw,self.service().mapping,self.app.settings)
                total=f"{normalized['packageGrossAmount']-normalized['packageSellerDiscount']:.2f} {raw['currencyCode']}"
                if not rec or rec["state"] in REISSUABLE:
                    self.service().draft(raw)
                    self.ready.append(pid);status="Pregătită pentru previzualizare"
            except ValueError as exc:
                if not rec or rec["state"] in REISSUABLE: status=str(exc).replace("Trendyol","eMAG")
            self.tree.insert("","end",iid=pid,values=(raw["orderNumber"],raw["_market"],raw["invoiceAddress"].get("fullName"),raw.get("shipmentPackageStatus"),total,status))
        self.tree.selection_set([i for i in selected if self.tree.exists(i)])
        self.history.delete(*self.history.get_children())
        for pid,rec in records.items():
            inv=rec["invoice"] or {};d=rec["draft"]
            self.history.insert("","end",iid=pid,values=(pid,f"{inv.get('series','')} {inv.get('number','')}",f"{d['total']} {d['currency']}",STATES.get(rec["state"],rec["state"]).replace("Trendyol","eMAG"),rec["error"].replace("Trendyol","eMAG")))
        self.mapping_tree.delete(*self.mapping_tree.get_children())
        for p in self.mapping.values():
            self.mapping_tree.insert("","end",values=(p.barcode,p.fgo_code,p.name if p.fgo_verified else "De preluat din FGO",p.vat if p.fgo_verified else ""))
        self.message.set(self.load_error or f"{len(self.tree.get_children())} comenzi locale • {len(self.ready)} pregătite. Acces API: Contul meu → Profil → Detalii tehnice; autorizează IP-ul public și drepturile utilizatorului.")

    def sync(self):
        if self.app.busy: return
        try: start,end=date.fromisoformat(self.start.get()),date.fromisoformat(self.end.get())
        except ValueError:
            messagebox.showerror("eMAG","Data trebuie să fie AAAA-LL-ZZ.",parent=self.app);return
        service=self.service()
        def done(result):
            self.mapping=service.mapping;self.app.refresh()
            count,errors=result
            messagebox.showinfo("Sincronizare eMAG",f"{count} comenzi preluate."+("\n\n"+"\n".join(errors[:15]) if errors else ""),parent=self.app)
        self.app.run_job("Preiau comenzile eMAG…",lambda progress:service.sync(start,end,progress),done)

    def fetch_articles(self):
        if self.app.busy:return
        service=self.service()
        def done(errors):
            self.mapping=service.mapping;self.app.refresh()
            if errors:messagebox.showerror("Articole eMAG → FGO","\n".join(errors),parent=self.app)
        self.app.run_job("Preiau articolele FGO pentru eMAG…",lambda progress:service.resolve_articles(progress=progress),done)

    def import_mapping(self):
        if self.app.busy:return
        path=filedialog.askopenfilename(parent=self.app,title="Mapare eMAG",filetypes=[("Excel","*.xlsx")])
        if not path:return
        try:
            products=load_mapping(path,identifier="product_id")
            target=self.store.path.parent/"mapare_emag.xlsx"
            if Path(path).resolve()!=target.resolve():shutil.copy2(path,target)
            settings=Settings(**{**asdict(self.app.settings),"emag_mapping_path":str(target)})
            save_settings(self.app.settings_path,settings);self.app.settings=settings
            self.app.fields["emag_mapping_path"].set(str(target))
            self.profile=(settings.emag_scope(),str(target));self.mapping=products
            self.app.refresh();self.fetch_articles()
        except Exception as exc:messagebox.showerror("Mapare eMAG",str(exc),parent=self.app)

    def export_template(self):
        path=filedialog.asksaveasfilename(parent=self.app,initialfile="Mapare_eMAG.xlsx",defaultextension=".xlsx",filetypes=[("Excel","*.xlsx")])
        if not path:return
        from openpyxl import Workbook
        try:
            book=Workbook();sheet=book.active;sheet.title="Mapare eMAG"
            sheet.append(["product_id","cod_fgo","denumire_emag"])
            products={}
            for raw in self.store.orders():
                for p in raw["_emag"].get("products",[]):
                    products[f"{raw['_market']}:{p['product_id']}"]=p.get("name","")
            for code,name in sorted(products.items()):sheet.append([code,"",name])
            for row in sheet:
                for cell in row:
                    cell.number_format="@"
                    if isinstance(cell.value,str):cell.data_type="s"
            for key,width in [("A",24),("B",24),("C",60)]:sheet.column_dimensions[key].width=width
            sheet.freeze_panes="A2";sheet.auto_filter.ref=sheet.dimensions
            book.save(path)
        except Exception as exc:messagebox.showerror("Export eMAG",str(exc),parent=self.app)

    def preview(self,emit):
        if self.app.busy:return
        ids=self.selected()
        if not ids:return
        service=self.service();drafts,errors=service.prepare(ids)
        def confirmed(approved):
            def work(progress):
                count=0;failures=[]
                for d in approved:
                    progress(f"Emit factura eMAG {d.order_number}…")
                    try:service.issue(d);count+=1
                    except Exception as exc:failures.append(str(exc).replace("Trendyol","eMAG"));break
                return count,failures
            self.app.run_job("Emit facturile eMAG…",work,self.result)
        self.app.preview_dialog(drafts,[e.replace("Trendyol","eMAG") for e in errors],confirmed if emit else None)

    def result(self,result):
        count,errors=result
        messagebox.showinfo("eMAG",f"{count} operațiuni finalizate."+("\n\n"+"\n".join(errors) if errors else ""),parent=self.app)

    def upload(self):
        if self.app.busy:return
        ids=self.selected()
        if not ids:return
        if not messagebox.askokcancel("Încarcă în eMAG",f"Încarci facturile FGO asociate celor {len(ids)} comenzi selectate?",parent=self.app):return
        service=self.service()
        def work(progress):
            count=0;errors=[]
            for pid in ids:
                progress(f"Încarc factura în eMAG • {pid}…")
                try:service.upload(pid);count+=1
                except Exception as exc:errors.append(f"{pid}: {exc}".replace("Trendyol","eMAG"))
            return count,errors
        self.app.run_job("Încarc facturile eMAG…",work,self.result)

    def open_invoice(self):
        for pid in self.selected(True):
            rec=self.store.record(pid)
            if rec["invoice"]:
                from .api import valid_invoice_url
                webbrowser.open(valid_invoice_url(rec["invoice"]["url"]));break

    def reconcile(self):
        if self.app.busy:return
        ids=self.selected(True)
        if len(ids)!=1:return
        series=simpledialog.askstring("Factură existentă","Seria verificată în FGO:",parent=self.app)
        if not series:return
        number=simpledialog.askstring("Factură existentă","Numărul verificat în FGO:",parent=self.app)
        if not number:return
        service=self.service()
        self.app.run_job("Verific factura FGO…",lambda _:service.reconcile(ids[0],series,number),lambda _:None)

    def release(self):
        if self.app.busy:return
        ids=self.selected(True)
        if len(ids)!=1:return
        if messagebox.askyesno("Verificare FGO","Ai verificat în FGO că factura acestei comenzi NU a fost emisă?",parent=self.app):
            try:self.service().confirm_not_issued(ids[0]);self.app.refresh()
            except ValueError as exc:messagebox.showerror("eMAG",str(exc),parent=self.app)

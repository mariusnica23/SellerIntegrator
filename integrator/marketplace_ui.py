"""Identical workspace for each marketplace; shared invoice preview."""
from datetime import date, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import webbrowser

from .domain import package_id
from .emag import EmagService, invoice_order
from .mapping import load_mapping, load_shared_mapping
from .store import Store, REISSUABLE
from .service import issue_batch


class MarketplaceTab(ttk.Frame):
    def __init__(self, app, provider):
        super().__init__(app.notebook, padding=18)
        self.app = app
        self.provider=provider
        self.label="eMAG" if provider=="emag" else "Trendyol"
        self.profile = None
        self.mapping = {}
        self.load_error = ""
        self.open_profile()
        ttk.Label(self, text=self.label+" → FGO", style="Section.TLabel").pack(anchor="w")
        text="Comenzi finalizate (status API 4), expediate de vânzător." if provider=="emag" else "Colete în expediere sau livrate; fiecare colet split are propria factură."
        ttk.Label(self, text=text+" Selectează cu Ctrl / Shift și verifică previzualizarea.",wraplength=1080).pack(anchor="w",pady=(5,10))
        filters = ttk.Frame(self)
        filters.pack(fill="x", pady=(0,10))
        self.start = tk.StringVar(master=app, value=(date.today()-timedelta(days=6)).isoformat())
        self.end = tk.StringVar(master=app, value=date.today().isoformat())
        for label,var in [("Modificate din",self.start),("până în",self.end)]:
            ttk.Label(filters,text=label).pack(side="left",padx=(0,5))
            ttk.Entry(filters,textvariable=var,width=11).pack(side="left",padx=(0,10))
        ttk.Button(filters,text="Preia comenzi",command=self.sync).pack(side="left")
        ttk.Button(filters,text="Configurare",command=self.configure_accounts).pack(side="right")
        ttk.Button(filters,text="Mapare comună",command=self.show_mapping).pack(side="right",padx=6)
        sub = ttk.Notebook(self)
        self.tabs = sub
        sub.pack(fill="both",expand=True)
        orders, history = (ttk.Frame(sub,padding=8) for _ in range(2))
        for frame,label in [(orders,"Comenzi & facturare"),(history,"Istoric")]:
            sub.add(frame,text=label)
        search = ttk.Frame(orders)
        search.pack(fill="x", pady=(0, 8))
        ttk.Label(search, text="Caută comandă, colet, client sau EAN").pack(side="left")
        self.search = tk.StringVar(master=app)
        ttk.Entry(search, textvariable=self.search, width=38).pack(side="left", padx=8)
        self.search.trace_add("write", lambda *_: self.refresh())
        self.tree = app.make_tree(orders,[("id","Comandă / colet",170),("market","Piață",65),("customer","Client",190),("status","Status",100),("amount","De facturat",120),("state","Factură / verificare",410)])
        bar = ttk.Frame(orders); bar.pack(fill="x",pady=(10,0))
        ttk.Button(bar,text="Selectează pregătite",command=lambda:self.tree.selection_set(self.ready)).pack(side="left")
        ttk.Button(bar,text="Previzualizare",command=lambda:self.preview(False)).pack(side="left",padx=6)
        ttk.Button(bar,text="Facturează comenzile selectate",command=lambda:self.preview(True),style="Primary.TButton").pack(side="left",padx=6)
        ttk.Button(bar,text="Încarcă facturile în "+self.label,command=self.upload).pack(side="left",padx=6)
        self.history=app.make_tree(history,[("id","Comandă",170),("invoice","Factură FGO",150),("total","Total",130),("state","Stare",200),("message","Detalii",400)])
        bar=ttk.Frame(history);bar.pack(fill="x",pady=(10,0))
        ttk.Button(bar,text="Deschide factura",command=self.open_invoice).pack(side="left")
        ttk.Button(bar,text="Asociază factura existentă",command=self.reconcile).pack(side="left",padx=6)
        ttk.Button(bar,text="Am verificat: factura NU există",command=self.release).pack(side="left",padx=6)
        ttk.Button(bar,text="Backup istoric",command=self.backup).pack(side="right")
        self.message=tk.StringVar(master=app)
        ttk.Label(self,textvariable=self.message,wraplength=1080).pack(anchor="w",pady=(10,0))

    def configure_accounts(self):
        self.app.notebook.select(self.app.params_tab)
        self.app.param_notebook.select(self.app.settings_tab)
        self.app.connection_notebook.select(self.app.emag_settings_tab if self.provider=="emag" else self.app.api_settings_tab)

    def show_mapping(self):
        self.app.notebook.select(self.app.params_tab)
        self.app.param_notebook.select(self.app.mapping_tab)

    def open_profile(self):
        settings=self.app.settings
        if self.provider=="trendyol":
            self.store=self.app.store;self.mapping=self.app.mapping;return
        profile=(settings.emag_scope(),settings.unified_mapping_path or settings.emag_mapping_path)
        if self.profile == profile:
            return
        self.profile=profile
        self.store=Store(self.app.data_dir / "profiles" / profile[0] / "history.sqlite3")
        self.store.recover()
        mapping_scope = self.mapping_scope()
        self.mapping=self.app.catalog.mappings(mapping_scope);self.load_error=""
        if profile[1]:
            try:
                imported=load_shared_mapping(profile[1])["emag"] if settings.unified_mapping_path else load_mapping(profile[1],identifier="product_id")
                self.mapping=self.app.catalog.merge_mappings(mapping_scope,imported)
            except Exception as exc:
                if not self.mapping:self.load_error=f"Maparea eMAG nu a fost încărcată: {exc}"
        if settings.mode != "demo":
            self.mapping=self.app.catalog.hydrate(settings.fgo_scope(),self.mapping)

    def mapping_scope(self):
        settings=self.app.settings
        return settings.emag_scope() + (":ean" if settings.unified_mapping_path else ":legacy")

    def service(self):
        if self.provider=="trendyol":return self.app.service()
        return EmagService(self.app.settings,self.store,dict(self.mapping),other_stores=(self.app.store,),catalog=self.app.catalog)

    def selected(self, history=False):
        ids=list((self.history if history else self.tree).selection())
        if not ids: messagebox.showinfo(self.label,"Selectează comenzile din tabel.",parent=self.app)
        return ids

    def refresh(self):
        from .ui import STATES
        self.open_profile()
        selected=self.tree.selection(); self.tree.delete(*self.tree.get_children())
        self.ready=[];records=self.store.records()
        for raw in self.store.orders():
            query = self.search.get().strip().casefold()
            if query and query not in str(raw).casefold():
                continue
            pid=package_id(raw);rec=records.get(pid);total="—"
            status=STATES.get(rec["state"],rec["state"]).replace("Trendyol",self.label) if rec else "De verificat"
            try:
                normalized=invoice_order(raw,self.service().mapping,self.app.settings)[0] if self.provider=="emag" else raw
                from .domain import dec
                total=f"{dec(normalized['packageGrossAmount'])-dec(normalized['packageSellerDiscount']):.2f} {raw['currencyCode']}"
                if not rec or rec["state"] in REISSUABLE:
                    self.service().draft(raw)
                    self.ready.append(pid);status="Pregătită pentru previzualizare"
            except ValueError as exc:
                if not rec or rec["state"] in REISSUABLE: status=str(exc).replace("Trendyol",self.label)
            order_label=raw['orderNumber'] if self.provider=="emag" else f"{raw['orderNumber']} / {pid}"
            lifecycle = {"Shipped":"Finalizată" if self.provider=="emag" else "În expediere", "Delivered":"Livrată", "Cancelled":"Anulată", "Returned":"Returnată", "UnPacked":"Împărțită în colete"}.get(raw.get("shipmentPackageStatus"),raw.get("shipmentPackageStatus"))
            self.tree.insert("","end",iid=pid,values=(order_label,raw.get("_market", ""),raw.get("invoiceAddress",{}).get("fullName"),lifecycle,total,status))
        self.tree.selection_set([i for i in selected if self.tree.exists(i)])
        self.history.delete(*self.history.get_children())
        for pid,rec in records.items():
            inv=rec["invoice"] or {};d=rec["draft"]
            self.history.insert("","end",iid=pid,values=(pid,f"{inv.get('series','')} {inv.get('number','')}",f"{d['total']} {d['currency']}",STATES.get(rec["state"],rec["state"]).replace("Trendyol",self.label),rec["error"].replace("Trendyol",self.label)))
        self.message.set(self.load_error or f"{len(self.tree.get_children())} comenzi/colete locale • {len(self.ready)} pregătite. Actualizarea verifică facturile asociate și prezența lor în marketplace.")

    def sync(self):
        if self.app.busy: return
        try: start,end=date.fromisoformat(self.start.get()),date.fromisoformat(self.end.get())
        except ValueError:
            messagebox.showerror(self.label,"Data trebuie să fie AAAA-LL-ZZ.",parent=self.app);return
        service=self.service()
        def done(result):
            self.mapping=service.mapping
            if self.provider=="trendyol":self.app.mapping=self.mapping
            self.app.refresh()
            count,errors=result
            messagebox.showinfo("Sincronizare "+self.label,f"{count} comenzi preluate."+("\n\n"+"\n".join(errors[:15]) if errors else ""),parent=self.app)
        self.app.run_job("Preiau comenzile "+self.label+"…",lambda progress:service.sync(start,end,progress),done)




    def preview(self,emit):
        if self.app.busy:return
        ids=self.selected()
        if not ids:return
        service=self.service();drafts,errors=service.prepare(ids)
        def confirmed(approved):
            def work(progress):
                count,failures=issue_batch(approved,{d.package_id:service for d in approved},progress)
                return count,[e.replace("Trendyol",self.label) for e in failures]
            self.app.run_job("Emit facturile "+self.label+"…",work,self.result)
        self.app.preview_dialog(drafts,[e.replace("Trendyol",self.label) for e in errors],confirmed if emit else None)

    def result(self,result):
        count,errors=result
        messagebox.showinfo(self.label,f"{count} operațiuni finalizate."+("\n\n"+"\n".join(errors) if errors else ""),parent=self.app)

    def upload(self):
        if self.app.busy:return
        ids=self.selected()
        if not ids:return
        if not messagebox.askokcancel("Încarcă în "+self.label,f"Încarci facturile FGO asociate celor {len(ids)} comenzi selectate?",parent=self.app):return
        service=self.service()
        def work(progress):
            count=0;errors=[]
            for pid in ids:
                progress(f"Încarc factura în {self.label} • {pid}…")
                try:service.upload(pid);count+=1
                except Exception as exc:errors.append(f"{pid}: {exc}".replace("Trendyol",self.label))
            return count,errors
        self.app.run_job("Încarc facturile "+self.label+"…",work,self.result)

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
        rec = self.store.record(ids[0])
        draft = rec["draft"]
        client = draft["payload"]["Client"]["Denumire"]
        if not messagebox.askyesno("Confirmă asocierea",f"Ai verificat că factura {series} {number} aparține comenzii {draft['order_number']}, clientului {client}, cu totalul {draft['total']} {draft['currency']}?",parent=self.app):
            return
        service=self.service()
        self.app.run_job("Verific factura FGO…",lambda _:service.reconcile(ids[0],series,number),lambda _:None)

    def release(self):
        if self.app.busy:return
        ids=self.selected(True)
        if len(ids)!=1:return
        if messagebox.askyesno("Verificare FGO","Ai verificat în FGO că factura acestei comenzi NU a fost emisă?",parent=self.app):
            try:self.service().confirm_not_issued(ids[0]);self.app.refresh()
            except ValueError as exc:messagebox.showerror(self.label,str(exc),parent=self.app)

    def backup(self):
        if self.app.busy:return
        path=filedialog.asksaveasfilename(parent=self.app,title="Backup istoric "+self.label,initialfile=f"Istoric-{self.label}-{date.today()}.sqlite3",defaultextension=".sqlite3")
        if path:
            if Path(path).resolve()==self.store.path.resolve():
                messagebox.showerror("Backup","Alege un fișier diferit de baza activă.",parent=self.app);return
            self.store.backup(path)

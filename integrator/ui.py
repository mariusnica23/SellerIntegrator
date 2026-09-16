from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import queue
import shutil
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
import webbrowser

from .config import Settings, load_settings, save_settings
from .demo import demo_mapping, demo_orders
from .domain import READY_STATUSES, build_draft, country, dec, package_id, remote_invoice_present
from .mapping import load_mapping, load_shared_mapping, shared_rows, write_shared_mapping
from .service import Service, issue_batch
from .store import Store, REISSUABLE
from .reports import sales_report
from .catalog import Catalog


STATES = {"issuing": "Se emite", "issued": "Emisă în FGO", "uploaded": "Încărcată în Trendyol", "uncertain": "Verifică în FGO", "rejected": "Emitere respinsă", "uploading": "Se încarcă", "upload_failed": "Încărcare nereușită", "upload_uncertain": "Verifică încărcarea"}
STATES.update(missing="Lipsește din FGO — de refăcut", remote_conflict="Factură diferită între platforme")
STATES["remote_invoice"] = "Factură prezentă în Trendyol"
BG = "#f3f5f8"
INK = "#18293e"
MUTED = "#62748a"
ACCENT = "#12695c"


def resource(name):
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / name


class App(tk.Tk):
    def __init__(self, data_dir: Path):
        super().__init__()
        self.title("SellerIntegrator | Trendyol + eMAG → FGO")
        self.geometry("1280x880")
        self.minsize(1180, 800)
        self.configure(bg=BG)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.catalog = Catalog(self.data_dir / "catalog.sqlite3")
        self.settings_path = data_dir / "settings.json"
        self.busy = False
        self.jobs = queue.Queue()
        self.mapping = {}
        self.load_error = ""
        try:
            self.settings = load_settings(self.settings_path)
        except Exception as exc:
            self.settings = Settings()
            self.load_error = f"Setările nu au putut fi citite: {exc}. Fișierul original a fost păstrat."
        self.open_profile()
        self.styles()
        self.header()
        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=1, column=0, sticky="nsew", padx=24, pady=(0, 10))
        self.pending_tab = ttk.Frame(self.notebook, padding=18)
        self.params_tab = ttk.Frame(self.notebook, padding=10)
        self.params_tab.columnconfigure(0, weight=1)
        self.params_tab.rowconfigure(1, weight=1)
        save_bar = ttk.Frame(self.params_tab)
        save_bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.save_button = ttk.Button(save_bar, text="Salvează", style="Primary.TButton", command=self.save_config)
        self.save_button.pack(side="left")
        ttk.Button(save_bar, text="Reîncarcă setările", command=self.reload_config).pack(side="left", padx=(8, 0))
        self.config_message = tk.StringVar(value="Parametrizările salvate se încarcă automat la fiecare deschidere.")
        ttk.Label(save_bar, textvariable=self.config_message, foreground=MUTED).pack(side="left", padx=16)
        ttk.Button(save_bar, text="Deschide folderul de date", command=lambda: os.startfile(str(self.data_dir))).pack(side="right")
        self.param_notebook = ttk.Notebook(self.params_tab)
        self.param_notebook.grid(row=1, column=0, sticky="nsew")
        self.mapping_tab = ttk.Frame(self.param_notebook, padding=18)
        self.settings_tab = ttk.Frame(self.param_notebook, padding=18)
        self.param_notebook.add(self.mapping_tab, text="Mapare Excel")
        self.param_notebook.add(self.settings_tab, text="Date de bază & conexiuni")
        self.reports_tab = ttk.Frame(self.notebook, padding=18)
        self.help_tab = ttk.Frame(self.notebook, padding=18)
        for tab, label in [(self.pending_tab, "De facturat"), (self.params_tab, "Parametrizare"), (self.help_tab, "Ghid & fiscalitate")]:
            self.notebook.add(tab, text=label)
        self.notebook.insert(self.help_tab, self.reports_tab, text="Rapoarte")
        self.make_pending()
        self.make_mapping()
        self.make_settings()
        from .marketplace_ui import MarketplaceTab
        self.trendyol_tab = MarketplaceTab(self,"trendyol")
        self.emag_tab = MarketplaceTab(self,"emag")
        self.notebook.insert(0,self.trendyol_tab,text="Trendyol")
        self.notebook.insert(1,self.emag_tab,text="eMAG")
        for variable in self.fields.values():
            variable.trace_add("write", self.update_config_message)
        self.bind("<Control-s>", lambda _: self.save_config())
        self.make_reports()
        self.make_help()
        self.status_text = tk.StringVar(value="Pregătit. Datele sunt salvate local pe acest calculator.")
        ttk.Label(self, textvariable=self.status_text, foreground=MUTED).grid(row=2, column=0, sticky="w", padx=28, pady=(0, 12))
        self.protocol("WM_DELETE_WINDOW", self.close_app)
        self.poll_id = self.after(120, self.poll)
        self.refresh()
        self.notebook.select(self.trendyol_tab)
        self.param_notebook.select(self.mapping_tab)
        if self.load_error:
            self.after(300, lambda: messagebox.showwarning("Configurare", self.load_error, parent=self))

    def open_profile(self):
        self.store = Store(self.data_dir / "profiles" / self.settings.scope() / "history.sqlite3")
        self.store.recover()
        if self.settings.mode == "demo":
            self.mapping = demo_mapping()
            if not self.store.orders():
                self.store.put_orders(demo_orders())
        else:
            self.mapping = self.catalog.mappings(self.settings.scope())
            if self.settings.unified_mapping_path or self.settings.mapping_path:
                try:
                    imported = load_shared_mapping(self.settings.unified_mapping_path)["trendyol"] if self.settings.unified_mapping_path else load_mapping(self.settings.mapping_path)
                    self.mapping = self.catalog.merge_mappings(self.settings.scope(), imported)
                except Exception as exc:
                    if not self.mapping:self.load_error = f"Maparea nu a fost încărcată: {exc}"
            self.mapping = self.catalog.hydrate(self.settings.fgo_scope(), self.mapping)
            # Migrate an existing EAN → FGO map without altering the original file.
            if self.mapping and not self.settings.unified_mapping_path and not self.settings.emag_mapping_path:
                target = self.store.path.parent / "mapare_comuna.xlsx"
                if not target.exists() and all(p.fgo_code and p.barcode.isprintable() and 1 <= len(p.barcode) <= 128 for p in self.mapping.values()):
                    try:
                        from dataclasses import replace
                        write_shared_mapping(target, shared_rows(self.mapping, {}))
                        load_shared_mapping(target)
                        updated = replace(self.settings, unified_mapping_path=str(target))
                        save_settings(self.settings_path, updated)
                        self.settings = updated
                    except Exception as exc:
                        self.load_error = f"Maparea existentă rămâne activă; conversia la Excel comun nu a reușit: {exc}"

    def service(self):
        others = (self.emag_tab.store,) if hasattr(self, "emag_tab") else ()
        return Service(self.settings, self.store, dict(self.mapping), other_stores=others, catalog=self.catalog)

    def styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), background=BG, foreground=INK)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG)
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(20, 11), background="#e6ebf1")
        style.map("TNotebook.Tab", background=[("selected", "#ffffff")], foreground=[("selected", ACCENT)])
        style.configure("TButton", padding=(12, 8), background="#ffffff", bordercolor="#d6dee8")
        style.configure("Primary.TButton", font=("Segoe UI Semibold", 11), background=ACCENT, foreground="white", padding=(22, 13))
        style.map("Primary.TButton", background=[("active", "#0a564a"), ("disabled", "#abb9b5")], foreground=[("disabled", "#f6f8f7")])
        style.configure("Secondary.TButton", font=("Segoe UI Semibold", 11), padding=(22, 13), foreground=ACCENT)
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 22), foreground=INK)
        style.configure("Sub.TLabel", foreground=MUTED)
        style.configure("Section.TLabel", font=("Segoe UI Semibold", 12))
        style.configure("Treeview", background="white", fieldbackground="white", rowheight=34, borderwidth=0)
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 10), background="#e9eef4", padding=(8, 10))
        style.map("Treeview", background=[("selected", "#daeae6")], foreground=[("selected", "#123d33")])
        style.configure("TEntry", padding=6, fieldbackground="white")
        style.configure("TLabelframe", background=BG)
        style.configure("TLabelframe.Label", font=("Segoe UI Semibold", 11))

    def header(self):
        header = ttk.Frame(self, padding=(26, 20, 26, 18))
        header.grid(row=0, column=0, sticky="ew")
        left = ttk.Frame(header)
        left.pack(side="left")
        ttk.Label(left, text="SellerIntegrator", style="Title.TLabel").pack(anchor="w")
        ttk.Label(left, text="Trendyol + eMAG → FGO • Comenzi, facturi și rapoarte.", style="Sub.TLabel").pack(anchor="w", pady=(4, 0))
        self.badge = tk.Label(header, bg="#fff0d5", fg="#82500c", font=("Segoe UI Semibold", 10), padx=16, pady=10)
        self.badge.pack(side="right")

    def make_tree(self, parent, columns, heights=10):
        box = ttk.Frame(parent)
        box.pack(fill="both", expand=True)
        tree = ttk.Treeview(box, columns=[x[0] for x in columns], show="headings", selectmode="extended", height=min(heights, 5))
        for key, title, width in columns:
            tree.heading(key, text=title)
            tree.column(key, width=width, minwidth=70, stretch=True, anchor="w")
        scroll = ttk.Scrollbar(box, orient="vertical", command=tree.yview)
        horizontal = ttk.Scrollbar(box, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        tree.tag_configure("blocked", foreground="#a5512b")
        tree.tag_configure("done", foreground=ACCENT)
        return tree


    def make_pending(self):
        ttk.Label(self.pending_tab, text="De facturat • Trendyol + eMAG", style="Section.TLabel").pack(anchor="w")
        self.pending_info = ttk.Label(self.pending_tab, text="Un rând pentru fiecare produs. Folosește bara orizontală pentru toate datele.", style="Sub.TLabel")
        self.pending_info.pack(anchor="w", pady=(5, 12))
        columns = [("select", "Selectează", 95), ("platform", "Platformă", 95), ("order", "Comandă", 140), ("pid", "Pachet", 160), ("state", "Status", 120), ("check", "Verificare", 310),
                   ("name", "Client facturat", 230), ("type", "Tip client", 90), ("country", "Țară", 75), ("county", "Județ", 130), ("city", "Localitate", 140), ("address", "Adresă facturare", 360),
                   ("barcode", "EAN", 170), ("product", "Denumire pe factură", 340), ("fgo", "Cod articol FGO", 145), ("qty", "Cantitate", 90), ("unit", "UM", 75),
                   ("price", "Preț unitar cu TVA*", 160), ("gross", "Total produs cu TVA", 160), ("vat", "Cota TVA %", 110), ("base", "Bază produs estimată", 175), ("tax", "TVA produs estimat", 160),
                   ("currency", "Monedă", 90), ("series", "Serie FGO", 110), ("date", "Data emiterii", 125), ("sales", "Valoarea vânzărilor", 160), ("discount", "Reducere comerciant", 180), ("total", "DE FACTURAT / pachet", 195), ("payment", "Metodă plată", 160), ("notes", "Explicații factură", 420)]
        self.pending_tree = self.make_tree(self.pending_tab, columns, 15)
        for key, _, width in columns:
            self.pending_tree.column(key, width=width, minwidth=width, stretch=False)
        self.pending_tree.bind("<Double-1>", lambda _: self.preview_only())
        self.pending_packages = {}
        self.pending_tree.bind("<Button-1>", self.toggle_pending)
        self.pending_tree.bind("<<TreeviewSelect>>", self.pending_selection_changed)
        bar = ttk.Frame(self.pending_tab)
        bar.pack(fill="x", pady=(12, 0))
        ttk.Button(bar, text="Actualizează ambele platforme", command=self.list_pending).pack(side="left")
        ttk.Button(bar, text="Selectează toate", command=lambda: self.pending_tree.selection_set(self.pending_tree.get_children())).pack(side="left", padx=8)
        ttk.Button(bar, text="Previzualizare", command=self.preview_only).pack(side="left")
        ttk.Button(bar, text="Facturează comenzi selectate", style="Primary.TButton", command=self.create_invoices).pack(side="right")
        ttk.Label(self.pending_tab, text="Trendyol: în expediere/livrat. eMAG: finalizate, livrate de vânzător. Selecția unui produs include întregul colet/comandă.\nTotalul se repetă pe produse și nu se însumează între rânduri. *Prețul unitar este informativ. Facturile emise în afara aplicației, fără link în marketplace, trebuie verificate separat.", foreground=MUTED, wraplength=1100).pack(anchor="w", pady=(12, 0))

    def refresh_pending(self, raws, records):
        if not hasattr(self, "pending_tree"):
            return
        selected = {self.pending_packages.get(i) for i in self.pending_tree.selection()}
        self.pending_tree.delete(*self.pending_tree.get_children())
        self.pending_packages = {}
        count = 0
        for raw in raws:
            pid = package_id(raw)
            rec = records.get(pid)
            if raw.get("shipmentPackageStatus", raw.get("status")) not in READY_STATUSES or remote_invoice_present(raw) or (rec and rec["state"] not in REISSUABLE):
                continue
            count += 1
            d = None
            is_emag=raw.get("_provider")=="emag"
            platform="eMAG" if is_emag else "Trendyol"
            service=self.service_for_pid(pid)
            mapping=service.mapping
            try:
                if is_emag:
                    from .emag import invoice_order
                    raw,mapping=invoice_order(raw,mapping,self.settings)
                d = service.draft(raw)
                check = "Date valide; EUR direct în FGO, fără verificarea plafonului" if d.net_ron is None else "Date valide; plafonul se verifică la emitere"
            except ValueError as exc:
                check = str(exc).replace("Trendyol",platform)
            address = raw.get("invoiceAddress") or {}
            client = d.payload["Client"] if d else {}
            currency = raw.get("currencyCode", "")
            try:
                expected = f"{dec(raw.get('packageGrossAmount'))-dec(raw.get('packageSellerDiscount')):.2f}"
            except ValueError:
                expected = "Lipsește"
            for i, line in enumerate(raw.get("lines") or [{}]):
                barcode = str(line.get("barcode") or "")
                product = mapping.get(barcode)
                entry = d.payload["Continut"][i] if d else {}
                gross = entry.get("PretTotal")
                base = (gross / (1 + product.vat/100)).quantize(Decimal("0.01"), rounding="ROUND_HALF_UP") if gross is not None else None
                unit_price = (gross / dec(line["quantity"])).quantize(Decimal("0.0001"), rounding="ROUND_HALF_UP") if gross is not None else ""
                row_id = f"{pid}:{i}"
                target = country(raw)
                status="Finalizată" if is_emag else "Livrat" if raw.get("shipmentPackageStatus", raw.get("status")) == "Delivered" else "În expediere"
                display_ean=product.barcode if is_emag and product else barcode
                values = ("☐", platform, raw.get("orderNumber", ""), pid, status, check,
                          client.get("Denumire", address.get("fullName", "")), client.get("Tip", "PF" if raw.get("commercial") is False else "DE VERIFICAT"), address.get("countryCode", ""), client.get("Judet", address.get("countyName", "")), address.get("city", ""), client.get("Adresa", address.get("fullAddress", address.get("address1", ""))),
                          display_ean, (product.name or "De preluat din FGO") if product else "NEMAPAT", product.fgo_code if product else "", line.get("quantity", ""), product.unit if product else "", unit_price, str(gross) if gross is not None else "", str(product.vat) if product and (not product.fgo_code or product.fgo_verified) else "", str(base) if base is not None else "", str(gross-base) if base is not None else "",
                          currency, d.payload["Serie"] if d else getattr(self.settings, f"{'emag_' if is_emag else ''}series_{target.lower()}", ""), date.today().isoformat(), raw.get("packageGrossAmount", ""), raw.get("packageSellerDiscount", ""), expected, raw.get("paymentMethod", ""), d.payload["Explicatii"] if d else "")
                self.pending_tree.insert("", "end", iid=row_id, values=values, tags=() if d else ("blocked",))
                self.pending_packages[row_id] = pid
                if pid in selected:
                    self.pending_tree.selection_add(row_id)
        self.pending_info.configure(text=f"{count} pachete • {len(self.pending_packages)} poziții de produs • Un rând per produs, toate câmpurile facturii în coloane.")

    def toggle_pending(self, event):
        if self.pending_tree.identify_column(event.x) != "#1":
            return
        row = self.pending_tree.identify_row(event.y)
        if row not in self.pending_packages:
            return
        pid = self.pending_packages[row]
        rows = [r for r, p in self.pending_packages.items() if p == pid]
        if row in self.pending_tree.selection():
            self.pending_tree.selection_remove(rows)
        else:
            self.pending_tree.selection_add(rows)
        return "break"

    def pending_selection_changed(self, event=None):
        selected = set(self.pending_tree.selection())
        packages = {self.pending_packages[r] for r in selected if r in self.pending_packages}
        full = {r for r, p in self.pending_packages.items() if p in packages}
        if full != selected:
            self.pending_tree.selection_set(list(full))
        for row in self.pending_tree.get_children():
            self.pending_tree.set(row, "select", "☑" if row in full else "☐")

    def list_pending(self):
        if self.busy:return
        self.notebook.select(self.pending_tab)
        services=[("Trendyol",self.service(),self.trendyol_tab), ("eMAG",self.emag_tab.service(),self.emag_tab)]
        try:
            periods=[(date.fromisoformat(tab.start.get()),date.fromisoformat(tab.end.get())) for _,_,tab in services]
        except ValueError:
            messagebox.showerror("Perioadă","Completează datele AAAA-LL-ZZ în fiecare tab de platformă.",parent=self);return
        if not any(getattr(self.settings,f"emag_user_{m}") for m in ("ro","bg","hu")):services=services[:1];periods=periods[:1]
        def work(progress):
            total=0;errors=[]
            for (name,service,_),(start,end) in zip(services,periods):
                count,failed=service.sync(start,end,progress);total+=count;errors.extend(f"{name}: {e}" for e in failed)
            return total,errors
        def done(result):
            self.mapping=services[0][1].mapping
            if len(services)>1:self.emag_tab.mapping=services[1][1].mapping
            self.refresh()
            total,errors=result
            messagebox.showinfo("Sincronizare",f"{total} comenzi/colete preluate."+("\n\n"+"\n".join(errors[:15]) if errors else ""),parent=self)
        self.run_job("Actualizez comenzile din ambele platforme…",work,done)

    def make_mapping(self):
        ttk.Label(self.mapping_tab,text="Mapare comună Trendyol + eMAG",style="Section.TLabel").pack(anchor="w")
        ttk.Label(self.mapping_tab,text="Excel: cod_fgo, ean_trendyol, ean_emag — toate ca TEXT. Trendyol acceptă și coduri alfanumerice. Importul adaugă sau actualizează asocieri; rândurile existente se păstrează.",wraplength=1080,style="Sub.TLabel").pack(anchor="w",pady=(5,14))
        bar=ttk.Frame(self.mapping_tab);bar.pack(fill="x",pady=(0,14))
        ttk.Button(bar,text="Importă Excel comun",command=self.import_mapping).pack(side="left")
        ttk.Button(bar,text="Exportă Excel comun",command=self.save_template).pack(side="left",padx=8)
        self.articles_button=ttk.Button(bar,text="Preia doar articolele noi",command=self.fetch_articles)
        self.articles_button.pack(side="left")
        self.mapping_label=ttk.Label(bar,style="Sub.TLabel");self.mapping_label.pack(side="left",padx=12)
        extra=ttk.Frame(self.mapping_tab);extra.pack(fill="x",pady=(0,10))
        self.refresh_articles_button=ttk.Button(extra,text="Reîmprospătează din FGO",command=lambda:self.fetch_articles(force=True))
        self.refresh_articles_button.pack(side="left")
        ttk.Label(extra,text="Denumirea, UM și TVA rămân salvate local. Folosește reîmprospătarea după modificări în FGO.",style="Sub.TLabel").pack(side="left",padx=12)
        self.mapping_tree=self.make_tree(self.mapping_tab,[("platform","Platformă",100),("ean","EAN",180),("fgo","Cod articol FGO",160),("name","Denumire FGO",400),("unit","UM",70),("vat","TVA %",80)])
        ttk.Label(self.mapping_tab,text="eMAG: dacă EAN-ul nu apare în comandă, aplicația îl citește din oferta produsului prin API. Un EAN poate indica un singur articol FGO pe fiecare platformă. Mai multe EAN-uri pot indica același articol FGO.",wraplength=1080,foreground=MUTED).pack(anchor="w",pady=(12,0))

    def fetch_articles(self, force=False):
        if self.busy:return
        from dataclasses import replace
        combined={}
        for prefix,mapping in [("T",self.mapping),("E",self.emag_tab.service().mapping)]:
            for ean,p in mapping.items():combined[prefix+":"+ean]=replace(p,barcode=prefix+":"+ean)
        service=Service(self.settings,self.store,combined,catalog=self.catalog)
        def done(errors):
            maps={"T":{},"E":{}}
            for key,p in service.mapping.items():
                prefix,ean=key.split(":",1);maps[prefix][ean]=replace(p,barcode=ean)
            self.mapping=maps["T"];self.emag_tab.mapping=maps["E"]
            self.refresh()
            if errors:messagebox.showerror("Articole FGO","\n".join(errors),parent=self)
        self.run_job("Reîmprospătez articolele din FGO…" if force else "Preiau doar articolele noi din FGO…",lambda progress:service.resolve_articles(progress=progress,force=force),done)

    def make_settings(self):
        ttk.Label(self.settings_tab, text="Conectează conturile și configurează facturarea", style="Section.TLabel").pack(anchor="w", pady=(0, 10))
        sub = ttk.Notebook(self.settings_tab)
        self.connection_notebook = sub
        sub.pack(fill="both", expand=True)
        connection = ttk.Frame(sub, padding=18)
        self.api_settings_tab = connection
        self.fiscal_tab = ttk.Frame(sub)
        sub.add(connection, text="Conexiuni API")
        sub.add(self.fiscal_tab, text="TVA & plafon UE")
        fiscal_canvas = tk.Canvas(self.fiscal_tab, bg=BG, highlightthickness=0)
        fiscal_scroll = ttk.Scrollbar(self.fiscal_tab, orient="vertical", command=fiscal_canvas.yview)
        fiscal_canvas.configure(yscrollcommand=fiscal_scroll.set)
        fiscal_scroll.pack(side="right", fill="y")
        fiscal_canvas.pack(side="left", fill="both", expand=True)
        fiscal = ttk.Frame(fiscal_canvas, padding=18)
        fiscal_window = fiscal_canvas.create_window((0, 0), window=fiscal, anchor="nw")
        fiscal.bind("<Configure>", lambda _: fiscal_canvas.configure(scrollregion=fiscal_canvas.bbox("all")))
        fiscal_canvas.bind("<Configure>", lambda event: fiscal_canvas.itemconfigure(fiscal_window, width=event.width))
        self.fields = {}
        for name, val in asdict(self.settings).items():
            self.fields[name] = tk.BooleanVar(master=self, value=val) if isinstance(val, bool) else tk.StringVar(master=self, value=val)
        def entry(parent, row, name, label, secret=False, options=None):
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=6, padx=(0, 20))
            if options:
                widget = ttk.Combobox(parent, textvariable=self.fields[name], values=options, state="readonly", width=28)
            else:
                widget = ttk.Entry(parent, textvariable=self.fields[name], show="•" if secret else "", width=30)
            widget.grid(row=row, column=1, sticky="ew", pady=4)
            parent.columnconfigure(1, weight=1)
            return widget
        left = ttk.Frame(connection)
        left.pack(side="left", fill="both", expand=True, padx=(0, 30))
        right = ttk.Frame(connection)
        right.pack(side="left", fill="both", expand=True)
        ttk.Label(left, text="Trendyol International", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        entry(left, 1, "mode", "Mediu", options=["demo", "test", "production"])
        entry(left, 2, "seller_id", "Seller ID")
        entry(left, 3, "api_key", "API Key", True)
        entry(left, 4, "api_secret", "API Secret", True)
        entry(left, 5, "markets", "Piețe active")
        ttk.Label(left, text="demo: exemple locale, fără conexiuni.\ntest: Trendyol Stage + FGO UAT.\nproduction: conturile reale.\n\nStage necesită activarea IP-ului la Trendyol.\nFolosește cheile corespunzătoare mediului ales.\n\nUn singur Seller ID pentru piețele activate.", style="Sub.TLabel", wraplength=390).grid(row=6, column=0, columnspan=2, sticky="nw", pady=18)
        ttk.Label(right, text="FGO", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        for row, (name, label, secret) in enumerate([("cui", "CUI (fără RO)", False), ("fgo_key", "Cheie privată API", True), ("series_ro", "Serie România", False), ("series_bg", "Serie Bulgaria", False), ("series_gr", "Serie Grecia", False), ("platform_url", "URL integrare FGO", False)], 1):
            entry(right, row, name, label, secret)
        ttk.Checkbutton(right, text="Firma aplică TVA la încasare", variable=self.fields["cash_vat"]).grid(row=7, column=0, columnspan=2, sticky="w", pady=14)
        ttk.Label(right, text="Cheile se criptează cu Windows DPAPI pentru utilizatorul curent. Seria trebuie să existe în FGO.\n\nVerifică automatizarea RO e-Factura în contul FGO; încărcarea PDF-ului în Trendyol este o operațiune separată.", style="Sub.TLabel", wraplength=390).grid(row=8, column=0, columnspan=2, sticky="w", pady=8)
        ttk.Checkbutton(fiscal, text="Confirm: plătitor TVA în RO, stabilit numai în RO, expedieri din RO, B2C, fără opțiune OSS / taxare la destinație.", variable=self.fields["fiscal_confirmed"]).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        self.eur_direct_check = ttk.Checkbutton(fiscal, text="EUR direct în FGO — ignoră cursul și soldurile pentru plafon din aplicație", variable=self.fields["eur_direct_fgo"])
        self.eur_direct_check.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 5))
        ttk.Label(fiscal, text="Se aplică doar comenzilor în EUR. Suma în EUR se trimite integral către FGO; comenzile RO în RON rămân în RON.\nCând bifa este activă, aplicația nu verifică plafonul UE pentru EUR. TVA, maparea, seriile și verificarea comenzilor se păstrează.", foreground=MUTED, wraplength=1000).grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 8))
        for row, (name, label) in enumerate([("fiscal_year", "An fiscal curent"), ("previous_sales_ron", "Vânzări UE anul precedent — RON fără TVA"), ("opening_sales_ron", "Vânzări UE anul curent înaintea aplicației — RON fără TVA"), ("other_sales_ron", "Vânzări UE ulterioare, în afara aplicației — RON fără TVA"), ("fx_date", "Data emiterii pentru curs (AAAA-LL-ZZ)"), ("eur_ron", "Curs aplicabil: 1 EUR = RON"), ("bgn_ron", "Curs aplicabil: 1 BGN = RON (dacă este necesar)")], 3):
            entry(fiscal, row, name, label)
        entry(fiscal, 10, "subsidy_policy", "Compensații reduceri Trendyol", options=["include", "review"])
        ttk.Label(fiscal, text="În modul cu monitorizare: plafon 46.337 RON fără TVA, cumulat pe toate țările UE și canalele eligibile. Actualizează soldurile și cursul pentru data emiterii.\nÎn modul EUR direct: cursul și soldurile de mai sus nu sunt cerute pentru EUR. Verifică documentul și cursul în FGO; plafonul trebuie urmărit separat. Revenirea la monitorizare cere reconcilierea în RON a facturilor emise fără curs local.\ninclude = vânzări minus reducerea comerciantului; review = oprește comenzile cu reduceri finanțate de platformă.", foreground=MUTED, wraplength=1000).grid(row=11, column=0, columnspan=2, sticky="w", pady=14)
        self.emag_settings_tab = ttk.Frame(sub)
        sub.add(self.emag_settings_tab, text="eMAG RO / BG / HU")
        emag_canvas=tk.Canvas(self.emag_settings_tab,bg=BG,highlightthickness=0)
        emag_scroll=ttk.Scrollbar(self.emag_settings_tab,orient="vertical",command=emag_canvas.yview)
        emag_canvas.configure(yscrollcommand=emag_scroll.set)
        emag_scroll.pack(side="right",fill="y");emag_canvas.pack(side="left",fill="both",expand=True)
        emag=ttk.Frame(emag_canvas,padding=18)
        emag_window=emag_canvas.create_window((0,0),window=emag,anchor="nw")
        emag.bind("<Configure>",lambda _:emag_canvas.configure(scrollregion=emag_canvas.bbox("all")))
        emag_canvas.bind("<Configure>",lambda event:emag_canvas.itemconfigure(emag_window,width=event.width))
        ttk.Label(emag, text="Utilizator cu drepturi API + IP public autorizat în eMAG → Contul meu → Profil → Detalii tehnice.\nConexiunea eMAG funcționează în modul production. Folosește seriile și contul FGO configurate pentru firma ta.", foreground=MUTED, wraplength=1050).pack(anchor="w", pady=(0,12))
        accounts=ttk.Frame(emag);accounts.pack(fill="x")
        for index,market in enumerate(("RO","BG","HU")):
            frame=ttk.LabelFrame(accounts,text=f"eMAG {market}",padding=10)
            frame.grid(row=0,column=index,sticky="nsew",padx=(0,8));accounts.columnconfigure(index,weight=1)
            for row,(key,label) in enumerate((("user","Utilizator"),("password","Parolă API"),("series","Serie FGO"))):
                ttk.Label(frame,text=label).grid(row=row*2,column=0,sticky="w")
                ttk.Entry(frame,textvariable=self.fields[f"emag_{key}_{market.lower()}"],show="•" if key=="password" else "",width=23).grid(row=row*2+1,column=0,sticky="ew",pady=(2,8))
            frame.columnconfigure(0,weight=1)
        options=ttk.Frame(emag);options.pack(fill="x",pady=10)
        entry(options,0,"emag_markets","Piețe eMAG active (RO,BG,HU)")
        entry(options,1,"emag_shipping_code","Cod articol FGO pentru transport")
        entry(options,2,"emag_shipping_tax_mode","shipping_tax din API include TVA?",options=["","cu_tva","fara_tva"])
        entry(options,3,"huf_ron","Curs aplicabil: 1 HUF = RON")
        ttk.Label(emag,text="Transportul se facturează separat când este nenul; confirmă în contul eMAG dacă shipping_tax include TVA.\nHUF se trimite către FGO în HUF; cursul RON este folosit la verificarea plafonului comun Trendyol + eMAG.\nCompletează și data cursului / soldurile în TVA & plafon UE. Maparea comună se importă în Parametrizare → Mapare Excel.",foreground=MUTED,wraplength=1080).pack(anchor="w")

    def update_config_message(self, *_):
        changed = any(variable.get() != getattr(self.settings, name) for name, variable in self.fields.items())
        self.config_message.set("Modificări nesalvate — apasă Salvează (Ctrl+S)." if changed else "Parametrizările salvate se încarcă automat la fiecare deschidere.")


    def make_reports(self):
        ttk.Label(self.reports_tab, text="Vânzări pe țări și top 5 produse", style="Section.TLabel").pack(anchor="w", pady=(0, 10))
        ttk.Label(self.reports_tab, text="Sursă curentă: comenzile sincronizate local. Rapoartele complete din FGO necesită acces la API-ul de raportare / OAuth, încă neconfigurat.", foreground=MUTED, wraplength=1080).pack(anchor="w", pady=(0,10))
        year_bar = ttk.Frame(self.reports_tab)
        year_bar.pack(fill="x", pady=(0,8))
        ttk.Label(year_bar,text="An").pack(side="left")
        self.report_year = tk.StringVar(master=self,value=str(date.today().year))
        ttk.Spinbox(year_bar,from_=2000,to=2100,textvariable=self.report_year,width=6).pack(side="left",padx=8)
        ttk.Button(year_bar,text="Tot anul",command=self.report_whole_year).pack(side="left")
        filters = ttk.Frame(self.reports_tab)
        filters.pack(fill="x", pady=(0, 10))
        self.report_start = tk.StringVar(master=self, value=date.today().replace(day=1).isoformat())
        self.report_end = tk.StringVar(master=self, value=date.today().isoformat())
        self.report_market = tk.StringVar(master=self, value="Toate")
        self.report_provider = tk.StringVar(master=self, value="Toate")
        self.report_shipped = tk.BooleanVar(master=self, value=False)
        for label,var in [("Din",self.report_start),("Până în",self.report_end)]:
            ttk.Label(filters,text=label).pack(side="left",padx=(0,5))
            ttk.Entry(filters,textvariable=var,width=11).pack(side="left",padx=(0,10))
        ttk.Combobox(filters,textvariable=self.report_market,values=["Toate","RO","BG","GR","HU"],state="readonly",width=8).pack(side="left",padx=8)
        ttk.Combobox(filters,textvariable=self.report_provider,values=["Toate","Trendyol","eMAG"],state="readonly",width=9).pack(side="left",padx=8)
        ttk.Checkbutton(filters,text="Include și în expediere",variable=self.report_shipped).pack(side="left",padx=8)
        ttk.Button(filters,text="Actualizează raportul",command=self.refresh_reports).pack(side="right")
        ttk.Label(self.reports_tab,text="Trendyol: livrate (opțional și în expediere). eMAG: finalizate, fără confirmare separată a livrării. Filtrare după data comenzii.\nValori cu TVA, după reducerea comerciantului, înainte de comision; monede separate. Transportul intră în total, dar nu în top produse. Retururile parțiale nu sunt reconciliate automat.",foreground=MUTED,wraplength=1080).pack(anchor="w",pady=(0,10))
        self.country_report_tree = self.make_tree(self.reports_tab,[("country","Țară",85),("currency","Monedă",90),("orders","Comenzi distincte",140),("packages","Colete",85),("qty","Bucăți",90),("total","Vânzări cu TVA",160)],4)
        ttk.Label(self.reports_tab,text="Top 5 produse pe monedă",style="Section.TLabel").pack(anchor="w",pady=10)
        self.product_report_tree = self.make_tree(self.reports_tab,[("rank","Loc",60),("code","Cod produs",170),("name","Produs",390),("qty","Bucăți",90),("total","Vânzări cu TVA",150),("currency","Monedă",85)],5)
        self.report_message = tk.StringVar(master=self)
        ttk.Label(self.reports_tab,textvariable=self.report_message,foreground=MUTED,wraplength=1080).pack(anchor="w",pady=(8,0))

    def report_whole_year(self):
        try:
            year = int(self.report_year.get())
            start, end = date(year,1,1), date(year,12,31)
        except ValueError:
            self.report_message.set("Introdu un an valid.")
            return
        self.report_start.set(start.isoformat())
        self.report_end.set(end.isoformat())
        self.refresh_reports()

    def refresh_reports(self):
        if not hasattr(self,"country_report_tree"):
            return
        try:
            start,end = date.fromisoformat(self.report_start.get()),date.fromisoformat(self.report_end.get())
            if start>end:
                raise ValueError("Data de început trebuie să fie înaintea datei de sfârșit.")
            rows = self.store.orders() if self.report_provider.get() != "eMAG" else []
            mapping=dict(self.mapping); normalization_errors=[]
            if self.report_provider.get() != "Trendyol":
                from .emag import invoice_order
                for raw in self.emag_tab.store.orders():
                    if raw.get("shipmentPackageStatus") != "Shipped" or not start.isoformat() <= raw.get("_ordered_on", "") <= end.isoformat():
                        continue
                    try:
                        normalized,products_map=invoice_order(raw,self.emag_tab.service().mapping,self.settings)
                        # eMAG exposes finalization, not a comparable delivery state.
                        normalized["shipmentPackageStatus"]="Delivered"
                        rows.append(normalized);mapping.update(products_map)
                    except ValueError as exc:normalization_errors.append(f"eMAG {raw['orderNumber']}: {exc}")
            countries,products,errors = sales_report(rows,mapping,start.isoformat(),end.isoformat(),self.report_market.get(),self.report_shipped.get())
            errors=normalization_errors+errors
            self.country_report_tree.delete(*self.country_report_tree.get_children())
            self.product_report_tree.delete(*self.product_report_tree.get_children())
            for row in countries:
                self.country_report_tree.insert("","end",values=(row["country"],row["currency"],row["orders"],row["packages"],row["quantity"],f"{row['total']:.2f}"))
            for row in products:
                self.product_report_tree.insert("","end",values=(row["rank"],row["code"],row["name"],row["quantity"],f"{row['total']:.2f}",row["currency"]))
            self.report_message.set(f"{sum(r['packages'] for r in countries)} colete incluse. " + (f"{len(errors)} colete excluse cu date incomplete: {errors[0]}" if errors else "Actualizează comenzile pentru informații recente."))
        except ValueError as exc:
            self.report_message.set(f"Raport neactualizat: {exc}")

    def make_help(self):
        text = tk.Text(self.help_tab, wrap="word", bg="white", fg=INK, relief="flat", padx=22, pady=18, font=("Segoe UI", 11))
        text.pack(fill="both", expand=True)
        guide = resource("GHID.md")
        text.insert("1.0", guide.read_text(encoding="utf-8") if guide.exists() else "Consultă GHID.md din distribuția aplicației.")
        text.configure(state="disabled")

    def refresh(self):
        if not hasattr(self,"emag_tab"):return
        self.trendyol_tab.refresh();self.emag_tab.refresh()
        raws=self.store.orders()+self.emag_tab.store.orders()
        records={**self.store.records(),**self.emag_tab.store.records()}
        self.refresh_pending(raws,records)
        self.mapping_tree.delete(*self.mapping_tree.get_children())
        for platform,mapping in [("Trendyol",self.mapping),("eMAG",self.emag_tab.mapping)]:
            for p in mapping.values():
                if p.barcode=="__transport__":continue
                verified=not p.fgo_code or p.fgo_verified
                self.mapping_tree.insert("","end",values=(platform,p.barcode,p.fgo_code,p.name if verified else "De preluat din FGO",p.unit if verified else "",str(p.vat) if verified else ""))
        self.mapping_label.configure(text=f"{len(self.mapping)} EAN Trendyol • {sum(e!='__transport__' for e in self.emag_tab.mapping)} EAN eMAG")
        modes={"demo":"DEMO • FĂRĂ FACTURI REALE","test":"TEST • FGO UAT + TRENDYOL STAGE","production":"PRODUCȚIE • FACTURI REALE"}
        self.badge.configure(text=modes[self.settings.mode],bg="#fff0d5" if self.settings.mode!="production" else "#dcefe8")
        self.refresh_reports()



    def run_job(self, title, work, done):
        if self.busy:
            return
        self.busy = True
        self.status_text.set(title)
        for button in (self.save_button, self.articles_button, self.refresh_articles_button):
            button.configure(state="disabled")
        def runner():
            try:
                result = work(lambda s: self.jobs.put(("progress", s)))
                self.jobs.put(("done", (done, result)))
            except Exception as exc:
                self.jobs.put(("error", str(exc)))
        threading.Thread(target=runner, daemon=True).start()

    def poll(self):
        try:
            while True:
                kind, data = self.jobs.get_nowait()
                if kind == "progress":
                    self.status_text.set(data)
                    continue
                self.busy = False
                for button in (self.save_button, self.articles_button, self.refresh_articles_button):
                    button.configure(state="normal")
                self.refresh()
                self.status_text.set("Operațiune încheiată. Rezultatele sunt păstrate în Istoric.")
                if kind == "error":
                    messagebox.showerror("Operațiune oprită", data, parent=self)
                else:
                    callback, result = data
                    callback(result)
        except queue.Empty:
            pass
        self.poll_id = self.after(120, self.poll)


    def selected(self):
        ids=list(dict.fromkeys(self.pending_packages[i] for i in self.pending_tree.selection()))
        if not ids:messagebox.showinfo("Selectează comenzile","Selectează unul sau mai multe rânduri din De facturat.",parent=self)
        return ids

    def service_for_pid(self,pid):
        return self.emag_tab.service() if pid.startswith("emag:") else self.service()

    def prepare_selected(self,ids):
        drafts,errors,extra=[],[],Decimal("0")
        from .store import claim_conflicts
        claimed=set()
        for pid in dict.fromkeys(ids):
            try:
                service=self.service_for_pid(pid)
                d=service.draft(service.store.order(pid),extra)
                if any(claim_conflicts(a,b) for a in claimed for b in d.source_lines):raise ValueError("Lotul conține aceleași bucăți în două colete.")
                claimed.update(d.source_lines)
                drafts.append(d)
                if d.net_ron is not None:extra+=d.net_ron
            except ValueError as exc:errors.append(f"{pid}: {exc}")
        if any(d.net_ron is None for d in drafts) and any(d.country!="RO" and d.net_ron is not None for d in drafts):
            errors.append("Lotul combină EUR fără echivalent RON cu facturi pentru care plafonul trebuie monitorizat. Reconciliază valorile RON înaintea unui lot mixt.")
        return drafts,errors

    def preview_only(self):
        if self.busy:return
        ids=self.selected()
        if ids:self.preview_dialog(*self.prepare_selected(ids),None)

    def preview_dialog(self, drafts, errors, confirm):
        dialog = tk.Toplevel(self)
        dialog.title("Verifică facturile înainte de emitere")
        dialog.geometry("900x650")
        dialog.transient(self)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"{len(drafts)} facturi • {self.settings.mode.upper()}", style="Section.TLabel").pack(anchor="w", pady=(0, 12))
        body = tk.Text(frame, wrap="word", font=("Segoe UI", 10), padx=12, pady=12, relief="flat")
        body.pack(fill="both", expand=True)
        totals = defaultdict(Decimal)
        for d in drafts:
            totals[d.currency] += d.total
            client = d.payload["Client"]
            platform = "eMAG" if d.package_id.startswith("emag:") else "Trendyol"
            body.insert("end", f"{platform} • COMANDA {d.order_number} / Pachet {d.package_id} • {d.country}\n{client['Denumire']}\n{client['Adresa']}, {client['Localitate']}, {client.get('Judet', '')} {client['Tara']}\nSerie: {d.payload['Serie']} • Data: {d.payload['DataEmitere']}\n")
            for line in d.payload["Continut"]:
                body.insert("end", f"  {line['NrProduse']} {line['UM']} × {line['Denumire']} | TVA {line['CotaTVA']}% | cu TVA {line['PretTotal']} {d.currency}\n")
            body.insert("end", f"Bază estimată {d.net} + TVA estimat {d.vat} = DE FACTURAT {d.total} {d.currency}\nPlătit de client: {d.customer_paid}; finanțat de marketplace: {d.subsidy}.\n\n")
            if d.net_ron is None:
                body.insert("end", "EUR direct în FGO: suma se transmite în EUR, fără curs local și fără verificarea plafonului UE în aplicație. Verifică documentul final în FGO.\n\n")
        if errors:
            body.insert("end", "PROBLEME CARE OPRESC LOTUL\n" + "\n\n".join(errors))
        body.configure(state="disabled")
        ttk.Label(frame, text="Totaluri: " + " • ".join(f"{amount:.2f} {currency}" for currency, amount in totals.items()), style="Section.TLabel").pack(anchor="w", pady=12)
        ttk.Label(frame, text="FGO calculează valorile fiscale finale din prețul cu TVA. Verifică adresele, produsele, cota și totalul. Crearea facturii poate declanșa automatizările e-Factura configurate în FGO.", wraplength=820, foreground=MUTED).pack(anchor="w", pady=(0, 12))
        bar = ttk.Frame(frame)
        bar.pack(fill="x")
        ttk.Button(bar, text="Închide", command=dialog.destroy).pack(side="right")
        if confirm and not errors and drafts:
            def approved():
                dialog.destroy()
                confirm(drafts)
            ttk.Button(bar, text="Simulează emiterea" if self.settings.mode == "demo" else "Emite facturile verificate", style="Primary.TButton", command=approved).pack(side="left")

    def create_invoices(self):
        if self.busy:return
        ids=self.selected()
        if not ids:return
        drafts,errors=self.prepare_selected(ids)
        services={d.package_id:self.service_for_pid(d.package_id) for d in drafts}
        def confirmed(approved):
            def work(progress):
                return issue_batch(approved,services,progress)
            def done(result):
                count,failed=result
                messagebox.showinfo("Facturare",f"{count} facturi procesate. {len(failed)} comenzi cu erori / de verificat. Toate comenzile selectate au fost încercate."+("\n\n"+"\n".join(failed) if failed else "\nÎncarcă facturile din tabul platformei respective."),parent=self)
            self.run_job("Emit facturile selectate…",work,done)
        self.preview_dialog(drafts,errors,confirmed)


    def import_mapping(self):
        if self.busy:return
        path=filedialog.askopenfilename(title="Mapare comună Trendyol + eMAG",filetypes=[("Excel","*.xlsx")],parent=self)
        if not path:return
        try:
            self.apply_mapping_import(path)
            self.refresh();self.fetch_articles()
        except Exception as exc:messagebox.showerror("Mapare comună",str(exc),parent=self)

    def apply_mapping_import(self, path):
        imported=load_shared_mapping(path)
        trendyol={**self.catalog.mappings(self.settings.scope()),**self.mapping,**imported["trendyol"]}
        emag_scope=self.settings.emag_scope()+":ean"
        existing_emag=self.emag_tab.mapping if self.settings.unified_mapping_path else {}
        emag={**self.catalog.mappings(emag_scope),**existing_emag,**imported["emag"]}
        target=self.data_dir/"profiles"/self.settings.scope()/"mapare_comuna.xlsx"
        target.parent.mkdir(parents=True,exist_ok=True)
        temporary=target.with_suffix(".tmp.xlsx")
        write_shared_mapping(temporary,shared_rows(trendyol,emag))
        load_shared_mapping(temporary)
        if target.exists():shutil.copy2(target,target.with_suffix(".backup.xlsx"))
        os.replace(temporary,target)
        updated=Settings(**{**asdict(self.settings),"unified_mapping_path":str(target)})
        save_settings(self.settings_path,updated);self.settings=updated
        self.fields["unified_mapping_path"].set(str(target))
        self.catalog.merge_mappings(self.settings.scope(),trendyol)
        self.catalog.merge_mappings(emag_scope,emag)
        self.mapping=self.catalog.hydrate(self.settings.fgo_scope(),trendyol) if self.settings.mode!="demo" else trendyol
        self.emag_tab.profile=None;self.emag_tab.open_profile()

    def save_template(self):
        if self.busy:return
        path=filedialog.asksaveasfilename(title="Exportă maparea comună",initialfile="Mapare_comuna.xlsx",defaultextension=".xlsx",filetypes=[("Excel","*.xlsx")],parent=self)
        if path:
            try:
                e=self.emag_tab.mapping if self.settings.unified_mapping_path else {}
                write_shared_mapping(path,shared_rows(self.mapping,e))
                self.status_text.set("Excel comun exportat. Completează EAN-urile și importă fișierul actualizat.")
            except Exception as exc:messagebox.showerror("Export mapare",str(exc),parent=self)

    def reload_config(self):
        if self.busy:
            return
        try:
            self.settings = load_settings(self.settings_path)
            self.open_profile()
            for name, value in asdict(self.settings).items():
                self.fields[name].set(value)
            self.refresh()
            self.config_message.set("Setările salvate au fost reîncărcate.")
        except Exception as exc:
            messagebox.showerror("Reîncărcare setări", str(exc), parent=self)

    def save_config(self):
        if self.busy:
            return False
        try:
            new = Settings(**{k: v.get() for k, v in self.fields.items()})
            new.markets = ",".join(x.strip().upper() for x in new.markets.split(","))
            new.emag_markets = ",".join(x.strip().upper() for x in new.emag_markets.split(","))
            new.cui = new.cui.strip().upper().removeprefix("RO")
            new.seller_id = new.seller_id.strip()
            for name in ("api_key", "api_secret", "fgo_key", "series_ro", "series_bg", "series_gr", "platform_url"):
                setattr(new, name, getattr(new, name).strip())
            new.validate()
            # Config changes are reversible; credential completeness is checked when used.
            if self.load_error and self.settings_path.exists():
                shutil.copy2(self.settings_path, self.settings_path.with_suffix(".previous.json"))
            save_settings(self.settings_path, new)
            scope_changed = new.scope() != self.settings.scope()
            self.settings = new
            if scope_changed:
                self.open_profile()
            for name, value in asdict(new).items():
                self.fields[name].set(value)
            self.refresh()
            self.config_message.set("Parametrizări salvate. Se vor încărca automat la următoarea deschidere.")
            self.status_text.set("Parametrizări salvate pe acest calculator. Poți închide și redeschide aplicația.")
            return True
        except Exception as exc:
            self.config_message.set("Salvarea nu a reușit. Modificările nu sunt salvate.")
            messagebox.showerror("Setări", str(exc), parent=self)
            return False






    def close_app(self):
        if self.busy:
            messagebox.showinfo("Operațiune în curs", "Așteaptă finalizarea operațiunii curente înainte să închizi aplicația.", parent=self)
            return
        self.destroy()

    def destroy(self):
        if hasattr(self, "poll_id"):
            self.after_cancel(self.poll_id)
        super().destroy()

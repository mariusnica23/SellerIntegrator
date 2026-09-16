from pathlib import Path
import argparse
import ctypes
import sys


def main():
    parser = argparse.ArgumentParser(description="Integrator Trendyol – FGO pentru Windows")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--smoke-test", action="store_true", help="Verifică pornirea interfeței și închide; fără apeluri API.")
    args = parser.parse_args()
    from integrator.config import InstanceLock, data_root
    from integrator.ui import App
    directory = args.data_dir or data_root()
    lock = None
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    try:
        lock = InstanceLock(directory)
        app = App(directory)
        if args.smoke_test:
            app.after(900, app.destroy)
        app.mainloop()
        return 0
    except Exception as exc:
        if args.smoke_test:
            raise
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Trendyol – FGO", str(exc), parent=root)
        root.destroy()
        return 1
    finally:
        if lock:
            lock.close()


if __name__ == "__main__":
    sys.exit(main())

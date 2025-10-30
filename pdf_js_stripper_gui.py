#!/usr/bin/env python3
# pdf_js_stripper_gui.py
# GUI for batch-removing JavaScript from PDFs using pikepdf.
# - Choose input folder in a file dialog
# - Output folder auto-named "JStripped_<original>"
# - Progress bar + log + cancel
# - Optionally copy non-PDF files

import os
import sys
import threading
import queue
from pathlib import Path
import shutil
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ---- PDF logic (pikepdf-based, no Ghostscript) -------------------
import pikepdf
from pikepdf import Pdf, Name

def pdf_root(pdf: Pdf):
    """Handle differences across pikepdf versions."""
    try:
        return pdf.root
    except AttributeError:
        return pdf.trailer[Name("/Root")]

def deref(obj):
    """Return dereferenced object if indirect, else itself."""
    try:
        return obj.get_object()
    except Exception:
        return obj

def is_pdf(path: Path) -> bool:
    return path.suffix.lower() == ".pdf"

def has_js(pdf: Pdf) -> bool:
    root = pdf_root(pdf)
    if Name("/OpenAction") in root or Name("/AA") in root:
        return True
    if Name("/Names") in root:
        names = root[Name("/Names")]
        if isinstance(names, pikepdf.Dictionary) and Name("/JavaScript") in names:
            return True
    for page in pdf.pages:
        pobj = page.obj
        if Name("/AA") in pobj:
            return True
        if Name("/Annots") in pobj:
            annots = pobj[Name("/Annots")]
            if isinstance(annots, pikepdf.Array):
                for a in annots:
                    d = deref(a)
                    if isinstance(d, pikepdf.Dictionary):
                        if Name("/A") in d or Name("/AA") in d or Name("/JS") in d:
                            return True
    if Name("/AcroForm") in root:
        af = root[Name("/AcroForm")]
        if isinstance(af, pikepdf.Dictionary):
            if Name("/AA") in af or Name("/XFA") in af:
                return True
            if Name("/Fields") in af and isinstance(af[Name("/Fields")], pikepdf.Array):
                for fld_ref in af[Name("/Fields")]:
                    fld = deref(fld_ref)
                    if isinstance(fld, pikepdf.Dictionary):
                        if Name("/A") in fld or Name("/AA") in fld or Name("/JS") in fld:
                            return True
    return False

def remove_key(d, key):
    if key in d:
        del d[key]
        return True
    return False

def is_js_action(action_dict):
    try:
        return action_dict.get(Name("/S")) == Name("/JavaScript")
    except Exception:
        return False

def scrub_actions_dict(d):
    changed = False
    if Name("/A") in d:
        val = d[Name("/A")]
        if isinstance(val, pikepdf.Dictionary):
            if is_js_action(val):
                del d[Name("/A")]
                changed = True
        elif isinstance(val, pikepdf.Array):
            new_arr = pikepdf.Array([
                v for v in val
                if not (isinstance(deref(v), pikepdf.Dictionary) and is_js_action(deref(v)))
            ])
            if len(new_arr) != len(val):
                d[Name("/A")] = new_arr
                changed = True
    if Name("/AA") in d:
        aa = d[Name("/AA")]
        if isinstance(aa, pikepdf.Dictionary):
            keys_to_delete = []
            for k, v in list(aa.items()):
                v = deref(v)
                if isinstance(v, pikepdf.Dictionary) and is_js_action(v):
                    keys_to_delete.append(k)
            for k in keys_to_delete:
                del aa[k]
                changed = True
            if len(list(aa.keys())) == 0:
                del d[Name("/AA")]
                changed = True
        else:
            del d[Name("/AA")]
            changed = True
    return changed

def scrub_catalog(pdf):
    cat = pdf_root(pdf)
    changed = False
    changed |= remove_key(cat, Name("/OpenAction"))
    changed |= remove_key(cat, Name("/AA"))
    if Name("/Names") in cat:
        names = cat[Name("/Names")]
        if isinstance(names, pikepdf.Dictionary) and Name("/JavaScript") in names:
            del names[Name("/JavaScript")]
            changed = True
        try:
            if isinstance(names, pikepdf.Dictionary) and len(list(names.keys())) == 0:
                del cat[Name("/Names")]
                changed = True
        except Exception:
            pass
    return changed

def scrub_pages(pdf):
    changed = False
    for page in pdf.pages:
        pobj = page.obj
        if Name("/AA") in pobj:
            del pobj[Name("/AA")]
            changed = True
        if Name("/Annots") in pobj:
            annots = pobj[Name("/Annots")]
            if isinstance(annots, pikepdf.Array):
                for annot in annots:
                    ad = deref(annot)
                    if isinstance(ad, pikepdf.Dictionary):
                        if scrub_actions_dict(ad):
                            changed = True
                        if Name("/JS") in ad:
                            del ad[Name("/JS")]
                            changed = True
    return changed

def scrub_acroform(pdf):
    changed = False
    root = pdf_root(pdf)
    if Name("/AcroForm") in root:
        af = root[Name("/AcroForm")]
        if isinstance(af, pikepdf.Dictionary):
            if Name("/AA") in af:
                del af[Name("/AA")]
                changed = True
            if Name("/XFA") in af:
                del af[Name("/XFA")]
                changed = True
            if Name("/Fields") in af and isinstance(af[Name("/Fields")], pikepdf.Array):
                for fld_ref in af[Name("/Fields")]:
                    fld = deref(fld_ref)
                    if isinstance(fld, pikepdf.Dictionary):
                        if scrub_actions_dict(fld):
                            changed = True
                        if Name("/JS") in fld:
                            del fld[Name("/JS")]
                            changed = True
    return changed

def clean_pdf_to(in_path: Path, out_path: Path) -> str:
    """Clean a PDF of JavaScript or copy it if clean."""
    try:
        with Pdf.open(in_path, allow_overwriting_input=False) as pdf:
            if has_js(pdf):
                scrub_catalog(pdf)
                scrub_pages(pdf)
                scrub_acroform(pdf)
                pdf.save(out_path, linearize=True)
                return "cleaned"
            else:
                shutil.copy2(in_path, out_path)
                return "copied_clean"
    except pikepdf.PasswordError:
        shutil.copy2(in_path, out_path)
        return "copied_encrypted"
    except Exception:
        shutil.copy2(in_path, out_path)
        return "copied_error"

# ---- Worker thread ----------------------------------------------

class CleanerWorker(threading.Thread):
    def __init__(self, src_root: Path, out_root: Path, copy_nonpdf: bool, q: queue.Queue, stop_event: threading.Event):
        super().__init__(daemon=True)
        self.src_root = src_root
        self.out_root = out_root
        self.copy_nonpdf = copy_nonpdf
        self.q = q
        self.stop_event = stop_event

    def run(self):
        try:
            # 1) Count files to set progress maximum
            pdfs = []
            nonpdfs = []
            for dirpath, _, filenames in os.walk(self.src_root):
                for fname in filenames:
                    p = Path(dirpath) / fname
                    if is_pdf(p):
                        pdfs.append(p)
                    else:
                        nonpdfs.append(p)
            total_steps = len(pdfs) + (len(nonpdfs) if self.copy_nonpdf else 0)
            self.q.put(("meta", {"total": total_steps, "pdfs": len(pdfs), "nonpdfs": len(nonpdfs) if self.copy_nonpdf else 0}))

            # 2) Ensure output dirs exist
            for dirpath, _, _ in os.walk(self.src_root):
                rel_dir = Path(dirpath).relative_to(self.src_root)
                target_dir = self.out_root / rel_dir
                target_dir.mkdir(parents=True, exist_ok=True)

            processed = 0
            cleaned = 0
            copied_clean = 0
            copied_nonpdf = 0
            errors = 0

            # 3) Process PDFs
            for src in pdfs:
                if self.stop_event.is_set():
                    self.q.put(("log", f"[CANCELLED] {src.relative_to(self.src_root)}"))
                    break
                rel = src.relative_to(self.src_root)
                dst = self.out_root / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                status = clean_pdf_to(src, dst)
                processed += 1
                if status == "cleaned":
                    cleaned += 1
                    self.q.put(("log", f"[CLEANED] {rel}"))
                elif status == "copied_clean":
                    copied_clean += 1
                    self.q.put(("log", f"[COPIED CLEAN] {rel}"))
                else:
                    errors += 1
                    self.q.put(("log", f"[{status.upper()}] {rel}"))
                self.q.put(("progress", processed))

            # 4) Copy non-PDFs if requested
            if not self.stop_event.is_set() and self.copy_nonpdf:
                for src in nonpdfs:
                    if self.stop_event.is_set():
                        self.q.put(("log", f"[CANCELLED] {src.relative_to(self.src_root)}"))
                        break
                    rel = src.relative_to(self.src_root)
                    dst = self.out_root / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.copy2(src, dst)
                        copied_nonpdf += 1
                        processed += 1
                        self.q.put(("log", f"[COPIED NONPDF] {rel}"))
                    except Exception:
                        errors += 1
                        processed += 1
                        self.q.put(("log", f"[ERROR NONPDF] {rel}"))
                    self.q.put(("progress", processed))

            # 5) Summary
            self.q.put(("done", {
                "pdf_total": len(pdfs),
                "cleaned": cleaned,
                "copied_clean": copied_clean,
                "copied_nonpdf": copied_nonpdf,
                "errors": errors
            }))
        except Exception as e:
            self.q.put(("fatal", str(e)))

# ---- GUI ---------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PDF JavaScript Stripper")
        self.geometry("760x520")
        self.minsize(720, 480)

        # Vars
        self.src_path = tk.StringVar()
        self.copy_nonpdf = tk.BooleanVar(value=True)
        self.out_path = tk.StringVar(value="")
        self.progress_total = 0
        self.stop_event = threading.Event()
        self.q = queue.Queue()
        self.worker = None

        # Layout
        pad = {"padx": 10, "pady": 6}
        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True, **pad)

        # Row 1: source folder
        row1 = ttk.Frame(frm)
        row1.pack(fill="x", pady=(0, 4))
        ttk.Label(row1, text="Input folder:").pack(side="left")
        ttk.Entry(row1, textvariable=self.src_path).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row1, text="Browse…", command=self.browse).pack(side="left")

        # Row 2: output folder display (read-only)
        row2 = ttk.Frame(frm)
        row2.pack(fill="x", pady=(0, 4))
        ttk.Label(row2, text="Output folder:").pack(side="left")
        self.out_entry = ttk.Entry(row2, textvariable=self.out_path, state="readonly")
        self.out_entry.pack(side="left", fill="x", expand=True, padx=6)

        # Row 3: options
        row3 = ttk.Frame(frm)
        row3.pack(fill="x", pady=(0, 4))
        ttk.Checkbutton(row3, text="Copy non-PDF files", variable=self.copy_nonpdf).pack(side="left")

        # Row 4: controls
        row4 = ttk.Frame(frm)
        row4.pack(fill="x", pady=(4, 8))
        self.start_btn = ttk.Button(row4, text="Start", command=self.start)
        self.start_btn.pack(side="left")
        self.cancel_btn = ttk.Button(row4, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_btn.pack(side="left", padx=(8,0))

        # Row 5: progress
        row5 = ttk.Frame(frm)
        row5.pack(fill="x")
        self.prog = ttk.Progressbar(row5, orient="horizontal", mode="determinate", maximum=100)
        self.prog.pack(fill="x", expand=True)
        self.prog_lbl = ttk.Label(row5, text="Idle")
        self.prog_lbl.pack(anchor="w", pady=(4,0))

        # Row 6: log
        row6 = ttk.Frame(frm)
        row6.pack(fill="both", expand=True, pady=(8,0))
        self.log = tk.Text(row6, height=16, wrap="none")
        self.log.pack(side="left", fill="both", expand=True)
        yscroll = ttk.Scrollbar(row6, orient="vertical", command=self.log.yview)
        yscroll.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=yscroll.set)

        # Poll queue for worker updates
        self.after(100, self.poll_queue)

    def browse(self):
        path = filedialog.askdirectory(title="Select input folder")
        if not path:
            return
        self.src_path.set(path)
        in_path = Path(path)
        out_folder_name = f"JStripped_{in_path.name}"
        out_root = in_path.parent / out_folder_name
        self.out_path.set(str(out_root))

    def start(self):
        src = self.src_path.get().strip()
        if not src:
            messagebox.showwarning("Select folder", "Please choose an input folder.")
            return
        in_path = Path(src)
        if not in_path.exists() or not in_path.is_dir():
            messagebox.showerror("Invalid folder", "The selected input folder does not exist.")
            return

        out_folder_name = f"JStripped_{in_path.name}"
        out_root = in_path.parent / out_folder_name
        try:
            out_root.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Cannot create output", str(e))
            return
        self.out_path.set(str(out_root))

        # Reset UI
        self.log_delete()
        self.progress_total = 0
        self.prog["value"] = 0
        self.prog_lbl.config(text="Preparing…")
        self.stop_event.clear()
        self.start_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")

        # Launch worker
        self.worker = CleanerWorker(
            src_root=in_path,
            out_root=out_root,
            copy_nonpdf=self.copy_nonpdf.get(),
            q=self.q,
            stop_event=self.stop_event
        )
        self.worker.start()

    def cancel(self):
        if self.worker and self.worker.is_alive():
            self.stop_event.set()
            self.log_append("[INFO] Cancel requested. Finishing current file…\n")

    def poll_queue(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "meta":
                    total = payload.get("total", 0)
                    self.progress_total = max(total, 1)
                    self.prog.configure(maximum=self.progress_total)
                    self.prog_lbl.config(text=f"0 / {self.progress_total}")
                elif kind == "progress":
                    val = payload
                    self.prog["value"] = val
                    self.prog_lbl.config(text=f"{val} / {self.progress_total}")
                elif kind == "log":
                    self.log_append(payload + "\n")
                elif kind == "done":
                    self.finish_ok(payload)
                elif kind == "fatal":
                    self.finish_err(payload)
        except queue.Empty:
            pass
        # keep polling
        self.after(100, self.poll_queue)

    def finish_ok(self, summary):
        self.start_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        self.prog_lbl.config(text="Done")
        msg = (
            f"\nSummary:\n"
            f"  PDFs processed: {summary.get('pdf_total', 0)}\n"
            f"    cleaned (JS removed): {summary.get('cleaned', 0)}\n"
            f"    already clean -> copied: {summary.get('copied_clean', 0)}\n"
            f"  non-PDFs copied: {summary.get('copied_nonpdf', 0)}\n"
            f"  errors or encrypted (copied as-is): {summary.get('errors', 0)}\n"
        )
        self.log_append(msg)

    def finish_err(self, err):
        self.start_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        self.prog_lbl.config(text="Error")
        self.log_append(f"\n[FATAL] {err}\n")
        messagebox.showerror("Error", err)

    def log_append(self, text):
        self.log.insert("end", text)
        self.log.see("end")

    def log_delete(self):
        self.log.delete("1.0", "end")

if __name__ == "__main__":
    app = App()
    app.mainloop()

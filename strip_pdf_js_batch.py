#!/usr/bin/env python3
# strip_pdf_js_batch.py
# Batch-remove JavaScript from PDFs using pikepdf.
# Creates a mirrored output folder named "JStripped_<original>" next to the input folder.

import argparse
import sys
import os
import shutil
from pathlib import Path
import pikepdf
from pikepdf import Pdf, Name
from tqdm import tqdm

# --- helpers ------------------------------------------------------

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

# --- detection and cleaning --------------------------------------

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

# --- cleaning logic ----------------------------------------------

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

# --- traversal ---------------------------------------------------

def mirror_tree(src_root: Path, out_root: Path, copy_nonpdf: bool, verbose: bool) -> int:
    src_root = src_root.resolve()
    out_root = out_root.resolve()
    count_total = count_cleaned = count_copied_clean = count_copied_nonpdf = count_errors = 0

    for dirpath, _, filenames in os.walk(src_root):
        rel_dir = Path(dirpath).relative_to(src_root)
        target_dir = out_root / rel_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        for fname in filenames:
            src = Path(dirpath) / fname
            rel_file = src.relative_to(src_root)
            dst = out_root / rel_file
            dst.parent.mkdir(parents=True, exist_ok=True)

            if is_pdf(src):
                count_total += 1
                status = clean_pdf_to(src, dst)
                if status == "cleaned":
                    count_cleaned += 1
                    if verbose: tqdm.write(f"[CLEANED] {rel_file}")
                elif status == "copied_clean":
                    count_copied_clean += 1
                    if verbose: tqdm.write(f"[COPIED CLEAN] {rel_file}")
                else:
                    count_errors += 1
                    if verbose: tqdm.write(f"[{status.upper()}] {rel_file}")
            elif copy_nonpdf:
                try:
                    shutil.copy2(src, dst)
                    count_copied_nonpdf += 1
                    if verbose: tqdm.write(f"[COPIED NONPDF] {rel_file}")
                except Exception:
                    count_errors += 1
                    if verbose: tqdm.write(f"[ERROR NONPDF] {rel_file}")

    print(f"\nSummary for {src_root.name}:")
    print(f"  PDFs processed: {count_total}")
    print(f"    cleaned (JS removed): {count_cleaned}")
    print(f"    already clean -> copied: {count_copied_clean}")
    print(f"  non-PDFs copied: {count_copied_nonpdf}")
    print(f"  errors or encrypted (copied as-is): {count_errors}")
    return 0

# --- entrypoint ---------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Mirror a folder and remove JavaScript from PDFs.")
    ap.add_argument("input", help="Input folder or single PDF")
    ap.add_argument("--skip-nonpdf", action="store_true", help="Skip copying non-PDF files.")
    ap.add_argument("-v", "--verbose", action="store_true", help="Verbose output per file.")
    args = ap.parse_args()

    in_path = Path(args.input).resolve()

    # Single file mode
    if in_path.is_file() and is_pdf(in_path):
        out_file = in_path.with_name(in_path.stem + "_nojs.pdf")
        status = clean_pdf_to(in_path, out_file)
        print(f"{in_path.name} -> {out_file.name} [{status}]")
        return 0

    if not in_path.is_dir():
        print("Input must be a folder or PDF file.")
        return 1

    parent = in_path.parent
    out_folder_name = f"JStripped_{in_path.name}"
    out_root = parent / out_folder_name
    out_root.mkdir(parents=True, exist_ok=True)

    print(f"Input:  {in_path}")
    print(f"Output: {out_root}  (mirroring subfolders)")

    return mirror_tree(in_path, out_root, copy_nonpdf=not args.skip_nonpdf, verbose=args.verbose)

if __name__ == "__main__":
    sys.exit(main())

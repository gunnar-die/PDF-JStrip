#ðŸ§° PDF-JStrip - PDF JavaScript Remover

**PDF-JStrip** is a standalone utility for removing embedded JavaScript from PDF files â€” especially useful for Audi / VW erWin manuals that prompt for JavaScript acknowledgment and canâ€™t open properly in most browsers or PDF viewers.

It automatically scans a folder (and its subfolders), removes any JavaScript actions, and outputs clean, browser-friendly PDFs.

---

## ðŸš€ Features

- Removes all JavaScript actions from PDFs using the `pikepdf` library  
- Recursively mirrors subfolders into a new `JStripped_<folder name>` directory  
- Optionally copies non-PDF files so the folder structure remains identical  
- Works completely offline  
- Available as both a **Python script** and a **standalone EXE** (no install required)

---

## ðŸ“¦ Download

ðŸ‘‰ [**Download JStripper for Windows**](https://github.com/gunnar-die/PDF-JStrip/releases/latest)

No installation required â€” just download and run the `.exe`, then:
1. Choose the folder containing your PDFs.  
2. Wait for processing to finish.  
3. Find your cleaned copies in `JStripped_<original folder name>`.

---

## ðŸ§  Usage (Python version)

If you prefer to run it from source:

```bash
pip install pikepdf
python pdf_js_stripper_gui.py
```
Then use the GUI to select a folder.

For the command-line version:

```bash
python strip_pdf_js_batch.py "path/to/input/folder"
```

---

## ðŸ§° Developer Notes

### Building the EXE
If you want to build your own executable:
```bash
pip install pyinstaller
pyinstaller --onefile --windowed pdf_js_stripper_gui.py
```
The compiled binary will appear under:
```
dist/pdf_js_stripper_gui.exe
```

### Building the Docker image
```bash
docker build -t pdf-js-stripper .
docker run --rm -v "C:\path\to\manuals:/work" pdf-js-stripper "/work/A8_Manual"
```

---

## ðŸ§¹ .gitignore
```
/dist/
/build/
*.spec
__pycache__/
*.pyc
```

---

## ðŸ§¾ License

MIT License â€” free for personal and commercial use.  
Use at your own risk; no warranty implied.

---

### âœ‰ï¸ Author

**Gunnar Diekmann**  
[github.com/gunnar-die](https://github.com/gunnar-die)



<div align="center">

**English** · [Español](README.es.md)

<img src="build/icon.png" width="88" alt="">

# Folio PDF

**Everything you need to do with a PDF, in one place.**

Read, fill in, sign, edit and organize your PDFs on Windows.<br>
Free, no ads, no accounts, and your documents are never uploaded anywhere.

### [⬇&nbsp; Download Folio for Windows](https://github.com/CuevasTech/FolioPDF/releases/latest)

<img src=".github/presupuesto.png" alt="Folio with a quote that calculates the amounts, the tax and the total by itself" width="860">

</div>

## What you can do

- **Fill in forms**, including the ones that calculate amounts, tax and totals by themselves, and write on any PDF even if it has no fields to fill in.
- **Sign** by hand (drawing, typing your name or with a photo of your signature) or with your **digital certificate**, such as the one from Spain’s FNMT, with a timestamp included.
- **Edit** the text of the PDF itself and add text, images and shapes.
- **Annotate**: highlight, underline, strike through, notes and freehand drawing.
- **Organize pages**: reorder, rotate, delete, extract, split and combine documents.
- **Print** with a preview: multiple pages per sheet (also the same page repeated, for labels or flyers), booklets to fold, posters across several sheets, other paper sizes and two-sided.
- **Scan paper documents** with photos from your phone or with your scanner or all-in-one printer: Folio finds the sheet in each photo, straightens it, removes the shadows and makes it look freshly scanned.
- **AI on your computer, optional**: summarizes the document, answers questions pointing to the pages each answer comes from, searches by meaning, translates and improves texts and finds personal data to redact. It works offline: the models are only downloaded if you turn it on, and your documents never leave your computer.
- And also: recognize the text of scanned documents (OCR), protect with a password, truly redact, compress and convert to Word or images.

Folio is available in English and Spanish, and follows the language of Windows.

<p align="center">
  <img src=".github/firma.png" alt="A permission form filled in and signed by hand in Folio" width="49%">
  <img src=".github/imprimir.png" alt="Folio’s print dialog, with the preview of four pages per sheet" width="49%">
</p>

## Your documents stay on your computer

Folio works offline: it doesn’t upload your PDFs to any server or ask you for an account. It only uses the Internet when you ask it to (the timestamp of a signature, which receives a fingerprint of the document, never the document itself; feedback you send us; or downloading the AI models, from Hugging Face) and to let you know about new versions. The AI runs on your computer too: what you ask and what it answers never leave it.

On top of that, each PDF is opened in an isolated Windows sandbox, designed so that a booby-trapped file can’t touch your other files or connect to the Internet.

## Installation

1. Download **Folio-Setup** from the [latest version](https://github.com/CuevasTech/FolioPDF/releases/latest).
2. Open it and follow the steps. If Windows shows “Windows protected your PC”, click **More info** and then **Run anyway**: it does this with new programs that don’t have a digital signature yet.
3. Done. When there’s a new version, Folio will let you know.

For 64-bit Windows 10 and 11.

## Ideas or something not working?

Tell us from Folio, in the **…** menu → **Send feedback**, or in [Issues](https://github.com/CuevasTech/FolioPDF/issues). And if you find Folio useful, give it a ⭐: it helps more people find it.

## Support Folio

Folio is free and will stay free, with no ads and no paid versions. If you find it useful, you can buy me a coffee on [GitHub Sponsors](https://github.com/sponsors/CuevasTech).

## License

Folio is free software under the [GNU AGPL, version 3 or later](LICENSE), and comes with no warranty. © 2026 CuevasTech.<br>
It includes third-party components with their own licenses: the list is in Folio, under **About Folio → Third-party components**.

<details>
<summary>Build from source</summary>

<br>

You need Node 22.12 or later and Python 3.12 or later. In PowerShell:

```powershell
npm ci --ignore-scripts
npm run electron:install
py -m venv engine\.venv
engine\.venv\Scripts\python.exe -m pip install --no-deps --only-binary :all: --require-hashes -r engine\requirements.txt
engine\.venv\Scripts\python.exe -m pip install --only-binary :all: --require-hashes -r engine\requirements-build.txt
powershell -File scripts\fetch-tessdata.ps1
npm run dist
```

The installer ends up in `dist\`.

</details>

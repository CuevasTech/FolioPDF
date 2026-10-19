import type { tools as source } from '../es/tools'

export const tools: typeof source = {
  back: 'Tools',
  backTip: 'Back to all tools',
  home: {
    title: 'More tools',
    lead: 'Recognize text, protect, shrink, convert, and add elements to every page.',
    privacy: 'Text and privacy',
    protection: 'Protection',
    convert: 'Shrink and convert',
    add: 'Add to pages'
  },
  exported: 'File exported',
  word: {
    title: 'Convert to Word',
    card: 'Turns the content into an editable .docx document.',
    busy: 'Converting to Word'
  },
  text: {
    title: 'Export to text',
    card: 'Extracts all the text to a .txt file.',
    busy: 'Exporting to text'
  },
  unprotect: {
    title: 'Remove protection',
    card: 'Saves the document without a password or restrictions.',
    notProtected: 'This document isn’t protected.',
    done: 'Protection will be removed when you save the document'
  },
  flatten: {
    title: 'Flatten',
    card: 'Turns fields and annotations into fixed content.',
    confirmTitle: 'Flatten form and annotations',
    confirmMessage: 'Filled-in fields and annotations will become part of the page and can no longer be edited.'
  },
  redact: {
    title: 'Redact',
    card: 'Permanently removes sensitive data from the document.',
    confirmTitle: { one: 'Redact {n} area', other: 'Redact {n} areas' },
    confirmMessage:
      'The marked content will be permanently removed from the document when you save: it can’t be recovered by copying the text or with other apps.',
    signedNote: 'The document’s digital signatures will no longer be valid.',
    flattenOption: 'Turn redacted pages into images',
    flattenHint:
      'Maximum security: those pages keep only an image of what you see, with no text or hidden layers. You can recognize the text with OCR afterward.',
    busy: 'Redacting',
    done: 'Content redacted. Save the document to make it permanent.',
    flattened: {
      one: 'The redacted page is now an image: its text can no longer be searched or copied.',
      other: 'The {n} redacted pages are now images: their text can no longer be searched or copied.'
    },
    recognize: 'Recognize text',
    alsoOn: {
      one: 'Some of the redacted content also appears on page {pages} and is still visible there. Redact it on that page too.',
      other: 'Some of the redacted content also appears on pages {pages} and is still visible there. Redact it on those pages too.'
    },
    ocrRemoved: 'The OCR text layer was removed from the redacted pages.',
    recognizeAgain: 'Recognize again',
    hiddenText: 'Some pages still have invisible text outside the redacted areas.',
    sharedFields: 'A redacted form field also appears on another page and keeps its value there.',
    markArea: 'Mark area',
    markAreaTip: 'Drag over what you want to redact',
    markText: 'Mark text',
    markTextTip: 'Select the text you want to redact',
    applyCount: 'Redact ({n})',
    removeMarkTip: 'Click to remove this mark',
    hintEmpty: 'Mark what you want to remove. Nothing is deleted until you click “Redact.”',
    hintMarked: {
      one: '{n} area marked. Click a mark to remove it.',
      other: '{n} areas marked. Click a mark to remove it.'
    }
  },
  redactFind: {
    title: 'Find and mark',
    run: 'Mark matches',
    found: { one: '{n} match marked for redaction', other: '{n} matches marked for redaction' },
    none: 'No matches found',
    byText: 'Specific text',
    byPattern: 'A type of data',
    textLabel: 'Text to redact throughout the document',
    patternLabel: 'Type of data',
    patterns: {
      dni: 'Spanish ID number (DNI)',
      nie: 'Spanish foreigner ID (NIE)',
      email: 'Email address',
      phone: 'Phone number',
      iban: 'Bank account (IBAN)',
      card: 'Credit or debit card'
    },
    note: 'Matches are marked so you can review them; nothing is removed until you click “Redact.”'
  },
  crop: {
    title: 'Crop',
    card: 'Removes margins or anything extra from the pages.',
    removed: 'Pages cropped: everything outside was removed',
    hidden: 'Pages cropped: everything outside is hidden, not removed',
    message: 'Only the marked area will be kept. You can crop this page or every page in the document with the same area.',
    thisPage: 'Crop this page',
    allPages: 'Crop all',
    removeOption: 'Remove what’s outside (don’t just hide it)',
    removeHint:
      'Everything outside is deleted from the file, as with redaction. Without this option it’s only hidden, and another app could show it again.',
    hint: 'Draw the area you want to keep on the page'
  },
  protect: {
    title: 'Protect with password',
    card: 'Encrypts the PDF and limits printing, copying, or editing.',
    run: 'Protect',
    done: 'The document will be encrypted when you save it',
    userLabel: 'Password to open (optional)',
    userHint: 'Leave it empty if anyone can open it but you want to limit what can be done with it.',
    ownerLabel: 'Owner password',
    ownerHint: 'Lets you change these restrictions. At least 4 characters.',
    repeatLabel: 'Repeat it',
    mismatch: 'The owner passwords don’t match.',
    same: 'The owner password must be different from the open password.',
    weak: 'An open password shorter than 8 characters can be guessed by brute force. Use a long phrase if the content is sensitive.',
    signed: 'The document is signed: saving it with new protection will invalidate its digital signatures.',
    permsLabel: 'Anyone who opens the document can…',
    perms: {
      print: 'Print',
      copy: 'Copy text and images',
      modify: 'Edit the content',
      annotate: 'Add comments',
      form: 'Fill in forms',
      assemble: 'Rearrange pages'
    },
    note: 'It uses 256-bit AES encryption. Folio doesn’t store passwords: if you forget them, there’s no way to recover the protected document.'
  },
  sanitize: {
    title: 'Remove hidden data',
    card: 'Removes metadata, attachments, scripts, and invisible text.',
    run: 'Remove',
    done: 'Hidden data removed. Save the document to make it permanent.',
    intro: 'Choose what information you want to remove before sharing the document.',
    items: {
      metadata: 'Metadata (author, title, app, dates)',
      attachments: 'Attached files',
      javascript: 'Embedded scripts',
      hiddenText: 'Invisible text',
      thumbnails: 'Saved thumbnails',
      comments: 'Comments and annotations',
      links: 'Links'
    },
    signed: 'The document is signed: removing hidden data will invalidate its digital signatures.'
  },
  optimize: {
    title: 'Compress',
    card: 'Saves a lighter copy to send by email.',
    run: 'Save compressed copy',
    busy: 'Compressing',
    busyMessage: 'This may take a few seconds…',
    saved: 'Copy saved: {before} → {after} ({percent}% smaller)',
    savedLittle: 'Copy saved. This document was already well optimized and could barely be reduced.',
    current: 'Current size: {size}. A copy is saved; the open document doesn’t change.',
    light: 'Light',
    balanced: 'Balanced',
    strong: 'Maximum',
    lightHint: 'Keeps print quality; reduces only a little.',
    balancedHint: 'Good on-screen quality; recommended for sending by email.',
    strongHint: 'The smallest possible size; images lose sharpness.',
    grayscale: 'Convert images to grayscale',
    signed: 'The compressed copy won’t keep the digital signatures.'
  },
  images: {
    title: 'Convert to images',
    card: 'One PNG or JPEG image for each page.',
    run: 'Choose folder and export',
    busy: 'Exporting images',
    saved: { one: 'Saved {n} image', other: 'Saved {n} images' },
    savedReduced: {
      one: 'Saved {n} image ({reduced} at a lower resolution because of its size)',
      other: 'Saved {n} images ({reduced} at a lower resolution because of their size)'
    },
    format: 'Format',
    resolution: 'Resolution',
    screen: 'Screen',
    normal: 'Normal',
    print: 'Print',
    count: {
      one: 'One image will be created for the page.',
      other: 'One image will be created for each of the {n} pages.'
    }
  },
  watermark: {
    title: 'Watermark',
    card: 'Text or an image over every page.',
    defaultText: 'CONFIDENTIAL',
    run: 'Add to all pages',
    busy: 'Adding watermark',
    text: 'Text',
    image: 'Image',
    bold: 'Bold',
    color: 'Color',
    pickImage: 'Choose image…',
    imageHint: 'PNG with transparency recommended',
    opacity: 'Opacity',
    size: 'Size (page width)',
    diagonal: 'Diagonal',
    tile: 'Repeat across the page',
    background: 'Behind the content'
  },
  headerFooter: {
    title: 'Header and footer',
    cardTitle: 'Header and footer',
    card: 'Fixed text at the top and bottom, with the date or file name.',
    run: 'Add',
    busy: 'Adding header and footer',
    header: 'Header',
    footer: 'Footer',
    hint: 'You can use {page}, {pages}, {date}, and {file}.',
    left: 'Left',
    center: 'Center',
    right: 'Right',
    fontSize: 'Font size'
  },
  pageNumbers: {
    title: 'Page numbers',
    card: 'Automatic numbering in the position you choose.',
    run: 'Add numbers',
    busy: 'Numbering pages',
    format: 'Format',
    formatPage: 'Page {page}',
    formatPageOf: 'Page {page} of {pages}',
    position: 'Position',
    positions: {
      bottomCenter: 'Bottom center',
      bottomRight: 'Bottom right',
      bottomLeft: 'Bottom left',
      topCenter: 'Top center',
      topRight: 'Top right',
      topLeft: 'Top left'
    },
    start: 'Start at',
    size: 'Size'
  },
  removeAdded: {
    title: 'Remove added items',
    card: 'Removes watermarks, headers, page numbers, or the OCR layer.',
    run: 'Remove',
    searching: 'Searching…',
    nothing: 'This document has no watermarks, headers, page numbers, or OCR layer added with Folio.',
    kinds: {
      watermark: 'Watermarks',
      header: 'Headers',
      footer: 'Footers',
      pagenum: 'Page numbers',
      ocr: 'OCR text layer'
    }
  },
  ocr: {
    title: 'Recognize text (OCR)',
    card: 'Makes the text of scanned pages selectable and searchable.',
    run: 'Recognize',
    busy: 'Recognizing text',
    analyzing: 'Analyzing the pages…',
    recognized: 'Text recognized on {pages}',
    recognizedSomeEmpty: 'Text recognized on {pages} (no text found on {empty})',
    straightened: {
      one: 'Straightened {n} page that was rotated',
      other: 'Straightened {n} pages that were rotated'
    },
    nothingFound: 'No text to recognize was found on {pages}',
    allHadText: 'All pages already had text: nothing needed to be recognized',
    missingData: 'The OCR language data is missing. Run scripts/fetch-tessdata.ps1 to download it.',
    intro: 'Adds an invisible text layer over scanned pages without changing the image. Then you can search, select, and copy.',
    languages: 'Document languages',
    languageNames: {
      spa: 'Spanish',
      eng: 'English',
      cat: 'Catalan',
      glg: 'Galician',
      eus: 'Basque',
      fra: 'French',
      deu: 'German',
      por: 'Portuguese',
      ita: 'Italian'
    },
    skipText: 'Skip pages that already have text',
    straighten: 'Straighten scanned pages that are sideways or upside down'
  }
}

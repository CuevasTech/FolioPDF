import type { scan as source } from '../es/scan'

export const scan: typeof source = {
  title: 'Scan document',
  styles: {
    auto: 'Auto',
    color: 'Color',
    gray: 'Grayscale',
    bw: 'B&W',
    original: 'Original'
  },
  styleHints: {
    auto: 'White paper and crisp text; in color only if there’s color (a stamp, a signature in blue).',
    color: 'White paper and brighter colors.',
    gray: 'White paper and crisp text, in grayscale.',
    bw: 'Black and white only: the smallest file, ideal for text.',
    original: 'The photo as is, just cropped and straightened.'
  },
  edges: {
    hint: 'Drag the corners to the corners of the paper.',
    photo: 'Full photo',
    topLeft: 'Top-left corner',
    topRight: 'Top-right corner',
    bottomRight: 'Bottom-right corner',
    bottomLeft: 'Bottom-left corner',
    position: '{x}%, {y}%',
    loading: 'Loading the photo…',
    whole: 'Whole photo',
    detected: 'Detected edges',
    crossed: 'The corners cross each other.'
  },
  notices: {
    noEdges: 'Couldn’t look for the edges of the paper: the photos are kept whole. You can adjust them by hand.',
    someWhole: 'The paper isn’t clear in some photos, so they’re kept whole. Adjust their corners if needed.'
  },
  busy: {
    photos: 'Preparing the photos…',
    scanning: 'Scanning…',
    creatingOcr: 'Creating the PDF and recognizing the text…',
    creating: 'Creating the PDF…'
  },
  stop: 'Stop',
  create: 'Create PDF',
  empty: {
    title: 'Add photos of the document or scan it',
    text: 'Folio finds the paper in each photo, straightens it, removes shadows and makes the background white, as if freshly scanned.'
  },
  page: {
    label: 'Page {n}. Alt and the arrow keys to move it; Delete to remove it.',
    alt: 'Page {n}',
    rotateLeft: 'Rotate left',
    rotateRight: 'Rotate right',
    edges: 'Adjust the edges',
    remove: 'Remove the page'
  },
  photos: {
    title: 'Photos',
    add: 'Add photos or images…',
    hint: 'Are the photos on your phone? Move them to your computer with Phone Link, WhatsApp Web or OneDrive. Take each photo from above, in good light, with the paper on a darker background.'
  },
  scanner: {
    title: 'Scanner',
    searching: 'Looking for scanners…',
    none: 'No scanner is installed. Connect one or add it in Windows Settings → Bluetooth & devices → Printers & scanners.',
    searchAgain: 'Search again',
    refresh: 'Search for scanners again',
    paper: 'Paper',
    flatbed: 'Flatbed',
    feeder: 'Feeder',
    duplex: 'Both sides',
    color: 'Color',
    colorLabel: 'Scan color',
    gray: 'Grayscale',
    bw: 'Black and white',
    resolution: 'Resolution',
    dpi: '{n} dpi',
    scan: 'Scan',
    scanMore: 'Scan more pages'
  },
  finish: {
    title: 'Finish',
    ocr: 'Recognize text (OCR) so you can search and copy it',
    ocrUnavailable: 'Text recognition isn’t available in this installation.'
  }
}

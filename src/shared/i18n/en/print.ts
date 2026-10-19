import type { print as source } from '../es/print'

export const print: typeof source = {
  print: 'Print',
  moreSettings: 'More printer settings…',
  moreSettingsTip: 'Opens the Windows print dialog (tray, quality…)',
  preview: 'Preview',
  empty: 'Choose which pages to print.',
  notAllowed: 'The author of this PDF doesn’t allow printing it.',
  tooMany: 'That would be more than {max} sheets: reduce the enlargement or the number of pages.',
  sentTo: 'Sent to {printer}',
  sent: 'Sent to the printer',
  status: {
    preparing: 'Preparing {n} of {total}…',
    sending: 'Sending to the printer…',
    summary: '{pages} · {sheets}'
  },
  sheetCount: { one: '{n} sheet', other: '{n} sheets' },
  moreSheets: { one: 'and {n} more sheet', other: 'and {n} more sheets' },
  sheetLabel: {
    sheet: 'Sheet {n}',
    front: 'Sheet {n} · front',
    back: 'Sheet {n} · back',
    tile: 'Row {row}, column {col}',
    tileOfPage: 'Page {page} · row {row}, column {col}'
  },
  poster: {
    sheets: { one: '{n} sheet ({cols} × {rows})', other: '{n} sheets ({cols} × {rows})' },
    sheetsOfPage: { one: 'Page {page} · {n} sheet ({cols} × {rows})', other: 'Page {page} · {n} sheets ({cols} × {rows})' },
    scale: 'Enlarge to',
    scaleHint: 'Sheets overlap by 5 mm so you can tape them together.',
    scaleTip: 'Poster enlargement'
  },
  modeHints: {
    size: 'One page on each sheet.',
    multiple: 'Several smaller pages on each sheet.',
    booklet: 'To fold the sheets in half and staple them.',
    poster: 'Each page enlarged across several sheets to tape together.'
  },
  scale: {
    fit: 'Fit to sheet',
    actual: 'Actual size',
    shrink: 'Shrink only pages that don’t fit',
    custom: 'Custom',
    tip: 'Scale'
  },
  multiple: {
    perSheet: 'Per sheet',
    perSheetLabel: 'Pages per sheet',
    order: 'Order',
    orderLabel: 'Page order',
    byRows: 'By rows',
    byColumns: 'By columns',
    fill: 'Repeat each page to fill the sheet',
    borders: 'Draw a border around each page'
  },
  booklet: {
    sides: 'Sides',
    sidesHint: 'When printing on both sides, the printer flips the paper on the short edge.',
    sidesLabel: 'Booklet sides',
    both: 'Both sides',
    front: 'Front only',
    back: 'Back only'
  },
  printer: {
    title: 'Printer',
    searching: 'Looking for printers…',
    none: 'No printers installed',
    isDefault: '{name} (default)',
    copies: 'Copies',
    color: 'Color',
    gray: 'Black and white'
  },
  pages: {
    all: 'All ({n})',
    selected: 'Selected ({n})',
    current: 'Current ({n})',
    custom: 'These:',
    customLabel: 'Pages to print',
    customPlaceholder: 'For example, 1-3, 5',
    customError: 'Pages 1 to {count}, separated by commas: 1-3, 5',
    subsetLabel: 'Odd or even',
    both: 'Odd and even',
    odd: 'Odd only',
    even: 'Even only',
    reverse: 'Reverse order'
  },
  layout: {
    title: 'Layout',
    size: 'Size',
    multiple: 'Multiple',
    booklet: 'Booklet',
    poster: 'Poster'
  },
  paper: {
    title: 'Paper',
    label: 'Paper',
    document: 'Same as each page',
    orientation: 'Orientation',
    auto: 'Automatic',
    portrait: 'Portrait',
    landscape: 'Landscape',
    duplex: 'Two-sided',
    simplex: 'No, one-sided',
    longEdge: 'Yes, flip on long edge',
    shortEdge: 'Yes, flip on short edge'
  },
  papers: {
    A4: 'A4',
    A3: 'A3',
    A5: 'A5',
    Letter: 'Letter',
    Legal: 'Legal',
    Tabloid: 'Tabloid'
  }
}

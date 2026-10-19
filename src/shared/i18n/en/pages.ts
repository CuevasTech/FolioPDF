import type { pages as source } from '../es/pages'

export const pages: typeof source = {
  rotateLeft: 'Rotate left',
  rotateRight: 'Rotate right',
  unprotected: 'The new files don’t have the original document’s password. Protect them separately if needed.',
  keepOne: 'A document must keep at least one page',
  tagsRemoved: 'The document’s accessibility tags were removed so no trace of the deleted pages remains.',
  extracted: { one: 'Extracted {n} page to a new PDF', other: 'Extracted {n} pages to a new PDF' },
  duplicate: 'Duplicate',
  duplicateTip: 'Duplicate the selected pages',
  extract: 'Extract',
  extractTip: 'Save the selected pages as a new PDF',
  deleteTip: 'Delete the selected pages (Del)',
  insert: 'Insert',
  insertTip: 'Add pages after the selection',
  insertBlank: 'Blank page',
  insertFile: 'Pages from another file…',
  split: 'Split',
  splitTip: 'Split the document into several files',
  selectNone: 'Clear selection',
  selectAll: 'Select all',
  splitDialog: {
    title: 'Split document',
    run: 'Choose folder and split',
    busy: 'Splitting the document',
    badRange: '“{part}” isn’t a valid range. For example, use 1-3, 4, 5-{total}.',
    noRanges: 'Enter at least one page range.',
    created: { one: 'Created {n} file', other: 'Created {n} files' },
    each: 'One page per file',
    every: 'Every N pages',
    ranges: 'By ranges',
    perFile: 'Pages per file',
    rangesLabel: 'Ranges',
    rangesHint: { one: 'Separated by commas. The document has {n} page.', other: 'Separated by commas. The document has {n} pages.' },
    note: 'The open document isn’t changed: the new files are saved in the folder you choose.'
  }
}

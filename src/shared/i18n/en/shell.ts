import type { shell as source } from '../es/shell'

export const shell: typeof source = {
  percent: '{n}%',
  dismiss: 'Dismiss',
  app: {
    dropToOpen: 'Drop to open'
  },
  titleBar: {
    unsaved: 'Unsaved changes',
    closeTab: 'Close {name}',
    openOrCreate: 'Open or create',
    isolation: {
      tip: 'Reduced protection',
      label: 'Reduced protection: the document engine isn’t isolated',
      warning:
        'Windows didn’t allow Folio to isolate the engine that reads PDFs. Folio works normally, but a malicious PDF would have more room to cause harm: only open documents you trust.'
    },
    undoAction: 'Undo: {action}',
    redoAction: 'Redo: {action}',
    save: 'Save (Ctrl+S)',
    noChanges: 'No unsaved changes',
    more: 'More',
    create: {
      open: 'Open…',
      blank: 'Blank document',
      merge: 'Combine files…',
      images: 'Images to PDF…',
      scan: 'Scan document…'
    },
    menu: {
      saveAsKbd: 'Ctrl+Shift+S',
      print: 'Print…',
      showInFolder: 'Show in folder',
      properties: 'Document properties',
      settings: 'Settings',
      shortcuts: 'Keyboard shortcuts',
      feedback: 'Send feedback…',
      sponsor: 'Buy me a coffee',
      about: 'About Folio'
    }
  },
  rail: {
    label: 'Modes',
    settings: 'Settings',
    view: { label: 'View', hint: 'Read, search, and copy' },
    edit: { label: 'Edit', hint: 'Text, images, and content' },
    annotate: { label: 'Annotate', hint: 'Highlight, notes, and drawing' },
    form: { label: 'Form', hint: 'Fill in and prepare fields' },
    sign: { label: 'Sign', hint: 'Handwritten and certificate signatures' },
    pages: { label: 'Pages', hint: 'Reorder, rotate, insert, and extract' },
    tools: { label: 'More', hint: 'OCR, protect, compress, convert…' }
  },
  toolbar: {
    label: 'Tools',
    select: 'Select',
    selectTip: 'Select and copy text',
    hand: 'Hand',
    handTip: 'Drag to scroll',
    search: 'Search',
    searchTip: 'Search (Ctrl+F)'
  },
  findBar: {
    placeholder: 'Search in document',
    searching: 'Searching…',
    noResults: 'No results',
    count: '{n} of {total}',
    countMore: '{n} of {total}+',
    matchCase: 'Match case',
    wholeWord: 'Whole words',
    previous: 'Previous',
    next: 'Next'
  },
  dock: {
    sidePanel: 'Sidebar',
    previousPage: 'Previous page',
    nextPage: 'Next page',
    ofTotal: 'of {total}',
    zoomOut: 'Zoom out',
    zoomIn: 'Zoom in',
    fitWidth: 'Fit width',
    fitPage: 'Fit page'
  },
  sidePanel: {
    outline: 'Bookmarks',
    noOutline: 'This document has no bookmarks.'
  },
  banner: {
    locked: 'This document is signed and locked: it can be viewed, printed, and exported, but not changed.',
    lockedForm: 'This document is signed: the signer only allows filling in the form and signing.',
    lockedComments: 'This document is signed: the signer only allows filling in, signing, and adding comments.',
    signed: 'This document is digitally signed.',
    invalidated: 'This document had digital signatures that the changes have invalidated.',
    restricted: 'The author of this PDF has restricted what can be done with it.',
    viewSignatures: 'View signatures',
    unlock: 'Unlock'
  },
  busy: {
    working: 'Working…'
  },
  update: {
    available: 'A new version of Folio is available',
    availableVersion: 'A new version of Folio is available ({version})',
    update: 'Update',
    notNow: 'Not now',
    downloading: 'Downloading Folio…',
    downloadingVersion: 'Downloading Folio {version}…',
    ready: 'The Folio update is ready',
    readyVersion: 'Folio {version} is ready to install',
    restart: 'Restart and update',
    laterTip: 'It will be installed when you close Folio',
    later: 'Later',
    failed: 'Couldn’t update Folio',
    tryLater: 'Try again later.'
  },
  recovery: {
    title: 'Recover documents',
    discardAll: 'Discard all',
    later: 'Later',
    text: 'Folio closed before saving the changes to these documents. You can recover them and save them wherever you like.',
    password: 'Document password',
    discard: 'Discard',
    recover: 'Recover'
  }
}

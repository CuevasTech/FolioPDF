import type { start as source } from '../es/start'

export const start: typeof source = {
  subtitle: 'Everything you need to do with a PDF, in one place.',
  defaultApp: {
    text: 'Folio isn’t your default app for PDFs. Do you want your PDFs to open with Folio?',
    use: 'Use Folio',
    dontAsk: 'Don’t ask again'
  },
  open: 'Open PDF',
  dropHint: 'or drop your files here',
  merge: 'Combine files',
  images: 'Images to PDF',
  scan: 'Scan',
  blank: 'Blank document',
  recent: {
    title: 'Recent',
    filtered: 'Recent · {shown} of {total}',
    search: 'Search recent files',
    clearSearch: 'Clear search',
    clearList: 'Clear list',
    removeOne: 'Remove “{name}” from recent files',
    removeTip: 'Remove from list',
    noMatch: 'No recent document matches “{query}”.'
  }
}

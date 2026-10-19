import type { state as source } from '../es/state'

export const state: typeof source = {
  actions: {
    engineRestarted: 'The document engine restarted, and the open documents had to be closed.',
    fileError: '{name}: {message}',
    saved: 'Document saved',
    savedSignaturesBroken: 'Saved. The document’s digital signatures are no longer valid because of the changes.',
    historyKept: 'To avoid breaking the signature, the file keeps the intermediate changes made after signing inside it.',
    changedOnDisk: {
      title: 'The file has changed on disk',
      message: 'Another program has changed this file since you opened it. If you save now, those changes will be lost.',
      overwrite: 'Overwrite'
    },
    unsaved: {
      title: 'Save changes?',
      message: '“{name}” has unsaved changes.',
      dontSave: 'Don’t save'
    }
  },
  defaultApp: {
    done: 'Done: PDFs will open with Folio.',
    hint: 'In the Windows window, click “Set default” next to Folio.'
  },
  errors: {
    unexpected: 'An unexpected error occurred.',
    cancelled: 'Operation canceled.'
  },
  dateStyles: {
    short: 'Short',
    shortTime: 'Short with time',
    medium: 'Abbreviated',
    long: 'Long',
    full: 'Long with weekday',
    iso: 'International'
  },
  timestamp: {
    recommended: '{name} (recommended)',
    other: 'Other service…',
    configured: 'the configured service'
  },
  when: {
    now: 'just now'
  }
}

import type { annotate as source } from '../es/annotate'

export const annotate: typeof source = {
  tools: {
    selectTip: 'Select text and annotations',
    highlight: 'Highlight',
    highlightTip: 'Highlight text',
    underline: 'Underline',
    strikeout: 'Strikethrough',
    note: 'Note',
    noteTip: 'Add a note',
    ink: 'Draw',
    inkTip: 'Draw freehand',
    shapesTip: 'Shapes and arrows',
    arrow: 'Arrow'
  },
  comment: {
    placeholder: 'Write a comment…',
    noAuthor: 'No author'
  },
  selection: {
    copy: 'Copy',
    copied: 'Text copied'
  },
  options: {
    stroke: 'Stroke',
    markupHint: 'Drag over the text',
    noteHint: 'Click where you want to place the note'
  }
}

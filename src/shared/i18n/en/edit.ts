import type { edit as source } from '../es/edit'

export const edit: typeof source = {
  tools: {
    selectTip: 'Select and move items',
    text: 'Text',
    textTip: 'Add text',
    image: 'Image',
    imageTip: 'Insert an image',
    shapeTip: 'Draw a shape',
    whiteout: 'Whiteout',
    whiteoutTip: 'Cover an area with white',
    content: 'Edit content',
    contentTip: 'Change the text and images already in the PDF'
  },
  object: {
    opacity: 'Opacity',
    bringToFront: 'Bring to front',
    sendToBack: 'Send to back',
    border: 'Border',
    strokeColor: 'Line color',
    fillColor: 'Fill color'
  },
  hints: {
    content: 'Click a paragraph to rewrite it (Alt+click: just one line), or an image to move, replace or remove it',
    whiteout: 'Drag over the area you want to cover. To truly remove data, use “Redact” in More'
  },
  existing: {
    signedTitle: 'This document is signed',
    signedMessage:
      'Changing its original content will invalidate the digital signatures it contains. If you only need to add text or fill in fields, you can do that without breaking them.',
    signedConfirm: 'Change anyway',
    paragraphTip: 'Click: rewrite the paragraph · Alt+click: edit just one line',
    mixedParagraph: 'This paragraph mixes styles: rewriting it will use the main one (Alt+click: edit just one line)',
    mixedLine: 'This line mixes styles: editing it will use the main style',
    moveOrScale: 'Move or resize',
    replace: 'Replace'
  }
}

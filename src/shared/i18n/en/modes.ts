import type { modes as source } from '../es/modes'

export const modes: typeof source = {
  colorPicker: {
    none: 'No color',
    custom: 'Other color…'
  },
  numberField: {
    up: 'Increase',
    down: 'Decrease'
  },
  textStyle: {
    font: 'Font',
    size: 'Size',
    bold: 'Bold',
    italic: 'Italic',
    color: 'Text color',
    alignLeft: 'Align left',
    alignCenter: 'Center',
    alignRight: 'Align right',
    justify: 'Justify'
  },
  select: 'Select',
  shape: 'Shape',
  shapes: {
    rect: 'Rectangle',
    ellipse: 'Ellipse',
    line: 'Line'
  },
  color: 'Color',
  fill: 'Fill',
  thickness: 'Thickness',
  deleteTip: 'Delete (Del)',
  deleteKey: 'Del'
}

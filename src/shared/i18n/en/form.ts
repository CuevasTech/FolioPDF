import type { form as source } from '../es/form'

export const form: typeof source = {
  tools: {
    fill: 'Fill in',
    design: 'Prepare',
    select: 'Select',
    detect: 'Detect',
    detectTip: 'Suggest fields based on the lines and boxes on this page',
    highlight: 'Highlight fields',
    clear: 'Clear',
    clearTip: 'Clear everything filled in',
    clearTitle: 'Clear the form',
    clearMessage: 'Everything you typed in all the fields will be erased. You can undo it afterward.'
  },
  create: {
    text: { label: 'Text', tip: 'Add a text field' },
    checkbox: { label: 'Checkbox', tip: 'Add a checkbox' },
    radio: { label: 'Option', tip: 'Add an option button' },
    combo: { label: 'Dropdown', tip: 'Add a dropdown' },
    list: { label: 'List', tip: 'Add a list' },
    signature: { label: 'Signature', tip: 'Add a signature field' }
  },
  detect: {
    found: { one: '{n} suggested field on this page', other: '{n} suggested fields on this page' },
    none: 'No areas for fields were found on this page',
    addField: 'Add field',
    addFieldNamed: 'Add field: {label}',
    addAll: 'Add all',
    dismiss: 'Dismiss'
  },
  hints: {
    place: 'Click or drag on the page to place the field',
    noFields: 'This PDF has no fields. Use “Prepare” to create them, or “Sign” to write on top of it',
    xfa: 'This form uses Adobe XFA technology and may not display completely'
  },
  inspector: {
    type: {
      text: 'Text field',
      checkbox: 'Checkbox',
      radio: 'Option button',
      combo: 'Dropdown',
      list: 'List',
      signature: 'Signature field',
      other: 'Field'
    },
    group: 'Group',
    groupHint: 'Only one option in the same group can be selected.',
    optionValue: 'Value of this option',
    name: 'Name',
    description: 'Description',
    descriptionHint: 'Shown when you hover over the field.',
    optional: 'Optional',
    required: 'Required',
    readOnly: 'Read-only',
    multiline: 'Multiple lines',
    password: 'Hide what’s typed (password)',
    maxLength: 'Maximum length',
    checkedValue: 'Value when checked',
    options: 'Options',
    optionsHint: 'One option per line.',
    appearance: 'Appearance',
    font: 'Font',
    fonts: { sans: 'Sans serif', serif: 'Serif', mono: 'Monospace' },
    fontSize: 'Size (0 = auto)',
    align: 'Alignment',
    alignLeft: 'Left',
    alignCenter: 'Center',
    alignRight: 'Right',
    textColor: 'Text',
    fill: 'Background',
    border: 'Border',
    duplicate: 'Duplicate'
  },
  fill: {
    calculatedTip: '{title} · calculated automatically',
    calculatedLabel: '{title}: calculated automatically',
    signHere: 'Sign here with a certificate'
  }
}

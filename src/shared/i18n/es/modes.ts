/**
 * Controles comunes de los modos de trabajo (modes/common, modes/index.ts) y
 * textos que comparten los modos Editar y Anotar.
 */
export const modes = {
  colorPicker: {
    none: 'Sin color',
    custom: 'Otro color…'
  },
  numberField: {
    up: 'Subir',
    down: 'Bajar'
  },
  textStyle: {
    font: 'Fuente',
    size: 'Tamaño',
    bold: 'Negrita',
    italic: 'Cursiva',
    color: 'Color del texto',
    alignLeft: 'Alinear a la izquierda',
    alignCenter: 'Centrar',
    alignRight: 'Alinear a la derecha',
    justify: 'Justificar'
  },
  select: 'Seleccionar',
  shape: 'Forma',
  shapes: {
    rect: 'Rectángulo',
    ellipse: 'Elipse',
    line: 'Línea'
  },
  color: 'Color',
  fill: 'Relleno',
  thickness: 'Grosor',
  deleteTip: 'Eliminar (Supr)',
  /** Nombre de la tecla de borrar, como aparece en los menús. */
  deleteKey: 'Supr'
}

/** Cuadro de imprimir (components/PrintDialog.tsx) y los papeles de shared/printLayout.ts. */
export const print = {
  print: 'Imprimir',
  moreSettings: 'Más ajustes de la impresora…',
  moreSettingsTip: 'Abre el cuadro de impresión de Windows (bandeja, calidad…)',
  preview: 'Vista previa',
  empty: 'Elige qué páginas imprimir.',
  notAllowed: 'El autor de este PDF no permite imprimirlo.',
  tooMany: 'Serían más de {max} hojas: reduce la ampliación o las páginas.',
  sentTo: 'Enviado a {printer}',
  sent: 'Enviado a la impresora',
  status: {
    preparing: 'Preparando {n} de {total}…',
    sending: 'Enviando a la impresora…',
    summary: '{pages} · {sheets}'
  },
  sheetCount: { one: '{n} hoja', other: '{n} hojas' },
  moreSheets: { one: 'y {n} hoja más', other: 'y {n} hojas más' },
  sheetLabel: {
    sheet: 'Hoja {n}',
    front: 'Hoja {n} · anverso',
    back: 'Hoja {n} · reverso',
    tile: 'Fila {row}, columna {col}',
    tileOfPage: 'Página {page} · fila {row}, columna {col}'
  },
  poster: {
    sheets: { one: '{n} hoja ({cols} × {rows})', other: '{n} hojas ({cols} × {rows})' },
    sheetsOfPage: { one: 'Página {page} · {n} hoja ({cols} × {rows})', other: 'Página {page} · {n} hojas ({cols} × {rows})' },
    scale: 'Ampliar al',
    scaleHint: 'Las hojas se solapan 5 mm para poder pegarlas.',
    scaleTip: 'Ampliación del póster'
  },
  modeHints: {
    size: 'Una página en cada hoja.',
    multiple: 'Varias páginas en cada hoja, más pequeñas.',
    booklet: 'Para doblar las hojas por la mitad y grapar.',
    poster: 'Cada página, ampliada en varias hojas para pegarlas.'
  },
  scale: {
    fit: 'Ajustar a la hoja',
    actual: 'Tamaño real',
    shrink: 'Reducir solo las que no quepan',
    custom: 'Personalizado',
    tip: 'Escala'
  },
  multiple: {
    perSheet: 'Por hoja',
    perSheetLabel: 'Páginas por hoja',
    order: 'Orden',
    orderLabel: 'Orden de las páginas',
    byRows: 'Por filas',
    byColumns: 'Por columnas',
    fill: 'Repetir cada página hasta llenar la hoja',
    borders: 'Recuadrar cada página'
  },
  booklet: {
    sides: 'Caras',
    sidesHint: 'A dos caras, la impresora gira el papel por el lado corto.',
    sidesLabel: 'Caras del folleto',
    both: 'Las dos caras',
    front: 'Solo el anverso',
    back: 'Solo el reverso'
  },
  printer: {
    title: 'Impresora',
    searching: 'Buscando impresoras…',
    none: 'No hay impresoras instaladas',
    isDefault: '{name} (predeterminada)',
    copies: 'Copias',
    color: 'Color',
    gray: 'Blanco y negro'
  },
  pages: {
    all: 'Todas ({n})',
    selected: 'Las seleccionadas ({n})',
    current: 'La actual ({n})',
    custom: 'Estas:',
    customLabel: 'Páginas que imprimir',
    customPlaceholder: 'Por ejemplo, 1-3, 5',
    customError: 'Páginas del 1 al {count}, separadas por comas: 1-3, 5',
    subsetLabel: 'Pares o impares',
    both: 'Pares e impares',
    odd: 'Solo impares',
    even: 'Solo pares',
    reverse: 'Al revés'
  },
  layout: {
    title: 'Disposición',
    size: 'Tamaño',
    multiple: 'Varias',
    booklet: 'Folleto',
    poster: 'Póster'
  },
  paper: {
    title: 'Papel',
    label: 'Papel',
    document: 'El de cada página',
    orientation: 'Orientación',
    auto: 'Automática',
    portrait: 'Vertical',
    landscape: 'Horizontal',
    duplex: 'Doble cara',
    simplex: 'No, a una cara',
    longEdge: 'Sí, por el lado largo',
    shortEdge: 'Sí, por el lado corto'
  },
  /** Nombres de los tamaños de papel (claves de PAPERS en shared/printLayout.ts). */
  papers: {
    A4: 'A4',
    A3: 'A3',
    A5: 'A5',
    Letter: 'Carta',
    Legal: 'Oficio (Legal)',
    Tabloid: 'Tabloide'
  }
}

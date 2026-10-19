/** Digitalizar documento (components/ScanDialog.tsx). */
export const scan = {
  title: 'Digitalizar documento',
  styles: {
    auto: 'Automático',
    color: 'Color',
    gray: 'Gris',
    bw: 'B/N',
    original: 'Original'
  },
  styleHints: {
    auto: 'Papel blanco y texto nítido; en color solo si hay color (un sello, una firma en azul).',
    color: 'Papel blanco y los colores avivados.',
    gray: 'Papel blanco y texto nítido, en escala de grises.',
    bw: 'Solo blanco y negro: el archivo más ligero, ideal para texto.',
    original: 'La foto tal cual, solo recortada y enderezada.'
  },
  edges: {
    hint: 'Arrastra las esquinas hasta las del papel.',
    photo: 'Foto entera',
    topLeft: 'Esquina superior izquierda',
    topRight: 'Esquina superior derecha',
    bottomRight: 'Esquina inferior derecha',
    bottomLeft: 'Esquina inferior izquierda',
    position: '{x} %, {y} %',
    loading: 'Cargando la foto…',
    whole: 'Toda la foto',
    detected: 'Bordes detectados',
    crossed: 'Las esquinas se cruzan.'
  },
  notices: {
    noEdges: 'No se han podido buscar los bordes del papel: las fotos van enteras. Puedes ajustarlas a mano.',
    someWhole: 'En alguna foto no se ve claro el papel: va entera. Ajusta sus esquinas si hace falta.'
  },
  busy: {
    photos: 'Preparando las fotos…',
    scanning: 'Escaneando…',
    creatingOcr: 'Creando el PDF y reconociendo el texto…',
    creating: 'Creando el PDF…'
  },
  stop: 'Detener',
  create: 'Crear PDF',
  empty: {
    title: 'Añade las fotos del documento o escanéalo',
    text: 'Folio encuentra el papel en cada foto, lo endereza, quita las sombras y deja el fondo blanco, como recién escaneado.'
  },
  page: {
    label: 'Página {n}. Alt y las flechas para moverla; Suprimir para quitarla.',
    alt: 'Página {n}',
    rotateLeft: 'Girar a la izquierda',
    rotateRight: 'Girar a la derecha',
    edges: 'Ajustar los bordes',
    remove: 'Quitar la página'
  },
  photos: {
    title: 'Fotos',
    add: 'Añadir fotos o imágenes…',
    hint: '¿Las fotos están en el móvil? Pásalas al ordenador con Enlace Móvil, WhatsApp Web u OneDrive. Haz cada foto desde arriba, con buena luz y el papel sobre un fondo más oscuro.'
  },
  scanner: {
    title: 'Escáner',
    searching: 'Buscando escáneres…',
    none: 'No hay ningún escáner instalado. Conéctalo o añádelo en Configuración de Windows → Bluetooth y dispositivos → Impresoras y escáneres.',
    searchAgain: 'Buscar otra vez',
    refresh: 'Buscar escáneres otra vez',
    paper: 'Papel',
    flatbed: 'Cristal',
    feeder: 'Alimentador',
    duplex: 'A dos caras',
    color: 'Color',
    colorLabel: 'Color del escaneo',
    gray: 'Gris',
    bw: 'Blanco y negro',
    resolution: 'Resolución',
    dpi: '{n} ppp',
    scan: 'Escanear',
    scanMore: 'Escanear más páginas'
  },
  finish: {
    title: 'Acabado',
    ocr: 'Reconocer el texto (OCR), para poder buscarlo y copiarlo',
    ocrUnavailable: 'El reconocimiento de texto no está disponible en esta instalación.'
  }
}

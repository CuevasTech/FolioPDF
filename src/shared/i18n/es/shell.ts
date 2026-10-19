/**
 * Marco de la aplicación (App.tsx y components/: barra de título, carril de
 * modos, barras flotantes, avisos, panel lateral, recuperación…).
 */
export const shell = {
  /** Porcentaje (zoom, progreso). */
  percent: '{n} %',
  dismiss: 'Cerrar aviso',
  app: {
    dropToOpen: 'Suelta para abrir'
  },
  titleBar: {
    unsaved: 'Cambios sin guardar',
    closeTab: 'Cerrar {name}',
    openOrCreate: 'Abrir o crear',
    isolation: {
      tip: 'Protección reducida',
      label: 'Protección reducida: el motor de documentos no está aislado',
      warning:
        'Windows no ha permitido aislar el motor que lee los PDF. Folio funciona con normalidad, pero un PDF malicioso tendría más margen: abre solo documentos de confianza.'
    },
    undoAction: 'Deshacer: {action}',
    redoAction: 'Rehacer: {action}',
    save: 'Guardar (Ctrl+S)',
    noChanges: 'Sin cambios pendientes',
    more: 'Más',
    create: {
      open: 'Abrir…',
      blank: 'Documento en blanco',
      merge: 'Combinar archivos…',
      images: 'Imágenes a PDF…',
      scan: 'Digitalizar documento…'
    },
    menu: {
      saveAsKbd: 'Ctrl+Mayús+S',
      print: 'Imprimir…',
      showInFolder: 'Mostrar en la carpeta',
      properties: 'Propiedades del documento',
      settings: 'Ajustes',
      shortcuts: 'Atajos de teclado',
      feedback: 'Enviar comentarios…',
      sponsor: 'Invítame a un café',
      about: 'Acerca de Folio'
    }
  },
  rail: {
    label: 'Modos',
    settings: 'Ajustes',
    view: { label: 'Ver', hint: 'Leer, buscar y copiar' },
    edit: { label: 'Editar', hint: 'Texto, imágenes y contenido' },
    annotate: { label: 'Anotar', hint: 'Resaltar, notas y dibujo' },
    form: { label: 'Formulario', hint: 'Rellenar y preparar campos' },
    sign: { label: 'Firmar', hint: 'Firma manuscrita y con certificado' },
    pages: { label: 'Páginas', hint: 'Ordenar, girar, insertar y extraer' },
    tools: { label: 'Más', hint: 'OCR, proteger, comprimir, convertir…' }
  },
  toolbar: {
    label: 'Herramientas',
    select: 'Seleccionar',
    selectTip: 'Seleccionar y copiar texto',
    hand: 'Mover',
    handTip: 'Arrastrar para desplazarse',
    search: 'Buscar',
    searchTip: 'Buscar (Ctrl+F)'
  },
  findBar: {
    placeholder: 'Buscar en el documento',
    searching: 'Buscando…',
    noResults: 'Sin resultados',
    count: '{n} de {total}',
    countMore: '{n} de {total}+',
    matchCase: 'Distinguir mayúsculas',
    wholeWord: 'Palabras completas',
    previous: 'Anterior',
    next: 'Siguiente'
  },
  dock: {
    sidePanel: 'Panel lateral',
    previousPage: 'Página anterior',
    nextPage: 'Página siguiente',
    ofTotal: 'de {total}',
    zoomOut: 'Alejar',
    zoomIn: 'Acercar',
    fitWidth: 'Ajustar al ancho',
    fitPage: 'Página completa'
  },
  sidePanel: {
    outline: 'Marcadores',
    noOutline: 'Este documento no tiene marcadores.'
  },
  banner: {
    locked: 'Este documento está firmado y bloqueado: se puede ver, imprimir y exportar, pero no modificar.',
    lockedForm: 'Este documento está firmado: quien lo firmó solo permite rellenar el formulario y firmar.',
    lockedComments: 'Este documento está firmado: quien lo firmó solo permite rellenar, firmar y añadir comentarios.',
    signed: 'Este documento está firmado digitalmente.',
    invalidated: 'Este documento tenía firmas digitales que los cambios han invalidado.',
    restricted: 'El autor de este PDF ha limitado lo que se puede hacer con él.',
    viewSignatures: 'Ver firmas',
    unlock: 'Desbloquear'
  },
  busy: {
    working: 'Trabajando…'
  },
  update: {
    available: 'Hay una versión nueva de Folio',
    availableVersion: 'Hay una versión nueva de Folio ({version})',
    update: 'Actualizar',
    notNow: 'Ahora no',
    downloading: 'Descargando Folio…',
    downloadingVersion: 'Descargando Folio {version}…',
    ready: 'Folio está lista',
    readyVersion: 'Folio {version} está lista',
    restart: 'Reiniciar y actualizar',
    laterTip: 'Se instalará al cerrar Folio',
    later: 'Más tarde',
    failed: 'No se pudo actualizar Folio',
    tryLater: 'Inténtalo más tarde.'
  },
  recovery: {
    title: 'Recuperar documentos',
    discardAll: 'Descartar todos',
    later: 'Más tarde',
    text: 'Folio se cerró antes de guardar los cambios de estos documentos. Puedes recuperarlos y guardarlos donde quieras.',
    password: 'Contraseña del documento',
    discard: 'Descartar',
    recover: 'Recuperar'
  }
}

/** Proceso principal (src/main): diálogos de Windows y mensajes que llegan a la interfaz. */
export const main = {
  /** Cliente del motor de documentos (engine.ts). */
  engine: {
    stopped: 'El motor de documentos se ha detenido.',
    failed: 'Error del motor.',
    tooLarge: 'La petición es demasiado grande.',
    cancelled: 'Operación cancelada.'
  },
  /** Errores de los mensajes de la interfaz (ipc.ts). */
  errors: {
    unexpected: 'Se produjo un error inesperado.',
    forbiddenOrigin: 'Origen no permitido.',
    invalidParams: 'Parámetros no válidos.',
    noWindow: 'La ventana no está disponible.',
    docClosed: 'El documento ya no está abierto.',
    forbiddenOperation: 'Operación no permitida desde la interfaz.',
    recentGone: 'Ese archivo ya no está en la lista de recientes.',
    openExpired: 'La solicitud de apertura ha caducado.',
    imageTooLarge: 'La imagen es demasiado grande.',
    mergeTwo: 'Elige al menos dos archivos para combinar.',
    tooManyPhotos: 'Elige como mucho 100 fotos cada vez; puedes añadir más después.',
    compressedSameName: 'Guarda la copia comprimida con otro nombre para conservar el original.',
    pickPfxAgain: 'Vuelve a elegir el archivo de certificado.',
    printNotAllowed: 'El autor de este PDF no permite imprimirlo.'
  },
  /** Títulos de los diálogos de Windows para abrir y guardar (ipc.ts). */
  dialogs: {
    savePdf: 'Guardar PDF',
    pickImage: 'Elegir imagen',
    pickMerge: 'Elegir los archivos que se van a combinar',
    pickImages: 'Elegir imágenes',
    pickPhotos: 'Elegir las fotos del documento',
    insertPages: 'Insertar páginas de otro archivo',
    extractPages: 'Extraer páginas a un PDF nuevo',
    filesFolder: 'Carpeta donde guardar los archivos',
    imagesFolder: 'Carpeta donde guardar las imágenes',
    saveCompressed: 'Guardar copia comprimida',
    exportText: 'Exportar a texto',
    exportWord: 'Exportar a Word',
    pickPfx: 'Elegir archivo de certificado'
  },
  /** Nombres de los filtros de archivos de esos diálogos. */
  filters: {
    pdfAndImages: 'PDF e imágenes',
    images: 'Imágenes',
    photos: 'Fotos e imágenes',
    text: 'Texto',
    certificate: 'Certificado'
  },
  /** Nombres que se proponen al guardar. */
  fileNames: {
    extract: '{name}-extracto.pdf',
    compressedSuffix: '-comprimido'
  },
  /** Pregunta antes de abrir un enlace de un PDF. */
  link: {
    title: 'Abrir enlace',
    mail: '¿Escribir un correo a esta dirección?',
    web: '¿Abrir este enlace de {host} en el navegador?'
  },
  /** Envío de comentarios (feedback.ts). */
  feedback: {
    failed: 'No se pudo enviar el comentario. Comprueba la conexión a Internet y vuelve a intentarlo.',
    viaGithub: 'Los comentarios se envían desde GitHub.',
    wait: 'Espera unos segundos antes de enviar otro comentario.'
  },
  /** Versiones nuevas (updates.ts). */
  updates: {
    mismatch: 'La descarga no coincide con la versión publicada y se ha descartado. No se ha instalado nada.',
    downloadFailed: 'No se pudo descargar la versión nueva. Inténtalo más tarde.',
    checkFailed: 'No se pudo comprobar si hay una versión nueva. Revisa la conexión a Internet.'
  },
  /** Impresión (print.ts). */
  print: {
    cancelled: 'Impresión cancelada.',
    printerGone: 'Esa impresora ya no está disponible. Elige otra.',
    noPages: 'No hay páginas que imprimir.',
    tooManySheets: 'Serían más de {max} hojas. Reduce la ampliación o las páginas.',
    failed: 'No se pudo imprimir. Comprueba que la impresora esté encendida y conectada, y vuelve a intentarlo.'
  },
  /** Escáneres (scanner.ts). */
  scanner: {
    testDevice: 'Escáner de prueba',
    unnamed: 'Escáner',
    cancelled: 'Cancelado.',
    failed: 'El escáner no ha podido escanear. Comprueba que está encendido y vuelve a intentarlo.',
    notFound: 'Ese escáner ya no está disponible. Comprueba que está encendido y conectado.',
    paperJam: 'El papel se ha atascado en el escáner.',
    noPaper: 'No hay papel en el alimentador.',
    paperProblem: 'Hay un problema con el papel del escáner.',
    notResponding: 'El escáner no responde. Comprueba que está encendido y conectado.',
    busy: 'El escáner está ocupado. Espera un momento y vuelve a intentarlo.',
    warmingUp: 'El escáner se está preparando. Vuelve a intentarlo en unos segundos.',
    userAction: 'El escáner necesita que hagas algo: mira su pantalla.',
    noConnection: 'No se puede comunicar con el escáner. Comprueba que está encendido y conectado.',
    locked: 'El escáner está bloqueado o lo está usando otro programa.',
    coverOpen: 'La tapa del escáner está abierta.',
    lampOff: 'La lámpara del escáner está apagada.'
  }
}

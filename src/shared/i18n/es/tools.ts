/** Más herramientas (modes/tools): portada, censura, recorte y sus diálogos. */
export const tools = {
  back: 'Herramientas',
  backTip: 'Volver a todas las herramientas',
  home: {
    title: 'Más herramientas',
    lead: 'Reconocer texto, proteger, reducir, convertir y añadir elementos a todas las páginas.',
    privacy: 'Texto y privacidad',
    protection: 'Protección',
    convert: 'Reducir y convertir',
    add: 'Añadir a las páginas'
  },
  exported: 'Archivo exportado',
  word: {
    title: 'Exportar a Word',
    card: 'Convierte el contenido a un documento .docx editable.',
    busy: 'Exportando a Word'
  },
  text: {
    title: 'Exportar a texto',
    card: 'Extrae todo el texto a un archivo .txt.',
    busy: 'Exportando a texto'
  },
  unprotect: {
    title: 'Quitar protección',
    card: 'Guarda el documento sin contraseña ni restricciones.',
    notProtected: 'Este documento no está protegido.',
    done: 'La protección se quitará al guardar el documento'
  },
  flatten: {
    title: 'Acoplar',
    card: 'Convierte campos y anotaciones en contenido fijo.',
    confirmTitle: 'Acoplar formulario y anotaciones',
    confirmMessage: 'Los campos rellenados y las anotaciones pasarán a formar parte de la página y ya no se podrán editar.'
  },
  redact: {
    title: 'Censurar',
    card: 'Elimina de forma definitiva datos sensibles del documento.',
    confirmTitle: { one: 'Censurar {n} zona', other: 'Censurar {n} zonas' },
    confirmMessage:
      'El contenido marcado se eliminará del documento de forma definitiva al guardar: no se podrá recuperar ni copiando el texto ni con otros programas.',
    signedNote: 'Las firmas digitales del documento dejarán de ser válidas.',
    flattenOption: 'Convertir en imagen las páginas censuradas',
    flattenHint:
      'Máxima seguridad: en esas páginas solo queda la imagen de lo que se ve, sin texto ni capas ocultas. Después podrás reconocer el texto con OCR.',
    busy: 'Censurando',
    done: 'Contenido censurado. Guarda el documento para hacerlo definitivo.',
    flattened: {
      one: 'La página censurada es ahora una imagen: su texto ya no se puede buscar ni copiar.',
      other: 'Las {n} páginas censuradas son ahora imágenes: su texto ya no se puede buscar ni copiar.'
    },
    recognize: 'Reconocer texto',
    alsoOn: {
      one: 'Parte de lo censurado se repite en la página {pages} y allí sigue visible. Censúralo también en esa página.',
      other: 'Parte de lo censurado se repite en las páginas {pages} y allí sigue visible. Censúralo también en esas páginas.'
    },
    ocrRemoved: 'Se ha quitado la capa de texto del OCR de las páginas censuradas.',
    recognizeAgain: 'Reconocer de nuevo',
    hiddenText: 'Quedan páginas con texto invisible fuera de las zonas censuradas.',
    sharedFields: 'Algún campo de formulario censurado aparece también en otra página y conserva allí su valor.',
    markArea: 'Marcar zona',
    markAreaTip: 'Arrastra sobre lo que quieras censurar',
    markText: 'Marcar texto',
    markTextTip: 'Selecciona el texto que quieras censurar',
    applyCount: 'Censurar ({n})',
    removeMarkTip: 'Clic para quitar esta marca',
    hintEmpty: 'Marca lo que quieras eliminar. Nada se borra hasta pulsar «Censurar».',
    hintMarked: {
      one: '{n} zona marcada. Haz clic en una marca para quitarla.',
      other: '{n} zonas marcadas. Haz clic en una marca para quitarla.'
    }
  },
  redactFind: {
    title: 'Buscar y marcar',
    run: 'Marcar coincidencias',
    found: { one: '{n} coincidencias marcadas para censurar', other: '{n} coincidencias marcadas para censurar' },
    none: 'No se han encontrado coincidencias',
    byText: 'Un texto concreto',
    byPattern: 'Un tipo de dato',
    textLabel: 'Texto a censurar en todo el documento',
    patternLabel: 'Tipo de dato',
    patterns: {
      dni: 'DNI',
      nie: 'NIE',
      email: 'Correo electrónico',
      phone: 'Teléfono',
      iban: 'Cuenta bancaria (IBAN)',
      card: 'Tarjeta bancaria'
    },
    note: 'Las coincidencias quedan marcadas para que las revises; nada se elimina hasta pulsar «Censurar».'
  },
  crop: {
    title: 'Recortar',
    card: 'Quita márgenes o lo que sobra de las páginas.',
    removed: 'Páginas recortadas: lo que quedaba fuera se ha eliminado',
    hidden: 'Páginas recortadas: lo de fuera queda oculto, no eliminado',
    message: 'Se conservará solo la zona marcada. Puedes recortar esta página o todas las del documento con la misma zona.',
    thisPage: 'Recortar esta página',
    allPages: 'Recortar todas',
    removeOption: 'Eliminar lo que queda fuera (no solo ocultarlo)',
    removeHint:
      'Lo de fuera desaparece del archivo, como al censurar. Sin esta opción solo se oculta y otro programa podría volver a mostrarlo.',
    hint: 'Dibuja sobre la página la zona que quieres conservar'
  },
  protect: {
    title: 'Proteger con contraseña',
    card: 'Cifra el PDF y limita imprimir, copiar o modificar.',
    run: 'Proteger',
    done: 'El documento se cifrará al guardarlo',
    userLabel: 'Contraseña para abrir (opcional)',
    userHint: 'Déjala vacía si cualquiera puede abrirlo pero quieres limitar lo que se hace con él.',
    ownerLabel: 'Contraseña de propietario',
    ownerHint: 'Permite cambiar estas restricciones. Mínimo 4 caracteres.',
    repeatLabel: 'Repítela',
    mismatch: 'Las contraseñas de propietario no coinciden.',
    same: 'La contraseña de propietario debe ser distinta de la de apertura.',
    weak: 'Una contraseña de apertura de menos de 8 caracteres se puede adivinar por fuerza bruta. Usa una frase larga si el contenido es sensible.',
    signed: 'El documento está firmado: guardarlo con una protección nueva invalidará sus firmas digitales.',
    permsLabel: 'Quien abra el documento podrá…',
    perms: {
      print: 'Imprimir',
      copy: 'Copiar texto e imágenes',
      modify: 'Modificar el contenido',
      annotate: 'Añadir comentarios',
      form: 'Rellenar formularios',
      assemble: 'Reorganizar páginas'
    },
    note: 'Se usa cifrado AES de 256 bits. Folio no guarda las contraseñas: si las olvidas, no hay forma de recuperar el documento protegido.'
  },
  sanitize: {
    title: 'Limpiar datos ocultos',
    card: 'Quita metadatos, adjuntos, scripts y texto invisible.',
    run: 'Limpiar',
    done: 'Datos ocultos eliminados. Guarda el documento para hacerlo definitivo.',
    intro: 'Elige qué información quieres eliminar antes de compartir el documento.',
    items: {
      metadata: 'Metadatos (autor, título, programa, fechas)',
      attachments: 'Archivos adjuntos',
      javascript: 'Scripts incrustados',
      hiddenText: 'Texto invisible',
      thumbnails: 'Miniaturas guardadas',
      comments: 'Comentarios y anotaciones',
      links: 'Enlaces'
    },
    signed: 'El documento está firmado: limpiarlo invalidará sus firmas digitales.'
  },
  optimize: {
    title: 'Comprimir',
    card: 'Guarda una copia más ligera para enviar por correo.',
    run: 'Guardar copia comprimida',
    busy: 'Comprimiendo',
    busyMessage: 'Esto puede tardar unos segundos…',
    saved: 'Copia guardada: {before} → {after} ({percent} % menos)',
    savedLittle: 'Copia guardada. Este documento ya estaba muy optimizado y apenas se ha podido reducir.',
    current: 'Tamaño actual: {size}. Se guarda una copia; el documento abierto no cambia.',
    light: 'Ligera',
    balanced: 'Equilibrada',
    strong: 'Máxima',
    lightHint: 'Conserva la calidad de impresión; reduce poco.',
    balancedHint: 'Buena calidad en pantalla; recomendada para enviar por correo.',
    strongHint: 'El menor tamaño posible; las imágenes pierden nitidez.',
    grayscale: 'Convertir las imágenes a escala de grises',
    signed: 'La copia comprimida no conservará las firmas digitales.'
  },
  images: {
    title: 'Exportar a imágenes',
    card: 'Una imagen PNG o JPEG por cada página.',
    run: 'Elegir carpeta y exportar',
    busy: 'Exportando imágenes',
    saved: { one: 'Se han guardado {n} imágenes', other: 'Se han guardado {n} imágenes' },
    savedReduced: {
      one: 'Se han guardado {n} imágenes ({reduced} a menor resolución por su tamaño)',
      other: 'Se han guardado {n} imágenes ({reduced} a menor resolución por su tamaño)'
    },
    format: 'Formato',
    resolution: 'Resolución',
    screen: 'Pantalla',
    normal: 'Normal',
    print: 'Impresión',
    count: {
      one: 'Se creará una imagen por cada una de las {n} páginas.',
      other: 'Se creará una imagen por cada una de las {n} páginas.'
    }
  },
  watermark: {
    title: 'Marca de agua',
    card: 'Un texto o una imagen sobre todas las páginas.',
    /** Texto que se propone y se escribe en el PDF. */
    defaultText: 'CONFIDENCIAL',
    run: 'Añadir a todas las páginas',
    busy: 'Añadiendo marca de agua',
    text: 'Texto',
    image: 'Imagen',
    bold: 'Negrita',
    color: 'Color',
    pickImage: 'Elegir imagen…',
    imageHint: 'PNG con transparencia recomendado',
    opacity: 'Opacidad',
    size: 'Tamaño (ancho de página)',
    diagonal: 'En diagonal',
    tile: 'Repetir por toda la página',
    background: 'Detrás del contenido'
  },
  headerFooter: {
    title: 'Encabezado y pie de página',
    cardTitle: 'Encabezado y pie',
    card: 'Texto fijo arriba y abajo, con fecha o nombre del archivo.',
    run: 'Añadir',
    busy: 'Añadiendo encabezado y pie',
    header: 'Encabezado',
    footer: 'Pie de página',
    hint: 'Puedes usar {page}, {pages}, {date} y {file}.',
    left: 'Izquierda',
    center: 'Centro',
    right: 'Derecha',
    fontSize: 'Tamaño de letra'
  },
  pageNumbers: {
    title: 'Numerar páginas',
    card: 'Numeración automática en la posición que elijas.',
    run: 'Numerar',
    busy: 'Numerando páginas',
    format: 'Formato',
    /** Formatos que se escriben en el PDF: {page} y {pages} los rellena el motor. */
    formatPage: 'Página {page}',
    formatPageOf: 'Página {page} de {pages}',
    position: 'Posición',
    positions: {
      bottomCenter: 'Abajo, centro',
      bottomRight: 'Abajo, derecha',
      bottomLeft: 'Abajo, izquierda',
      topCenter: 'Arriba, centro',
      topRight: 'Arriba, derecha',
      topLeft: 'Arriba, izquierda'
    },
    start: 'Empezar en',
    size: 'Tamaño'
  },
  removeAdded: {
    title: 'Quitar lo añadido',
    card: 'Elimina marcas de agua, encabezados, numeración o la capa de OCR.',
    run: 'Quitar',
    searching: 'Buscando…',
    nothing: 'Este documento no tiene marcas de agua, encabezados, numeración ni capa de OCR añadidos con Folio.',
    kinds: {
      watermark: 'Marcas de agua',
      header: 'Encabezados',
      footer: 'Pies de página',
      pagenum: 'Numeración',
      ocr: 'Capa de texto del OCR'
    }
  },
  ocr: {
    title: 'Reconocer texto (OCR)',
    card: 'Hace seleccionable y buscable el texto de páginas escaneadas.',
    run: 'Reconocer',
    busy: 'Reconociendo texto',
    analyzing: 'Analizando las páginas…',
    recognized: 'Texto reconocido en {pages}',
    recognizedSomeEmpty: 'Texto reconocido en {pages} (en {empty} no se encontró texto)',
    straightened: {
      one: 'Se ha enderezado {n} página que estaba girada',
      other: 'Se han enderezado {n} páginas que estaban giradas'
    },
    nothingFound: 'No se ha encontrado texto que reconocer en {pages}',
    allHadText: 'Todas las páginas tenían ya texto: no ha hecho falta reconocer nada',
    missingData: 'Faltan los datos de idioma del OCR. Ejecuta scripts/fetch-tessdata.ps1 para descargarlos.',
    intro: 'Añade una capa de texto invisible sobre las páginas escaneadas, sin tocar la imagen. Después podrás buscar, seleccionar y copiar.',
    languages: 'Idiomas del documento',
    languageNames: {
      spa: 'Español',
      eng: 'Inglés',
      cat: 'Catalán',
      glg: 'Gallego',
      eus: 'Euskera',
      fra: 'Francés',
      deu: 'Alemán',
      por: 'Portugués',
      ita: 'Italiano'
    },
    skipText: 'Saltar las páginas que ya tienen texto',
    straighten: 'Enderezar las páginas escaneadas de lado o del revés'
  }
}

/** Modo Firmar (modes/sign): firma manuscrita, firma con certificado y comprobación de firmas. */
export const sign = {
  tools: {
    select: 'Seleccionar',
    signature: 'Firma',
    signatureTip: 'Estampar tu firma manuscrita',
    signatureItem: 'Firma {n}',
    initialsItem: 'Iniciales {n}',
    newSignature: 'Nueva firma…',
    manage: 'Gestionar firmas guardadas…',
    text: 'Texto',
    textTip: 'Escribir sobre el documento',
    check: 'Marca',
    cross: 'Aspa',
    date: 'Fecha de hoy',
    cert: 'Con certificado',
    certTip: 'Firmar con tu certificado digital',
    signatures: 'Firmas',
    signaturesTip: 'Comprobar las firmas digitales del documento'
  },
  options: {
    format: 'Formato',
    dateFormat: 'Formato de la fecha',
    size: 'Tamaño',
    color: 'Color',
    clickToPlace: 'Haz clic en el documento donde quieras colocarlo',
    placeSignature: 'Haz clic o arrastra donde quieras estampar la firma',
    createFirst: 'Crea primero tu firma en el menú Firma',
    drawCertBox: 'Dibuja el recuadro donde se verá la firma',
    signInvisible: 'Firmar sin marca visible'
  },
  /** Diálogo «Nueva firma» (firma manuscrita). */
  pad: {
    title: 'Nueva firma',
    saveForReuse: 'Guardar para reutilizarla',
    use: 'Usar esta firma',
    draw: 'Dibujar',
    type: 'Escribir',
    image: 'Imagen',
    signature: 'Firma',
    initials: 'Iniciales',
    drawHint: 'Firma aquí con el ratón o el dedo',
    yourName: 'Tu nombre',
    importedAlt: 'Firma importada',
    imageHint: 'Elige una foto o escaneo de tu firma sobre papel blanco',
    noStrokes: 'No se ha encontrado ningún trazo en la imagen',
    pickImage: 'Elegir imagen…',
    whiteRemoved: 'El fondo blanco se elimina automáticamente.',
    ink: 'Tinta',
    inkBlue: 'Azul',
    inkBlack: 'Negro'
  },
  /** Diálogo «Firmas guardadas». */
  saved: {
    title: 'Firmas guardadas',
    done: 'Listo',
    empty: 'No tienes firmas guardadas.',
    alt: 'Firma guardada',
    remove: 'Eliminar esta firma',
    privacy: 'Las firmas se guardan cifradas con tu cuenta de Windows y no salen de este equipo.'
  },
  /** Diálogo «Firmar con certificado digital». */
  cert: {
    title: 'Firmar con certificado digital',
    usePfx: 'Usar un archivo .pfx…',
    sign: 'Firmar',
    signing: 'Firmando…',
    certificate: 'Certificado',
    pfxFile: 'Archivo de certificado',
    change: 'Cambiar',
    issuedBy: 'Emitido por {issuer}',
    validUntil: 'válido hasta {date}',
    notValid: 'caducado o aún no válido',
    hardware: 'en tarjeta o dispositivo',
    password: 'Contraseña del certificado',
    passwordHint: 'Solo se usa para esta firma; no se guarda.',
    wrongPassword: 'La contraseña del certificado no es correcta.',
    searching: 'Buscando certificados en este equipo…',
    none: 'No hay certificados con clave privada instalados en tu cuenta de Windows. Puedes usar un archivo .pfx o .p12.',
    /** Motivos por los que un certificado puede no servir. */
    issue: {
      expired: 'caducado',
      notYetValid: 'aún no es válido',
      weakKey: 'clave demasiado débil',
      unsupportedKey: 'tipo de clave no admitido',
      noSigningUsage: 'no declara uso de firma',
      other: 'no sirve para firmar'
    },
    reason: 'Motivo (opcional)',
    reasonPlaceholder: 'Conforme',
    location: 'Lugar (opcional)',
    /**
     * Vista previa de la firma visible. El motor escribe estos mismos textos
     * dentro del PDF (engine/folio_engine/sign/appearance.py).
     */
    appearance: {
      signedBy: 'Firmado digitalmente por',
      date: 'Fecha: {date}',
      reason: 'Motivo: {text}',
      location: 'Lugar: {text}'
    },
    includeHandwritten: 'Incluir mi firma manuscrita',
    includeHandwrittenNone: 'Incluir mi firma manuscrita (crea una primero en el menú Firma)',
    invisible: 'La firma no dejará ninguna marca visible en las páginas, pero quedará incrustada en el documento y se podrá comprobar.',
    level: 'Tipo de firma',
    /** Tipos de firma con certificado, de menos a más. */
    levels: {
      basic: {
        title: 'Básica',
        text: 'Como AutoFirma o Adobe sin más ajustes: la fecha de la firma es la del reloj de tu equipo. No necesita Internet.'
      },
      timestamp: {
        title: 'Con sello de tiempo · recomendada',
        text: '{service} certifica la fecha y hora exactas de la firma, en lugar del reloj de tu equipo. Necesita Internet y solo recibe una huella, nunca el documento.',
        anyService: 'Un servicio de confianza'
      },
      longTerm: {
        title: 'De larga duración',
        text: 'Además guarda en el PDF la prueba de que tu certificado era válido al firmar, para que se pueda comprobar dentro de muchos años aunque haya caducado. Necesita Internet y tarda unos segundos más.'
      }
    },
    noTimestampService: 'Para firmar con sello de tiempo, elige un servicio en Ajustes → Servicio de sello de tiempo.',
    lock: 'Bloquear el documento: después no se podrá modificar ni firmar',
    unlockedHint: 'Sin bloquear se puede seguir firmando (por ejemplo, otra persona) y cualquier cambio posterior quedará a la vista al comprobar la firma.',
    withoutTimestamp: 'Firmar sin sello de tiempo',
    timestampOnly: 'Firmar solo con sello de tiempo',
    done: 'Documento firmado. Guárdalo para conservar la firma.',
    notLocked: 'Firmado, pero sin bloquear: el documento ya tenía firmas y este campo no admite bloqueo. Para bloquearlo, firma en una zona nueva.',
    /** Nombre propuesto al guardar el documento firmado; {name} es el original sin «.pdf». */
    fileName: '{name}-firmado.pdf'
  },
  /** Diálogo «Firmas digitales del documento». */
  check: {
    title: 'Firmas digitales del documento',
    revocation: 'Comprobar revocación en línea',
    revocationTip: 'Consulta a la autoridad de certificación si los certificados siguen en vigor',
    checking: 'Comprobando las firmas…',
    none: 'Este documento no contiene firmas digitales.',
    timestampAuthority: 'Autoridad de sellado',
    unknownSigner: 'Firmante desconocido',
    issuedBy: 'emitido por {issuer}',
    status: {
      valid: 'Firma válida',
      timestampValid: 'Sello de tiempo válido',
      timestampWarning: 'Sello de tiempo con avisos',
      warning: 'Firma válida con avisos',
      unverified: 'Firma íntegra, identidad sin verificar',
      invalid: 'Firma no válida',
      timestampInvalid: 'Sello de tiempo no válido',
      unknown: 'No se ha podido comprobar'
    }
  }
}

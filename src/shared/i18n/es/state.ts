/** Estado y utilidades de la interfaz (renderer/src/state y renderer/src/lib): avisos, errores y formatos. */
export const state = {
  /** Avisos de abrir, guardar y cerrar documentos (state/actions.ts). */
  actions: {
    engineRestarted: 'El motor de documentos se reinició y hubo que cerrar los documentos abiertos.',
    fileError: '{name}: {message}',
    saved: 'Documento guardado',
    savedSignaturesBroken: 'Guardado. Las firmas digitales que tenía el documento han dejado de ser válidas por los cambios.',
    historyKept: 'Para no romper la firma, el archivo conserva dentro los cambios intermedios hechos después de firmar.',
    changedOnDisk: {
      title: 'El archivo ha cambiado en el disco',
      message: 'Otro programa ha modificado este archivo desde que lo abriste. Si guardas ahora se perderán esos cambios.',
      overwrite: 'Sobrescribir'
    },
    unsaved: {
      title: '¿Guardar los cambios?',
      message: '«{name}» tiene cambios sin guardar.',
      dontSave: 'No guardar'
    },
    closeMany: {
      title: '¿Qué quieres cerrar?',
      message: 'Tienes {count} archivos abiertos: puedes cerrarlos todos o solo «{name}».',
      all: 'Cerrar todos',
      current: 'Solo el actual',
      remember: 'Recordar mi elección',
      rememberHint: 'Puedes cambiarla en Ajustes.'
    }
  },
  /** Programa predeterminado para PDF (state/defaultApp.ts). */
  defaultApp: {
    done: 'Listo: los PDF se abrirán con Folio.',
    hint: 'En la ventana de Windows, pulsa «Establecer predeterminada» junto a Folio.'
  },
  /** Errores genéricos (lib/api.ts). */
  errors: {
    unexpected: 'Se produjo un error inesperado.',
    cancelled: 'Operación cancelada.'
  },
  /** Formatos de «Fecha de hoy» (lib/dates.ts). */
  dateStyles: {
    short: 'Corta',
    shortTime: 'Corta con hora',
    medium: 'Abreviada',
    long: 'Larga',
    full: 'Larga con el día',
    iso: 'Internacional'
  },
  /** Servicios de sello de tiempo (lib/timestamp.ts). */
  timestamp: {
    recommended: '{name} (recomendado)',
    other: 'Otro servicio…',
    configured: 'el servicio configurado'
  },
  /** Fechas relativas (lib/util.ts). */
  when: {
    now: 'ahora'
  }
}

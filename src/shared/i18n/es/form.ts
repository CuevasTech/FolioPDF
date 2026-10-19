/** Modo Formularios (modes/form): rellenar y preparar formularios. */
export const form = {
  tools: {
    fill: 'Rellenar',
    design: 'Preparar',
    select: 'Seleccionar',
    detect: 'Detectar',
    detectTip: 'Proponer campos a partir de las líneas y recuadros de esta página',
    highlight: 'Resaltar campos',
    clear: 'Vaciar',
    clearTip: 'Borrar todo lo rellenado',
    clearTitle: 'Vaciar el formulario',
    clearMessage: 'Se borrará lo que hayas escrito en todos los campos. Puedes deshacerlo después.'
  },
  /** Herramientas para crear campos (botón y su descripción). */
  create: {
    text: { label: 'Texto', tip: 'Añadir: texto' },
    checkbox: { label: 'Casilla', tip: 'Añadir: casilla' },
    radio: { label: 'Opción', tip: 'Añadir: opción' },
    combo: { label: 'Desplegable', tip: 'Añadir: desplegable' },
    list: { label: 'Lista', tip: 'Añadir: lista' },
    signature: { label: 'Firma', tip: 'Añadir: firma' }
  },
  detect: {
    // El original dice «campos» también con uno; se deja igual (se puede pasar a singular).
    found: { one: '{n} campos propuestos en esta página', other: '{n} campos propuestos en esta página' },
    none: 'No se han encontrado zonas para campos en esta página',
    addField: 'Añadir campo',
    addFieldNamed: 'Añadir campo: {label}',
    addAll: 'Añadir todos',
    dismiss: 'Descartar'
  },
  hints: {
    place: 'Haz clic o arrastra en la página para colocar el campo',
    noFields: 'Este PDF no tiene campos. Usa «Preparar» para crearlos, o «Firmar» para escribir encima',
    xfa: 'Este formulario usa tecnología XFA de Adobe y puede no mostrarse completo'
  },
  /** Panel de propiedades del campo seleccionado. */
  inspector: {
    type: {
      text: 'Campo de texto',
      checkbox: 'Casilla',
      radio: 'Opción',
      combo: 'Desplegable',
      list: 'Lista',
      signature: 'Campo de firma',
      other: 'Campo'
    },
    group: 'Grupo',
    groupHint: 'Las opciones del mismo grupo son excluyentes entre sí.',
    optionValue: 'Valor de esta opción',
    name: 'Nombre',
    description: 'Descripción',
    descriptionHint: 'Aparece al pasar el ratón por el campo.',
    optional: 'Opcional',
    required: 'Obligatorio',
    readOnly: 'Solo lectura',
    multiline: 'Varias líneas',
    password: 'Ocultar lo escrito (contraseña)',
    maxLength: 'Longitud máxima',
    checkedValue: 'Valor al marcar',
    options: 'Opciones',
    optionsHint: 'Una opción por línea.',
    appearance: 'Aspecto',
    font: 'Letra',
    fonts: { sans: 'Sin serifa', serif: 'Con serifa', mono: 'Monoespaciada' },
    fontSize: 'Tamaño (0 = automático)',
    align: 'Alineación',
    alignLeft: 'Izq.',
    alignCenter: 'Centro',
    alignRight: 'Der.',
    textColor: 'Texto',
    fill: 'Fondo',
    border: 'Borde',
    duplicate: 'Duplicar'
  },
  /** Rellenado (FillLayer). */
  fill: {
    calculatedTip: '{title} · se calcula automáticamente',
    calculatedLabel: '{title}: se calcula automáticamente',
    signHere: 'Firmar aquí con certificado'
  }
}

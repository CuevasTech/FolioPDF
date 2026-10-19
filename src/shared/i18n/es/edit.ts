/** Modo Editar (modes/edit). */
export const edit = {
  tools: {
    selectTip: 'Seleccionar y mover elementos',
    text: 'Texto',
    textTip: 'Añadir texto',
    image: 'Imagen',
    imageTip: 'Insertar una imagen',
    shapeTip: 'Dibujar una forma',
    whiteout: 'Tapar',
    whiteoutTip: 'Tapar una zona con blanco',
    content: 'Editar contenido',
    contentTip: 'Cambiar el texto y las imágenes que ya tiene el PDF'
  },
  object: {
    opacity: 'Opacidad',
    bringToFront: 'Traer al frente',
    sendToBack: 'Enviar al fondo',
    border: 'Borde',
    strokeColor: 'Color del trazo',
    fillColor: 'Color de relleno'
  },
  hints: {
    content:
      'Haz clic en un párrafo para reescribirlo (Alt+clic: solo una línea), o en una imagen para moverla, reemplazarla o quitarla',
    whiteout: 'Arrastra sobre la zona que quieras tapar. Para eliminar datos de verdad usa «Censurar» en Más'
  },
  existing: {
    signedTitle: 'Este documento está firmado',
    signedMessage:
      'Modificar su contenido original invalidará las firmas digitales que contiene. Si solo necesitas añadir texto o rellenar campos, puedes hacerlo sin romperlas.',
    signedConfirm: 'Modificar de todos modos',
    paragraphTip: 'Clic: reescribir el párrafo · Alt+clic: editar solo una línea',
    mixedParagraph: 'Este párrafo mezcla estilos: al reescribirlo tomará el predominante (Alt+clic: editar solo una línea)',
    mixedLine: 'Esta línea mezcla estilos: al editarla tomará el estilo predominante',
    moveOrScale: 'Mover o escalar',
    replace: 'Reemplazar'
  }
}

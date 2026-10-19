"""Catalogo ingles de los textos del motor (ver ``i18n.py``).

Clave: el texto espanol exacto que aparece en el codigo dentro de ``_()`` o
``N_()``. Valor: su traduccion, con los mismos marcadores ``{nombre}``.

Estilo: ingles de EE. UU., sencillo y breve; comillas tipograficas y apostrofo
curvo. Glosario: censurar = redact, firmar = sign, sello de tiempo = timestamp,
certificado = certificate, digitalizar = scan, motor de documentos = document
engine, contrasena = password, pagina = page.

Las pruebas (tests/test_i18n.py) comprueban que cada texto de ``_()`` tiene
aqui su traduccion, que no sobra ninguna entrada y que los marcadores coinciden.
"""

from __future__ import annotations

CATALOG: dict[str, str] = {
    # ------------------------------------------------------------ comunes
    "Parámetros no válidos.": "Invalid parameters.",
    "No se encontró el elemento.": "The item wasn’t found.",
    "Operación cancelada.": "Operation canceled.",
    "Operación no permitida.": "Operation not allowed.",
    "Operación desconocida.": "Unknown operation.",
    "Se produjo un error inesperado al procesar el documento.": "An unexpected error occurred while processing the document.",
    "No hay memoria suficiente para completar la operación.": "There isn’t enough memory to complete the operation.",
    "No se pudo iniciar el motor de documentos.": "Couldn’t start the document engine.",
    "El motor de documentos se ha detenido.": "The document engine has stopped.",
    "Error del proceso intermediario.": "Broker process error.",
    "Respuesta no válida del proceso intermediario.": "Invalid response from the broker process.",
    "El documento ya no está abierto.": "The document is no longer open.",
    # ------------------------------------------------------------ fechas
    "{day}/{month}/{year}": "{month}/{day}/{year}",
    "{day} de {month} de {year}": "{month} {day}, {year}",
    "enero": "January",
    "febrero": "February",
    "marzo": "March",
    "abril": "April",
    "mayo": "May",
    "junio": "June",
    "julio": "July",
    "agosto": "August",
    "septiembre": "September",
    "octubre": "October",
    "noviembre": "November",
    "diciembre": "December",
    "fecha desconocida": "unknown date",
    # ------------------------------------------------- archivos y carpetas
    "No se pudo escribir el archivo. Puede estar abierto en otro programa o la carpeta ser de solo lectura.": (
        "Couldn’t write the file. It may be open in another program, or the folder may be read-only."
    ),
    "El archivo es demasiado grande.": "The file is too large.",
    "El archivo resultante es demasiado grande.": "The resulting file is too large.",
    "No se pudo leer el archivo.": "Couldn’t read the file.",
    "No se pudo guardar el archivo.": "Couldn’t save the file.",
    "No se pudo guardar el archivo. Comprueba que hay espacio en el disco.": (
        "Couldn’t save the file. Check that there’s enough disk space."
    ),
    "No se pudo guardar el archivo. Comprueba que la carpeta admite escritura.": (
        "Couldn’t save the file. Check that the folder isn’t read-only."
    ),
    "documento": "document",
    "pagina": "page",
    "Ya hay demasiados archivos con ese nombre en la carpeta.": "There are already too many files with that name in the folder.",
    "Extensión de archivo no permitida.": "File extension not allowed.",
    "Nombre de archivo no válido.": "Invalid file name.",
    "No se pudo preparar la copia de trabajo.": "Couldn’t prepare the working copy.",
    "No se pudo crear el archivo en la carpeta de destino.": "Couldn’t create the file in the destination folder.",
    "No se pudo escribir en la carpeta de destino.": "Couldn’t write to the destination folder.",
    "La ruta contiene caracteres no válidos.": "The path contains invalid characters.",
    "La ruta debe ser absoluta y apuntar a una carpeta normal.": "The path must be absolute and point to a regular folder.",
    "Nombre de archivo reservado por el sistema.": "This file name is reserved by the system.",
    "El archivo no existe o no es un archivo normal.": "The file doesn’t exist or isn’t a regular file.",
    "No se pudo acceder al archivo.": "Couldn’t access the file.",
    "La carpeta de destino no existe.": "The destination folder doesn’t exist.",
    "El destino no es un archivo normal.": "The destination isn’t a regular file.",
    "No se pudo acceder a la carpeta de destino.": "Couldn’t access the destination folder.",
    "Los archivos ocupan demasiado en conjunto.": "The files are too large altogether.",
    # ------------------------------------------------------- Word
    "La conversión a Word tarda demasiado y se ha detenido.": "The Word conversion was taking too long and was stopped.",
    "No se pudo convertir este documento a Word.": "Couldn’t convert this document to Word.",
    "La conversión a Word no está disponible en esta instalación.": "Word conversion isn’t available in this installation.",
    "Convirtiendo el documento…": "Converting the document…",
    # ---------------------------------------------------- abrir y guardar
    "Abrir": "Open",
    "El archivo no es un PDF válido o está dañado.": "The file isn’t a valid PDF or is damaged.",
    "El archivo no es un PDF.": "The file isn’t a PDF.",
    "El PDF no contiene páginas.": "The PDF has no pages.",
    "Este PDF está protegido con contraseña.": "This PDF is password-protected.",
    "La contraseña no es correcta.": "The password is incorrect.",
    "Esa no es la contraseña de propietario.": "That isn’t the owner password.",
    "No se pudo reabrir el documento protegido.": "Couldn’t reopen the protected document.",
    "No se pudo leer el documento protegido.": "Couldn’t read the protected document.",
    "Este tipo de archivo no es compatible. Abre un PDF o una imagen.": (
        "This file type isn’t supported. Open a PDF or an image."
    ),
    "Este tipo de archivo no es compatible. Usa un PDF o una imagen.": "This file type isn’t supported. Use a PDF or an image.",
    "El archivo ha cambiado en el disco desde que se abrió. ¿Quieres sobrescribirlo?": (
        "The file has changed on disk since it was opened. Do you want to overwrite it?"
    ),
    "Editar propiedades": "Edit properties",
    "No hay nada que deshacer.": "There’s nothing to undo.",
    "No hay nada que rehacer.": "There’s nothing to redo.",
    "Falta la información para rehacer.": "The information needed to redo is missing.",
    "No se pudo completar la operación de forma segura. No se ha cambiado el documento.": (
        "Couldn’t complete the operation safely. The document wasn’t changed."
    ),
    # ------------------------------------------------- permisos y bloqueos
    "El autor de este PDF no permite {what}. Introduce la contraseña de propietario para desbloquearlo.": (
        "The author of this PDF doesn’t allow {what}. Enter the owner password to unlock it."
    ),
    "modificar su contenido": "changing its content",
    "añadir comentarios": "adding comments",
    "rellenar el formulario": "filling in the form",
    "reorganizar sus páginas": "reorganizing its pages",
    "copiar su contenido": "copying its content",
    "imprimirlo": "printing it",
    "hacer esto": "this",
    "El documento está bloqueado por su firma: ya no admite cambios.": (
        "The document is locked by its signature: it no longer accepts changes."
    ),
    "Quien firmó este documento solo permite rellenar el formulario y firmar.": (
        "The signer of this document only allows filling in the form and signing."
    ),
    "Quien firmó este documento solo permite rellenar, firmar y añadir comentarios.": (
        "The signer of this document only allows filling in, signing, and adding comments."
    ),
    "Este campo lo ha dejado fijo una firma del documento: no se puede cambiar.": (
        "A signature in the document has locked this field: it can’t be changed."
    ),
    # ------------------------------------------------------ elementos
    "El elemento ya no existe en la página.": "The item no longer exists on the page.",
    "No se pudo colocar el elemento.": "Couldn’t place the item.",
    "Tipo de elemento desconocido.": "Unknown item type.",
    "El elemento no tiene tamaño.": "The item has no size.",
    "El elemento no es un texto.": "The item isn’t text.",
    "El elemento no es una forma.": "The item isn’t a shape.",
    "La zona de destino es demasiado pequeña.": "The target area is too small.",
    "La forma necesita color de borde o de relleno.": "The shape needs a border or fill color.",
    "La fuente recibida no es válida.": "The font received isn’t valid.",
    "Forma desconocida.": "Unknown shape.",
    "Añadir texto": "Add text",
    "Editar texto": "Edit text",
    "Eliminar texto": "Delete text",
    "Editar párrafo": "Edit paragraph",
    "Eliminar párrafo": "Delete paragraph",
    "Añadir imagen": "Add image",
    "Eliminar imagen": "Delete image",
    "Mover imagen": "Move image",
    "Reemplazar imagen": "Replace image",
    "Añadir firma": "Add signature",
    "Añadir sello": "Add stamp",
    "Añadir forma": "Add shape",
    "Editar forma": "Edit shape",
    "Mover elemento": "Move item",
    "Girar elemento": "Rotate item",
    "Eliminar elemento": "Delete item",
    "Cambiar tamaño": "Resize",
    "Traer al frente": "Bring to front",
    "Enviar al fondo": "Send to back",
    "El contenido de la página ha cambiado. Vuelve a seleccionar el texto.": (
        "The page content has changed. Select the text again."
    ),
    "El contenido de la página ha cambiado. Vuelve a seleccionar el párrafo.": (
        "The page content has changed. Select the paragraph again."
    ),
    "El contenido de la página ha cambiado. Vuelve a seleccionar la imagen.": (
        "The page content has changed. Select the image again."
    ),
    "Esta imagen no se puede convertir en un elemento editable.": "This image can’t be turned into an editable item.",
    # ---------------------------------------------------------- imagenes
    "La imagen está vacía o es demasiado grande.": "The image is empty or too large.",
    "La imagen no tiene dimensiones válidas.": "The image doesn’t have valid dimensions.",
    "No se ha indicado ninguna imagen.": "No image was specified.",
    "La imagen es demasiado grande.": "The image is too large.",
    "La imagen es demasiado pequeña.": "The image is too small.",
    "No se pudo leer la imagen.": "Couldn’t read the image.",
    "Formato de imagen no compatible.": "Unsupported image format.",
    "Las esquinas no forman un cuadrilátero válido.": "The corners don’t form a valid quadrilateral.",
    "«{name}» no es una imagen compatible.": "“{name}” isn’t a supported image.",
    "«{name}»: {message}": "“{name}”: {message}",
    # ------------------------------------------------------- anotaciones
    "Añadir nota": "Add note",
    "Dibujar": "Draw",
    "Añadir rectángulo": "Add rectangle",
    "Añadir elipse": "Add ellipse",
    "Añadir línea": "Add line",
    "Añadir flecha": "Add arrow",
    "Resaltar": "Highlight",
    "Subrayar": "Underline",
    "Tachar": "Strike through",
    "Subrayado ondulado": "Squiggly underline",
    "La anotación ya no existe.": "The annotation no longer exists.",
    "Faltan los trazos del dibujo.": "The drawing strokes are missing.",
    "Editar anotación": "Edit annotation",
    "Mover anotación": "Move annotation",
    "Mover nota": "Move note",
    "Eliminar anotación": "Delete annotation",
    "Acoplar anotaciones": "Flatten annotations",
    "No hay ninguna zona que marcar.": "There’s no area to mark.",
    "Falta la posición de la nota.": "The note position is missing.",
    "Falta el rectángulo de la forma.": "The shape rectangle is missing.",
    "La forma es demasiado pequeña.": "The shape is too small.",
    "Faltan los extremos de la línea.": "The line endpoints are missing.",
    "La línea es demasiado corta.": "The line is too short.",
    "Este tipo de anotación no se puede modificar.": "This type of annotation can’t be changed.",
    "Las marcas de texto no se pueden mover.": "Text markups can’t be moved.",
    # -------------------------------------------------------- formularios
    "Texto": "Text",
    "Casilla": "Checkbox",
    "Grupo": "Group",
    "Desplegable": "Dropdown",
    "Lista": "List",
    "Opcion": "Option",
    "Opción 1": "Option 1",
    "Opción 2": "Option 2",
    "El campo ya no existe.": "The field no longer exists.",
    "El documento no tiene formulario.": "The document has no form.",
    "Añadir campo": "Add field",
    "Añadir {count} campos": "Add {count} fields",
    "Rellenar campo": "Fill in field",
    "Vaciar formulario": "Clear form",
    "Acoplar formulario": "Flatten form",
    "Editar campo": "Edit field",
    "Mover campo": "Move field",
    "Eliminar campo": "Delete field",
    "Duplicar campo": "Duplicate field",
    "Una firma del documento deja fijos algunos campos: el formulario no se puede acoplar.": (
        "A signature in the document locks some fields: the form can’t be flattened."
    ),
    "El campo es demasiado pequeño.": "The field is too small.",
    "Este campo es de solo lectura.": "This field is read-only.",
    "Este tipo de campo no se puede editar.": "This type of field can’t be edited.",
    "Un campo ya firmado no se puede modificar.": "A field that’s already signed can’t be changed.",
    "El valor debe ser un texto.": "The value must be text.",
    "Ya existe otro campo con ese nombre.": "Another field with that name already exists.",
    "No se puede eliminar un campo ya firmado.": "A field that’s already signed can’t be deleted.",
    "Este tipo de campo no se puede rellenar.": "This type of field can’t be filled in.",
    "Esa opción no existe en la lista.": "That option isn’t in the list.",
    "Este campo solo admite números.": "This field only accepts numbers.",
    "Fecha no válida. Escríbela así: {pattern}.": "Invalid date. Type it like this: {pattern}.",
    "El valor debe estar entre {low} y {high}.": "The value must be between {low} and {high}.",
    "El valor debe ser mayor o igual que {low}.": "The value must be greater than or equal to {low}.",
    "El valor debe ser menor o igual que {high}.": "The value must be less than or equal to {high}.",
    # ------------------------------------------------------------ paginas
    "La página ya no existe.": "The page no longer exists.",
    "La lista de páginas contiene elementos repetidos.": "The page list contains duplicates.",
    "El documento tendría demasiadas páginas.": "The document would have too many pages.",
    "No se pudo reorganizar el documento.": "Couldn’t reorganize the document.",
    "El autor de este PDF no permite extraer sus páginas a otro archivo. "
    "Introduce la contraseña de propietario para desbloquearlo.": (
        "The author of this PDF doesn’t allow extracting its pages to another file. "
        "Enter the owner password to unlock it."
    ),
    "El autor de este PDF no permite reutilizar sus páginas en otro documento. Indica su contraseña de propietario.": (
        "The author of this PDF doesn’t allow reusing its pages in another document. Enter its owner password."
    ),
    "Ese archivo está abierto en Folio. Elige otro nombre para la copia.": (
        "That file is open in Folio. Choose another name for the copy."
    ),
    "Girar páginas": "Rotate pages",
    "Eliminar página": "Delete page",
    "Eliminar páginas": "Delete pages",
    "No se pueden eliminar todas las páginas: el documento debe conservar al menos una.": (
        "You can’t delete all the pages: the document must keep at least one."
    ),
    "Mover página": "Move page",
    "Mover páginas": "Move pages",
    "Duplicar página": "Duplicate page",
    "Duplicar páginas": "Duplicate pages",
    "Duplicando páginas…": "Duplicating pages…",
    "Insertar página en blanco": "Insert blank page",
    "Insertar páginas en blanco": "Insert blank pages",
    "Insertar página": "Insert page",
    "Insertar páginas": "Insert pages",
    "Insertando páginas…": "Inserting pages…",
    "El archivo no tiene alguna de las páginas indicadas.": "The file is missing some of the specified pages.",
    "Extrayendo páginas…": "Extracting pages…",
    "Indica cada cuántas páginas hay que dividir.": "Specify how many pages to split by.",
    "Indica los intervalos de páginas.": "Specify the page ranges.",
    "Hay un intervalo de páginas que no existe en el documento.": "A page range doesn’t exist in the document.",
    "Se generarían demasiados archivos.": "Too many files would be created.",
    "Dividiendo el documento ({done} de {total})…": "Splitting the document ({done} of {total})…",
    "La zona de recorte queda fuera de la página o es demasiado pequeña.": (
        "The crop area is outside the page or too small."
    ),
    "La zona de recorte es demasiado pequeña.": "The crop area is too small.",
    "Recortar página": "Crop page",
    "Recortar páginas": "Crop pages",
    "Eliminando lo que queda fuera…": "Removing what’s outside…",
    "No se pudo eliminar lo que queda fuera del recorte. No se ha modificado el documento.": (
        "Couldn’t remove what’s outside the crop. The document wasn’t changed."
    ),
    "Sin título.pdf": "Untitled.pdf",
    "Imágenes.pdf": "Images.pdf",
    "Documento combinado.pdf": "Merged document.pdf",
    "Documento": "Document",
    "(sin título)": "(untitled)",
    "Convirtiendo imágenes ({done} de {total})…": "Converting images ({done} of {total})…",
    "Combinando archivos ({done} de {total})…": "Merging files ({done} of {total})…",
    "El recorte queda fuera de la página.": "The clip area is outside the page.",
    "La zona a dibujar es demasiado pequeña.": "The area to render is too small.",
    "La zona a dibujar es demasiado grande para esta escala.": "The area to render is too large for this scale.",
    # ---------------------------------------------------------- exportar
    "Elige otro nombre: la copia no puede sustituir al documento que tienes abierto.": (
        "Choose another name: the copy can’t replace the document you have open."
    ),
    "No se pudo preparar el archivo.": "Couldn’t prepare the file.",
    "No se pudo abrir la copia del documento.": "Couldn’t open the copy of the document.",
    "No se pudo preparar la copia del documento.": "Couldn’t prepare the copy of the document.",
    "Preparando la copia…": "Preparing the copy…",
    "Reduciendo las imágenes…": "Shrinking images…",
    "Compactando las fuentes…": "Compacting fonts…",
    "Guardando…": "Saving…",
    "No se pudo guardar la copia. Comprueba que la carpeta admite escritura.": (
        "Couldn’t save the copy. Check that the folder isn’t read-only."
    ),
    "Exportando la página {page} de {total}": "Exporting page {page} of {total}",
    "Extrayendo el texto de la página {page} de {total}": "Extracting text from page {page} of {total}",
    "===== Página {page} =====": "===== Page {page} =====",
    "No se pudo escribir el archivo de texto.": "Couldn’t write the text file.",
    # --------------------------------------------------------------- OCR
    "Faltan los datos de idioma del OCR. Vuelve a instalar la aplicación para recuperarlos.": (
        "The OCR language data is missing. Reinstall the app to restore it."
    ),
    "No se pudo iniciar el reconocimiento de texto (OCR).": "Couldn’t start text recognition (OCR).",
    "Reconocer texto (OCR)": "Recognize text (OCR)",
    "Reconociendo el texto de la página {page} de {total}": "Recognizing text on page {page} of {total}",
    # ---------------------------------------------------------- recuperar
    "{stem} (recuperado).pdf": "{stem} (recovered).pdf",
    "Ese documento ya no se puede recuperar.": "That document can no longer be recovered.",
    "El documento recuperado es demasiado grande.": "The recovered document is too large.",
    "No se pudo leer el documento recuperado.": "Couldn’t read the recovered document.",
    # -------------------------------------------------------- digitalizar
    "Buscando los bordes del papel…": "Finding the paper edges…",
    "Documento digitalizado.pdf": "Scanned document.pdf",
    "Esta digitalización ya no está abierta.": "This scan is no longer open.",
    "Esa página ya no está en la digitalización.": "That page is no longer in the scan.",
    "Como mucho {count} páginas por documento.": "No more than {count} pages per document.",
    "Hay páginas repetidas.": "Some pages are repeated.",
    "Preparando la página {page} de {total}…": "Preparing page {page} of {total}…",
    "Preparando las imágenes ({done} de {total})…": "Preparing images ({done} of {total})…",
    # ------------------------------------------- proteccion, limpieza, censura
    "La contraseña contiene caracteres con formas especiales que otros programas no reconocerían.": (
        "The password contains special character forms that other programs wouldn’t recognize."
    ),
    "La contraseña contiene caracteres no admitidos (de control o espacios especiales).": (
        "The password contains unsupported characters (control characters or special spaces)."
    ),
    "La contraseña es demasiado larga.": "The password is too long.",
    "Para cambiar la protección de este PDF hace falta su contraseña de propietario.": (
        "Changing the protection of this PDF requires its owner password."
    ),
    "La contraseña de propietario debe ser distinta de la de apertura: "
    "si coinciden, cualquiera que abra el documento puede saltarse las restricciones y quitar la protección.": (
        "The owner password must be different from the open password: if they match, anyone who opens "
        "the document can bypass the restrictions and remove the protection."
    ),
    "Limpiar información oculta": "Remove hidden information",
    "Limpiando…": "Cleaning up…",
    "No se pudo limpiar el documento. No se ha modificado.": "Couldn’t clean up the document. It wasn’t changed.",
    "Marcar para censurar": "Mark for redaction",
    "Quitar marca de censura": "Remove redaction mark",
    "Una de las zonas queda fuera de la página o es demasiado pequeña.": "One of the areas is outside the page or too small.",
    "Indica un texto o un tipo de dato que buscar, pero no los dos.": "Specify text or a type of data to search for, not both.",
    "Escribe el texto que quieres censurar.": "Type the text you want to redact.",
    "Buscando…": "Searching…",
    "Censurar": "Redact",
    "Censurando…": "Redacting…",
    "No se pudo censurar el documento. No se ha modificado.": "Couldn’t redact the document. It wasn’t changed.",
    "No se pudo completar la operación de forma segura: el contenido del documento está anidado en exceso. "
    "No se ha modificado el documento.": (
        "Couldn’t complete the operation safely: the document content is nested too deeply. "
        "The document wasn’t changed."
    ),
    "No se pudo modificar la página de forma segura. No se ha cambiado el documento.": (
        "Couldn’t change the page safely. The document wasn’t changed."
    ),
    "No se pudo censurar esta página de forma segura: su contenido tiene un formato que no se reconoce. "
    "No se ha modificado el documento.": (
        "Couldn’t redact this page safely: its content has an unrecognized format. The document wasn’t changed."
    ),
    "No se pudo censurar esta página de forma segura: tiene texto dentro de una máscara de transparencia, "
    "que no se puede eliminar. No se ha modificado el documento.": (
        "Couldn’t redact this page safely: it has text inside a transparency mask, which can’t be removed. "
        "The document wasn’t changed."
    ),
    "No se pudo comprobar el resultado de la censura. No se ha modificado el documento.": (
        "Couldn’t verify the redaction result. The document wasn’t changed."
    ),
    "No se pudo eliminar por completo lo que hay bajo una zona censurada. No se ha modificado el documento.": (
        "Couldn’t completely remove what’s under a redacted area. The document wasn’t changed."
    ),
    "No se pudo eliminar por completo el texto de una zona censurada. No se ha modificado el documento.": (
        "Couldn’t completely remove the text in a redacted area. The document wasn’t changed."
    ),
    "No se pudo convertir la página en imagen de forma segura. No se ha modificado el documento.": (
        "Couldn’t convert the page to an image safely. The document wasn’t changed."
    ),
    "La página convertida en imagen no coincide con la original. No se ha modificado el documento.": (
        "The page converted to an image doesn’t match the original. The document wasn’t changed."
    ),
    "No se pudo eliminar todo el texto oculto. No se ha modificado el documento.": (
        "Couldn’t remove all the hidden text. The document wasn’t changed."
    ),
    # -------------------------------- marcas de agua, encabezados, numeracion
    "El texto tiene demasiadas líneas.": "The text has too many lines.",
    "El texto resultante es demasiado largo.": "The resulting text is too long.",
    "No se pudo preparar el texto.": "Couldn’t prepare the text.",
    "Indica el texto o la imagen de la marca de agua.": "Specify the watermark text or image.",
    "El texto de la marca de agua está vacío.": "The watermark text is empty.",
    "Añadir marca de agua": "Add watermark",
    "Añadiendo la marca de agua": "Adding the watermark",
    "Escribe al menos un texto para el encabezado o el pie.": "Type at least one text for the header or footer.",
    "Añadir encabezado y pie": "Add header and footer",
    "Añadir encabezado": "Add header",
    "Añadir pie de página": "Add footer",
    "Añadiendo encabezado y pie": "Adding header and footer",
    "El formato de la numeración está vacío.": "The page number format is empty.",
    "Numerar páginas": "Number pages",
    "Numerando las páginas": "Numbering pages",
    "Quitar marca de agua": "Remove watermark",
    "Quitar encabezado": "Remove header",
    "Quitar pie de página": "Remove footer",
    "Quitar numeración": "Remove page numbers",
    "Quitar texto reconocido": "Remove recognized text",
    "Quitar elementos añadidos": "Remove added items",
    "Quitando elementos": "Removing items",
    # ----------------------------------------------------------------- firmar
    "Esta operación no está disponible en el motor aislado.": "This operation isn’t available in the sandboxed document engine.",
    "Firma no preparada.": "The signature isn’t ready.",
    "Algoritmo de resumen inesperado.": "Unexpected digest algorithm.",
    "Algoritmo de resumen no admitido.": "Unsupported digest algorithm.",
    "El resumen que se iba a firmar no tiene el tamaño esperado.": "The digest to sign isn’t the expected size.",
    "La zona de la firma es demasiado pequeña o queda fuera de la página.": (
        "The signature area is too small or outside the page."
    ),
    "La zona de la firma es demasiado pequeña.": "The signature area is too small.",
    "El resultado de la operación no es un PDF utilizable. No se ha cambiado nada.": (
        "The result of the operation isn’t a usable PDF. Nothing was changed."
    ),
    "La operación habría alterado el contenido ya firmado. No se ha cambiado nada.": (
        "The operation would have altered content that’s already signed. Nothing was changed."
    ),
    "Preparar para firmar": "Prepare for signing",
    "Reparar documento": "Repair document",
    "La estructura de este PDF no permite añadirle una firma sin dañar las que ya tiene.": (
        "The structure of this PDF doesn’t allow adding a signature without damaging the existing ones."
    ),
    "La estructura de este PDF no permite añadirle una firma.": "The structure of this PDF doesn’t allow adding a signature.",
    "La estructura de este PDF no permite añadirle un campo de firma.": (
        "The structure of this PDF doesn’t allow adding a signature field."
    ),
    "Se produjo un error inesperado al procesar la firma.": "An unexpected error occurred while processing the signature.",
    "Añadir campo de firma": "Add signature field",
    "Indica un campo de firma existente o una zona nueva, no las dos cosas.": (
        "Specify an existing signature field or a new area, not both."
    ),
    "Falta indicar la página donde colocar la firma.": "The page to place the signature on is missing.",
    "Hay un cambio de protección pendiente. Guarda el documento y fírmalo después.": (
        "There’s a pending protection change. Save the document and then sign it."
    ),
    "Ese campo ya está firmado.": "That field is already signed.",
    "La firma de larga duración necesita un sello de tiempo: configura un servidor en Ajustes.": (
        "A long-term signature needs a timestamp: set up a server in Settings."
    ),
    "El origen del certificado no es válido.": "The certificate source isn’t valid.",
    "El certificado no es válido.": "The certificate isn’t valid.",
    "La imagen de la firma es demasiado grande.": "The signature image is too large.",
    "La firma visible no tiene nada que mostrar: activa algún dato o añade tu firma manuscrita.": (
        "The visible signature has nothing to show: turn on some details or add your handwritten signature."
    ),
    "La firma creada no ha superado la comprobación. No se ha cambiado el documento.": (
        "The new signature failed verification. The document wasn’t changed."
    ),
    "El sello de tiempo recibido no es válido para esta firma. No se ha cambiado el documento.": (
        "The timestamp received isn’t valid for this signature. The document wasn’t changed."
    ),
    "Este equipo no reconoce como de confianza a la autoridad que emitió el certificado: "
    "quien reciba el documento puede ver la firma como no fiable.": (
        "This computer doesn’t trust the authority that issued the certificate: "
        "whoever receives the document may see the signature as untrusted."
    ),
    "El sello de tiempo procede de una autoridad que este equipo no reconoce como de confianza.": (
        "The timestamp comes from an authority this computer doesn’t trust."
    ),
    "El PDF tiene irregularidades de formato; algunos validadores pueden mostrar avisos.": (
        "The PDF has formatting irregularities; some validators may show warnings."
    ),
    "Firmar con certificado": "Sign with certificate",
    "Firma{number}": "Signature{number}",
    "Firmado digitalmente por": "Digitally signed by",
    "Fecha: {date}": "Date: {date}",
    "Motivo: {reason}": "Reason: {reason}",
    "Lugar: {location}": "Location: {location}",
    "No se pudo preparar la apariencia de la firma.": "Couldn’t prepare the signature appearance.",
    "El documento está bloqueado por su firma: no admite más firmas ni campos.": (
        "The document is locked by its signature: it doesn’t accept more signatures or fields."
    ),
    "Solo se puede certificar un documento que todavía no tiene firmas.": "Only a document with no signatures yet can be certified.",
    "Este PDF está cifrado con certificado y no se puede firmar ni validar desde aquí.": (
        "This PDF is encrypted with a certificate and can’t be signed or validated here."
    ),
    "No se pudo abrir el cifrado de este PDF para trabajar con sus firmas.": (
        "Couldn’t open the encryption of this PDF to work with its signatures."
    ),
    "Ya existe un campo con ese nombre en el documento.": "A field with that name already exists in the document.",
    "No hay ningún campo de firma vacío con ese nombre.": "There’s no empty signature field with that name.",
    "No se pudo obtener el sello de tiempo del servidor configurado. El documento no se ha firmado.": (
        "Couldn’t get a timestamp from the configured server. The document wasn’t signed."
    ),
    "No se pudo firmar: el documento o el campo de firma no admiten esta firma.": (
        "Couldn’t sign: the document or the signature field doesn’t accept this signature."
    ),
    "La firma de larga duración necesita un certificado emitido por una autoridad que este equipo reconozca "
    "(como la FNMT): con este certificado no se puede comprobar si sigue vigente. Firma con sello de tiempo, "
    "sin larga duración. No se ha firmado nada.": (
        "A long-term signature needs a certificate issued by an authority this computer recognizes "
        "(such as the FNMT): with this certificate, it can’t be checked whether it’s still valid. Sign with a "
        "timestamp, without long-term validation. Nothing was signed."
    ),
    "Tu certificado (o el del servicio de sello de tiempo) está revocado: no se ha firmado nada.": (
        "Your certificate (or the timestamp service’s) is revoked: nothing was signed."
    ),
    "Tu certificado (o el del servicio de sello de tiempo) no está en vigor en este momento: no se ha firmado nada.": (
        "Your certificate (or the timestamp service’s) isn’t valid right now: nothing was signed."
    ),
    "No se pudo comprobar en línea que tu certificado (o el del servicio de sello de tiempo) no está revocado, "
    "y una firma de larga duración necesita incluir esa comprobación. Revisa la conexión o firma solo con "
    "sello de tiempo. No se ha firmado nada.": (
        "Couldn’t check online that your certificate (or the timestamp service’s) isn’t revoked, and a "
        "long-term signature needs to include that check. Check your connection or sign with a timestamp only. "
        "Nothing was signed."
    ),
    "La clave del certificado ya no está disponible.": "The certificate key is no longer available.",
    "La firma obtenida no corresponde con el certificado elegido. No se ha firmado nada.": (
        "The signature obtained doesn’t match the chosen certificate. Nothing was signed."
    ),
    # ------------------------------------------------------- certificados
    "Windows no encuentra una autoridad de confianza para este certificado: "
    "quien reciba el documento puede ver la firma como no fiable.": (
        "Windows can’t find a trusted authority for this certificate: "
        "whoever receives the document may see the signature as untrusted."
    ),
    "Clave no compatible": "Unsupported key",
    "Este certificado todavía no es válido: lo será a partir del {date}.": (
        "This certificate isn’t valid yet: it will be starting {date}."
    ),
    "Este certificado caducó el {date} y ya no sirve para firmar.": (
        "This certificate expired on {date} and can no longer be used to sign."
    ),
    "La clave RSA de este certificado tiene {bits} bits. "
    "Por seguridad no se firma con claves de menos de {minimum} bits.": (
        "The RSA key of this certificate has {bits} bits. For security, keys with fewer than {minimum} bits "
        "aren’t used to sign."
    ),
    "El tipo de clave de este certificado no es compatible con la firma de documentos.": (
        "This certificate’s key type isn’t supported for signing documents."
    ),
    "El certificado no declara el uso de firma digital ni de no repudio: "
    "otros programas pueden no dar por buena esta firma.": (
        "The certificate doesn’t declare digital signature or non-repudiation usage: "
        "other programs may not accept this signature."
    ),
    "Windows devolvió una firma con un formato inesperado.": "Windows returned a signature in an unexpected format.",
    "Windows devolvió una firma con un tamaño inesperado.": "Windows returned a signature of an unexpected size.",
    "El archivo no es un certificado .pfx o .p12 válido.": "The file isn’t a valid .pfx or .p12 certificate.",
    "La contraseña del archivo de certificado no es correcta.": "The certificate file password is incorrect.",
    "El archivo no contiene un certificado con su clave privada.": "The file doesn’t contain a certificate with its private key.",
    "La clave del archivo no corresponde con su certificado.": "The key in the file doesn’t match its certificate.",
    "El certificado no se puede leer.": "The certificate can’t be read.",
    "No se pudo leer el archivo del certificado.": "Couldn’t read the certificate file.",
    "El certificado del archivo no se puede leer.": "The certificate in the file can’t be read.",
    "El certificado ya no está en el almacén de Windows o no tiene clave privada.": (
        "The certificate is no longer in the Windows certificate store or has no private key."
    ),
    "Windows no pudo firmar con la clave de este certificado.": "Windows couldn’t sign with this certificate’s key.",
    "El almacén de certificados de Windows solo está disponible en Windows.": (
        "The Windows certificate store is only available on Windows."
    ),
    "No se pudo abrir el almacén de certificados de Windows.": "Couldn’t open the Windows certificate store.",
    "Se ha cancelado el uso de la clave del certificado.": "Use of the certificate key was canceled.",
    "El PIN no es correcto.": "The PIN is incorrect.",
    "El PIN está bloqueado. Desbloquéalo con el programa de tu tarjeta o dispositivo.": (
        "The PIN is blocked. Unblock it with your card or device software."
    ),
    "No se encuentra la tarjeta o el dispositivo que guarda la clave de este certificado.": (
        "The card or device that holds this certificate’s key can’t be found."
    ),
    "Windows no encuentra la clave privada de este certificado.": "Windows can’t find this certificate’s private key.",
    "Windows no ha permitido usar la clave de este certificado.": "Windows didn’t allow using this certificate’s key.",
    "El proveedor que guarda la clave de este certificado no admite firmas con SHA-256. "
    "Vuelve a importar el certificado en Windows para poder firmar con él.": (
        "The provider that holds this certificate’s key doesn’t support SHA-256 signatures. "
        "Import the certificate into Windows again to sign with it."
    ),
    "El proveedor que guarda la clave de este certificado no es compatible.": (
        "The provider that holds this certificate’s key isn’t supported."
    ),
    "Este tipo de clave no es compatible.": "This key type isn’t supported.",
    # ---------------------------------------------- red: sello de tiempo, revocacion
    "La dirección del servidor de sello de tiempo no es válida. Debe empezar por http:// o https://.": (
        "The timestamp server address isn’t valid. It must start with http:// or https://."
    ),
    "La dirección del servidor de sello de tiempo no puede llevar usuario ni contraseña.": (
        "The timestamp server address can’t include a user name or password."
    ),
    "Petición de sello de tiempo no válida.": "Invalid timestamp request.",
    "No se pudo obtener el sello de tiempo del servidor configurado.": "Couldn’t get a timestamp from the configured server.",
    "Dirección de consulta de revocación no válida.": "Invalid revocation check address.",
    "No se pudo consultar la revocación del certificado.": "Couldn’t check the certificate’s revocation status.",
    # ------------------------------------------------------- validar firmas
    "No se pudo validar esta firma: su formato no es compatible o está dañado.": (
        "Couldn’t validate this signature: its format isn’t supported or it’s damaged."
    ),
    "La firma o su certificado usan un algoritmo que ya no se considera seguro (por ejemplo SHA-1 o una clave corta).": (
        "The signature or its certificate uses an algorithm that’s no longer considered secure "
        "(for example, SHA-1 or a short key)."
    ),
    "El certificado del firmante caducó el {date}. Sin un sello de tiempo de confianza "
    "no se puede comprobar que se firmara mientras era válido.": (
        "The signer’s certificate expired on {date}. Without a trusted timestamp, it can’t be verified "
        "that it was signed while the certificate was valid."
    ),
    "El certificado del firmante está autofirmado: ninguna autoridad de confianza avala su identidad.": (
        "The signer’s certificate is self-signed: no trusted authority vouches for their identity."
    ),
    "El certificado del firmante no está autorizado para firmar documentos.": (
        "The signer’s certificate isn’t authorized to sign documents."
    ),
    "El certificado del firmante no procede de ninguna autoridad en la que confíe este equipo.": (
        "The signer’s certificate doesn’t come from any authority this computer trusts."
    ),
    "No se pudo comprobar que el certificado del firmante proceda de una autoridad de confianza.": (
        "Couldn’t verify that the signer’s certificate comes from a trusted authority."
    ),
    "La firma no es válida: el contenido firmado se ha alterado o la firma está dañada.": (
        "The signature isn’t valid: the signed content was altered or the signature is damaged."
    ),
    "La firma no abarca el documento de la forma habitual: no se puede garantizar que lo que se ve sea lo que se firmó.": (
        "The signature doesn’t cover the document in the usual way: there’s no guarantee that what you see "
        "is what was signed."
    ),
    "El documento no se ha modificado desde que se aplicó esta firma.": (
        "The document hasn’t been changed since this signature was applied."
    ),
    "Después de esta firma el documento se modificó de una forma que la propia firma no permitía.": (
        "After this signature, the document was changed in a way the signature itself didn’t allow."
    ),
    "La firma es íntegra. Después solo se añadió información para poder validarla más adelante.": (
        "The signature is intact. Afterward, only information to validate it later was added."
    ),
    "La firma es íntegra. Después se añadieron otras firmas o se rellenaron campos, cambios que esta firma admite.": (
        "The signature is intact. Afterward, other signatures were added or fields were filled in, "
        "changes this signature allows."
    ),
    "La firma es íntegra, pero después se añadieron comentarios o anotaciones al documento.": (
        "The signature is intact, but comments or annotations were added to the document afterward."
    ),
    "La firma es íntegra, pero el documento se modificó después de firmarlo: "
    "lo que se muestra puede no coincidir con lo que se firmó.": (
        "The signature is intact, but the document was changed after it was signed: "
        "what’s shown may not match what was signed."
    ),
    "El certificado se revocó el {date}, después de la fecha del sello de tiempo: "
    "la firma se hizo cuando todavía era válido.": (
        "The certificate was revoked on {date}, after the timestamp date: "
        "the signature was made while it was still valid."
    ),
    "El certificado ha caducado, pero el sello de tiempo demuestra que se firmó mientras era válido.": (
        "The certificate has expired, but the timestamp proves it was signed while it was valid."
    ),
    "La identidad del firmante la avala una autoridad en la que confía este equipo.": (
        "The signer’s identity is vouched for by an authority this computer trusts."
    ),
    "El certificado del firmante fue revocado el {date} y no hay prueba de que la firma sea anterior.": (
        "The signer’s certificate was revoked on {date}, and there’s no proof that the signature is older."
    ),
    "Se ha comprobado en línea que el certificado no está revocado.": "It was verified online that the certificate isn’t revoked.",
    "No se pudo comprobar en línea si el certificado está revocado.": "Couldn’t check online whether the certificate is revoked.",
    "No se ha comprobado si el certificado está revocado (requiere conexión).": (
        "Whether the certificate is revoked hasn’t been checked (requires a connection)."
    ),
    "El sello de tiempo de la firma no es válido.": "The signature’s timestamp isn’t valid.",
    "Lleva un sello de tiempo, pero de una autoridad en la que este equipo no confía.": (
        "It has a timestamp, but from an authority this computer doesn’t trust."
    ),
    "Lleva un sello de tiempo de confianza del {date}.": "It has a trusted timestamp from {date}.",
    "La fecha de la firma es la del equipo de quien firmó; no lleva sello de tiempo.": (
        "The signing date comes from the signer’s computer; it has no timestamp."
    ),
    "El documento incluye la información de revocación de los certificados, para poder comprobar la firma "
    "a largo plazo (aunque el certificado caduque).": (
        "The document includes the certificates’ revocation information, so the signature can be checked "
        "in the long term (even if the certificate expires)."
    ),
    "Es la firma con la que el autor certificó el documento.": "This is the signature the author used to certify the document.",
    "La firma no cumple las condiciones que imponía el campo de firma.": (
        "The signature doesn’t meet the conditions set by the signature field."
    ),
    "El archivo tiene irregularidades de formato: la validación es menos fiable.": (
        "The file has formatting irregularities: the validation is less reliable."
    ),
    "El sello de tiempo del documento no es válido: el contenido se ha alterado o el sello está dañado.": (
        "The document timestamp isn’t valid: the content was altered or the timestamp is damaged."
    ),
    "El sello de tiempo no abarca el documento de la forma habitual.": "The timestamp doesn’t cover the document in the usual way.",
    "El documento se modificó después de este sello de tiempo.": "The document was changed after this timestamp.",
    "Sello de tiempo del documento del {date}.": "Document timestamp from {date}.",
    "El sello procede de una autoridad en la que este equipo no confía.": (
        "The timestamp comes from an authority this computer doesn’t trust."
    ),
    # --- IA local
    'La IA no está activada o sus modelos no están descargados.': 'AI isn’t turned on or its models aren’t downloaded.',
    'No se pudo iniciar la IA.': 'The AI couldn’t start.',
    'La IA está ocupada con otra respuesta.': 'The AI is busy with another answer.',
    'La IA no ha podido hacerlo.': 'The AI couldn’t do it.',
    'La IA ha dejado de responder. Inténtalo de nuevo.': 'The AI stopped responding. Try again.',
    'Preparando la búsqueda…': 'Preparing the search…',
    'El documento no tiene texto que la IA pueda leer. Prueba antes con «Reconocer texto (OCR)».': 'The document has no text the AI can read. Try “Recognize text (OCR)” first.',
    'Pensando…': 'Thinking…',
    'Resumiendo las páginas {first}–{last}…': 'Summarizing pages {first}–{last}…',
    'Escribiendo…': 'Writing…',
    'Añadir nota de la IA': 'Add AI note',
    'Buscando datos personales…': 'Looking for personal data…',
    'Resumiendo…': 'Summarizing…',
    'Uniendo el resumen…': 'Putting the summary together…',
    'IA de Folio': 'Folio AI',
    'Generado con IA (Folio)': 'AI-generated (Folio)',
}

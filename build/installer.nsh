; Folio como aplicacion registrada para abrir PDF: asi aparece en
; Configuracion > Aplicaciones > Aplicaciones predeterminadas y Folio puede
; abrir directamente su pagina para que el usuario lo elija como
; predeterminado (Windows no deja que un programa se ponga solo como
; predeterminado: lo decide siempre el usuario).
; El tipo de archivo (ProgId) Folio.PDF lo crea electron-builder con
; fileAssociations (ver electron-builder.yml).
!macro customInstall
  WriteRegStr SHELL_CONTEXT "Software\CuevasTech\Folio\Capabilities" "ApplicationName" "Folio"
  ; Descripcion en el idioma del instalador (1034 y 3082: espanol).
  ${If} $LANGUAGE == 1034
  ${OrIf} $LANGUAGE == 3082
    WriteRegStr SHELL_CONTEXT "Software\CuevasTech\Folio\Capabilities" "ApplicationDescription" "Ver, rellenar, firmar y editar documentos PDF."
  ${Else}
    WriteRegStr SHELL_CONTEXT "Software\CuevasTech\Folio\Capabilities" "ApplicationDescription" "View, fill in, sign and edit PDF documents."
  ${EndIf}
  WriteRegStr SHELL_CONTEXT "Software\CuevasTech\Folio\Capabilities" "ApplicationIcon" "$INSTDIR\${APP_EXECUTABLE_FILENAME},0"
  WriteRegStr SHELL_CONTEXT "Software\CuevasTech\Folio\Capabilities\FileAssociations" ".pdf" "Folio.PDF"
  WriteRegStr SHELL_CONTEXT "Software\RegisteredApplications" "Folio" "Software\CuevasTech\Folio\Capabilities"
  ; Que el Explorador vuelva a leer las asociaciones.
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
!macroend

; Al desinstalar Folio se borra tambien el contenedor aislado del motor: el
; perfil de AppContainer de Windows y su carpeta, con las copias de trabajo de
; documentos que hubieran quedado tras un cierre inesperado. Al actualizar a
; una version nueva no se toca (la nueva lo sigue usando), y el registro de
; aplicacion tampoco (la version nueva lo vuelve a escribir).
!macro customUnInstall
  ${ifNot} ${isUpdated}
    nsExec::Exec '"$INSTDIR\resources\engine\folio-engine.exe" --remove-sandbox'
    Pop $0
    ; Los modelos de IA descargados (pueden ser varios GB); los ajustes y el
    ; registro de Folio se quedan, como hasta ahora.
    RMDir /r "$LOCALAPPDATA\Folio\models"
    DeleteRegValue SHELL_CONTEXT "Software\RegisteredApplications" "Folio"
    DeleteRegKey SHELL_CONTEXT "Software\CuevasTech\Folio"
    DeleteRegKey /ifempty SHELL_CONTEXT "Software\CuevasTech"
    ; El instalador deja .pdf apuntando a Folio.PDF (electron-builder no lo
    ; deshace): se quita solo si sigue siendo el de Folio, para no dejar a
    ; Windows con un tipo de archivo que ya no existe. Si otro programa lo ha
    ; cambiado despues, no se toca.
    ReadRegStr $0 SHELL_CONTEXT "Software\Classes\.pdf" ""
    ${if} $0 == "Folio.PDF"
      DeleteRegValue SHELL_CONTEXT "Software\Classes\.pdf" ""
    ${endIf}
    System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
  ${endIf}
!macroend
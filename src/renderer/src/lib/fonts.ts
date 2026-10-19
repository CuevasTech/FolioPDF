import type { FontFamily } from '@shared/types'

const loaded = new Set<string>()

/**
 * Aptos no viene con Windows: el proceso principal la sirve en
 * folio://app/fonts/… (ver src/main/fonts.ts). Se carga aquí para que lo que se
 * ve al escribir coincida con lo que el motor escribe en el PDF.
 */
export async function loadExtraFontFaces(fonts: FontFamily[]): Promise<void> {
  const aptos = fonts.find((font) => font.family === 'Aptos')
  if (!aptos || loaded.has('Aptos')) return
  loaded.add('Aptos')
  const variants: [string, FontFaceDescriptors][] = [
    ['regular', {}],
    ...(aptos.bold ? ([['bold', { weight: '700' }]] as [string, FontFaceDescriptors][]) : []),
    ...(aptos.italic ? ([['italic', { style: 'italic' }]] as [string, FontFaceDescriptors][]) : []),
    ...(aptos.boldItalic ? ([['boldItalic', { weight: '700', style: 'italic' }]] as [string, FontFaceDescriptors][]) : [])
  ]
  for (const [style, descriptors] of variants) {
    try {
      const face = new FontFace('Aptos', `url(folio://app/fonts/aptos-${style}.ttf)`, descriptors)
      document.fonts.add(await face.load())
    } catch {
      // La vista previa usará otra fuente; el PDF lleva Aptos igualmente.
    }
  }
}

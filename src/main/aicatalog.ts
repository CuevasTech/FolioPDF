import { readFileSync } from 'node:fs'
import { app } from 'electron'
import { z } from 'zod'
import type { AiKind, AiTier } from '@shared/ai'

/**
 * Catálogo de modelos de IA de ESTA versión de Folio: la única fuente de lo que
 * se puede descargar (OWASP A03:2025, LLM03:2025). Cada archivo lleva su
 * dirección fijada a una revisión concreta de Hugging Face (no cambia aunque el
 * autor publique otra), su tamaño exacto y su huella SHA-256.
 *
 * Para actualizar un modelo se cambian aquí revisión, tamaño, huella y
 * `version`, y se publica una versión nueva de Folio; quien tenga la IA
 * activada verá en Ajustes que hay modelos mejorados.
 *
 * Modelos elegidos tras la prueba de concepto (PLAN-IA.md, octubre de 2026):
 * - IA básica: Qwen3.5 4B, el más preciso y el que mejor resiste las
 *   instrucciones escondidas en los documentos.
 * - IA avanzada: Granite 4.1 8B de IBM (GGUF oficial): respuestas más
 *   completas a cambio de más memoria y más lentitud sin gráfica.
 */

export interface AiFile {
  /** Nombre del archivo en la carpeta del componente (sin rutas). */
  name: string
  url: string
  size: number
  sha256: string
}

export interface AiComponent {
  id: string
  kind: AiKind
  /** Cambia cuando cambia cualquier archivo: así se detectan las actualizaciones. */
  version: string
  files: AiFile[]
  /** Contexto del modelo de lenguaje (tokens). */
  ctx?: number
  license: string
  licenseUrl: string
}

const hf = (repo: string, revision: string, file: string): string => `https://huggingface.co/${repo}/resolve/${revision}/${file}`

const QWEN_4B = 'unsloth/Qwen3.5-4B-GGUF'
const QWEN_4B_REV = 'e87f176479d0855a907a41277aca2f8ee7a09523'
const GRANITE_8B = 'ibm-granite/granite-4.1-8b-GGUF'
const GRANITE_8B_REV = '865b82c2e7970d82e3731278c88c57ae7138359c'
const EMBED = 'ibm-granite/granite-embedding-97m-multilingual-r2'
const EMBED_REV = '835ad14087e140460703cf0fae09f97d469d65c2'
const NER = 'Wismut/nym-pii-multilingual-small'
const NER_REV = '4348999cd3c2e20c49615e9af7c6bbb45b64cd85'
const UNWARP = 'monkt/paddleocr-onnx'
const UNWARP_REV = '7b02d0a30a07ba2b92ad1ff5a8941ae2c633de65'

export const AI_COMPONENTS: Record<string, AiComponent> = {
  'llm-basic': {
    id: 'llm-basic',
    kind: 'llm',
    version: 'qwen3.5-4b-q4km-1',
    ctx: 8192,
    files: [
      {
        name: 'model.gguf',
        url: hf(QWEN_4B, QWEN_4B_REV, 'Qwen3.5-4B-Q4_K_M.gguf'),
        size: 2_740_937_888,
        sha256: '00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4'
      }
    ],
    license: 'Apache-2.0 (Qwen3.5 4B)',
    licenseUrl: 'https://huggingface.co/Qwen/Qwen3.5-4B'
  },
  'llm-advanced': {
    id: 'llm-advanced',
    kind: 'llm',
    version: 'granite-4.1-8b-q4km-1',
    ctx: 8192,
    files: [
      {
        name: 'model.gguf',
        url: hf(GRANITE_8B, GRANITE_8B_REV, 'granite-4.1-8b-Q4_K_M.gguf'),
        size: 5_347_914_400,
        sha256: 'ed902ac9eb6adce5a90c6a08c8ea201b50e23fdc5976d1cd0362006afac5309e'
      }
    ],
    license: 'Apache-2.0 (IBM Granite 4.1 8B)',
    licenseUrl: 'https://huggingface.co/ibm-granite/granite-4.1-8b'
  },
  embed: {
    id: 'embed',
    kind: 'embed',
    version: 'granite-embedding-97m-r2-int8-1',
    files: [
      {
        name: 'model.onnx',
        url: hf(EMBED, EMBED_REV, 'onnx/model_quint8_avx2.onnx'),
        size: 98_247_878,
        sha256: 'a6022dd8220ea6f6595562a1328ee216f4a94faa55362f2f4747c80f1e78772e'
      },
      {
        name: 'tokenizer.json',
        url: hf(EMBED, EMBED_REV, 'tokenizer.json'),
        size: 25_301_672,
        sha256: '4f2842d568e2724370aec203652a42ac783c7937f8347a1a2cc7506d71f1582f'
      }
    ],
    license: 'Apache-2.0 (IBM Granite Embedding)',
    licenseUrl: 'https://huggingface.co/ibm-granite/granite-embedding-97m-multilingual-r2'
  },
  ner: {
    id: 'ner',
    kind: 'ner',
    version: 'nym-pii-multilingual-small-int8-1',
    files: [
      {
        name: 'model.onnx',
        url: hf(NER, NER_REV, 'int8/model_int8.onnx'),
        size: 138_730_982,
        sha256: '139006aea2cbd8e709d322f056232570de54661f624143be4893aaa387190286'
      },
      {
        name: 'tokenizer.json',
        url: hf(NER, NER_REV, 'tokenizer.json'),
        size: 12_389_891,
        sha256: 'c299144e68dfec1dc536204a7ae3712710c5c6cade9269a83f5042250d47d8de'
      },
      {
        name: 'config.json',
        url: hf(NER, NER_REV, 'config.json'),
        size: 5_688,
        sha256: '3f07065571e22bb73eba28ddb1fae4509c0cf703762e3bfa7a6ab2111cf7cb88'
      }
    ],
    license: 'MIT (nym-pii-multilingual-small)',
    licenseUrl: 'https://huggingface.co/Wismut/nym-pii-multilingual-small'
  },
  unwarp: {
    id: 'unwarp',
    kind: 'unwarp',
    version: 'uvdoc-onnx-1',
    files: [
      {
        name: 'model.onnx',
        url: hf(UNWARP, UNWARP_REV, 'preprocessing/doc-unwarping/UVDoc.onnx'),
        size: 31_684_112,
        sha256: '3f5ec09f1c6c5947ae7cc8db93ed7dd6dd2d164d3d99a6a70387c98848cfb00c'
      }
    ],
    license: 'Apache-2.0 (UVDoc, PaddleOCR)',
    licenseUrl: 'https://huggingface.co/monkt/paddleocr-onnx'
  }
}

/** Componentes de cada nivel. */
export const AI_TIERS: Record<Exclude<AiTier, 'off'>, string[]> = {
  basic: ['llm-basic', 'embed', 'ner', 'unwarp'],
  advanced: ['llm-advanced', 'embed', 'ner', 'unwarp']
}

// ---------------------------------------------------------------- pruebas

const TestCatalog = z.object({
  origin: z.string().regex(/^http:\/\/127\.0\.0\.1:\d{2,5}$/),
  components: z.record(
    z.string(),
    z.object({
      id: z.string(),
      kind: z.enum(['llm', 'embed', 'ner', 'unwarp']),
      version: z.string(),
      files: z.array(z.object({ name: z.string(), url: z.string(), size: z.number().int().positive(), sha256: z.string() })),
      ctx: z.number().int().optional(),
      license: z.string(),
      licenseUrl: z.string()
    })
  ),
  tiers: z.object({ basic: z.array(z.string()), advanced: z.array(z.string()) })
})

/**
 * Solo en pruebas (sin empaquetar): FOLIO_TEST_AI_CATALOG apunta a un catálogo
 * pequeño servido desde un servidor local, para probar la descarga sin bajar
 * modelos de verdad. En la aplicación instalada no existe.
 */
function testCatalog(): z.infer<typeof TestCatalog> | null {
  const path = !app.isPackaged ? process.env.FOLIO_TEST_AI_CATALOG : undefined
  if (!path) return null
  try {
    return TestCatalog.parse(JSON.parse(readFileSync(path, 'utf8')))
  } catch (error) {
    // Un catálogo de prueba que no vale deja la IA sin nada que descargar: una
    // prueba nunca debe acabar bajando los modelos de verdad.
    console.error('catálogo de IA de prueba no válido:', error instanceof Error ? error.message.slice(0, 300) : error)
    return { origin: 'http://127.0.0.1:1', components: {}, tiers: { basic: [], advanced: [] } }
  }
}

const test = testCatalog()
/** Catálogo en uso: el de esta versión o, en pruebas, el de prueba. */
export const CATALOG = {
  components: test ? (test.components as Record<string, AiComponent>) : AI_COMPONENTS,
  tiers: test ? test.tiers : AI_TIERS,
  testOrigin: test ? test.origin : null
}

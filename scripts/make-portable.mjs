// Portable version: node scripts/make-portable.mjs  (after npm run dist)
//
// Zips the app that electron-builder leaves in dist\win-unpacked, adds
// build\portable.txt (which makes Folio keep its data in FolioData, next to
// Folio.exe; see src/main/portable.ts) and writes dist\Folio-Portable.zip.
// The files go at the root of the zip, so unzipping a new version over the old
// folder updates it and keeps FolioData.
//
// Uses Windows' own tar (bsdtar, in System32) with fixed arguments and no shell.
import { spawnSync } from 'node:child_process'
import { existsSync, readdirSync, rmSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(fileURLToPath(new URL('.', import.meta.url)), '..')
const unpacked = join(root, 'dist', 'win-unpacked')
const marker = join(root, 'build', 'portable.txt')
const output = join(root, 'dist', 'Folio-Portable.zip')
const tar = join(process.env.SystemRoot ?? 'C:\\Windows', 'System32', 'tar.exe')

function fail(message) {
  console.error(`ERROR: ${message}`)
  process.exit(1)
}

if (!existsSync(join(unpacked, 'Folio.exe'))) fail('dist\\win-unpacked\\Folio.exe is missing: run npm run dist first.')
if (!existsSync(marker)) fail('build\\portable.txt is missing.')
if (!existsSync(tar)) fail('Windows tar was not found.')
// A portable.txt inside win-unpacked would turn the installed app into a portable one.
if (existsSync(join(unpacked, 'portable.txt'))) fail('dist\\win-unpacked must not contain portable.txt.')
for (const name of ['FolioData']) if (existsSync(join(unpacked, name))) fail(`dist\\win-unpacked contains ${name}: build again from a clean folder.`)

rmSync(output, { force: true })
const entries = readdirSync(unpacked)
const result = spawnSync(
  tar,
  ['--format', 'zip', '-c', '-f', output, '-C', unpacked, ...entries, '-C', join(root, 'build'), 'portable.txt'],
  { stdio: 'inherit', windowsHide: true }
)
if (result.error || result.status !== 0) fail('tar could not create the zip.')
console.log(`dist\\Folio-Portable.zip: ${Math.round(statSync(output).size / 1024 / 1024)} MB`)

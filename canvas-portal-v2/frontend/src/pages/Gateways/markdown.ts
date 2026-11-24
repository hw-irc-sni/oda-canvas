/**
 * A small Markdown parser for MCP tool and resource descriptions.
 *
 * Upstream MCP servers control this text, so it is parsed into a plain tree that
 * MarkdownText renders as React elements: no raw HTML is ever produced, and links
 * are kept only for http, https and mailto URLs.
 *
 * Supported: ATX headings, paragraphs, fenced code, bullet and numbered lists
 * (nested by indentation), blockquotes, horizontal rules, GFM tables, and inline
 * code, **strong**, *emphasis*, [links](https://...), <https://autolinks> and
 * backslash escapes. Anything else stays as text. Descriptions may be cut short
 * by the portal, so an unclosed code fence runs to the end instead of failing.
 */

export type Inline =
  | { kind: 'text'; text: string }
  | { kind: 'code'; text: string }
  | { kind: 'strong'; children: Inline[] }
  | { kind: 'em'; children: Inline[] }
  | { kind: 'link'; href: string; children: Inline[] }

export type Block =
  | { kind: 'heading'; level: number; content: Inline[] }
  | { kind: 'paragraph'; content: Inline[] }
  | { kind: 'code'; lang: string; text: string }
  | { kind: 'list'; ordered: boolean; start: number; items: Block[][] }
  | { kind: 'quote'; blocks: Block[] }
  | { kind: 'table'; header: Inline[][]; rows: Inline[][][] }
  | { kind: 'rule' }

const FENCE = /^ {0,3}(`{3,}|~{3,})\s*([\w+#.-]*)[^`]*$/
const HEADING = /^ {0,3}(#{1,6})(?:\s+(.*?))?\s*#*\s*$/
const RULE = /^ {0,3}([-*_])(?:\s*\1){2,}\s*$/
const QUOTE = /^ {0,3}>\s?/
const LIST_ITEM = /^( *)([-*+]|\d{1,9}[.)])(?:\s+(.*))?$/
const TABLE_SEPARATOR = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/
const SAFE_URL = /^(https?:\/\/|mailto:)/i
const MAX_DEPTH = 8

function indentOf(line: string): number {
  return line.length - line.trimStart().length
}

function isBlank(line: string): boolean {
  return line.trim() === ''
}

function startsBlock(line: string): boolean {
  return FENCE.test(line) || HEADING.test(line) || RULE.test(line) || QUOTE.test(line) || LIST_ITEM.test(line)
}

function splitRow(line: string): string[] {
  let row = line.trim()
  if (row.startsWith('|')) row = row.slice(1)
  if (row.endsWith('|') && !row.endsWith('\\|')) row = row.slice(0, -1)
  const cells: string[] = []
  let cell = ''
  for (let i = 0; i < row.length; i++) {
    if (row[i] === '\\' && row[i + 1] === '|') {
      cell += '|'
      i++
    } else if (row[i] === '|') {
      cells.push(cell.trim())
      cell = ''
    } else {
      cell += row[i]
    }
  }
  cells.push(cell.trim())
  return cells
}

function parseList(lines: string[], start: number, depth: number): { block: Block; next: number } {
  const first = LIST_ITEM.exec(lines[start])!
  const baseIndent = first[1].length
  const ordered = /\d/.test(first[2])
  const items: Block[][] = []
  let i = start
  while (i < lines.length) {
    const match = LIST_ITEM.exec(lines[i])
    if (!match || match[1].length !== baseIndent || /\d/.test(match[2]) !== ordered) break
    const contentIndent = baseIndent + match[2].length + 1
    const content = [match[3] ?? '']
    i++
    while (i < lines.length) {
      const line = lines[i]
      if (isBlank(line)) {
        // A blank line ends the item unless the next line is indented under it.
        const after = lines[i + 1]
        if (after !== undefined && !isBlank(after) && indentOf(after) > baseIndent) {
          content.push('')
          i++
          continue
        }
        break
      }
      if (indentOf(line) > baseIndent) {
        content.push(line.slice(Math.min(indentOf(line), contentIndent)))
      } else if (!startsBlock(line)) {
        content.push(line.trim()) // lazy continuation of the item's paragraph
      } else {
        break
      }
      i++
    }
    items.push(parseBlocks(content, depth + 1))
    while (i < lines.length && isBlank(lines[i]) && LIST_ITEM.test(lines[i + 1] ?? '')) i++
  }
  return { block: { kind: 'list', ordered, start: ordered ? parseInt(first[2], 10) : 1, items }, next: i }
}

function parseBlocks(lines: string[], depth: number): Block[] {
  if (depth > MAX_DEPTH) return [{ kind: 'paragraph', content: [{ kind: 'text', text: lines.join(' ').trim() }] }]
  const blocks: Block[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (isBlank(line)) {
      i++
      continue
    }
    const fence = FENCE.exec(line)
    if (fence) {
      const marker = fence[1]
      const indent = indentOf(line)
      const body: string[] = []
      i++
      while (i < lines.length) {
        const close = lines[i].trim()
        if (close.length >= marker.length && close === close[0].repeat(close.length) && close[0] === marker[0]) {
          i++
          break
        }
        body.push(lines[i].slice(Math.min(indent, indentOf(lines[i]))))
        i++
      }
      blocks.push({ kind: 'code', lang: fence[2], text: body.join('\n') })
      continue
    }
    const heading = HEADING.exec(line)
    if (heading) {
      blocks.push({ kind: 'heading', level: heading[1].length, content: parseInline(heading[2] ?? '') })
      i++
      continue
    }
    if (RULE.test(line)) {
      blocks.push({ kind: 'rule' })
      i++
      continue
    }
    if (QUOTE.test(line)) {
      const quoted: string[] = []
      while (i < lines.length && QUOTE.test(lines[i])) quoted.push(lines[i++].replace(QUOTE, ''))
      blocks.push({ kind: 'quote', blocks: parseBlocks(quoted, depth + 1) })
      continue
    }
    if (LIST_ITEM.test(line)) {
      const { block, next } = parseList(lines, i, depth)
      blocks.push(block)
      i = next
      continue
    }
    if (line.includes('|') && i + 1 < lines.length && TABLE_SEPARATOR.test(lines[i + 1]) && lines[i + 1].includes('-')) {
      const header = splitRow(line)
      const rows: Inline[][][] = []
      i += 2
      while (i < lines.length && !isBlank(lines[i]) && lines[i].includes('|')) {
        const cells = splitRow(lines[i++])
        rows.push(header.map((_, c) => parseInline(cells[c] ?? '')))
      }
      blocks.push({ kind: 'table', header: header.map((cell) => parseInline(cell)), rows })
      continue
    }
    const paragraph: string[] = []
    while (i < lines.length && !isBlank(lines[i]) && (paragraph.length === 0 || !startsBlock(lines[i]))) {
      paragraph.push(lines[i++].trim())
    }
    blocks.push({ kind: 'paragraph', content: parseInline(paragraph.join('\n')) })
  }
  return blocks
}

function isWordChar(ch: string | undefined): boolean {
  return ch !== undefined && /[\p{L}\p{N}]/u.test(ch)
}

/** The index of the closing delimiter for emphasis opened at `from`, or -1. */
function findClose(text: string, delim: string, from: number): number {
  let i = from
  while (i < text.length) {
    if (text[i] === '\\') {
      i += 2
      continue
    }
    if (text[i] === '`') {
      const run = /^`+/.exec(text.slice(i))![0]
      const end = text.indexOf(run, i + run.length)
      i = end < 0 ? i + run.length : end + run.length
      continue
    }
    if (text.startsWith(delim, i) && i > from && !/\s/.test(text[i - 1])) {
      const single = delim.length === 1
      const doubled = single && (text[i + 1] === delim || text[i - 1] === delim)
      const intraword = delim[0] === '_' && isWordChar(text[i + delim.length])
      if (!doubled && !intraword) return i
    }
    i++
  }
  return -1
}

export function parseInline(text: string, depth = 0): Inline[] {
  const out: Inline[] = []
  let buffer = ''
  const flush = () => {
    if (buffer) out.push({ kind: 'text', text: buffer.replace(/\s*\n\s*/g, ' ') })
    buffer = ''
  }
  if (depth > MAX_DEPTH) return [{ kind: 'text', text }]
  let i = 0
  while (i < text.length) {
    const ch = text[i]
    if (ch === '\\' && i + 1 < text.length && /[!-/:-@[-`{-~]/.test(text[i + 1])) {
      buffer += text[i + 1]
      i += 2
      continue
    }
    if (ch === '`') {
      const run = /^`+/.exec(text.slice(i))![0]
      const end = text.indexOf(run, i + run.length)
      if (end >= 0 && text[end + run.length] !== '`') {
        flush()
        let code = text.slice(i + run.length, end).replace(/\n/g, ' ')
        if (code.length > 2 && code.startsWith(' ') && code.endsWith(' ')) code = code.slice(1, -1)
        out.push({ kind: 'code', text: code })
        i = end + run.length
        continue
      }
      buffer += run
      i += run.length
      continue
    }
    if ((ch === '*' || ch === '_') && !/\s/.test(text[i + 1] ?? ' ')) {
      const strong = text[i + 1] === ch
      const delim = strong ? ch + ch : ch
      const opensIntraword = ch === '_' && isWordChar(text[i - 1])
      const close = opensIntraword ? -1 : findClose(text, delim, i + delim.length)
      if (close > i + delim.length) {
        flush()
        const children = parseInline(text.slice(i + delim.length, close), depth + 1)
        out.push(strong ? { kind: 'strong', children } : { kind: 'em', children })
        i = close + delim.length
        continue
      }
    }
    if (ch === '[') {
      const link = /^\[([^\]]*)\]\(\s*<?([^\s)>]+)>?(?:\s+"[^"]*")?\s*\)/.exec(text.slice(i))
      if (link) {
        flush()
        const children = parseInline(link[1], depth + 1)
        if (SAFE_URL.test(link[2])) out.push({ kind: 'link', href: link[2], children })
        else out.push(...children)
        i += link[0].length
        continue
      }
    }
    if (ch === '<') {
      const auto = /^<((?:https?:\/\/|mailto:)[^\s<>]+)>/i.exec(text.slice(i))
      if (auto) {
        flush()
        out.push({ kind: 'link', href: auto[1], children: [{ kind: 'text', text: auto[1] }] })
        i += auto[0].length
        continue
      }
    }
    buffer += ch
    i++
  }
  flush()
  return out
}

export function parseMarkdown(source: string): Block[] {
  return parseBlocks(source.replace(/\r\n?/g, '\n').replace(/\t/g, '    ').split('\n'), 0)
}

/** The first heading or paragraph, for a one-line summary such as a table cell. */
export function summaryOf(blocks: Block[]): Inline[] {
  for (const block of blocks) {
    if (block.kind === 'paragraph' || block.kind === 'heading') return block.content
    if (block.kind === 'list' && block.items[0]) return summaryOf(block.items[0])
    if (block.kind === 'quote') return summaryOf(block.blocks)
  }
  return []
}

interface Token {
  text: string
  cls?: 'k' | 's' | 'n'
}

const TOKEN_RE = /("(?:[^"\\]|\\.)*")(:)?|(-?\d+\.?\d*\b|true|false|null)/g

/** Tokenizes pretty-printed JSON for syntax-colored rendering, distinguishing object keys
 * (a string immediately followed by ':') from string values \u2014 same visual language as
 * IsoXmlView, built as React nodes rather than dangerouslySetInnerHTML. */
function tokenize(json: string): Token[] {
  const tokens: Token[] = []
  let last = 0
  let m: RegExpExecArray | null
  TOKEN_RE.lastIndex = 0
  while ((m = TOKEN_RE.exec(json))) {
    if (m.index > last) tokens.push({ text: json.slice(last, m.index) })
    if (m[1]) {
      tokens.push({ text: m[1], cls: m[2] ? 'k' : 's' })
      if (m[2]) tokens.push({ text: ':' })
    } else if (m[3]) {
      tokens.push({ text: m[3], cls: 'n' })
    }
    last = m.index + m[0].length
  }
  if (last < json.length) tokens.push({ text: json.slice(last) })
  return tokens
}

export default function JsonView({ data }: { data: unknown }) {
  const tokens = tokenize(JSON.stringify(data, null, 2))
  return (
    <pre className="code">
      {tokens.map((t, i) => (t.cls ? <span key={i} className={t.cls}>{t.text}</span> : t.text))}
    </pre>
  )
}

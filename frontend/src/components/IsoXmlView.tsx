interface Token {
  text: string
  cls?: 'tg' | 'k' | 's' | 'cm'
}

const COMMENT_RE = /<!--[\s\S]*?-->/g
const TAG_RE = /<(\/?)([\w:.]+)([^>]*)>/g
const ATTR_RE = /(\w+)=("[^"]*")/g

/** Tokenizes generated ISO 20022 XML for syntax-colored rendering \u2014 React port of the POC's
 * xmlHi(), avoiding dangerouslySetInnerHTML by building React nodes directly. */
function tokenize(xml: string): Token[] {
  const tokens: Token[] = []
  let i = 0
  while (i < xml.length) {
    COMMENT_RE.lastIndex = i
    TAG_RE.lastIndex = i
    const cm = COMMENT_RE.exec(xml)
    const tg = TAG_RE.exec(xml)
    const isComment = !!cm && (!tg || cm.index <= tg.index)
    const next = isComment ? cm : tg

    if (!next) {
      tokens.push({ text: xml.slice(i) })
      break
    }
    if (next.index > i) {
      tokens.push({ text: xml.slice(i, next.index) })
    }
    if (isComment) {
      tokens.push({ text: next[0], cls: 'cm' })
    } else {
      const [, slash, name, rest] = next
      tokens.push({ text: `<${slash}${name}`, cls: 'tg' })
      let last = 0
      let am: RegExpExecArray | null
      ATTR_RE.lastIndex = 0
      while ((am = ATTR_RE.exec(rest))) {
        if (am.index > last) tokens.push({ text: rest.slice(last, am.index) })
        tokens.push({ text: am[1], cls: 'k' })
        tokens.push({ text: '=' })
        tokens.push({ text: am[2], cls: 's' })
        last = am.index + am[0].length
      }
      if (last < rest.length) tokens.push({ text: rest.slice(last) })
      tokens.push({ text: '>', cls: 'tg' })
    }
    i = next.index + next[0].length
  }
  return tokens
}

export default function IsoXmlView({ xml }: { xml: string }) {
  const tokens = tokenize(xml)
  return (
    <pre className="code">
      {tokens.map((t, i) => (t.cls ? <span key={i} className={t.cls}>{t.text}</span> : t.text))}
    </pre>
  )
}

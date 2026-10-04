interface Props {
  title: string
}

export default function ComingSoon({ title }: Props) {
  return (
    <section className="blk" style={{ marginTop: 6 }}>
      <div className="card" style={{ textAlign: 'center', padding: '48px 24px' }}>
        <h2>{title}</h2>
        <p className="muted" style={{ marginTop: 8 }}>
          This screen hasn't been built yet — coming in a future iteration.
        </p>
      </div>
    </section>
  )
}

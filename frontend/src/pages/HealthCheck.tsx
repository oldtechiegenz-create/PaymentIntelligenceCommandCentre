import { useQuery } from '@tanstack/react-query'
import { getHealth } from '../lib/api'

export default function HealthCheck() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['health'],
    queryFn: getHealth,
  })

  return (
    <div>
      <h1>Payment Command Center</h1>
      <h2>Backend connectivity check</h2>
      {isLoading && <p>Checking backend…</p>}
      {isError && <p>Backend unreachable: {(error as Error).message}</p>}
      {data && <p>Backend says: {data.status}</p>}
    </div>
  )
}

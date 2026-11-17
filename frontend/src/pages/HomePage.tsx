import { useQuery } from '@tanstack/react-query'
import { API_URL, apiGet } from '../api/client'

export default function HomePage() {
  const health = useQuery({ queryKey: ['health'], queryFn: ({ signal }) => apiGet('/health', { signal }) })

  return (
    <section>
      <h1 className="text-2xl font-semibold">DocuAgent</h1>
      <p className="mt-1 text-slate-600">Front end setup. The real screens come next.</p>

      <div className="mt-6 rounded border border-slate-200 bg-white p-4" role="status" aria-live="polite">
        {health.isPending && <p className="text-slate-500">Checking the backend at {API_URL} ...</p>}
        {health.isError && (
          <p className="whitespace-pre-line text-red-700">
            Backend: not reachable.
            {'\n'}
            {health.error.message}
          </p>
        )}
        {/* health.data.status is typed as exactly "ok" (HealthResponse in the generated schema) */}
        {health.isSuccess && <p className="font-medium text-green-700">Backend: {health.data.status}</p>}
        <button
          type="button"
          onClick={() => void health.refetch()}
          disabled={health.isFetching}
          className="mt-3 rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-100 disabled:opacity-50"
        >
          Check again
        </button>
      </div>
    </section>
  )
}

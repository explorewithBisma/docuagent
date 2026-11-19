import { Link, useLocation, useParams } from 'react-router'
import { ApiError } from '../api/client'
import { useDocument } from '../api/documents'
import DocumentReview from '../components/document/DocumentReview'

/** The list's filter (?status=draft) that the list passed along in the link, so "Back" returns to the same filtered list. */
function listSearchFrom(state: unknown): string {
  const value = (state as { listSearch?: unknown } | null)?.listSearch
  return typeof value === 'string' && value.startsWith('?') ? value : ''
}

function NotFound({ what }: { what: string }) {
  return (
    <div role="alert" className="rounded border border-slate-300 bg-white p-6 text-center">
      <h1 className="text-xl font-semibold">Document not found</h1>
      <p className="mt-1 text-slate-600">{what}</p>
    </div>
  )
}

export default function DocumentDetailPage() {
  const { id: rawId } = useParams()
  const location = useLocation()
  const id = rawId !== undefined && /^\d+$/.test(rawId) ? Number(rawId) : undefined // "/documents/abc" is not a document: no request is made
  const document = useDocument(id)

  let body
  if (id === undefined) {
    body = <NotFound what={`"${rawId ?? ''}" is not a document number.`} />
  } else if (document.isPending) {
    body = (
      <p role="status" className="text-slate-500">
        Loading document #{id}…
      </p>
    )
  } else if (document.isError) {
    body =
      document.error instanceof ApiError && document.error.status === 404 ? (
        <NotFound what={`There is no document #${id}. It may have been deleted, or the number is wrong.`} />
      ) : (
        <div role="alert" className="rounded border border-red-300 bg-red-50 p-4 text-red-800">
          <p className="font-medium">Could not load document #{id}.</p>
          <p className="mt-1 text-sm whitespace-pre-line">{document.error.message}</p>
          <button type="button" onClick={() => void document.refetch()} disabled={document.isFetching} className="mt-3 rounded border border-red-300 bg-white px-3 py-1 text-sm hover:bg-red-100 disabled:opacity-50">
            {document.isFetching ? 'Retrying…' : 'Retry'}
          </button>
        </div>
      )
  } else {
    body = <DocumentReview key={document.data.id} doc={document.data} />
  }

  return (
    <section>
      <Link to={`/documents${listSearchFrom(location.state)}`} className="mb-4 inline-block text-sm text-blue-700 underline">
        ← Back to the documents
      </Link>
      {body}
    </section>
  )
}

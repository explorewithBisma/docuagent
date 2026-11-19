import { Link, useLocation, useSearchParams } from 'react-router'
import { DOCUMENT_STATUSES, parseStatus, useDocuments, type DocumentSummary } from '../api/documents'
import StatusBadge from '../components/StatusBadge'
import { formatDate, formatDateTime, formatMoney } from '../lib/format'
import { STATUS_LABELS } from '../lib/status'

const EMPTY = '—'

function IssueCount({ count }: { count: number }) {
  if (count === 0) return <span className="text-slate-400">0</span>
  return (
    <span className="inline-block rounded bg-red-100 px-2 py-0.5 text-xs font-semibold text-red-800 ring-1 ring-inset ring-red-300" title={`${count} issue(s) to check`}>
      {count}
    </span>
  )
}

function DocumentRow({ doc, listSearch }: { doc: DocumentSummary; listSearch: string }) {
  return (
    <tr className="border-t border-slate-100 hover:bg-slate-50">
      <td className="px-3 py-2 whitespace-nowrap">
        {/* the filter (?status=draft) travels with the link, so the detail page's "Back" returns to the same filtered list */}
        <Link to={`/documents/${doc.id}`} state={{ listSearch }} className="font-medium text-blue-700 underline" aria-label={`Open document ${doc.id}`}>
          #{doc.id}
        </Link>
      </td>
      <td className="px-3 py-2">
        <StatusBadge status={doc.status} />
      </td>
      <td className="px-3 py-2">{doc.vendor_name || EMPTY}</td>
      <td className="px-3 py-2 whitespace-nowrap">{doc.invoice_number || EMPTY}</td>
      <td className="px-3 py-2 whitespace-nowrap">{formatDate(doc.invoice_date)}</td>
      <td className="px-3 py-2">{doc.currency || EMPTY}</td>
      <td className="px-3 py-2 text-right whitespace-nowrap tabular-nums">{formatMoney(doc.total, doc.currency)}</td>
      <td className="px-3 py-2 text-center">
        <IssueCount count={doc.issue_count} />
      </td>
      <td className="max-w-[14rem] px-3 py-2">
        {/* one line; a long name ends in an ellipsis and the full name is in the hover title */}
        <span className="block truncate" title={doc.filename}>
          {doc.filename}
        </span>
      </td>
      <td className="px-3 py-2 whitespace-nowrap text-slate-600">{formatDateTime(doc.created_at)}</td>
    </tr>
  )
}

const COLUMNS = ['ID', 'Status', 'Vendor', 'Invoice no.', 'Date', 'Currency', 'Total', 'Issues', 'File', 'Created']

export default function DocumentsPage() {
  const location = useLocation()
  const [searchParams, setSearchParams] = useSearchParams()
  const status = parseStatus(searchParams.get('status')) // the URL is the single source of truth, so refresh and the back button keep the filter
  const documents = useDocuments(status)

  function chooseStatus(value: string) {
    setSearchParams(value === 'all' ? {} : { status: value }) // a new history entry, so "back" returns to the previous filter
  }

  const rows = documents.data

  return (
    <section>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h1 className="text-2xl font-semibold">Documents</h1>
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-sm text-slate-600">
            Status
            <select
              value={status ?? 'all'}
              onChange={(event) => chooseStatus(event.target.value)}
              className="mt-1 block rounded border border-slate-300 bg-white px-2 py-1 text-slate-900"
            >
              <option value="all">All</option>
              {DOCUMENT_STATUSES.map((value) => (
                <option key={value} value={value}>
                  {STATUS_LABELS[value]}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={() => void documents.refetch()}
            disabled={documents.isFetching}
            className="rounded border border-slate-300 bg-white px-3 py-1 text-sm hover:bg-slate-100 disabled:opacity-50"
          >
            {documents.isFetching && !documents.isPending ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </div>

      <div className="mt-6">
        {documents.isPending && (
          <p role="status" className="text-slate-500">
            Loading documents…
          </p>
        )}

        {documents.isError && (
          <div role="alert" className="mb-4 rounded border border-red-300 bg-red-50 p-4 text-red-800">
            <p className="font-medium">Could not load the documents.</p>
            <p className="mt-1 whitespace-pre-line text-sm">{documents.error.message}</p>
            <button
              type="button"
              onClick={() => void documents.refetch()}
              disabled={documents.isFetching}
              className="mt-3 rounded border border-red-300 bg-white px-3 py-1 text-sm hover:bg-red-100 disabled:opacity-50"
            >
              {documents.isFetching ? 'Retrying…' : 'Retry'}
            </button>
          </div>
        )}

        {rows && rows.length === 0 && (
          <div className="rounded border border-dashed border-slate-300 bg-white p-8 text-center text-slate-600">
            {status ? (
              <>
                <p>No {STATUS_LABELS[status].toLowerCase()} documents.</p>
                <button type="button" onClick={() => chooseStatus('all')} className="mt-3 text-sm text-blue-700 underline">
                  Show all documents
                </button>
              </>
            ) : (
              <>
                <p>No documents yet.</p>
                <Link to="/upload" className="mt-3 inline-block text-sm text-blue-700 underline">
                  Upload your first invoice or receipt
                </Link>
              </>
            )}
          </div>
        )}

        {rows && rows.length > 0 && (
          <>
            <p className="mb-2 text-sm text-slate-500" aria-live="polite">
              {rows.length} document{rows.length === 1 ? '' : 's'}
              {status ? ` (${STATUS_LABELS[status].toLowerCase()})` : ''}
            </p>
            {/* the table scrolls sideways on a narrow screen instead of squashing its columns */}
            <div className="overflow-x-auto rounded border border-slate-200 bg-white">
              <table className="min-w-full text-left text-sm">
                <thead className="bg-slate-100 text-xs tracking-wide text-slate-600 uppercase">
                  <tr>
                    {COLUMNS.map((name) => (
                      <th key={name} scope="col" className={`px-3 py-2 font-medium whitespace-nowrap ${name === 'Total' ? 'text-right' : ''} ${name === 'Issues' ? 'text-center' : ''}`}>
                        {name}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((doc) => (
                    <DocumentRow key={doc.id} doc={doc} listSearch={location.search} />
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </section>
  )
}

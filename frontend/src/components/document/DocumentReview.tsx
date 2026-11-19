import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { useBlocker } from 'react-router'
import { API_URL } from '../../api/client'
import { useConfirmDocument, useSaveDocument, type DocumentDetail } from '../../api/documents'
import { diffForm, toForm, type FormValues } from '../../lib/documentForm'
import { formatDateTime } from '../../lib/format'
import StatusBadge from '../StatusBadge'
import DocumentForm, { type Marks } from './DocumentForm'
import { DuplicatePanel, IssuesPanel } from './DocumentPanels'
import DocumentReadOnly from './DocumentReadOnly'

function Header({ doc }: { doc: DocumentDetail }) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <h1 className="flex flex-wrap items-center gap-2 text-2xl font-semibold">
          <span>Document #{doc.id}</span>
          <StatusBadge status={doc.status} />
        </h1>
        <p className="mt-1 break-words text-slate-700">{doc.filename}</p>
        <p className="text-sm text-slate-500">
          Uploaded {formatDateTime(doc.created_at)}
          {doc.confirmed_at ? ` · Confirmed ${formatDateTime(doc.confirmed_at)}` : ''}
          {doc.provider ? ` · read by ${doc.provider}` : ''}
        </p>
      </div>
      <a href={`${API_URL}/documents/${doc.id}/file`} target="_blank" rel="noreferrer" className="rounded border border-slate-300 bg-white px-3 py-1 text-sm text-blue-700 hover:bg-slate-100">
        Open original file ↗
      </a>
    </header>
  )
}

/** For each field the backend flagged: the worst severity (an error beats a warning). */
function marksFrom(issues: DocumentDetail['issues']): Marks {
  const marks: Marks = {}
  for (const { field, severity } of issues) if (marks[field] !== 'error') marks[field] = severity
  return marks
}

const noun = (count: number, word: string) => `${count} ${word}${count === 1 ? '' : 's'}`

export default function DocumentReview({ doc }: { doc: DocumentDetail }) {
  const locked = doc.status === 'confirmed'
  const saved = useMemo(() => toForm(doc.data), [doc.data])
  const [form, setForm] = useState<FormValues>(saved)
  const [asking, setAsking] = useState(false) // the in-page "Are you sure?" is open
  const [notice, setNotice] = useState<string | null>(null)
  const save = useSaveDocument(doc.id)
  const confirm = useConfirmDocument(doc.id)

  const { patch, errors: typedErrors, changed } = useMemo(() => diffForm(form, saved), [form, saved])
  const dirty = !locked && changed
  const hasTypedErrors = Object.keys(typedErrors).length > 0

  // --- leaving the page with unsaved edits: the browser's own prompt for a closed tab or reload, and an in-page one for in-app links ---
  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname)

  // --- Confirm is allowed only when nothing stands in the way ---
  const errorCount = doc.issues.filter((issue) => issue.severity === 'error').length
  const blockers: string[] = []
  if (doc.duplicate?.kind === 'exact') blockers.push(`This is an exact duplicate of document #${doc.duplicate.document_id}, so it cannot be confirmed.`)
  if (errorCount > 0) blockers.push(`Fix the ${noun(errorCount, 'error')} listed above, then save.`)
  if (dirty) blockers.push('Save your changes first.')
  const canConfirm = blockers.length === 0 && !save.isPending && !confirm.isPending
  const showSure = asking && canConfirm

  function onSave(event: FormEvent) {
    event.preventDefault()
    if (!changed || hasTypedErrors) return
    setNotice(null)
    save.mutate(patch, {
      onSuccess: (updated) => {
        setForm(toForm(updated.data)) // show what the server kept (e.g. "usd" -> "USD")
        setNotice('Changes saved. The checks above are the new ones.')
      },
    })
  }

  function onConfirm() {
    setNotice(null)
    confirm.mutate(undefined, {
      onSuccess: () => {
        setAsking(false)
        setNotice('Document confirmed. It can no longer be edited.')
      },
      onError: () => setAsking(false),
    })
  }

  return (
    <div className="space-y-4">
      <Header doc={doc} />

      {blocker.state === 'blocked' && (
        <div role="alertdialog" aria-label="Unsaved changes" className="rounded border border-amber-400 bg-amber-50 p-4 text-sm text-amber-900">
          <p className="font-medium">You have unsaved changes.</p>
          <p className="mt-1">If you leave this page they will be lost.</p>
          <div className="mt-3 flex gap-3">
            <button type="button" onClick={() => blocker.reset()} className="rounded border border-slate-300 bg-white px-3 py-1 hover:bg-slate-100">
              Stay on this page
            </button>
            <button type="button" onClick={() => blocker.proceed()} className="rounded bg-amber-700 px-3 py-1 text-white hover:bg-amber-800">
              Leave without saving
            </button>
          </div>
        </div>
      )}

      {notice && (
        <p role="status" className="rounded border border-green-300 bg-green-50 p-3 text-sm text-green-800">
          {notice}
        </p>
      )}
      {save.isError && (
        <p role="alert" className="rounded border border-red-300 bg-red-50 p-3 text-sm whitespace-pre-line text-red-800">
          Could not save the changes. {save.error.message}
        </p>
      )}
      {confirm.isError && (
        <p role="alert" className="rounded border border-red-300 bg-red-50 p-3 text-sm whitespace-pre-line text-red-800">
          Could not confirm the document. {confirm.error.message}
        </p>
      )}

      <DuplicatePanel duplicate={doc.duplicate} />
      <IssuesPanel issues={doc.issues} stale={dirty} />

      <section aria-label="Document details" className="rounded border border-slate-200 bg-white p-4">
        {locked ? (
          <DocumentReadOnly data={doc.data} />
        ) : (
          <form onSubmit={onSave} noValidate>
            <DocumentForm form={form} onChange={setForm} errors={typedErrors} marks={marksFrom(doc.issues)} disabled={save.isPending} />
            <div className="mt-5 flex flex-wrap items-center gap-3">
              <button type="submit" disabled={!changed || hasTypedErrors || save.isPending} className="rounded bg-slate-900 px-4 py-1.5 text-sm text-white hover:bg-slate-700 disabled:opacity-40">
                {save.isPending ? 'Saving…' : 'Save changes'}
              </button>
              {dirty && <span className="text-sm text-amber-700">You have unsaved changes.</span>}
            </div>
          </form>
        )}
      </section>

      {!locked && (
        <section aria-label="Confirm" className="rounded border border-slate-200 bg-white p-4">
          <button type="button" onClick={() => setAsking(true)} disabled={!canConfirm} aria-describedby="confirm-blockers" className="rounded bg-green-700 px-4 py-1.5 text-sm text-white hover:bg-green-800 disabled:opacity-40">
            Confirm
          </button>
          {blockers.length > 0 && (
            <ul id="confirm-blockers" className="mt-2 list-disc pl-5 text-sm text-slate-600">
              {blockers.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          )}
          {showSure && (
            <div role="alertdialog" aria-label="Are you sure?" className="mt-3 rounded border border-green-300 bg-green-50 p-3 text-sm text-green-900">
              <p className="font-medium">Are you sure?</p>
              <p className="mt-1">Confirming saves document #{doc.id}. A confirmed document can no longer be edited.</p>
              <div className="mt-3 flex gap-3">
                <button type="button" onClick={onConfirm} disabled={confirm.isPending} className="rounded bg-green-700 px-3 py-1 text-white hover:bg-green-800 disabled:opacity-50">
                  {confirm.isPending ? 'Confirming…' : 'Yes, confirm'}
                </button>
                <button type="button" onClick={() => setAsking(false)} disabled={confirm.isPending} className="rounded border border-slate-300 bg-white px-3 py-1 hover:bg-slate-100">
                  Cancel
                </button>
              </div>
            </div>
          )}
        </section>
      )}
    </div>
  )
}

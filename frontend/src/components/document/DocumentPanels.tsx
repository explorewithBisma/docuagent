import { Link } from 'react-router'
import type { DuplicateMatch, Issue } from '../../api/documents'
import { fieldLabel } from '../../lib/documentForm'

function IssueGroup({ title, issues, tone }: { title: string; issues: Issue[]; tone: 'error' | 'warning' }) {
  if (issues.length === 0) return null
  const styles = tone === 'error' ? 'border-red-300 bg-red-50 text-red-900' : 'border-amber-300 bg-amber-50 text-amber-900'
  return (
    <section aria-label={title} className={`rounded border p-3 ${styles}`}>
      <h3 className="text-sm font-semibold">
        {title} ({issues.length})
      </h3>
      <ul className="mt-1 space-y-1 text-sm">
        {issues.map((issue, index) => (
          <li key={`${issue.code}-${issue.field}-${index}`}>
            <span className="font-medium">{fieldLabel(issue.field)}:</span> {issue.message}
          </li>
        ))}
      </ul>
    </section>
  )
}

/** The backend's checks, errors and warnings apart. `stale` = the form has unsaved edits, so these describe the last SAVED version. */
export function IssuesPanel({ issues, stale }: { issues: Issue[]; stale: boolean }) {
  const errors = issues.filter((issue) => issue.severity === 'error')
  const warnings = issues.filter((issue) => issue.severity === 'warning')
  return (
    <section aria-label="Checks" className="space-y-2">
      {issues.length === 0 && <p className="rounded border border-green-300 bg-green-50 p-3 text-sm text-green-800">No problems found.</p>}
      <IssueGroup title="Errors" issues={errors} tone="error" />
      <IssueGroup title="Warnings" issues={warnings} tone="warning" />
      {stale && <p className="text-xs text-slate-500">These checks are for the last saved version. Save your changes to check them again.</p>}
    </section>
  )
}

export function DuplicatePanel({ duplicate }: { duplicate: DuplicateMatch | null | undefined }) {
  if (!duplicate) return null
  const exact = duplicate.kind === 'exact'
  return (
    <section
      aria-label="Duplicate check"
      className={`rounded border p-3 text-sm ${exact ? 'border-red-300 bg-red-50 text-red-900' : 'border-amber-300 bg-amber-50 text-amber-900'}`}
    >
      <h3 className="font-semibold">{exact ? 'Exact duplicate' : 'Possible duplicate'}</h3>
      <p className="mt-1">{duplicate.reason}</p>
      <p className="mt-1">
        <Link to={`/documents/${duplicate.document_id}`} className="font-medium underline">
          Open document #{duplicate.document_id}
        </Link>
        {exact ? ' · This document cannot be confirmed.' : ' · A warning only: you can still confirm it.'}
      </p>
    </section>
  )
}

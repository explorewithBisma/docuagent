import type { DocumentStatus } from '../api/documents'
import { STATUS_LABELS } from '../lib/status'

const BADGE_STYLES: Record<DocumentStatus, string> = {
  draft: 'bg-amber-100 text-amber-800 ring-amber-300',
  confirmed: 'bg-green-100 text-green-800 ring-green-300',
}

export default function StatusBadge({ status }: { status: DocumentStatus }) {
  return <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${BADGE_STYLES[status]}`}>{STATUS_LABELS[status]}</span>
}

/** Reading the document list: GET /documents?status=..., the same endpoint the Streamlit "Saved documents" tab uses. */

import { useQuery } from '@tanstack/react-query'
import { apiGet } from './client'
import type { components } from './schema'

export type DocumentSummary = components['schemas']['DocumentSummary']
export type DocumentStatus = components['schemas']['DocumentStatus']

export const DOCUMENT_STATUSES: readonly DocumentStatus[] = ['draft', 'confirmed']

/** A value from the URL (?status=...) as a status, or undefined for "all" and for anything unknown. */
export function parseStatus(value: string | null): DocumentStatus | undefined {
  return DOCUMENT_STATUSES.find((status) => status === value)
}

export function useDocuments(status: DocumentStatus | undefined) {
  return useQuery({
    queryKey: ['documents', status ?? 'all'],
    queryFn: ({ signal }) => apiGet('/documents', { query: status ? { status } : {}, signal }),
  })
}

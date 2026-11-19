/** Reading the document list: GET /documents?status=..., the same endpoint the Streamlit "Saved documents" tab uses. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { DocumentPatch } from '../lib/documentForm'
import { apiGet, apiPatch, apiPost } from './client'
import type { components } from './schema'

export type DocumentSummary = components['schemas']['DocumentSummary']
export type DocumentStatus = components['schemas']['DocumentStatus']
export type DocumentDetail = components['schemas']['DocumentOut']
export type Issue = components['schemas']['Issue']
export type DuplicateMatch = components['schemas']['DuplicateMatch']

export const DOCUMENT_STATUSES: readonly DocumentStatus[] = ['draft', 'confirmed']

/** A value from the URL (?status=...) as a status, or undefined for "all" and for anything unknown. */
export function parseStatus(value: string | null): DocumentStatus | undefined {
  return DOCUMENT_STATUSES.find((status) => status === value)
}

/** One document: GET /documents/{id}. Pass undefined (e.g. a bad id in the URL) to not ask at all. */
export const documentKey = (id: number) => ['document', id] as const

export function useDocument(id: number | undefined) {
  return useQuery({
    queryKey: documentKey(id ?? -1),
    queryFn: ({ signal }) => apiGet('/documents/{document_id}', { params: { document_id: id as number }, signal }),
    enabled: id !== undefined,
  })
}

/** After a save or a confirm: keep the fresh document and mark every Documents-list query out of date, so the list is fresh next time it is shown. */
function useRefreshAfterChange(id: number) {
  const queryClient = useQueryClient()
  return {
    onChanged: (doc: DocumentDetail) => {
      queryClient.setQueryData(documentKey(id), doc)
      void queryClient.invalidateQueries({ queryKey: ['documents'] })
    },
    // a refused request can still have changed the document on the server (it re-checks everything), so read it again
    onRefused: () => void queryClient.invalidateQueries({ queryKey: documentKey(id) }),
  }
}

/** PATCH /documents/{id}: send the changed fields; the answer is the document with its re-validated issues. */
export function useSaveDocument(id: number) {
  const { onChanged, onRefused } = useRefreshAfterChange(id)
  return useMutation({
    mutationFn: (patch: DocumentPatch) => apiPatch('/documents/{document_id}', { params: { document_id: id }, body: patch }),
    onSuccess: onChanged,
    onError: onRefused,
  })
}

/** POST /documents/{id}/confirm. Never overrides errors: the page only offers Confirm when there are none. */
export function useConfirmDocument(id: number) {
  const { onChanged, onRefused } = useRefreshAfterChange(id)
  return useMutation({
    mutationFn: () => apiPost('/documents/{document_id}/confirm', { params: { document_id: id }, body: { override_issues: false } }),
    onSuccess: onChanged,
    onError: onRefused,
  })
}

export function useDocuments(status: DocumentStatus | undefined) {
  return useQuery({
    queryKey: ['documents', status ?? 'all'],
    queryFn: ({ signal }) => apiGet('/documents', { query: status ? { status } : {}, signal }),
  })
}

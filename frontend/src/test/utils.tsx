import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { vi } from 'vitest'
import type { DocumentSummary } from '../api/documents'
import DocumentsPage from '../pages/DocumentsPage'

/** Answers every fetch() with handler(url). Returns the mock so a test can see which requests were made. */
export function mockFetch(handler: (url: URL, init?: RequestInit) => Response | Promise<Response>) {
  const mock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => Promise.resolve(handler(new URL(String(input)), init)))
  vi.stubGlobal('fetch', mock)
  return mock
}

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

export function summary(overrides: Partial<DocumentSummary> & Pick<DocumentSummary, 'id'>): DocumentSummary {
  return { status: 'draft', filename: `file-${overrides.id}.pdf`, issue_count: 0, created_at: '2026-11-17T18:32:43.779644', ...overrides }
}

/** The Documents page inside a memory router (so tests can read and move the URL) and a fresh query client with no retries. */
export function renderDocuments(initialUrl = '/documents') {
  const router = createMemoryRouter(
    [
      { path: '/documents', element: <DocumentsPage /> },
      { path: '/documents/:id', element: <p>detail page</p> },
      { path: '/upload', element: <p>upload page</p> },
    ],
    { initialEntries: [initialUrl] },
  )
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { router }
}

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { vi } from 'vitest'
import type { DocumentDetail, DocumentSummary } from '../api/documents'
import DocumentsPage from '../pages/DocumentsPage'
import { routes } from '../routes'

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

/** The whole app (the real route table, with its nav bar) at a URL, in a memory router. Returns the router and the query client, to inspect both. */
export function renderApp(initialUrl: string) {
  const router = createMemoryRouter(routes, { initialEntries: [initialUrl] })
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { router, queryClient }
}

/** A draft document as GET /documents/{id} returns it. */
export function makeDoc(overrides: Partial<DocumentDetail> = {}): DocumentDetail {
  return {
    id: 5,
    status: 'draft',
    filename: '3_math_error.pdf',
    content_type: 'application/pdf',
    file_hash: 'abc',
    provider: 'groq',
    data: {
      doc_type: 'invoice',
      vendor_name: 'Blue Harbor Logistics',
      vendor_tax_id: 'TAX-1',
      invoice_number: 'INV-77',
      invoice_date: '2024-03-10',
      due_date: '2024-04-09',
      currency: 'EUR',
      subtotal: 500,
      tax: 50,
      total: 560,
      line_items: [
        { description: 'Freight', quantity: 2, unit_price: 100, amount: 200 },
        { description: 'Storage', quantity: 3, unit_price: 100, amount: 300 },
      ],
      overall_confidence: 0.9,
      low_confidence_fields: [],
    },
    issues: [],
    duplicate: null,
    created_at: '2026-11-17T09:00:00Z', // mid-morning UTC: the same calendar day in nearly every time zone
    confirmed_at: null,
    ...overrides,
  }
}

export interface Call {
  method: string
  path: string
  body: unknown
}

/** A fake backend for one document: serves it, applies a PATCH, confirms it, and records every call. `onPatch` decides what the "re-validated" answer is. */
export class FakeBackend {
  doc: DocumentDetail
  calls: Call[] = []
  onPatch: (body: Record<string, unknown>, doc: DocumentDetail) => DocumentDetail = (body, doc) => ({ ...doc, data: { ...doc.data, ...body } })

  constructor(doc: DocumentDetail = makeDoc()) {
    this.doc = doc
    mockFetch((url, init) => this.handle(url, init))
  }

  private handle(url: URL, init?: RequestInit): Response {
    const method = init?.method ?? 'GET'
    const body: unknown = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined
    this.calls.push({ method, path: url.pathname, body })
    const own = `/documents/${this.doc.id}`
    if (method === 'GET' && url.pathname === own) return json(this.doc)
    if (method === 'PATCH' && url.pathname === own) {
      this.doc = this.onPatch(body as Record<string, unknown>, this.doc)
      return json(this.doc)
    }
    if (method === 'POST' && url.pathname === `${own}/confirm`) {
      this.doc = { ...this.doc, status: 'confirmed', confirmed_at: '2026-11-18T09:00:00Z' }
      return json(this.doc)
    }
    if (method === 'GET' && url.pathname === '/documents') return json([])
    return json({ detail: `Document ${url.pathname.split('/')[2] ?? '?'} was not found.` }, 404)
  }

  /** The calls of one kind, e.g. only('PATCH'). */
  only(method: string): Call[] {
    return this.calls.filter((call) => call.method === method)
  }
}

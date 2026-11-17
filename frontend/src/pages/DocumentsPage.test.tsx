import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { DocumentSummary } from '../api/documents'
import { json, mockFetch, renderDocuments, summary } from '../test/utils'

const DOCS: DocumentSummary[] = [
  summary({ id: 5, status: 'draft', vendor_name: 'Blue Harbor Logistics', invoice_number: 'INV-77', invoice_date: '2024-03-10', currency: 'EUR', total: 560, issue_count: 1, filename: '3_math_error.pdf' }),
  summary({ id: 4, status: 'draft', vendor_name: 'Corner Cafe', invoice_number: 'R-5521', invoice_date: '2024-03-05', currency: 'USD', total: 10.53, issue_count: 0, filename: '2_receipt.pdf' }),
  summary({ id: 3, status: 'confirmed', vendor_name: 'Sunrise Printing Co', invoice_number: 'INV-2231', invoice_date: '2024-03-20', currency: 'AED', total: 1234.5, issue_count: 0, filename: '4_scan.pdf' }),
]

/** A fake backend: GET /documents[?status=...] answers from DOCS, like the real endpoint. */
function backend() {
  return mockFetch((url) => {
    const status = url.searchParams.get('status')
    return json(status ? DOCS.filter((doc) => doc.status === status) : DOCS)
  })
}

const requested = (fetchMock: ReturnType<typeof mockFetch>) => fetchMock.mock.calls.map(([input]) => new URL(String(input)))
const rowOf = (text: string) => screen.getByText(text).closest('tr') as HTMLElement

describe('Documents page', () => {
  it('shows a loading state, then one row per document with readable values', async () => {
    const fetchMock = backend()
    renderDocuments()

    expect(screen.getByRole('status')).toHaveTextContent('Loading documents')
    expect(await screen.findByText('Blue Harbor Logistics')).toBeInTheDocument()
    expect(screen.queryByText(/Loading documents/)).not.toBeInTheDocument()

    expect(screen.getAllByRole('row')).toHaveLength(1 + DOCS.length) // header + 3
    const first = within(rowOf('Blue Harbor Logistics'))
    expect(first.getByText('INV-77')).toBeInTheDocument()
    expect(first.getByText('10 Mar 2024')).toBeInTheDocument() // not 2024-03-10
    expect(first.getByText('€560.00')).toBeInTheDocument() // money with its currency
    expect(first.getByText('3_math_error.pdf')).toBeInTheDocument()
    expect(first.getByText('Draft')).toBeInTheDocument()
    expect(first.getByText(/17 Nov 2026/)).toBeInTheDocument() // created at
    expect(within(rowOf('Sunrise Printing Co')).getByText('Confirmed')).toBeInTheDocument()
    expect(within(rowOf('Sunrise Printing Co')).getByText('AED 1,234.50')).toBeInTheDocument()

    expect(screen.getByText('3 documents')).toBeInTheDocument()
    expect(requested(fetchMock)).toHaveLength(1)
    expect(requested(fetchMock)[0]?.pathname).toBe('/documents')
    expect(requested(fetchMock)[0]?.search).toBe('') // "all" sends no filter
  })

  it('highlights an issue count above 0 and not a zero', async () => {
    backend()
    renderDocuments()
    await screen.findByText('Blue Harbor Logistics')
    const withIssue = within(rowOf('Blue Harbor Logistics')).getByTitle('1 issue(s) to check')
    expect(withIssue).toHaveTextContent('1')
    expect(withIssue).toHaveClass('bg-red-100')
    expect(within(rowOf('Corner Cafe')).queryByTitle(/issue/)).not.toBeInTheDocument()
  })

  it('links every row to its document page', async () => {
    backend()
    const { router } = renderDocuments()
    await screen.findByText('Corner Cafe')
    expect(within(rowOf('Corner Cafe')).getByRole('link', { name: 'Open document 4' })).toHaveAttribute('href', '/documents/4')
    await userEvent.click(within(rowOf('Corner Cafe')).getByRole('link'))
    expect(router.state.location.pathname).toBe('/documents/4')
    expect(await screen.findByText('detail page')).toBeInTheDocument()
  })

  it('keeps a filename on one line, truncated with an ellipsis, with the full name on hover', async () => {
    const longName = 'scanned_invoice_from_the_northwind_office_supplies_march_2024_final_version_2.pdf'
    mockFetch(() => json([summary({ id: 1, vendor_name: 'Northwind', filename: longName }), summary({ id: 2, vendor_name: 'Corner Cafe', filename: '2_receipt.pdf' })]))
    renderDocuments()
    await screen.findByText('Northwind')

    const long = within(rowOf('Northwind')).getByTitle(longName)
    expect(long).toHaveTextContent(longName) // the full text is still in the page (for copy, search and screen readers)
    expect(long).toHaveClass('truncate') // = overflow hidden + text-overflow ellipsis + no wrapping
    expect(long.closest('td')).toHaveClass('max-w-[14rem]') // the cap that makes the ellipsis appear
    expect(within(rowOf('Corner Cafe')).getByTitle('2_receipt.pdf')).toHaveClass('truncate') // short names look the same
    expect(long.closest('td')?.className).not.toMatch(/break-/) // never broken mid-word
  })

  it('lets the table scroll sideways instead of squashing it on a narrow screen', async () => {
    backend()
    renderDocuments()
    await screen.findByText('Corner Cafe')
    expect(screen.getByRole('table').parentElement).toHaveClass('overflow-x-auto')
  })

  describe('status filter', () => {
    it('changes the request and the URL', async () => {
      const fetchMock = backend()
      const { router } = renderDocuments()
      await screen.findByText('Sunrise Printing Co')

      await userEvent.selectOptions(screen.getByLabelText('Status'), 'draft')

      await waitFor(() => expect(screen.queryByText('Sunrise Printing Co')).not.toBeInTheDocument()) // the confirmed one is gone
      expect(screen.getByText('Corner Cafe')).toBeInTheDocument()
      expect(requested(fetchMock).at(-1)?.search).toBe('?status=draft')
      expect(router.state.location.search).toBe('?status=draft')
      expect(screen.getByText('2 documents (draft)')).toBeInTheDocument()

      await userEvent.selectOptions(screen.getByLabelText('Status'), 'all')
      expect(await screen.findByText('Sunrise Printing Co')).toBeInTheDocument()
      expect(router.state.location.search).toBe('') // "all" clears the parameter
    })

    it('is read from the URL, so a refresh keeps it', async () => {
      const fetchMock = backend()
      renderDocuments('/documents?status=confirmed')
      expect(await screen.findByText('Sunrise Printing Co')).toBeInTheDocument()
      expect(screen.queryByText('Corner Cafe')).not.toBeInTheDocument()
      expect(screen.getByLabelText('Status')).toHaveValue('confirmed')
      expect(requested(fetchMock)[0]?.search).toBe('?status=confirmed')
    })

    it('is restored by the back button', async () => {
      const fetchMock = backend()
      const { router } = renderDocuments()
      await screen.findByText('Sunrise Printing Co')
      await userEvent.selectOptions(screen.getByLabelText('Status'), 'draft')
      await waitFor(() => expect(screen.queryByText('Sunrise Printing Co')).not.toBeInTheDocument())

      await act(() => router.navigate(-1))

      expect(router.state.location.search).toBe('')
      expect(await screen.findByText('Sunrise Printing Co')).toBeInTheDocument()
      expect(screen.getByLabelText('Status')).toHaveValue('all')
      expect(requested(fetchMock).every((url) => url.searchParams.get('status') !== 'bogus')).toBe(true)
    })

    it('ignores an unknown status in the URL and shows everything', async () => {
      const fetchMock = backend()
      renderDocuments('/documents?status=bogus')
      expect(await screen.findByText('Sunrise Printing Co')).toBeInTheDocument()
      expect(screen.getByLabelText('Status')).toHaveValue('all')
      expect(requested(fetchMock)[0]?.search).toBe('') // never sent to the backend, which would answer 422
    })
  })

  describe('empty state', () => {
    it('invites you to upload when there are no documents at all', async () => {
      mockFetch(() => json([]))
      renderDocuments()
      expect(await screen.findByText('No documents yet.')).toBeInTheDocument()
      expect(screen.getByRole('link', { name: /Upload your first/ })).toHaveAttribute('href', '/upload')
      expect(screen.queryByRole('table')).not.toBeInTheDocument()
    })

    it('says which filter found nothing and offers to show all', async () => {
      mockFetch((url) => json(url.searchParams.get('status') ? [] : DOCS))
      const { router } = renderDocuments('/documents?status=confirmed')
      expect(await screen.findByText('No confirmed documents.')).toBeInTheDocument()
      await userEvent.click(screen.getByRole('button', { name: 'Show all documents' }))
      expect(await screen.findByText('Blue Harbor Logistics')).toBeInTheDocument()
      expect(router.state.location.search).toBe('')
    })
  })

  describe('error state', () => {
    it('shows the backend message with a Retry button that loads the data', async () => {
      let healthy = false
      const fetchMock = mockFetch(() => (healthy ? json(DOCS) : json({ detail: 'The database is down.' }, 500)))
      renderDocuments()

      const alert = await screen.findByRole('alert')
      expect(alert).toHaveTextContent('Could not load the documents.')
      expect(alert).toHaveTextContent('The database is down.')
      expect(screen.queryByRole('table')).not.toBeInTheDocument()

      healthy = true
      await userEvent.click(screen.getByRole('button', { name: 'Retry' }))

      expect(await screen.findByText('Blue Harbor Logistics')).toBeInTheDocument()
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
      expect(fetchMock).toHaveBeenCalledTimes(2)
    })

    it('explains when the backend cannot be reached at all', async () => {
      mockFetch(() => {
        throw new TypeError('Failed to fetch')
      })
      renderDocuments()
      const alert = await screen.findByRole('alert')
      expect(alert).toHaveTextContent('Could not reach the backend')
      expect(within(alert).getByRole('button', { name: 'Retry' })).toBeInTheDocument()
    })
  })

  it('Refresh asks the backend again and shows the new data', async () => {
    let docs = DOCS.slice(0, 1)
    const fetchMock = mockFetch(() => json(docs))
    renderDocuments()
    await screen.findByText('Blue Harbor Logistics')
    expect(screen.queryByText('Corner Cafe')).not.toBeInTheDocument()

    docs = DOCS
    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }))

    expect(await screen.findByText('Corner Cafe')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})

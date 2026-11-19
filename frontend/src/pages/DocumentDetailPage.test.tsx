import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { Issue } from '../api/documents'
import { FakeBackend, json, makeDoc, mockFetch, renderApp, summary } from '../test/utils'

const TOTAL_ERROR: Issue = {
  code: 'total_mismatch',
  field: 'total',
  severity: 'error',
  message: 'Subtotal 500.00 + tax 50.00 = 550.00, but the total says 560.00 (difference 10.00).',
}
const VENDOR_WARNING: Issue = { code: 'low_confidence', field: 'vendor_name', severity: 'warning', message: 'The vendor name was hard to read. Please check it.' }

async function open(backend: FakeBackend, url = '/documents/5') {
  const app = renderApp(url)
  await screen.findByRole('heading', { name: /Document #5/ })
  return { backend, ...app, user: userEvent.setup() }
}
const field = (label: RegExp) => screen.getByLabelText(label)
const confirmButton = () => screen.getByRole('button', { name: 'Confirm' })
const saveButton = () => screen.getByRole('button', { name: /Save changes|Saving/ })

async function retype(user: ReturnType<typeof userEvent.setup>, input: HTMLElement, value: string) {
  await user.clear(input)
  if (value) await user.type(input, value)
}

describe('Document detail page', () => {
  describe('a draft', () => {
    it('shows the header, the form and the original-file link', async () => {
      const { backend } = await open(new FakeBackend())

      const heading = screen.getByRole('heading', { name: /Document #5/ })
      expect(within(heading).getByText('Draft')).toBeInTheDocument()
      expect(screen.getByText('3_math_error.pdf')).toBeInTheDocument()
      expect(screen.getByText(/Uploaded 17 Nov 2026/)).toBeInTheDocument()
      expect(screen.getByText(/read by groq/)).toBeInTheDocument()
      const original = screen.getByRole('link', { name: /Open original file/ })
      expect(original).toHaveAttribute('href', 'http://localhost:8000/documents/5/file')
      expect(original).toHaveAttribute('target', '_blank')

      expect(field(/^Vendor name/)).toHaveValue('Blue Harbor Logistics')
      expect(field(/^Invoice number/)).toHaveValue('INV-77')
      expect(field(/^Invoice date/)).toHaveValue('2024-03-10')
      expect(field(/^Currency/)).toHaveValue('EUR')
      expect(field(/^Total/)).toHaveValue('560')
      expect(field(/^Document type/)).toHaveValue('invoice')
      expect(field(/Line 2 description/)).toHaveValue('Storage')
      expect(field(/Line 2 amount/)).toHaveValue('300')
      expect(screen.getByText('No problems found.')).toBeInTheDocument()

      expect(saveButton()).toBeDisabled() // nothing to save yet
      expect(confirmButton()).toBeEnabled() // clean, no errors, no duplicate
      expect(backend.calls).toEqual([{ method: 'GET', path: '/documents/5', body: undefined }]) // opening the page only reads
    })

    it('shows errors and warnings apart, each with its field, and marks the fields', async () => {
      await open(new FakeBackend(makeDoc({ issues: [TOTAL_ERROR, VENDOR_WARNING] })))

      const errors = screen.getByRole('region', { name: 'Errors' })
      expect(errors).toHaveTextContent('Errors (1)')
      expect(errors).toHaveTextContent('Total: Subtotal 500.00 + tax 50.00 = 550.00')
      expect(errors).not.toHaveTextContent('Vendor name')
      expect(errors).toHaveClass('bg-red-50')

      const warnings = screen.getByRole('region', { name: 'Warnings' })
      expect(warnings).toHaveTextContent('Warnings (1)')
      expect(warnings).toHaveTextContent('Vendor name: The vendor name was hard to read')
      expect(warnings).toHaveClass('bg-amber-50')

      expect(screen.queryByText('No problems found.')).not.toBeInTheDocument()
      expect(field(/^Total/)).toHaveAttribute('aria-invalid', 'true') // the flagged field is marked
      expect(field(/^Total/)).toHaveClass('border-red-400')
      expect(field(/^Vendor name/)).toHaveClass('border-amber-400')
    })
  })

  describe('duplicates', () => {
    it('an exact duplicate links to the other document and blocks Confirm', async () => {
      await open(new FakeBackend(makeDoc({ duplicate: { kind: 'exact', document_id: 2, reason: 'Document #2 is already saved with the same vendor and invoice number.' } })))
      const panel = screen.getByRole('region', { name: 'Duplicate check' })
      expect(panel).toHaveTextContent('Exact duplicate')
      expect(panel).toHaveTextContent('already saved with the same vendor')
      expect(within(panel).getByRole('link', { name: 'Open document #2' })).toHaveAttribute('href', '/documents/2')
      expect(confirmButton()).toBeDisabled()
      expect(screen.getByText(/exact duplicate of document #2, so it cannot be confirmed/)).toBeInTheDocument()
    })

    it('a possible duplicate links to it as a warning only: Confirm stays available', async () => {
      await open(new FakeBackend(makeDoc({ duplicate: { kind: 'possible', document_id: 3, reason: 'Document #3 has a similar vendor and the same total.' } })))
      const panel = screen.getByRole('region', { name: 'Duplicate check' })
      expect(panel).toHaveTextContent('Possible duplicate')
      expect(panel).toHaveClass('bg-amber-50')
      expect(within(panel).getByRole('link', { name: 'Open document #3' })).toHaveAttribute('href', '/documents/3')
      expect(confirmButton()).toBeEnabled()
    })
  })

  describe('editing and saving', () => {
    it('sends only the changed fields, with the edited line items, and shows the re-validated issues', async () => {
      const backend = new FakeBackend(makeDoc({ issues: [TOTAL_ERROR], data: { ...makeDoc().data, low_confidence_fields: ['tax'] } }))
      backend.onPatch = (body, doc) => ({ ...doc, data: { ...doc.data, ...body }, issues: [VENDOR_WARNING] }) // the server re-validated: the error is gone
      const { user, queryClient } = await open(backend)
      queryClient.setQueryData(['documents', 'all'], [summary({ id: 5 })]) // a list the page should mark out of date

      await retype(user, field(/^Vendor name/), 'Blue Harbor Ltd')
      await retype(user, field(/^Total/), '1,250.50') // a comma and decimals are fine
      await retype(user, field(/Line 2 amount/), '350')
      await user.click(screen.getByRole('button', { name: 'Add line' }))
      await user.type(field(/Line 3 description/), 'Handling')
      await user.type(field(/Line 3 amount/), '40')
      expect(screen.getByText('You have unsaved changes.')).toBeInTheDocument()

      await user.click(saveButton())

      await waitFor(() => expect(backend.only('PATCH')).toHaveLength(1))
      expect(backend.only('PATCH')[0]).toEqual({
        method: 'PATCH',
        path: '/documents/5',
        body: {
          vendor_name: 'Blue Harbor Ltd', // only what changed: not tax, not the dates (so the low-confidence flag on "tax" is not cleared)
          total: 1250.5,
          line_items: [
            { description: 'Freight', quantity: 2, unit_price: 100, amount: 200 },
            { description: 'Storage', quantity: 3, unit_price: 100, amount: 350 },
            { description: 'Handling', quantity: null, unit_price: null, amount: 40 },
          ],
        },
      })

      expect(await screen.findByText(/Changes saved/)).toBeInTheDocument()
      expect(screen.queryByRole('region', { name: 'Errors' })).not.toBeInTheDocument() // the response's issues are shown
      expect(screen.getByRole('region', { name: 'Warnings' })).toHaveTextContent('Vendor name')
      expect(screen.queryByText('You have unsaved changes.')).not.toBeInTheDocument()
      expect(saveButton()).toBeDisabled()
      expect(queryClient.getQueryState(['documents', 'all'])?.isInvalidated).toBe(true) // the Documents list is marked stale
    })

    it('sends a single changed field alone, and a cleared field as null', async () => {
      const backend = new FakeBackend()
      const { user } = await open(backend)
      await retype(user, field(/^Invoice number/), 'INV-78')
      await retype(user, field(/^Due date/), '')
      await user.click(saveButton())
      await waitFor(() => expect(backend.only('PATCH')).toHaveLength(1))
      expect(backend.only('PATCH')[0]?.body).toEqual({ invoice_number: 'INV-78', due_date: null })
    })

    it('removing a line sends the remaining lines', async () => {
      const backend = new FakeBackend()
      const { user } = await open(backend)
      await user.click(screen.getByRole('button', { name: 'Remove line 1' }))
      await user.click(saveButton())
      await waitFor(() => expect(backend.only('PATCH')).toHaveLength(1))
      expect(backend.only('PATCH')[0]?.body).toEqual({ line_items: [{ description: 'Storage', quantity: 3, unit_price: 100, amount: 300 }] })
    })

    it('a blank extra line, or the same value with spaces, is not a change', async () => {
      const { user } = await open(new FakeBackend())
      await user.click(screen.getByRole('button', { name: 'Add line' }))
      expect(saveButton()).toBeDisabled()
      await user.type(field(/^Vendor name/), '   ')
      await user.type(field(/^Total/), '.00') // 560.00 is the same number as 560
      expect(saveButton()).toBeDisabled()
      expect(screen.queryByText('You have unsaved changes.')).not.toBeInTheDocument()
    })

    it('a value that is not a number is explained, and Save stays off (nothing is sent)', async () => {
      const backend = new FakeBackend()
      const { user } = await open(backend)
      await retype(user, field(/^Total/), 'abc')
      expect(screen.getByRole('alert')).toHaveTextContent('Total must be a number (you typed "abc")')
      expect(saveButton()).toBeDisabled()
      await retype(user, field(/Line 1 quantity/), 'two')
      expect(screen.getAllByRole('alert').some((a) => a.textContent?.includes('line 1 quantity'))).toBe(true)
      expect(backend.only('PATCH')).toHaveLength(0)
    })

    it('shows the backend message when a save is refused, and keeps the edits', async () => {
      const backend = new FakeBackend()
      const { user } = await open(backend)
      mockFetch((_url, init) => (init?.method === 'PATCH' ? json({ detail: 'currency: Value error' }, 422) : json(backend.doc)))
      await retype(user, field(/^Currency/), 'zzz')
      await user.click(saveButton())
      expect(await screen.findByText(/Could not save the changes\. currency: Value error/)).toBeInTheDocument()
      expect(field(/^Currency/)).toHaveValue('zzz') // not lost
    })
  })

  describe('Confirm', () => {
    it('is blocked while the document has errors, and says why', async () => {
      await open(new FakeBackend(makeDoc({ issues: [TOTAL_ERROR] })))
      expect(confirmButton()).toBeDisabled()
      expect(screen.getByText('Fix the 1 error listed above, then save.')).toBeInTheDocument()
    })

    it('is blocked by unsaved edits until they are saved', async () => {
      const { user } = await open(new FakeBackend())
      expect(confirmButton()).toBeEnabled()

      await retype(user, field(/^Vendor name/), 'Someone Else')
      expect(confirmButton()).toBeDisabled()
      expect(screen.getByText('Save your changes first.')).toBeInTheDocument()

      await user.click(saveButton())
      await waitFor(() => expect(confirmButton()).toBeEnabled())
      expect(screen.queryByText('Save your changes first.')).not.toBeInTheDocument()
    })

    it('asks "Are you sure?" in the page (no browser dialog), then confirms, goes read-only and marks the list stale', async () => {
      const browserDialog = vi.spyOn(window, 'confirm').mockReturnValue(true)
      const backend = new FakeBackend()
      const { user, queryClient } = await open(backend)
      queryClient.setQueryData(['documents', 'all'], [summary({ id: 5 })])

      await user.click(confirmButton())
      const sure = screen.getByRole('alertdialog', { name: 'Are you sure?' })
      expect(sure).toHaveTextContent('A confirmed document can no longer be edited')
      expect(backend.only('POST')).toHaveLength(0) // nothing is sent until the second click

      await user.click(within(sure).getByRole('button', { name: 'Cancel' }))
      expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
      expect(backend.only('POST')).toHaveLength(0)

      await user.click(confirmButton())
      await user.click(screen.getByRole('button', { name: 'Yes, confirm' }))

      expect(await screen.findByText(/Document confirmed/)).toBeInTheDocument()
      expect(backend.only('POST')).toEqual([{ method: 'POST', path: '/documents/5/confirm', body: { override_issues: false } }]) // never overrides errors
      expect(within(screen.getByRole('heading', { name: /Document #5/ })).getByText('Confirmed')).toBeInTheDocument()
      expect(screen.queryAllByRole('textbox')).toHaveLength(0) // now read-only
      expect(screen.queryByRole('button', { name: 'Confirm' })).not.toBeInTheDocument()
      expect(queryClient.getQueryState(['documents', 'all'])?.isInvalidated).toBe(true)
      expect(browserDialog).not.toHaveBeenCalled()
    })

    it('shows the backend message when a confirm is refused, and reads the document again', async () => {
      const backend = new FakeBackend()
      const { user } = await open(backend)
      // from here on the backend has found that the document duplicates a saved one: the confirm is refused, and any later read shows the duplicate
      const fetchMock = mockFetch((_url, init) => {
        if (init?.method === 'POST') return json({ detail: { message: 'This is a duplicate of a saved document, so it cannot be saved again.', duplicate: {} } }, 409)
        return json(makeDoc({ duplicate: { kind: 'exact', document_id: 2, reason: 'Duplicate of #2.' } }))
      })
      await user.click(confirmButton())
      await user.click(screen.getByRole('button', { name: 'Yes, confirm' }))
      expect(await screen.findByText(/Could not confirm the document\. This is a duplicate of a saved document/)).toBeInTheDocument()
      expect(await screen.findByRole('region', { name: 'Duplicate check' })).toHaveTextContent('Exact duplicate') // the page re-read it
      expect(fetchMock.mock.calls.filter(([, init]) => (init?.method ?? 'GET') === 'GET')).toHaveLength(1) // exactly one fresh read, not a loop
      expect(confirmButton()).toBeDisabled()
    })
  })

  it('a confirmed document is read-only: no inputs and no buttons, just the values', async () => {
    await open(new FakeBackend(makeDoc({ status: 'confirmed', confirmed_at: '2026-11-18T09:00:00Z' })))
    expect(screen.queryAllByRole('textbox')).toHaveLength(0)
    expect(screen.queryAllByRole('combobox')).toHaveLength(0)
    expect(screen.queryAllByRole('button')).toHaveLength(0) // no Save, Confirm, Add line or Remove
    expect(within(screen.getByRole('heading', { name: /Document #5/ })).getByText('Confirmed')).toBeInTheDocument()
    expect(screen.getByText(/Confirmed 18 Nov 2026/)).toBeInTheDocument()
    expect(screen.getByText('Blue Harbor Logistics')).toBeInTheDocument()
    expect(screen.getByText('€560.00')).toBeInTheDocument()
    expect(screen.getByText('10 Mar 2024')).toBeInTheDocument()
    expect(screen.getByText('Storage')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Open original file/ })).toBeInTheDocument() // links still work
  })

  describe('leaving with unsaved edits', () => {
    it('asks in the page before an in-app link leaves, and lets you stay or go', async () => {
      const { user, router } = await open(new FakeBackend())
      await retype(user, field(/^Vendor name/), 'Changed')

      await user.click(screen.getByRole('link', { name: /Back to the documents/ }))
      const dialog = await screen.findByRole('alertdialog', { name: 'Unsaved changes' })
      expect(router.state.location.pathname).toBe('/documents/5') // still here

      await user.click(within(dialog).getByRole('button', { name: 'Stay on this page' }))
      expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
      expect(field(/^Vendor name/)).toHaveValue('Changed') // the edit is still there

      await user.click(screen.getByRole('link', { name: /Back to the documents/ }))
      await user.click(await screen.findByRole('button', { name: 'Leave without saving' }))
      await waitFor(() => expect(router.state.location.pathname).toBe('/documents'))
    })

    it('does not ask when there is nothing unsaved, or after saving', async () => {
      const { user, router } = await open(new FakeBackend())
      await retype(user, field(/^Vendor name/), 'Changed')
      await user.click(saveButton())
      await screen.findByText(/Changes saved/)
      await user.click(screen.getByRole('link', { name: /Back to the documents/ }))
      await waitFor(() => expect(router.state.location.pathname).toBe('/documents'))
      expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    })

    it('the browser warns before closing or reloading the tab only while there are unsaved edits', async () => {
      const { user } = await open(new FakeBackend())
      const close = () => {
        const event = new Event('beforeunload', { cancelable: true })
        window.dispatchEvent(event)
        return event.defaultPrevented
      }
      expect(close()).toBe(false)
      await retype(user, field(/^Vendor name/), 'Changed')
      expect(close()).toBe(true)
      await user.click(saveButton())
      await screen.findByText(/Changes saved/)
      expect(close()).toBe(false)
    })
  })

  describe('the Back link', () => {
    it('keeps the list filter it came from', async () => {
      mockFetch((url) => (url.pathname === '/documents' ? json([summary({ id: 5, status: 'draft', vendor_name: 'Blue Harbor Logistics' })]) : json(makeDoc())))
      const { user, router } = renderAppAt('/documents?status=draft')
      await user.click(await screen.findByRole('link', { name: 'Open document 5' }))
      const back = await screen.findByRole('link', { name: /Back to the documents/ })
      expect(back).toHaveAttribute('href', '/documents?status=draft')
      await user.click(back)
      await waitFor(() => expect(router.state.location.search).toBe('?status=draft'))
      expect(await screen.findByLabelText('Status')).toHaveValue('draft')
    })

    it('goes to the plain list when the page was opened directly', async () => {
      await open(new FakeBackend())
      expect(screen.getByRole('link', { name: /Back to the documents/ })).toHaveAttribute('href', '/documents')
    })
  })

  describe('loading, errors and not found', () => {
    it('shows a loading state first', async () => {
      mockFetch(() => new Promise<Response>(() => {})) // never answers
      renderApp('/documents/5')
      expect(screen.getByRole('status')).toHaveTextContent('Loading document #5')
    })

    it('says "document not found" for a 404, with the way back', async () => {
      mockFetch(() => json({ detail: 'Document 99 was not found.' }, 404))
      renderApp('/documents/99')
      expect(await screen.findByRole('heading', { name: 'Document not found' })).toBeInTheDocument()
      expect(screen.getByText(/There is no document #99/)).toBeInTheDocument()
      expect(screen.getByRole('link', { name: /Back to the documents/ })).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument() // retrying a 404 is pointless
    })

    it('does not even ask the backend for an id that is not a number', async () => {
      const fetchMock = mockFetch(() => json({}))
      renderApp('/documents/abc')
      expect(await screen.findByRole('heading', { name: 'Document not found' })).toBeInTheDocument()
      expect(screen.getByText(/"abc" is not a document number/)).toBeInTheDocument()
      expect(fetchMock).not.toHaveBeenCalled()
    })

    it('shows the error with a Retry that loads the document', async () => {
      let healthy = false
      mockFetch(() => (healthy ? json(makeDoc()) : json({ detail: 'The database is down.' }, 500)))
      renderApp('/documents/5')
      const alert = await screen.findByRole('alert')
      expect(alert).toHaveTextContent('Could not load document #5.')
      expect(alert).toHaveTextContent('The database is down.')

      healthy = true
      await userEvent.setup().click(screen.getByRole('button', { name: 'Retry' }))
      expect(await screen.findByRole('heading', { name: /Document #5/ })).toBeInTheDocument()
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    })

    it('explains when the backend cannot be reached', async () => {
      mockFetch(() => {
        throw new TypeError('Failed to fetch')
      })
      renderApp('/documents/5')
      expect(await screen.findByRole('alert')).toHaveTextContent('Could not reach the backend')
    })
  })
})

/** The app at a URL, with a user to click with. */
function renderAppAt(url: string) {
  return { ...renderApp(url), user: userEvent.setup() }
}

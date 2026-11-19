import type { RouteObject } from 'react-router'
import Layout from './components/Layout'
import AgentPage from './pages/AgentPage'
import DocumentDetailPage from './pages/DocumentDetailPage'
import DocumentsPage from './pages/DocumentsPage'
import HomePage from './pages/HomePage'
import NotFoundPage from './pages/NotFoundPage'
import UploadPage from './pages/UploadPage'

/** The app's pages. A route table (not <Routes>) so the app runs on a data router, which `useBlocker` (the unsaved-changes warning) needs. */
export const routes: RouteObject[] = [
  {
    element: <Layout />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'documents', element: <DocumentsPage /> },
      { path: 'documents/:id', element: <DocumentDetailPage /> },
      { path: 'upload', element: <UploadPage /> },
      { path: 'agent', element: <AgentPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]

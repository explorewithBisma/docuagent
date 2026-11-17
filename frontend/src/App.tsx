import { Route, Routes } from 'react-router'
import Layout from './components/Layout'
import AgentPage from './pages/AgentPage'
import DocumentDetailPage from './pages/DocumentDetailPage'
import DocumentsPage from './pages/DocumentsPage'
import HomePage from './pages/HomePage'
import NotFoundPage from './pages/NotFoundPage'
import UploadPage from './pages/UploadPage'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<HomePage />} />
        <Route path="documents" element={<DocumentsPage />} />
        <Route path="documents/:id" element={<DocumentDetailPage />} />
        <Route path="upload" element={<UploadPage />} />
        <Route path="agent" element={<AgentPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}

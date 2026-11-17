import { Link, useParams } from 'react-router'
import Placeholder from '../components/Placeholder'

export default function DocumentDetailPage() {
  const { id } = useParams()
  return (
    <>
      <Placeholder title={`Document #${id ?? '?'}`} note="Not built yet: the document details and review screen (step 3) will go here." />
      <Link to="/documents" className="mt-4 inline-block text-sm text-blue-700 underline">
        ← Back to the documents
      </Link>
    </>
  )
}

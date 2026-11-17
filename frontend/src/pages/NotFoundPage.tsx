import { Link } from 'react-router'
import Placeholder from '../components/Placeholder'

export default function NotFoundPage() {
  return (
    <>
      <Placeholder title="Page not found" note="There is nothing at this address." />
      <Link to="/" className="mt-4 inline-block text-sm text-blue-700 underline">
        Back to the home page
      </Link>
    </>
  )
}

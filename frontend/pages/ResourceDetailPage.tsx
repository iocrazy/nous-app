import { useParams } from 'react-router-dom';
import { ResourceDetail } from '../components/ResourceDetail';

export function ResourceDetailPage() {
  const { resourceId } = useParams<{ resourceId: string }>();

  if (!resourceId) {
    return null;
  }

  return <ResourceDetail resourceId={resourceId} />;
}

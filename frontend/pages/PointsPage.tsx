import { useState } from 'react';
import { PointPackage } from '../types';
import { PointsCenter } from '../components/PointsCenter';
import { PaymentModal } from '../components/PaymentModal';
import { useTeamContext } from '../contexts/TeamContext';

export function PointsPage() {
  const { selectedTeamId } = useTeamContext();
  const [selectedPaymentPackage, setSelectedPaymentPackage] = useState<PointPackage | null>(null);

  return (
    <>
      <PointsCenter teamId={selectedTeamId ?? undefined} onBuyPackage={(pkg) => setSelectedPaymentPackage(pkg)} />
      {selectedPaymentPackage && selectedTeamId && (
        <PaymentModal
          package={selectedPaymentPackage}
          teamId={selectedTeamId}
          onClose={() => setSelectedPaymentPackage(null)}
          onSuccess={() => setSelectedPaymentPackage(null)}
        />
      )}
    </>
  );
}

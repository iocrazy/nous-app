import { useState } from 'react';
import { PointPackage } from '../types';
import { BillingView } from '../components/BillingView';
import { PaymentModal } from '../components/PaymentModal';
import { useTeamContext } from '../contexts/TeamContext';

export function BillingPage() {
  const { selectedTeamId, userPermissions } = useTeamContext();
  const [selectedPaymentPackage, setSelectedPaymentPackage] = useState<PointPackage | null>(null);

  if (!selectedTeamId) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-zinc-500">
        <p className="text-lg font-medium text-zinc-400">No team selected</p>
        <p className="text-sm mt-1">Select a team to view billing</p>
      </div>
    );
  }

  return (
    <>
      <BillingView
        teamId={selectedTeamId}
        permissions={userPermissions}
        onBuyPackage={(pkg) => setSelectedPaymentPackage(pkg)}
      />
      {selectedPaymentPackage && (
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

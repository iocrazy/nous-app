import React, { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { X, Coins, AlertTriangle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { checkQuota } from '../services/pointsService';
import { QuotaCheck } from '../types';

interface PointsConfirmDialogProps {
  isOpen: boolean;
  actionType: string;
  actionCount?: number;
  onConfirm: () => void;
  onClose: () => void;
}

export const PointsConfirmDialog: React.FC<PointsConfirmDialogProps> = ({
  isOpen,
  actionType,
  actionCount = 1,
  onConfirm,
  onClose,
}) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { teamId } = useParams();
  const [quotaInfo, setQuotaInfo] = useState<QuotaCheck | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    setIsLoading(true);
    setError(null);

    checkQuota(actionType, actionCount)
      .then((data) => {
        setQuotaInfo(data);
      })
      .catch((err) => {
        // 402 means insufficient — parse cost info from error if possible
        setError(err.message);
        setQuotaInfo(null);
      })
      .finally(() => setIsLoading(false));
  }, [isOpen, actionType, actionCount]);

  if (!isOpen) return null;

  const hasError = error != null && quotaInfo == null;
  const insufficient = quotaInfo != null && !quotaInfo.allowed;
  const cost = quotaInfo?.points_cost ?? 0;
  const balance = quotaInfo?.current_balance ?? 0;
  const remaining = balance - cost;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />
      <div className="relative bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl w-full max-w-sm mx-4 animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-zinc-800">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-indigo-500/20 rounded-lg">
              <Coins size={20} className="text-indigo-400" />
            </div>
            <h2 className="text-lg font-semibold text-white">
              {t('points.confirmAction', 'Confirm Action')}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="p-2 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors"
          >
            <X size={20} />
          </button>
        </div>

        {/* Body */}
        <div className="p-5 space-y-4">
          {isLoading ? (
            <div className="flex items-center justify-center py-6">
              <div className="w-5 h-5 rounded-full border-2 border-zinc-600 border-t-indigo-400 animate-spin" />
              <span className="ml-2 text-sm text-zinc-400">{t('common.loading', 'Loading...')}</span>
            </div>
          ) : hasError ? (
            /* API connection error — show error, allow proceeding */
            <div className="flex items-center gap-2 p-3 bg-amber-500/10 border border-amber-500/20 rounded-lg">
              <AlertTriangle size={16} className="text-amber-400 flex-shrink-0" />
              <span className="text-sm text-amber-400">
                {error}
              </span>
            </div>
          ) : (
            <>
              {/* Cost display */}
              <div className="bg-zinc-800/50 rounded-xl p-4 space-y-3">
                <div className="flex justify-between text-sm">
                  <span className="text-zinc-400">
                    {t('points.actionWillCost', 'This will cost {{cost}} points', { cost })}
                  </span>
                  <span className="text-white font-semibold">{cost} pts</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-zinc-400">
                    {t('points.currentBalance', 'Current Balance: {{balance}} points', { balance })}
                  </span>
                  <span className="text-zinc-300">{balance} pts</span>
                </div>
                <div className="border-t border-zinc-700" />
                <div className="flex justify-between text-sm">
                  <span className="text-zinc-400">
                    {t('points.remainingAfter', 'Remaining: {{remaining}} points', { remaining: Math.max(remaining, 0) })}
                  </span>
                  <span className={remaining >= 0 ? 'text-green-400 font-semibold' : 'text-red-400 font-semibold'}>
                    {Math.max(remaining, 0)} pts
                  </span>
                </div>
              </div>

              {/* Insufficient warning */}
              {insufficient && (
                <div className="flex items-center gap-2 p-3 bg-red-500/10 border border-red-500/20 rounded-lg">
                  <AlertTriangle size={16} className="text-red-400 flex-shrink-0" />
                  <span className="text-sm text-red-400">
                    {t('points.insufficientForAction', 'Insufficient points for this action')}
                  </span>
                </div>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex gap-3 p-5 border-t border-zinc-800">
          <button
            type="button"
            onClick={onClose}
            className="flex-1 px-4 py-3 text-zinc-300 bg-zinc-800 hover:bg-zinc-700 rounded-xl font-medium transition-colors"
          >
            {t('common.cancel', 'Cancel')}
          </button>
          {insufficient ? (
            <button
              type="button"
              onClick={() => { onClose(); navigate(teamId ? `/t/${teamId}/points` : '/points'); }}
              className="flex-1 px-4 py-3 text-white bg-amber-500 hover:bg-amber-400 rounded-xl font-medium transition-colors"
            >
              {t('points.buyPoints', 'Buy Points')}
            </button>
          ) : (
            <button
              type="button"
              onClick={onConfirm}
              disabled={isLoading}
              className="flex-1 px-4 py-3 text-white bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-600/50 disabled:cursor-not-allowed rounded-xl font-medium transition-colors"
            >
              {t('points.confirmAndProceed', 'Confirm & Proceed')}
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

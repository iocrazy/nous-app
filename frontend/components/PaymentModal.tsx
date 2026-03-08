import React, { useState, useEffect, useRef, useCallback } from 'react';
import { X, Wallet, CreditCard, Loader2, CheckCircle2, AlertCircle } from 'lucide-react';
import { QRCodeSVG } from 'qrcode.react';
import { createOrder, pollOrderStatus } from '../services/paymentService';
import { PointPackage } from '../types';

const MAX_POLL_ATTEMPTS = 100; // 100 × 3s = 5 minutes max

type PaymentStep = 'select' | 'processing' | 'success' | 'error';
type PaymentMethod = 'wechat' | 'alipay';

interface PaymentModalProps {
  package: PointPackage;
  teamId: string;
  onClose: () => void;
  onSuccess: () => void;
}

const formatPrice = (cents: number): string => `\u00a5${(cents / 100).toFixed(2)}`;

export const PaymentModal: React.FC<PaymentModalProps> = ({
  package: pkg,
  teamId,
  onClose,
  onSuccess,
}) => {
  const [step, setStep] = useState<PaymentStep>('select');
  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod>('wechat');
  const [isCreatingOrder, setIsCreatingOrder] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [paymentUrl, setPaymentUrl] = useState<string | null>(null);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const successTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const cleanupTimers = useCallback(() => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
    if (successTimeoutRef.current) {
      clearTimeout(successTimeoutRef.current);
      successTimeoutRef.current = null;
    }
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      cleanupTimers();
    };
  }, [cleanupTimers]);

  // Handle Escape key
  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        cleanupTimers();
        onClose();
      }
    };
    document.addEventListener('keydown', handleEsc);
    return () => document.removeEventListener('keydown', handleEsc);
  }, [cleanupTimers, onClose]);

  const handleClose = () => {
    cleanupTimers();
    onClose();
  };

  const startPolling = (orderId: string) => {
    let attempts = 0;
    pollingRef.current = setInterval(async () => {
      attempts++;
      if (attempts > MAX_POLL_ATTEMPTS) {
        cleanupTimers();
        setErrorMessage('Payment timed out. Please check your order status later.');
        setStep('error');
        return;
      }
      try {
        const status = await pollOrderStatus(orderId);
        if (status.payment_status === 'paid') {
          cleanupTimers();
          setStep('success');
          successTimeoutRef.current = setTimeout(() => {
            onSuccess();
          }, 2000);
        } else if (status.payment_status === 'failed' || status.payment_status === 'expired') {
          cleanupTimers();
          setErrorMessage(
            status.payment_status === 'expired'
              ? 'Payment has expired. Please try again.'
              : 'Payment failed. Please try again.'
          );
          setStep('error');
        }
      } catch {
        // Silently continue polling on network errors
      }
    }, 3000);
  };

  const handleConfirmPayment = async () => {
    setIsCreatingOrder(true);
    setErrorMessage(null);

    try {
      const order = await createOrder(pkg.id, paymentMethod, teamId);
      setPaymentUrl(order.payment_url || null);
      setStep('processing');
      startPolling(order.id);
    } catch (err: unknown) {
      const errorObj = err as { message?: string };
      setErrorMessage(errorObj?.message || 'Failed to create order');
      setStep('error');
    } finally {
      setIsCreatingOrder(false);
    }
  };

  const handleRetry = () => {
    cleanupTimers();
    setStep('select');
    setErrorMessage(null);
    setPaymentUrl(null);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={handleClose}
      />

      {/* Modal */}
      <div className="relative bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl w-full max-w-md mx-4 animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-zinc-800">
          <div className="flex-1 min-w-0">
            <h2 className="text-lg font-semibold text-white">{pkg.name}</h2>
            <div className="flex items-center gap-3 mt-1">
              <span className="text-amber-400 font-medium">
                {pkg.points_amount.toLocaleString()} Points
              </span>
              <span className="text-zinc-500">|</span>
              <span className="text-zinc-300 font-medium">
                {formatPrice(pkg.price_cents)}
              </span>
            </div>
          </div>
          <button
            onClick={handleClose}
            className="p-2 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors"
          >
            <X size={20} />
          </button>
        </div>

        {/* Content */}
        <div className="p-6">
          {/* Select Step */}
          {step === 'select' && (
            <div className="space-y-6">
              {/* Payment method tabs */}
              <div>
                <p className="text-sm text-zinc-400 mb-3">Payment Method</p>
                <div className="flex gap-3">
                  <button
                    onClick={() => setPaymentMethod('wechat')}
                    className={`flex-1 flex items-center justify-center gap-2 px-4 py-3 rounded-xl border text-sm font-medium transition-all ${
                      paymentMethod === 'wechat'
                        ? 'border-amber-500/50 bg-amber-500/10 text-amber-400'
                        : 'border-zinc-700 bg-zinc-800/50 text-zinc-400 hover:border-zinc-600 hover:text-zinc-300'
                    }`}
                  >
                    <Wallet size={18} />
                    WeChat Pay
                  </button>
                  <button
                    onClick={() => setPaymentMethod('alipay')}
                    className={`flex-1 flex items-center justify-center gap-2 px-4 py-3 rounded-xl border text-sm font-medium transition-all ${
                      paymentMethod === 'alipay'
                        ? 'border-amber-500/50 bg-amber-500/10 text-amber-400'
                        : 'border-zinc-700 bg-zinc-800/50 text-zinc-400 hover:border-zinc-600 hover:text-zinc-300'
                    }`}
                  >
                    <CreditCard size={18} />
                    Alipay
                  </button>
                </div>
              </div>

              {/* Order summary */}
              <div className="p-4 bg-zinc-800/50 rounded-xl space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-zinc-400">Package</span>
                  <span className="text-zinc-200">{pkg.name}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-zinc-400">Points</span>
                  <span className="text-amber-400">{pkg.points_amount.toLocaleString()}</span>
                </div>
                <div className="border-t border-zinc-700 my-2" />
                <div className="flex justify-between text-sm font-medium">
                  <span className="text-zinc-300">Total</span>
                  <span className="text-white text-lg">{formatPrice(pkg.price_cents)}</span>
                </div>
              </div>

              {/* Confirm button */}
              <button
                onClick={handleConfirmPayment}
                disabled={isCreatingOrder}
                className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-amber-600 hover:bg-amber-500 disabled:bg-amber-600/50 disabled:cursor-not-allowed text-white rounded-xl font-medium transition-colors"
              >
                {isCreatingOrder ? (
                  <>
                    <Loader2 size={18} className="animate-spin" />
                    Creating Order...
                  </>
                ) : (
                  'Confirm Payment'
                )}
              </button>
            </div>
          )}

          {/* Processing Step */}
          {step === 'processing' && (
            <div className="space-y-6 text-center py-4">
              <div className="flex justify-center">
                <div className="relative">
                  <Loader2 size={48} className="animate-spin text-amber-400" />
                </div>
              </div>

              <div>
                <h3 className="text-lg font-semibold text-white mb-2">
                  Waiting for Payment
                </h3>
                <p className="text-sm text-zinc-400">
                  Please complete the payment in your{' '}
                  {paymentMethod === 'wechat' ? 'WeChat' : 'Alipay'} app
                </p>
              </div>

              {/* Payment URL / QR code placeholder */}
              {paymentUrl && (
                <div className="p-4 bg-zinc-800/50 rounded-xl">
                  <div className="w-48 h-48 mx-auto bg-white rounded-lg flex items-center justify-center p-2">
                    <QRCodeSVG
                      value={paymentUrl}
                      size={176}
                      level="M"
                      includeMargin={false}
                    />
                  </div>
                  <p className="text-xs text-zinc-500 mt-3">
                    Scan with {paymentMethod === 'wechat' ? 'WeChat' : 'Alipay'} to pay
                  </p>
                </div>
              )}

              {!paymentUrl && (
                <div className="p-4 bg-zinc-800/50 rounded-xl">
                  <p className="text-sm text-zinc-400">
                    Order created. Checking payment status...
                  </p>
                </div>
              )}

              <div className="flex items-center justify-center gap-2 text-xs text-zinc-500">
                <Loader2 size={12} className="animate-spin" />
                Checking payment status...
              </div>

              <button
                onClick={handleClose}
                className="text-sm text-zinc-500 hover:text-zinc-300 transition-colors"
              >
                Cancel
              </button>
            </div>
          )}

          {/* Success Step */}
          {step === 'success' && (
            <div className="space-y-4 text-center py-8">
              <div className="flex justify-center">
                <div className="p-3 bg-green-500/20 rounded-full">
                  <CheckCircle2 size={48} className="text-green-400" />
                </div>
              </div>
              <div>
                <h3 className="text-xl font-semibold text-white mb-2">
                  Payment Successful!
                </h3>
                <p className="text-sm text-zinc-400">
                  <span className="text-amber-400 font-medium">
                    {pkg.points_amount.toLocaleString()}
                  </span>{' '}
                  points have been added to your account
                </p>
              </div>
            </div>
          )}

          {/* Error Step */}
          {step === 'error' && (
            <div className="space-y-6 text-center py-4">
              <div className="flex justify-center">
                <div className="p-3 bg-red-500/20 rounded-full">
                  <AlertCircle size={48} className="text-red-400" />
                </div>
              </div>
              <div>
                <h3 className="text-lg font-semibold text-white mb-2">
                  Payment Failed
                </h3>
                {errorMessage && (
                  <p className="text-sm text-red-400">{errorMessage}</p>
                )}
              </div>
              <div className="flex gap-3">
                <button
                  onClick={handleClose}
                  className="flex-1 px-4 py-3 text-zinc-300 bg-zinc-800 hover:bg-zinc-700 rounded-xl font-medium transition-colors"
                >
                  Close
                </button>
                <button
                  onClick={handleRetry}
                  className="flex-1 px-4 py-3 text-white bg-amber-600 hover:bg-amber-500 rounded-xl font-medium transition-colors"
                >
                  Try Again
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

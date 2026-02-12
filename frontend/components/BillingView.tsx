import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Coins, Package, CreditCard, HardDrive, TrendingUp, BarChart3, Loader2, MessageSquare } from 'lucide-react';
import { TeamQuota, PointTransaction, PointPricing, PointPackage, PaymentOrder } from '../types';
import { fetchPointsBalance, fetchPointsPricing, fetchUsageStats } from '../services/pointsService';
import { fetchPackages, fetchOrders } from '../services/paymentService';

interface BillingViewProps {
  teamId: string;
  permissions: string[];
  onBuyPackage: (pkg: PointPackage) => void;
}

const formatBytes = (bytes: number): string => {
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const k = 1024;
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  const value = bytes / Math.pow(k, i);
  return `${value.toFixed(i === 0 ? 0 : 2)} ${units[i]}`;
};

const formatPrice = (cents: number): string => {
  return `\u00A5${(cents / 100).toFixed(2)}`;
};

const getStatusBadge = (status: string) => {
  const styles: Record<string, string> = {
    pending: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
    paid: 'bg-green-500/20 text-green-400 border-green-500/30',
    failed: 'bg-red-500/20 text-red-400 border-red-500/30',
    expired: 'bg-zinc-700/50 text-zinc-400 border-zinc-600/30',
    refunded: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  };
  return styles[status] || styles.pending;
};

export const BillingView: React.FC<BillingViewProps> = ({ teamId, permissions, onBuyPackage }) => {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(true);
  const [balance, setBalance] = useState<TeamQuota | null>(null);
  const [packages, setPackages] = useState<PointPackage[]>([]);
  const [orders, setOrders] = useState<PaymentOrder[]>([]);
  const [pricing, setPricing] = useState<PointPricing[]>([]);
  const [usageStats, setUsageStats] = useState<any>(null);

  useEffect(() => {
    const loadData = async () => {
      setLoading(true);
      try {
        const [balanceData, pkgData, orderData, pricingData, statsData] = await Promise.all([
          fetchPointsBalance(teamId).catch(() => null),
          fetchPackages().catch(() => []),
          fetchOrders(teamId).catch(() => []),
          fetchPointsPricing().catch(() => []),
          fetchUsageStats(teamId).catch(() => null),
        ]);
        setBalance(balanceData);
        setPackages(pkgData);
        setOrders(orderData);
        setPricing(pricingData);
        setUsageStats(statsData);
      } finally {
        setLoading(false);
      }
    };
    loadData();
  }, [teamId]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-zinc-400">
        <Loader2 className="animate-spin" size={24} />
      </div>
    );
  }

  const storagePercent = balance ? balance.storage_used_percent : 0;

  return (
    <div className="max-w-5xl mx-auto space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
      {/* ── Balance & Storage Hero ─────────────────────────── */}
      <section className="bg-gradient-to-r from-amber-500/10 to-orange-500/10 border border-amber-500/20 rounded-2xl p-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="p-3 bg-amber-500/20 rounded-xl">
              <Coins size={28} className="text-amber-400" />
            </div>
            <div>
              <p className="text-sm text-zinc-400">{t('billing.pointsBalance')}</p>
              <p className="text-4xl font-bold text-amber-400">
                {balance ? balance.points_balance.toLocaleString() : '0'}
              </p>
            </div>
          </div>
          <button
            onClick={() => {
              if (packages.length > 0) {
                // Scroll to packages section
                document.getElementById('billing-packages')?.scrollIntoView({ behavior: 'smooth' });
              }
            }}
            className="px-5 py-2.5 bg-amber-500 hover:bg-amber-400 text-black font-semibold rounded-xl transition-colors"
          >
            {t('billing.buyPoints')}
          </button>
        </div>

        {/* Storage usage bar */}
        {balance && (
          <div className="mt-4">
            <div className="flex items-center justify-between text-xs text-zinc-400 mb-1.5">
              <span className="flex items-center gap-1.5">
                <HardDrive size={12} />
                {t('billing.storageUsage')}
              </span>
              <span>
                {formatBytes(balance.storage_used_bytes)} / {formatBytes(balance.storage_limit_bytes)}{' '}
                ({storagePercent.toFixed(1)}%)
              </span>
            </div>
            <div className="w-full h-2 bg-zinc-800 rounded-full overflow-hidden">
              <div
                className="h-full rounded-full transition-all duration-500 bg-gradient-to-r from-amber-500 to-orange-500"
                style={{ width: `${Math.min(storagePercent, 100)}%` }}
              />
            </div>
          </div>
        )}
      </section>

      {/* ── Package Grid ───────────────────────────────────── */}
      {packages.length > 0 && (
        <section id="billing-packages">
          <div className="flex items-center gap-2 mb-4">
            <Package size={20} className="text-amber-400" />
            <h2 className="text-lg font-semibold text-zinc-100">{t('billing.packages')}</h2>
          </div>

          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {packages.map((pkg) => {
              const unitPrice = pkg.points_amount > 0 ? pkg.price_cents / pkg.points_amount : 0;
              return (
                <button
                  key={pkg.id}
                  onClick={() => onBuyPackage(pkg)}
                  className="bg-zinc-900 border border-zinc-800 hover:border-amber-500/50 rounded-xl p-5 text-left transition-all duration-200 group"
                >
                  <p className="text-3xl font-bold text-zinc-100 group-hover:text-amber-400 transition-colors">
                    {pkg.points_amount.toLocaleString()}
                  </p>
                  <p className="text-xs text-zinc-500 mt-1">{pkg.name}</p>
                  <div className="mt-3 flex items-baseline justify-between">
                    <span className="text-lg font-semibold text-amber-400">
                      {formatPrice(pkg.price_cents)}
                    </span>
                    <span className="text-xs text-zinc-500">
                      {formatPrice(unitPrice)}/pt
                    </span>
                  </div>
                </button>
              );
            })}
          </div>
        </section>
      )}

      {/* ── Team Consumption ───────────────────────────────── */}
      <section>
        <div className="flex items-center gap-2 mb-4">
          <BarChart3 size={20} className="text-amber-400" />
          <h2 className="text-lg font-semibold text-zinc-100">{t('billing.teamConsumption')}</h2>
        </div>

        <div className="border border-zinc-800 rounded-xl p-5 bg-zinc-900">
          <div className="flex items-center gap-8 mb-6">
            <div>
              <p className="text-xs text-zinc-500 uppercase font-medium">{t('billing.pointsUsedThisMonth')}</p>
              <p className="text-2xl font-bold text-white">
                {usageStats?.total_consumed_this_month?.toLocaleString() ?? '0'}
              </p>
            </div>
            {balance && (
              <div>
                <p className="text-xs text-zinc-500 uppercase font-medium">{t('billing.currentBalance')}</p>
                <p className="text-2xl font-bold text-amber-400">
                  {balance.points_balance.toLocaleString()}
                </p>
              </div>
            )}
          </div>

          {/* Top consumers */}
          {usageStats?.top_consumers && usageStats.top_consumers.length > 0 && (
            <div>
              <p className="text-xs text-zinc-500 uppercase font-medium mb-3">{t('billing.topConsumers')}</p>
              <div className="space-y-2">
                {usageStats.top_consumers.map((consumer: any, idx: number) => {
                  const maxUsage = usageStats.top_consumers[0]?.points_used || 1;
                  const widthPercent = (consumer.points_used / maxUsage) * 100;
                  return (
                    <div key={consumer.user_id || idx} className="flex items-center gap-3">
                      <span className="text-sm text-zinc-300 w-32 truncate">
                        {consumer.name || consumer.email || 'Unknown'}
                      </span>
                      <div className="flex-1 h-2 bg-zinc-800 rounded-full overflow-hidden">
                        <div
                          className="h-full rounded-full bg-gradient-to-r from-amber-500 to-orange-500 transition-all duration-500"
                          style={{ width: `${widthPercent}%` }}
                        />
                      </div>
                      <span className="text-xs text-zinc-400 font-mono w-20 text-right">
                        {consumer.points_used?.toLocaleString() ?? 0} pts
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {!usageStats && (
            <p className="text-sm text-zinc-500">No consumption data available yet.</p>
          )}
        </div>
      </section>

      {/* ── Order History ──────────────────────────────────── */}
      <section>
        <div className="flex items-center gap-2 mb-4">
          <CreditCard size={20} className="text-amber-400" />
          <h2 className="text-lg font-semibold text-zinc-100">{t('billing.orderHistory')}</h2>
        </div>

        {orders.length === 0 ? (
          <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-12 text-center text-zinc-500">
            {t('billing.noOrders')}
          </div>
        ) : (
          <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="bg-zinc-800/50 text-zinc-400 text-xs uppercase">
                  <th className="px-5 py-3 font-medium">{t('billing.orderDate')}</th>
                  <th className="px-5 py-3 font-medium">{t('billing.orderPackage')}</th>
                  <th className="px-5 py-3 font-medium text-right">{t('billing.orderPoints')}</th>
                  <th className="px-5 py-3 font-medium text-right">{t('billing.orderAmount')}</th>
                  <th className="px-5 py-3 font-medium text-center">{t('billing.orderMethod')}</th>
                  <th className="px-5 py-3 font-medium text-center">{t('billing.orderStatus')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800/50">
                {orders.map((order) => (
                  <tr key={order.id} className="hover:bg-zinc-800/30 transition-colors">
                    <td className="px-5 py-3 text-zinc-300">
                      {new Date(order.created_at).toLocaleDateString()}
                    </td>
                    <td className="px-5 py-3 text-zinc-300">
                      {order.package_id}
                    </td>
                    <td className="px-5 py-3 text-right font-mono text-amber-400">
                      {order.points_amount.toLocaleString()}
                    </td>
                    <td className="px-5 py-3 text-right text-zinc-300">
                      {formatPrice(order.amount_cents)}
                    </td>
                    <td className="px-5 py-3 text-center">
                      <MessageSquare size={16} className="inline text-zinc-400" />
                      <span className="ml-1 text-xs text-zinc-400">
                        {order.payment_method === 'wechat' ? 'WeChat' : 'Alipay'}
                      </span>
                    </td>
                    <td className="px-5 py-3 text-center">
                      <span className={`px-2 py-0.5 rounded-full text-xs font-medium border ${getStatusBadge(order.payment_status)}`}>
                        {t(`billing.status${order.payment_status.charAt(0).toUpperCase() + order.payment_status.slice(1)}`)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ── Pricing Table ──────────────────────────────────── */}
      {pricing.length > 0 && (
        <section>
          <div className="flex items-center gap-2 mb-4">
            <TrendingUp size={20} className="text-amber-400" />
            <h2 className="text-lg font-semibold text-zinc-100">{t('billing.pricingTable')}</h2>
          </div>

          <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="bg-zinc-800/50 text-zinc-400 text-xs uppercase">
                  <th className="px-6 py-3 font-medium">Action</th>
                  <th className="px-6 py-3 font-medium text-right">Points</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800/50">
                {pricing.map((item) => (
                  <tr key={item.action_type} className="hover:bg-zinc-800/30 transition-colors">
                    <td className="px-6 py-3 text-zinc-300">
                      {item.description || item.action_type}
                    </td>
                    <td className="px-6 py-3 text-right font-mono text-amber-400">
                      {item.points_cost}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
};

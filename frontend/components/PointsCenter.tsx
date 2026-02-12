import React, { useState, useEffect } from 'react';
import { Coins, Package, ArrowUpRight, ArrowDownRight, TrendingUp, HardDrive } from 'lucide-react';
import { TeamQuota, PointTransaction, PointPricing, PointPackage } from '../types';
import {
  fetchPointsBalance,
  fetchPointsTransactions,
  fetchPointsPricing,
  fetchUsageStats,
} from '../services/pointsService';
import { fetchPackages } from '../services/paymentService';

interface PointsCenterProps {
  onBuyPackage: (pkg: PointPackage) => void;
}

/** Convert bytes to human-readable format (B, KB, MB, GB, TB). */
const formatBytes = (bytes: number): string => {
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const k = 1024;
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  const value = bytes / Math.pow(k, i);
  return `${value.toFixed(i === 0 ? 0 : 2)} ${units[i]}`;
};

/** Convert cents to display price in CNY. */
const formatPrice = (cents: number): string => {
  return `\u00A5${(cents / 100).toFixed(2)}`;
};

export const PointsCenter: React.FC<PointsCenterProps> = ({ onBuyPackage }) => {
  const [loading, setLoading] = useState(true);
  const [balance, setBalance] = useState<TeamQuota | null>(null);
  const [transactions, setTransactions] = useState<PointTransaction[]>([]);
  const [pricing, setPricing] = useState<PointPricing[]>([]);
  const [packages, setPackages] = useState<PointPackage[]>([]);
  const [_usageStats, setUsageStats] = useState<any>(null);

  useEffect(() => {
    const loadData = async () => {
      setLoading(true);
      try {
        const [balanceData, txData, pricingData, pkgData, usageData] = await Promise.all([
          fetchPointsBalance().catch(() => null),
          fetchPointsTransactions(undefined, 20).catch(() => []),
          fetchPointsPricing().catch(() => []),
          fetchPackages().catch(() => []),
          fetchUsageStats().catch(() => null),
        ]);

        setBalance(balanceData);
        setTransactions(txData);
        setPricing(pricingData);
        setPackages(pkgData);
        setUsageStats(usageData);
      } finally {
        setLoading(false);
      }
    };

    loadData();
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-zinc-400">
        Loading...
      </div>
    );
  }

  const storagePercent = balance ? balance.storage_used_percent : 0;

  return (
    <div className="max-w-5xl mx-auto space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
      {/* ── Balance Card ────────────────────────────────────── */}
      <section className="bg-gradient-to-r from-amber-500/10 to-orange-500/10 border border-amber-500/20 rounded-2xl p-6">
        <div className="flex items-center gap-4 mb-4">
          <div className="p-3 bg-amber-500/20 rounded-xl">
            <Coins size={28} className="text-amber-400" />
          </div>
          <div>
            <p className="text-sm text-zinc-400">Points Balance</p>
            <p className="text-4xl font-bold text-amber-400">
              {balance ? balance.points_balance.toLocaleString() : '0'}
            </p>
          </div>
        </div>

        {/* Storage usage bar */}
        {balance && (
          <div className="mt-4">
            <div className="flex items-center justify-between text-xs text-zinc-400 mb-1.5">
              <span className="flex items-center gap-1.5">
                <HardDrive size={12} />
                Storage Usage
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

      {/* ── Package Cards ───────────────────────────────────── */}
      {packages.length > 0 && (
        <section>
          <div className="flex items-center gap-2 mb-4">
            <Package size={20} className="text-amber-400" />
            <h2 className="text-lg font-semibold text-zinc-100">Buy Points</h2>
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

      {/* ── Pricing Table ───────────────────────────────────── */}
      {pricing.length > 0 && (
        <section>
          <div className="flex items-center gap-2 mb-4">
            <TrendingUp size={20} className="text-amber-400" />
            <h2 className="text-lg font-semibold text-zinc-100">Pricing</h2>
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

      {/* ── Transaction History ──────────────────────────────── */}
      <section>
        <h2 className="text-lg font-semibold text-zinc-100 mb-4">Recent Transactions</h2>

        {transactions.length === 0 ? (
          <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-12 text-center text-zinc-500">
            No transactions yet
          </div>
        ) : (
          <div className="bg-zinc-900 border border-zinc-800 rounded-xl divide-y divide-zinc-800/50">
            {transactions.map((tx) => {
              const isCredit = tx.amount > 0;
              return (
                <div
                  key={tx.id}
                  className="flex items-center gap-4 px-5 py-4 hover:bg-zinc-800/30 transition-colors"
                >
                  <div
                    className={`p-2 rounded-lg ${
                      isCredit ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'
                    }`}
                  >
                    {isCredit ? <ArrowUpRight size={18} /> : <ArrowDownRight size={18} />}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-zinc-300 truncate">
                      {tx.description || tx.type}
                    </p>
                    <p className="text-xs text-zinc-500">
                      {new Date(tx.created_at).toLocaleString()}
                    </p>
                  </div>
                  <span
                    className={`text-sm font-semibold tabular-nums ${
                      isCredit ? 'text-green-400' : 'text-red-400'
                    }`}
                  >
                    {isCredit ? '+' : ''}
                    {tx.amount}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
};

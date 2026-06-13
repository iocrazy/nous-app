import React, { useState, useEffect, useCallback } from 'react';
import { Coins, Package, ArrowUpRight, ArrowDownRight, TrendingUp, HardDrive, Search, Filter, ExternalLink } from 'lucide-react';
import { TeamQuota, PointTransaction, PointPricing, PointPackage } from '../types';
import {
  fetchPointsBalance,
  fetchPointsTransactions,
  fetchPointsPricing,
  fetchUsageStats,
} from '../services/pointsService';
import { fetchPackages } from '../services/paymentService';

interface PointsCenterProps {
  teamId?: string;
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

/** Reference type options for the action filter dropdown. */
const REFERENCE_TYPE_OPTIONS = [
  { value: '', label: 'All Actions' },
  { value: 'video_parse', label: 'Video Parse' },
  { value: 'ai_transcription', label: 'AI Transcription' },
  { value: 'ai_summary', label: 'AI Summary' },
  { value: 'welcome_bonus', label: 'Welcome Bonus' },
  { value: 'refund', label: 'Refund' },
] as const;

/** Date range options. */
const DATE_RANGE_OPTIONS = [
  { value: 0, label: 'All Time' },
  { value: 7, label: 'Last 7 Days' },
  { value: 30, label: 'Last 30 Days' },
  { value: 90, label: 'Last 90 Days' },
] as const;

export const PointsCenter: React.FC<PointsCenterProps> = ({ teamId, onBuyPackage }) => {
  const [loading, setLoading] = useState(true);
  const [balance, setBalance] = useState<TeamQuota | null>(null);
  const [transactions, setTransactions] = useState<PointTransaction[]>([]);
  const [pricing, setPricing] = useState<PointPricing[]>([]);
  const [packages, setPackages] = useState<PointPackage[]>([]);
  const [_usageStats, setUsageStats] = useState<any>(null);

  // Transaction filters
  const [searchText, setSearchText] = useState('');
  const [referenceTypeFilter, setReferenceTypeFilter] = useState('');
  const [daysFilter, setDaysFilter] = useState(0);
  const [txLoading, setTxLoading] = useState(false);

  const loadTransactions = useCallback(async () => {
    setTxLoading(true);
    try {
      const txData = await fetchPointsTransactions(
        teamId,
        50,
        0,
        undefined,
        referenceTypeFilter || undefined,
        searchText || undefined,
        daysFilter > 0 ? daysFilter : undefined,
      );
      setTransactions(txData);
    } catch {
      setTransactions([]);
    } finally {
      setTxLoading(false);
    }
  }, [teamId, referenceTypeFilter, searchText, daysFilter]);

  useEffect(() => {
    const loadData = async () => {
      setLoading(true);
      try {
        const [balanceData, txData, pricingData, pkgData, usageData] = await Promise.all([
          fetchPointsBalance(teamId).catch(() => null),
          fetchPointsTransactions(teamId, 50).catch(() => []),
          fetchPointsPricing().catch(() => []),
          fetchPackages().catch(() => []),
          fetchUsageStats(teamId).catch(() => null),
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
  }, [teamId]);

  // Reload transactions when filters change (skip initial load)
  useEffect(() => {
    if (!loading) {
      loadTransactions();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [referenceTypeFilter, daysFilter]);

  // Debounced search
  useEffect(() => {
    if (loading) return;
    const timer = setTimeout(() => {
      loadTransactions();
    }, 400);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchText]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-ink-400">
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
            <p className="text-sm text-ink-400">Points Balance</p>
            <p className="text-4xl font-bold text-amber-400">
              {balance ? balance.points_balance.toLocaleString() : '0'}
            </p>
          </div>
        </div>

        {/* Storage usage bar */}
        {balance && (
          <div className="mt-4">
            <div className="flex items-center justify-between text-xs text-ink-400 mb-1.5">
              <span className="flex items-center gap-1.5">
                <HardDrive size={12} />
                Storage Usage
              </span>
              <span>
                {formatBytes(balance.storage_used_bytes)} / {formatBytes(balance.storage_limit_bytes)}{' '}
                ({storagePercent.toFixed(1)}%)
              </span>
            </div>
            <div className="w-full h-2 bg-ink-800 rounded-full overflow-hidden">
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
            <h2 className="text-lg font-semibold text-ink-100">Buy Points</h2>
          </div>

          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {packages.map((pkg) => {
              const unitPrice = pkg.points_amount > 0 ? pkg.price_cents / pkg.points_amount : 0;
              return (
                <button
                  key={pkg.id}
                  onClick={() => onBuyPackage(pkg)}
                  className="bg-ink-900 border border-ink-800 hover:border-amber-500/50 rounded-xl p-5 text-left transition-all duration-200 group"
                >
                  <p className="text-3xl font-bold text-ink-100 group-hover:text-amber-400 transition-colors">
                    {pkg.points_amount.toLocaleString()}
                  </p>
                  <p className="text-xs text-ink-500 mt-1">{pkg.name}</p>
                  <div className="mt-3 flex items-baseline justify-between">
                    <span className="text-lg font-semibold text-amber-400">
                      {formatPrice(pkg.price_cents)}
                    </span>
                    <span className="text-xs text-ink-500">
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
            <h2 className="text-lg font-semibold text-ink-100">Pricing</h2>
          </div>

          <div className="bg-ink-900 border border-ink-800 rounded-xl overflow-hidden">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="bg-ink-800/50 text-ink-400 text-xs uppercase">
                  <th className="px-6 py-3 font-medium">Action</th>
                  <th className="px-6 py-3 font-medium text-right">Points</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-800/50">
                {pricing.map((item) => (
                  <tr key={item.action_type} className="hover:bg-ink-800/30 transition-colors">
                    <td className="px-6 py-3 text-ink-300">
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
        <h2 className="text-lg font-semibold text-ink-100 mb-4">Transaction History</h2>

        {/* Search & Filter Bar */}
        <div className="flex flex-wrap items-center gap-3 mb-4">
          {/* Search input */}
          <div className="relative flex-1 min-w-[200px]">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-500" />
            <input
              type="text"
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              placeholder="Search transactions..."
              className="w-full pl-9 pr-3 py-2 bg-ink-800 border border-ink-700 rounded-lg text-sm text-ink-200 placeholder-ink-500 focus:outline-none focus:border-amber-500/50 transition-colors"
            />
          </div>

          {/* Action type filter */}
          <div className="relative">
            <Filter size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-500 pointer-events-none" />
            <select
              value={referenceTypeFilter}
              onChange={(e) => setReferenceTypeFilter(e.target.value)}
              className="pl-8 pr-8 py-2 bg-ink-800 border border-ink-700 rounded-lg text-sm text-ink-200 focus:outline-none focus:border-amber-500/50 appearance-none cursor-pointer transition-colors"
            >
              {REFERENCE_TYPE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </div>

          {/* Date range filter */}
          <select
            value={daysFilter}
            onChange={(e) => setDaysFilter(Number(e.target.value))}
            className="px-3 py-2 bg-ink-800 border border-ink-700 rounded-lg text-sm text-ink-200 focus:outline-none focus:border-amber-500/50 appearance-none cursor-pointer transition-colors"
          >
            {DATE_RANGE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </div>

        {txLoading ? (
          <div className="bg-ink-900 border border-ink-800 rounded-xl p-12 text-center text-ink-500">
            Loading...
          </div>
        ) : transactions.length === 0 ? (
          <div className="bg-ink-900 border border-ink-800 rounded-xl p-12 text-center text-ink-500">
            No transactions found
          </div>
        ) : (
          <div className="bg-ink-900 border border-ink-800 rounded-xl divide-y divide-ink-800/50">
            {transactions.map((tx) => {
              const isCredit = tx.amount > 0;
              return (
                <div
                  key={tx.id}
                  className="flex items-center gap-4 px-5 py-4 hover:bg-ink-800/30 transition-colors"
                >
                  <div
                    className={`p-2 rounded-lg shrink-0 ${
                      isCredit ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'
                    }`}
                  >
                    {isCredit ? <ArrowUpRight size={18} /> : <ArrowDownRight size={18} />}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-ink-300">
                      {tx.description || tx.type}
                    </p>
                    <div className="flex items-center gap-2 mt-1 flex-wrap">
                      {/* Reference type badge */}
                      {tx.reference_type && (
                        <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-ink-700/60 text-ink-400">
                          {tx.reference_type}
                        </span>
                      )}
                      {/* Reference ID link */}
                      {tx.reference_id && (
                        <a
                          href={`/resources/${tx.reference_id}`}
                          className="inline-flex items-center gap-0.5 text-[10px] text-amber-400/70 hover:text-amber-400 transition-colors"
                          title={`Resource: ${tx.reference_id}`}
                        >
                          <ExternalLink size={10} />
                          <span className="font-mono">{tx.reference_id.slice(0, 8)}...</span>
                        </a>
                      )}
                      <span className="text-[10px] text-ink-600">
                        {new Date(tx.created_at).toLocaleString()}
                      </span>
                    </div>
                  </div>
                  <div className="text-right shrink-0">
                    <span
                      className={`text-sm font-semibold tabular-nums ${
                        isCredit ? 'text-green-400' : 'text-red-400'
                      }`}
                    >
                      {isCredit ? '+' : ''}
                      {tx.amount}
                    </span>
                    {tx.balance_after !== null && tx.balance_after !== undefined && (
                      <p className="text-[10px] text-ink-600 tabular-nums">
                        bal: {tx.balance_after}
                      </p>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
};

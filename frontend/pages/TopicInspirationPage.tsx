// frontend/pages/TopicInspirationPage.tsx
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Lightbulb } from 'lucide-react';
import { islandUI } from '../utils/featureFlags';
import { getHotspots, getHotspotDates, type Hotspot } from '../services/topicService';
import { useToast } from '../components/Toast';

export const TopicInspirationPage: React.FC = () => {
  const { t } = useTranslation();
  const island = islandUI();
  const { addToast } = useToast();
  const [hotspots, setHotspots] = useState<Hotspot[]>([]);
  const [dates, setDates] = useState<string[]>([]);
  const [day, setDay] = useState<string | undefined>(undefined);
  const [category, setCategory] = useState<string>('all');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      try {
        const [hs, ds] = await Promise.all([getHotspots(day, category), getHotspotDates()]);
        if (!alive) return;
        setHotspots(hs);
        setDates(ds);
      } catch (err) {
        if (alive) addToast(`Failed to load topics: ${(err as Error).message}`, 'error');
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [day, category, addToast]);

  const cPrimary = island ? 'text-content' : 'text-ink-50';
  const cSub = island ? 'text-content-3' : 'text-ink-400';

  return (
    <div className="max-w-[1180px] mx-auto px-6 py-6">
      <div className="flex items-center gap-2">
        <Lightbulb size={22} className={island ? 'text-content-2' : 'text-ink-300'} />
        <h1 className={`text-[22px] font-bold ${cPrimary}`}>{t('topic.title')}</h1>
      </div>
      <p className={`text-xs mt-1 ${cSub}`}>{t('topic.subtitle')}</p>
      {/* Timeline + info panel + floating parse mount in later tasks */}
      <div className="mt-4 text-sm text-content-3">
        {loading ? t('common.loading') : `${hotspots.length} hotspots · ${dates.length} days`}
      </div>
    </div>
  );
};
